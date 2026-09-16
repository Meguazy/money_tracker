# AD-021: Full-Fidelity Local LLM Traces

- Status: accepted
- Implementation: tracing boundary, Langfuse adapter, model-artifact identity,
  last-line trace redaction, and tracing of `money-tracker-admin route`
  implemented in the first Step 5.5 slice; worker, gateway, and web-advice
  entry points, end-to-end channel correlation, evaluation linkage, retention,
  export, deletion, and the backup decision remain open
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

### Non-interference and degradation

Instrumentation never changes what is sent to the model endpoint. A real
integration test records the inference request on the wire with tracing off and
on and requires identical bytes. Identity resolution adds a separate `/props`
read, reused for sixty seconds; weights and server-executable digests are
measured once per file revision and cached privately.

An SDK fault never becomes an application fault: starting, updating, and ending
an observation are guarded, and no exception reaches an SDK context manager, so
OpenTelemetry never attaches exception text or a stack trace. Export runs on the
SDK's background thread. A stopped or slow store costs at most the export
timeout (default two seconds) when a process flushes; spans the store could not
take are dropped and the drop is recorded, never retried later. Tracing SDK log
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
  text baseline; tracing is not a promotion input.

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
- Whether the Langfuse volumes enter the encrypted backup set is an explicit
  owner decision this step must record; until then they remain outside it.
- Evaluation datasets, run manifests, and committed reports stay synthetic or
  pseudonymous under the experiment policy; runtime traces are not evaluation
  artifacts and are never committed.
- A traced process pays a small, measured latency cost per work unit, one digest
  measurement per new model artifact, and a bounded flush at exit.
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
