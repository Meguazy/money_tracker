# AD-021: Full-Fidelity Local LLM Traces

- Status: accepted
- Implementation: tracing boundary, Langfuse adapter, model-artifact identity,
  last-line trace redaction, and tracing of `money-tracker-admin route`
  implemented in the first Step 5.5 slice; the Telegram gateway and worker as
  one correlated trace per update, deterministic decision metadata, and
  background identity for services in the second; the backup and retention
  decisions taken by the owner on 2026-09-17; one trace per web advice request
  in the third; evaluation linkage in the fourth; the retention, export, and
  verified deletion command in the fifth; scheduling retention remains Step 7
- Date: 2026-09-16
- Extends: [AD-002](../README.md#ad-002-self-hosted-langfuse)
- Refines: [AD-004](AD-004-local-first-telegram-boundary.md) and
  [AD-019](AD-019-structured-application-logging.md) for the trace store
- Supersedes: the arc42 cross-cutting statement that traces use pseudonymous
  profile and session identifiers

## Context

AD-002 installed a local Langfuse and kept tracing optional, but no product
path used it. The structured-output boundary invoked the model with no
callbacks, the dispatcher passed each graph only a thread identifier, and the
worker, gateway, web advice, and administration command never imported the
observability module. When a model misroutes a message or a boundary exhausts
its corrective retry, the only record is the journal, and AD-019 deliberately
keeps owner text, completions, and amounts out of it. The question "what did
the model see and say, and which artifact answered" had no answer anywhere.

Architecture documentation described traces as carrying pseudonymous profile
and session identifiers. A pseudonymous summary cannot explain a model decision:
the decision depends on the exact instructions, the owner's exact words, the
repair turn, and the raw completion. The owner decided that traces must carry
everything at data and metadata level, because the store is local, runs on the
same workstation as the authoritative database, and is under the owner's
control, so full fidelity does not widen the AD-004 boundary the way an
external sink would.

That decision does not change what the journal may hold. The two surfaces have
different audiences, retention, and exposure: a journal excerpt ends up in a
support report, a trace store does not leave the workstation.

## Decision

### What a trace contains

Traces are full fidelity. For every model call they carry the complete system
instructions, the owner message, any repair turns, the bound `response_format`
with its strict JSON Schema, the raw completion, the parsed contract or the
field-path validation summary that triggers the corrective retry, the attempt
number, token counts, and latency. Around the calls they carry the complete
normalized work unit, the trusted dispatch context, graph and node state, the
typed outcome with any grounded tool facts, and the prompt, contract, policy,
and threshold versions in force.

Every model call names the artifact that answered, using the fields the
capability report already defines and the same completeness rule: model alias,
weights filename and SHA-256, quantization, chat-template digest, context size,
server build, and server executable digest. The identity comes from the running
server's own `/props` report, not from configuration.

### What a trace never contains

Credentials (the Langfuse secret key, the inference API key, database
passwords, Bot API tokens, pairing codes, CSRF values, and backup secrets),
token-bearing URLs, database URLs, Telegram download URLs, raw Telegram updates,
receipt-archive paths, and receipt bytes never enter a trace. Components keep
them out by construction: no model-facing value holds one, the tracing port
accepts no exception text, and graph errors are recorded by exception class.
A redactor applied by the SDK to every input, output, and metadata value is the
last line of defence: it replaces configured secret literals, database and
credential-bearing URLs, Telegram API URLs, token shapes, archive paths, and raw
bytes.

The Langfuse project public key appears on every span as an
instrumentation-scope attribute, which the SDK uses to route spans to their
project. It identifies the project and grants nothing without the secret key,
so it is not treated as a credential.

### Where traces go

Only to the local Langfuse instance. Tracing is off unless
`MONEY_TRACKER_TRACING_ENABLED` is true; the project keys that Langfuse
initialization writes into `.env` do not enable it, because every host unit
reads that file. When enabled, the base URL must be an http(s) URL on a
loopback host without user information; the SDK's cloud default is never used.
Configuration that cannot be honored disables tracing with an `ERROR` journal
record and never stops the entry point.

### How tracing enters the code

`money_tracker.tracing` owns a narrow port. Entry points call `open_tracer()`
once and inject the result into `LocalStructuredOutputModel` and the
`WorkflowDispatcher`. Domain, application, and workflow packages import neither
the port nor the SDK, and nothing below the Langfuse adapter imports the SDK,
which is loaded only when tracing is enabled. The disabled tracer returns an
empty graph configuration and records nothing.

- The structured-output adapter records each request as one generation, because
  only it holds the complete request. When a trace is recording it validates
  the completion against the contract a second time, purely for the trace; the
  result never reaches the caller.
- The dispatcher opens one span per dispatch and hands each graph only the
  callbacks the tracer returns. LangGraph copies `metadata` and primitive
  `configurable` values into checkpoint metadata, so the tracer never returns
  either, and a traced invocation checkpoints exactly what an untraced one does.
- The graph callback records graph and node runs and ignores chat model runs,
  so a model call is recorded once, by the adapter.
- The trace identifier is derived from the work unit's durable source
  reference, so every process that handles the same unit can join one trace.
  A Telegram update's trace, named `telegram-update`, holds a `telegram-ingest`
  root from the gateway after its batch commits, a `work-unit` root from the
  worker per claim with the dispatch, the rendered response, and the terminal
  commit or failure, and a `telegram-delivery` root per delivery attempt of a
  response that answers the update. Each is opened and closed on the thread
  that does the work, so no span context crosses threads.
- The dispatch span also carries the deterministic decisions taken around the
  model as flat, filterable metadata: routed intent, source, reason code,
  confidence, policy veto, allocation resolution bases, admitted read tools,
  the admitted request, grounding, review resumption, and the graph threads
  used. They are read from the typed decision and final graph states, so
  recording them cannot alter a decision.
- Browser advice has no durable work unit, so each request generates its own
  `web:advice:<uuid>` reference, from which its `web-advice` trace is derived;
  the read graph keeps its fixed source reference and an untraced request
  invokes it without configuration.

### Retention, export, and deletion

Runtime traces are kept 30 days by owner decision. Langfuse OSS has no
retention setting, so `money-tracker-traces` applies it through the public API:
it plans by age within one environment, keeping a trace whole until its most
recent observation passes the cutoff; exports the selected traces, content
included, to a new owner-only file outside the checkout; and deletes by age or
identifier only with `--confirm`, verifying each asynchronous deletion by
re-reading. It uses the same loopback-only settings as tracing, is run by hand,
and gains a timer only in Step 7. The Langfuse volumes stay outside the
encrypted backup set.

### Non-interference and degradation

Instrumentation never changes what is sent to the model endpoint. A real
integration test records the inference request on the wire with tracing off and
on and requires identical bytes. Identity resolution adds a separate `/props`
read, reused for sixty seconds; weights and server-executable digests are
measured once per file revision and cached privately. A one-shot command
measures an unknown digest inline so its only call is complete; a long-running
service resolves identity on a background thread from start-up and never makes
a request wait for a digest, tracing calls made during the measurement with
`digest_pending`.

An SDK fault never becomes an application fault: starting, updating, and ending
an observation are guarded, and no exception reaches an SDK context manager, so
OpenTelemetry never attaches exception text or a stack trace. Export runs on the
SDK's background thread. A stopped or slow store costs at most the export
timeout (default two seconds) when a process flushes; spans the store could not
take are dropped and the drop is recorded, never retried later. The adapter
enforces that bound itself: each flush runs on a helper thread waited for no
longer than the timeout, its OpenTelemetry provider has no exit handler, and the
SDK's exit handler is released at shutdown, because OpenTelemetry waits for an
export in flight before honouring a flush timeout and both SDKs otherwise flush
again at interpreter exit with a thirty-second or unbounded wait. Tracing SDK log
records are rewritten into content-free `tracing.*` events before any handler
formats them, and export failures are recorded at `WARNING`.

## Relationship to other decisions

- **AD-002** remains: Langfuse is local, optional, and not a runtime source of
  truth. This decision defines what the product sends it and how.
- **AD-004** remains: traces stay on the workstation. Full fidelity is
  acceptable precisely because the store is local; a remote trace sink would
  need a new decision.
- **AD-019** remains unchanged for the journal. The journal still carries no
  owner content, amounts, completions, tokens, or URLs, and tracing SDK records
  are made content-free before they reach it.
- **AD-012 and AD-017** remain: prompts, schemas, tools, thresholds, and the
  read-selection policy are code-owned and unchanged. EVD-004 stays the promoted
  text baseline; tracing is not a promotion input. Traces do point back at
  promotions: a code-owned record of each decision, held by a unit test to the
  decision log and the committed runs, lets every runtime version set name the
  decision and run identifiers its configuration was promoted by, and the
  evaluation command publishes that committed evidence through the same
  loopback-only rules into a separate `evaluation` environment.

## Alternatives considered

- **Pseudonymous summaries only.** Cheap and safe to share, but it cannot answer
  why a model chose a route or failed its schema, which is the question traces
  exist for. Rejected by the owner.
- **Put the same content in the journal.** Rejected: it contradicts AD-019, and
  the journal is the surface that ends up in support reports.
- **Rely on the SDK's LangChain callback for model calls.** It drops the
  `response_format`, knows nothing of the attempt or parse outcome, and would
  record a second generation beside the adapter's. Kept for graph and node
  runs only.
- **Enable tracing whenever project keys exist.** Every service reads `.env`,
  so a restart would silently begin exporting complete prompts. Rejected in
  favour of an explicit switch.
- **Generic OpenTelemetry collector instead of Langfuse.** More infrastructure
  and no LLM-specific view, while AD-002 already accepted Langfuse.

## Consequences

- The Langfuse volumes now hold owner financial content with the sensitivity of
  the ledger itself. Access stays loopback and login-protected, traces must
  never be attached to a support report, and retention, export, and deletion
  procedures are Step 5.5 deliverables rather than Step 7 afterthoughts.
- The owner decided on 2026-09-17 that the Langfuse volumes stay outside the
  encrypted backup set as a reconstructible observability copy: losing them
  loses traces, never finance data. Runtime traces are kept 30 days and may be
  exported privately before deletion. Including the stores in a consistent
  Restic snapshot, or exporting traces into it, was rejected.
- Evaluation datasets, run manifests, and committed reports stay synthetic or
  pseudonymous under the experiment policy; runtime traces are not evaluation
  artifacts and are never committed.
- A traced process pays a small, measured latency cost per work unit (budget:
  at most 25 ms median while the store runs), one digest measurement per new
  model artifact, and a flush at exit bounded by the export timeout.
- Identity can lag a server restart with different weights by at most the
  sixty-second `/props` reuse interval.

## Implementation evidence

The first Step 5.5 slice, recorded in the
[step document](../../roadmap/step-5-5-llm-traceability.md#first-slice-tracing-boundary-and-administrative-dispatch-2026-09-16),
implements the port, the disabled tracer, the Langfuse adapter with its owned
OpenTelemetry provider and bounded flush, the redactor, the identity resolver
with its revision-keyed digest cache, the version set, and tracing of
`money-tracker-admin route` in both routing-only and specialist modes. Real
integration tests against the running Langfuse and llama.cpp prove emission of
the complete call, absence of forbidden values from a message deliberately
carrying them, silence of the disabled path against a positive control, and
byte-identical inference requests with tracing on and off.

The [second slice](../../roadmap/step-5-5-llm-traceability.md#second-slice-telegram-gateway-and-worker-2026-09-17)
injects the tracer into `money-tracker-telegram` and `money-tracker-worker`,
records the ingest, claim, dispatch, render, commit, and delivery stages of one
update in one trace, adds the decision metadata, and resolves identity in the
background for services. Real tests drive the production gateway, worker, and
dispatcher composition over PostgreSQL, the model, and Langfuse, with only the
Bot API answered in-process, and prove the correlated trace, complete calls for
every worker specialist including a resumed review, absence of forbidden values
and Telegram identities, and a silent disabled channel. Measured in-process,
a deterministic update adds about 2.7 ms and a model-backed one 6–12 ms.

The [third slice](../../roadmap/step-5-5-llm-traceability.md#third-slice-web-advice-2026-09-17)
injects the tracer into `money-tracker-api`'s advice composition. Real tests
start the production API process and prove over HTTP a complete trace per
request, absence of forbidden values, a silent disabled API, a byte-identical
selection request, and a clean exit on SIGINT; an advice request adds 2.8 ms
median against an instant endpoint and 7.6 ms against the promoted model.

The [fourth slice](../../roadmap/step-5-5-llm-traceability.md#fourth-slice-evaluation-linkage-2026-09-17)
adds `workflows.promotion` and routes `evaluations.publication` through the
tracing package's publication port, so no evaluation code imports the SDK and a
non-loopback or keyless store publishes nothing. A real test publishes the
EVD-004 evidence and reads the decision, configuration, and six runs back.

The [fifth slice](../../roadmap/step-5-5-llm-traceability.md#fifth-slice-retention-export-and-deletion-2026-09-17)
adds `tracing.retention` and `money-tracker-traces`. A real test writes traces
on both sides of a cutoff and proves the plan, a private 0600 export with its
digest, a dry run that deletes nothing, and verified deletions by age and by
identifier that leave the spanning and newer traces until they are named.

The [sixth slice](../../roadmap/step-5-5-llm-traceability.md#sixth-slice-degradation-in-real-processes-2026-09-17)
runs the gateway and worker, the API, and the worker service entry point in
processes of their own against a stopped, slow, and refusing store, proves
identical behaviour, the latency budget, a bounded flush and stop, and a
content-free journal, and corrects the flush bound the slow store exceeded.
