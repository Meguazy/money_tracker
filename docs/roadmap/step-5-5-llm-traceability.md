# Step 5.5: Full LLM call traceability

Part of the [delivery roadmap](../roadmap.md).

Status: **In progress (started 2026-09-16).** The prerequisites were verified
and [AD-021](../arc42/decisions/AD-021-full-fidelity-llm-traces.md) records the
trace-content decision. The
[first slice](#first-slice-tracing-boundary-and-administrative-dispatch-2026-09-16)
delivers the tracing boundary and traces `money-tracker-admin route`; the
[second slice](#second-slice-telegram-gateway-and-worker-2026-09-17) traces the
Telegram gateway and the sequential worker as one correlated trace per update;
the [third slice](#third-slice-web-advice-2026-09-17) traces browser advice;
the [fourth slice](#fourth-slice-evaluation-linkage-2026-09-17) links a
promoted configuration to the runs that promoted it; the
[fifth slice](#fifth-slice-retention-export-and-deletion-2026-09-17)
implements and exercises retention, private export, and verified deletion; the
[sixth slice](#sixth-slice-degradation-in-real-processes-2026-09-17) proves the
product unchanged and bounded against a stopped, slow, and refusing store. The
owner decided the backup, latency-budget, and retention questions on
2026-09-17. Restarting the installed services with tracing and owner
acceptance with a real test-bot message remain open, as the
[exit-criteria status](#exit-criteria-status) lists.

This step is numbered 5.5 because it was inserted between two already-documented
steps. Renumbering Steps 6 and 7 would have rewritten dated evaluation records
under [`docs/experiments/`](../experiments/README.md) that name them, and those
are evidence rather than plans. Do not renumber them later for tidiness.

## Prerequisites

Step 5 is complete. The Langfuse stack delivered in
[Step 1](step-1-platform-and-evaluation.md) is healthy and digest-pinned, and
the owner has accepted the trace-content decision this step records.

## The gap this step closes

This section describes the repository when the step was planned; the
[implementation evidence](#implementation-evidence) records what has changed
since.

Langfuse is installed but the product does not use it. Only two paths in the
repository emit anything:

- `money_tracker.cli` attaches callbacks to `money_tracker.graph`, which is the
  initial one-node assistant scaffold rather than any promoted workflow.
- `money_tracker.evaluations.publication` copies redacted aggregate capability
  scores under `--publish-langfuse`.

Every LLM call the product actually makes is untraced. The structured-output
boundary in `money_tracker.runtime.model` invokes the chat model with no
callbacks, and `money_tracker.runtime.dispatcher` invokes each compiled graph
with a configuration carrying only a LangGraph thread identifier. The worker,
the Telegram gateway, and the administration CLI never import the observability
module at all. The router, expense/refund, correction/undo, category-review and
read graphs therefore produce no traces in the runtime that Step 4 accepted.

No roadmap step ever owned that wiring: Step 1 delivered the stack and the
aggregate publication, and Step 7 assumes traces already exist when it schedules
their retention and deletion. This step owns it.

## Owner decision recorded by this step

Traces must carry everything, at both data and metadata level: complete prompts,
completions, and decision context, not pseudonymous summaries. Langfuse is a
local, owner-controlled store on the same workstation as the authoritative
database, so full fidelity does not widen the
[AD-004](../arc42/decisions/AD-004-local-first-telegram-boundary.md) privacy
boundary the way an external sink would.

This supersedes the current cross-cutting statement that traces use pseudonymous
identifiers, so the step must record a new ADR and update the affected arc42
sections rather than leaving the two descriptions in conflict. It does not relax
the separate rule that owner content stays out of logs: the journal and the
trace store have different audiences, different retention, and different
exposure in a support report.

## Owner decisions of 2026-09-17

Asked with their alternatives and consequences before the slices that depend
on them:

- **Langfuse volumes and backup: excluded.** The Langfuse PostgreSQL,
  ClickHouse, Redis, and MinIO volumes stay outside the encrypted Restic set
  and are treated as a reconstructible observability copy bounded by the
  retention below. Losing them loses traces, never finance data. Including them
  (a consistent multi-store snapshot) or exporting traces into the backup were
  rejected; both would also have needed a Restic drill this workstation cannot
  run.
- **Latency budget: confirmed.** Tracing may add at most 25 ms median per work
  unit or advice request while the store runs, and a process flush may wait at
  most the export timeout (default 2 s) whether the store is up, slow, or
  stopped.
- **Retention: 30 days, export optional.** Runtime traces older than 30 days
  are deleted; before deleting, the owner may export them to a private local
  file that never enters Git. Until Step 7 schedules it, the deletion is run by
  hand.

## Deliverables

- A tracing boundary that reaches the real inference path: callbacks and run
  metadata flow into the structured-output model and every compiled graph, from
  every entry point that executes them, without those components importing the
  Langfuse SDK directly.
- Complete call payloads: system instructions, owner message, repair turns, raw
  completion, parsed contract, and the validation errors that triggered the
  single schema retry.
- Model artifact identity on every call: model alias, GGUF and chat-template
  digests, server version, temperature, timeout, and context size, reusing the
  identity fields the capability report already collects so a trace names the
  exact artifact that answered.
- Versioned contract and policy metadata: router, extraction, category-fit and
  tool-selection versions, the active read-selection policy, and the routing
  confidence threshold in force.
- Deterministic decision metadata beside the model output: routed intent, stable
  reason code, confidence, policy vetoes, allocation resolution reasons, the
  admitted read tools, and their grounded results.
- End-to-end correlation: one Telegram update, its durable inbox work unit, the
  worker claim, the dispatched specialist, every model call, and the committed
  movement or rendered response belong to one navigable trace, keyed by the
  existing source reference and thread identifiers.
- Cost and performance observations: token counts, per-call and per-node
  latency, retry counts, and endpoint failures.
- Evaluation linkage so a promoted version can be traced back to the dataset run
  that promoted it, extending the existing aggregate publication rather than
  replacing it.
- Lifecycle and degradation: an explicit enable/disable switch, flushing that
  suits long-running services rather than only short-lived commands, and bounded
  behavior when Langfuse is slow, full, or stopped.
- Trace retention, export, and deletion procedures, plus an explicit decision on
  whether the Langfuse volumes enter the backup set or are treated as a
  reconstructible observability copy.
- Corrected current-state documentation: the operations guide currently lists
  Langfuse callbacks among executable operations and arc42 lists a Langfuse
  adapter component, both of which overstate what the scaffold CLI actually
  covers today.

## Exit criteria

- Every LLM call made by the promoted workflows appears in Langfuse with its
  complete input, output, and the model artifact identity that produced it.
- One owner message produces one correlated end-to-end trace spanning ingestion,
  dispatch, inference, and commitment.
- With Langfuse stopped, core finance, Telegram, worker, API, and dashboard
  behavior is unchanged, and the added latency stays inside a stated budget when
  it is running.
- No credential, token-bearing URL, database URL, or receipt-archive path
  appears in any trace; receipt bytes remain outside the trace store.
- Trace retention and deletion are documented and exercised, not deferred.
- Real integration tests verify trace emission against a running local Langfuse
  instead of a mocked SDK, and verify the disabled path emits nothing.
- A new ADR records the full-fidelity trace decision and its consequences, and
  arc42, the operations guides, and the experiment documentation agree with it
  in the same change.

## Evaluation impact

This step changes no model, prompt, routing threshold, tool contract, taxonomy
policy, or receipt pipeline, so it triggers no promotion gate and EVD-004
remains the promoted text baseline. Instrumentation must not alter the prompts
or schemas sent to the endpoint; if capturing a payload would require changing
what is sent, that is a contract change and needs its own evaluation evidence.

## Implementation evidence

### Prerequisites verified (2026-09-16)

- Step 5 is complete (2026-09-16).
- Every observability-profile service (`langfuse-web`, `langfuse-worker`,
  `clickhouse`, `minio`, `redis`, `postgres`) and the application PostgreSQL
  reported healthy, and each running image's digest matched both `compose.yaml`
  and [Container Versions](../operations/container-versions.md). The public
  health endpoint answered 200 and the project keys authenticated against the
  public API.
- Langfuse 4.26.0 runs in its v4 events-only mode: the legacy traces listing
  answers 404, observations are read through `/api/public/v2/observations`, and
  `DELETE /api/public/traces/{id}` removes a trace asynchronously (a probe trace
  disappeared from the observations API within six seconds).
- The owner accepted the trace-content decision recorded above; AD-021 records
  it.

### First slice: tracing boundary and administrative dispatch (2026-09-16)

What is traced, and from where. `money-tracker-admin route` is the first
traced entry point, in routing-only mode and with `--profile-id` for every
specialist. One trace per work unit, identified from its source reference,
holds:

- `work-unit`: the entry point's root span with the command's JSON result;
- `dispatch`: the complete `ChannelMessage`, the trusted `DispatchContext`, the
  typed outcome with any grounded tool facts, and the version set (router,
  extraction, category-fit, and tool-selection prompt identifiers, contract,
  schema, workflow-state, and taxonomy-seed versions, the routing threshold,
  and the read-selection policy);
- `graph:<namespace>` and one span per node, with state in and out, from the
  SDK callback, which ignores chat model runs;
- `model:<Contract>` generations from the structured-output adapter: every
  turn including repair turns, the full `response_format`, the chat model's
  allowlisted parameters (alias, temperature, timeout, retry budget), the raw
  completion, provider response metadata, token usage, the parsed contract or
  the validation summary, the attempt, and the model artifact identity.

Against the running stack a routed message produced `work-unit` → `dispatch`
→ `graph:router` → `pre_route`/`classify` → `model:RouteClassification` with
1,167 input and 44 output tokens. Its identity named `Qwen3-14B-Q5_K_M.gguf`,
GGUF `sha256:e7c9aba1…3e31`, chat template `sha256:57f1fd00…0361`, server
build `b1-3466812`, server executable `sha256:19eaa4a7…695cb2`, and context
8192 with `metadata_complete` true, matching every digest EVD-004 pinned.

Components. `money_tracker.tracing` adds the port and disabled tracer, the
explicit settings, the Langfuse adapter (the only SDK import, loaded lazily),
the redactor, and the identity resolver; `money_tracker.runtime.trace_metadata`
builds the version set. `LocalStructuredOutputModel` and `WorkflowDispatcher`
accept an injected tracer and default to the disabled one, so evaluations and
every other caller are unchanged. `money_tracker.observability` is removed and
the scaffold `money-tracker` CLI uses the same switch. The capability report's
identity assembly moved into `model_metadata_from_properties` so the runner and
the tracer share one definition and completeness rule. Tracing SDK log records
are rewritten into content-free `tracing.*` events.

Payloads sent to the model are unchanged.
`test_tracing_leaves_the_model_request_byte_identical` puts a recording proxy
between the production entry point and the real llama.cpp and requires the
`/v1/chat/completions` request body to be byte-identical with tracing off and
on; the only additional traffic is the traced run's `/props` read. It also
requires the generation's recorded messages and `response_format` to equal what
was sent. A unit test repeats the comparison through a repair turn. Graph
configuration gains only `callbacks`, never `metadata`, so checkpoints are
unchanged; a unit test pins the untraced configuration to the thread identifier
alone.

Secrets and forbidden values.
`test_forbidden_values_never_reach_the_trace_store` routes a message that
itself contains a Bot API token shape, a Telegram API URL, a credential-bearing
PostgreSQL URL, and a path under the configured receipt archive, then reads
every exported observation back and requires none of those values, nor the
Langfuse secret key, the configured database URLs, or the inference key, to
appear, while the owner's words and the `<redacted-database-url>` placeholder
do. The only credential-like value exported is the Langfuse public key, which
the SDK stamps on each span as a scope attribute (see AD-021).

Disabled path. `test_the_disabled_path_exports_nothing` runs one work unit with
the switch off and one with it on, each carrying a unique marker, waits until
the traced marker is searchable, and then requires both the untraced trace
identifier and its marker to return no observation. Without the switch the SDK
is never imported and no OpenTelemetry provider exists.

Langfuse stopped and latency. Latency budget: tracing may add at most 25 ms
median per work unit while the store runs, and a process flush may wait at most
the export timeout (default 2 s) whether the store is up, slow, or stopped. The
first traced call after a new model artifact additionally measures its digest
once (7.7 s for the 10.5 GB promoted GGUF), after which a revision-keyed private
cache answers. Measured on the target workstation:

| Measurement | Untraced | Traced |
| --- | ---: | ---: |
| Routing work unit, in-process endpoint returning instantly, 200 runs × 2, median | 1.28 / 1.30 ms | 3.66 / 3.68 ms |
| Same, 95th percentile | 1.37 / 1.64 ms | 4.14 / 4.02 ms |
| Routing work unit against the real model, 10 alternating runs, median | 788.0 ms | 792.1 ms |
| Flush with the store running | — | 7–174 ms |
| Routing work unit with `langfuse-web` stopped, 50 runs, median | 1.27 ms | 3.69 ms |
| Flush with `langfuse-web` stopped | — | 959 ms |
| `money-tracker-admin route` end to end with `langfuse-web` stopped | 2.13 s | 3.48 s |

With `langfuse-web` stopped the command printed the identical JSON result and
exited 0; the journal gained only `tracing.export.degraded` records without
URL, payload, or traceback. The container was stopped and started with
`docker compose --profile observability stop|start langfuse-web`, no volume was
touched, and the stack returned healthy. The worker, gateway, API, and
dashboard do not trace yet, so their behaviour cannot change; proving it with
tracing enabled belongs to the slices that wire them.

Validation. `uv run ruff check .`, `uv run ruff format --check .`,
`uv run mypy src` (130 source files), `uv run money-tracker-validate-docs`, and
`git diff --check` pass. `uv run pytest -q` reports 1,229 passed and 147
skipped, against the Step 5 baseline of 1,174 and 142: 55 new unit tests, and
the 5 real Langfuse tests skip without their flag. The real PostgreSQL suite
reports 1,363 passed and 13 skipped, against 1,308 and 8. The real Langfuse
suite,

    RUN_POSTGRES_INTEGRATION_TESTS=1 RUN_LANGFUSE_INTEGRATION_TESTS=1 \
      LOG_LEVEL=CRITICAL uv run --env-file .env pytest -q \
      tests/integration/money_tracker/tracing

reports 5 passed in 17 s. The real advice HTTP test reports 1 passed. The
frontend was not touched, so the frontend checks and browser E2E were not
rerun. Integration traces use the
`integration-test` environment and are deleted by the tests that created them;
smoke and measurement traces were deleted through the public API.

### Second slice: Telegram gateway and worker (2026-09-17)

What is traced, and from where. `money-tracker-telegram run` and `poll-once`
and `money-tracker-worker run` and `process-once` open the tracer the switch
selects and flush it within the export timeout when they stop, including after
SIGINT or SIGTERM ends the loops. Every stage of one Telegram update lands in
the trace derived from its source reference `telegram:<bot-key>:<update-id>`,
named `telegram-update`:

- `telegram-ingest`, one per accepted update after its batch commits, from the
  gateway's ingress thread: the normalized update exactly as the inbox stores
  it (text or callback data, message and reply identities, image media type
  and size), the batch counters, poll and commit durations, and whether a
  callback acknowledgement succeeded. The Telegram user, chat, and file
  identities and the callback query identity are left out, the raw update was
  never retained, and rejected or pairing messages open no trace.
- `work-unit`, one per claim, from the worker: inbox and update identity,
  attempt and replay counts, queue wait, what a callback or reply resolved to,
  and the terminal outcome (`committed`, `retry_scheduled`, `dead_lettered`,
  or `lease_lost`) with its reason code. It holds the dispatcher's `dispatch`
  span with every graph, node, and model generation, a `render` span with the
  exact owner response and controls, and a `commit` span naming the outbox row,
  logical key, pending action, consumed action, and revocations.
- `telegram-delivery`, one per delivery attempt of a response or control
  revocation that answers an inbox item, from the gateway's outbox thread: the
  delivered text and controls, the attempt, and the Telegram message identity
  or the transport reason code. The pairing confirmation and downtime notices
  answer no update and stay untraced.

A retried or replayed update adds another `work-unit` to the same trace, and a
retried delivery another `telegram-delivery`, so the history of one message is
one place. `OutboxClaim` now carries the source reference of the inbox row it
answers, and `telegram_source_reference` is the single definition of that
identity for finance keys, threads, and traces.

Deterministic decision metadata. The dispatch span of every entry point now
records flat, filterable keys beside the version set: `routing_intent`,
`routing_source`, `routing_reason_code`, `routing_confidence`,
`routing_policy_veto` (a valid classification overruled by policy, from the new
`POLICY_VETO_REASONS`), `routing_command`, `routing_callback_action`,
`specialist`, `outcome_status`, `outcome_clarification_reason`, and
`thread_ids`. Movement specialists add the converted `extraction` and
`category_fit`, `resolution_reason`, `allocation_funding_basis`
(`profile_default`, `stated_single`, `stated_split`), and
`allocation_category_basis`; correction and undo add `movement_target_basis`
and `movement_target_id`; the read specialist adds `read_admitted_tools`,
`read_tool_request`, `read_executed_tool`, `read_grounded`, and
`read_clarification_reason`; review resumes add `review_proposal_id`,
`review_decision_action`, `review_resumed`, and `review_opened` on the expense
that opened one. They are read from the typed decision and final graph states
after the fact, so they cannot change a decision, and they are recorded even
when a specialist fails.

Cost and performance. Each generation carries its token usage, latency, attempt
and repair flag, and endpoint failures as a reason code; graph nodes carry their
own latency; work units carry attempts, replays, queue wait, and retry
schedule; deliveries carry their attempt.

Identity in the services. The worker's `run` resolves model identity on a
background thread from start-up, and measures an unknown artifact digest off
the request path: a call made while it is measured is traced with
`digest_pending` and an incomplete identity, and the next call after it is
complete. Measured on the target workstation with an empty cache: the first
identity read took 7,758.7 ms inline and 10.3 ms in the background mode, whose
digest was ready 7.77 s later, with the next call complete in 11.6 ms; a
restarted process reading the persisted cache took 10.5–11.3 ms in either mode.
The worker and API unit templates now declare `CacheDirectory=money-tracker`
(mode 0700), which the resolver finds through `CACHE_DIRECTORY` and which is
the same per-user cache commands use, and put `~/.local/bin` on `PATH` so the
bare `llama-server` name resolves for the executable digest after an
unattended boot. The installed units are not yet re-rendered; that restart is
announced with the service measurements.

Finding: the checked-in units' mount-namespace hardening is not in effect.
Probing the running worker and a transient user unit showed a private user
namespace but the host's mount namespace, and a write under a
`ProtectHome=read-only` home succeeded: with
`kernel.apparmor_restrict_unprivileged_userns=1` the user manager cannot apply
`ProtectHome`, `ProtectSystem`, or `PrivateTmp`. The digest cache is therefore
writable by the services today; `CacheDirectory=` keeps it writable once the
hardening works. Fixing the sandbox is Step 7 work and is recorded as a risk.

Payloads sent to the model are unchanged. The worker and gateway only open
spans around the existing calls; the model adapter and dispatcher are the ones
the first slice proved byte-identical, and graph configuration still gains only
`callbacks`.

Secrets and forbidden values.
`test_forbidden_values_never_reach_a_channel_trace` sends, through the gateway
and worker, an owner message carrying a Bot API token shape, a
credential-bearing PostgreSQL URL, a Telegram API URL, and a path under the
receipt archive, while the production `BotApiClient` puts its own token in
every request URL. Reading the whole exported trace back, none of those values,
the owner's Telegram identity, the Langfuse secret key, the configured database
URLs, or the inference key appears; the owner's words and
`<redacted-database-url>` do. Unit tests require the worker and gateway spans
to carry no user or chat identity.

Test hygiene incident. The first run of the new channel test failed an
assertion, and pytest's long traceback printed the test's arguments, which
included the Langfuse project key pair and the credential-bearing test database
URL, into the development session. `--tb=short` is now part of the pytest
configuration, so a failure names lines and assertion values without
arguments, and the test reader's key pair is excluded from its representation.
The owner was told to rotate both credentials; that rotation touches every
service and is the owner's decision.

Real integration tests. `tests/integration/money_tracker/tracing/test_channel_tracing.py`
drives the production gateway, ingestor, worker, and the worker's dispatcher
composition over the real repositories, checkpointer, model, and Langfuse; only
the Bot API is answered in-process beneath the production client, as in every
channel test. It proves one trace per update with `telegram-ingest`,
`work-unit`, and `telegram-delivery` as roots and the delivered text equal to
the rendered and committed response; complete input, output, usage, and
artifact identity for every generation of the expense, query, advice,
correction reply, and a category review resumed with *Proponine un'altra*
through its own delivered control (the turn that asks the review question is
replayed from its outcome, because whether a model proposes a category is not
deterministic); `Modifica` and `Annulla` traced without model calls; forbidden
values absent; and a silent disabled channel against a traced positive control.
Shared helpers moved to `store.py` and `conftest.py` beside the tests.

Latency. Measured in-process on the target workstation against PostgreSQL,
the promoted model, and the running Langfuse, alternating traced and untraced
components over the same database:

| Stage, median (95th percentile) | Untraced | Traced | Added |
| --- | ---: | ---: | ---: |
| `/help` ingest, 100 runs each | 13.48 (13.97) ms | 13.79 (14.44) ms | +0.31 ms |
| `/help` claim to commit, no model | 45.69 (48.17) ms | 47.74 (50.10) ms | +2.05 ms |
| `/help` delivery | 15.62 (16.66) ms | 15.91 (16.79) ms | +0.29 ms |
| Query with the real model, claim to commit, 10 runs each, run 1 | 2,274.89 ms | 2,280.72 ms | +5.83 ms |
| Same, run 2 | 1,965.91 ms | 1,978.00 ms | +12.09 ms |
| Flush with the store running | — | 6.8–7.6 ms | — |

A whole deterministic update adds about 2.7 ms and a model-backed one 6–12 ms,
inside the 25 ms budget; the model-backed difference is within the model's own
run-to-run spread. Measurement traces used a temporary `measurement-slice2`
environment and were deleted through the public API and verified absent. The
behaviour with the store stopped, slow, or refusing on the running services
belongs to the degradation slice.

Validation. `uv run ruff check .`, `uv run ruff format --check .`,
`uv run mypy src` (130 source files), `uv run money-tracker-validate-docs`, and
`git diff --check` pass. `uv run pytest -q` reports 1,254 passed and 152
skipped (from 1,229 and 147: 25 new unit tests, and the 5 real channel tests
skip without their flag). The real PostgreSQL suite reports 1,388 passed and 18
skipped (from 1,363 and 13). The real Langfuse suite,

    RUN_POSTGRES_INTEGRATION_TESTS=1 RUN_LANGFUSE_INTEGRATION_TESTS=1 \
      LOG_LEVEL=CRITICAL uv run --env-file .env pytest -q \
      tests/integration/money_tracker/tracing

reports 10 passed in 56 s, and the real advice HTTP test 1 passed. The frontend
was not touched. After every run the `integration-test`, `measurement-slice2`,
and `development` environments held no observation.

### Third slice: web advice (2026-09-17)

What is traced, and from where. `money-tracker-api run` opens the tracer with
background identity and injects it into `AdviceWorkflow` and its model adapter;
it flushes after uvicorn stops. Each `POST /api/v1/advice` is one trace named
`web-advice`, derived from a `web:advice:<uuid>` reference generated for that
request, never from the fixed `web:advice` constant. Its `advice-request` root
holds the owner question, trusted profile, and instant; the selection versions
(tool-selection prompt, tool contract, schema, read-selection policy, workflow
state); the read decision metadata shared with the dispatcher
(`read_admitted_tools`, `read_tool_request`, `read_executed_tool`,
`read_grounded`, `read_clarification_reason`) with `routing_intent` `advice`
and `routing_source` `interface`; and the tool facts. Beneath it are
`graph:read`, its nodes, and the `model:WireToolRequest` generation. The
journal's `web.advice.completed` gains only the request reference, so an
operator finds the trace without owner content. `read_decision_metadata`
moved into `runtime.trace_metadata`, and `workflow_version_set` accepts an
assembly without a router.

Payloads sent to the model are unchanged. The read graph still receives the
fixed `web:advice` source reference and the same state, and an untraced
request invokes it with no configuration at all. A unit test compares the
selection instructions and results with tracing on and off, and
`test_tracing_leaves_the_advice_model_request_byte_identical` puts a recording
proxy between two real API processes and llama.cpp and requires the
`/v1/chat/completions` bodies to be identical and equal to what the generation
recorded.

Secrets and forbidden values. `test_forbidden_values_never_reach_an_advice_trace`
sends a question carrying a Bot API token shape, a credential-bearing
PostgreSQL URL, a Telegram API URL, and an archive path to the real API process
and finds none of them, the Langfuse secret key, the configured or test database
URLs, or the inference key in the exported trace, while the question and
`<redacted-database-url>` are there.

Real integration tests. `tests/integration/money_tracker/tracing/test_advice_tracing.py`
starts the production entry point on a free loopback port against the isolated
database and sends real HTTP requests through Host, Origin, and CSRF checks. It
proves the complete trace with artifact identity, forbidden-value absence, a
silent disabled API whose journal has no `tracing.enabled` against a traced
positive control, the byte-identical selection request, and exit code 0 within
10 s of SIGINT. The browser E2E runtime now forces tracing off for its API, so
a switch set in `.env` cannot export its fixtures.

Latency. Two real API processes, one traced, ran side by side on the same
database, with requests alternating:

| Advice request over HTTP, median (95th percentile) | Untraced | Traced | Added |
| --- | ---: | ---: | ---: |
| Instant in-process endpoint, 100 each | 41.24 (45.63) ms | 44.00 (45.79) ms | +2.76 ms |
| Promoted model, 10 each | 1,163.26 (1,183.54) ms | 1,170.88 (1,183.94) ms | +7.62 ms |
| SIGINT to exit, store running | 0.36 s | 0.36–0.46 s | — |

Both are inside the 25 ms budget. The instant endpoint answered `/props` with
503, so that run also paid the identity retry path. Measurement traces used a
temporary `measurement-slice3` environment and were deleted and verified absent.

Validation. `uv run ruff check .`, `uv run ruff format --check .`,
`uv run mypy src` (130 source files), `uv run money-tracker-validate-docs`, and
`git diff --check` pass. `uv run pytest -q` reports 1,257 passed and 156
skipped (3 new unit tests; the 4 real advice tests skip without their flag). The
real PostgreSQL suite reports 1,391 passed and 22 skipped. The real Langfuse
suite reports 14 passed in 74 s, and the real advice HTTP test 1 passed. The
frontend was not changed, but the web process and the E2E runtime were:
`npm test -- --run` reports 67 passed, `npm run typecheck` and `npm run build`
pass, and `npm run test:e2e` reports 5 passed. After every run the
`integration-test`, `measurement-slice3`, and `development` environments held no
observation.

### Fourth slice: evaluation linkage (2026-09-17)

What links a promoted version to its runs. `money_tracker.workflows.promotion`
records EVD-004 as code: the decision and date, profile `text-qwen3-14b` and
its GGUF digest, the pinned router, extraction, category-fit, and
tool-selection prompt identifiers, the 0.5 routing bar, the
`explicit-finance-scope-v1` policy, and the published run of each suite:
`ROU-20260909-145350-b1919d44`, `ING-20260909-150059-3f9abaac`,
`CAT-20260909-150354-f2f4e790`, `TOO-20260909-150703-7b2c9501`,
`ADV-20260909-144755-762f53cc`, and `REV-20260909-150839-7a1a525f`.
`workflow_version_set` compares the versions an assembly composes with the
record: when they match, every dispatch and advice span carries
`promotion_decision`, `promotion_evaluation_runs`, `promotion_model_profile`,
and `promotion_gguf_sha256`, which a generation's artifact identity can be
checked against; when they do not, as with `model-only-v0` or another bar,
`promotion_decision` is `none` and `promotion_mismatch` names the keys.

Holding the record to the evidence. Unit tests require every named run to exist
under `out/evaluations/<suite>/text-qwen3-14b/`, carry its identifier, be
eligible and passing, and have measured the promoted weights; the EVD-004 entry
of the decision log to name the same runs, digest, and profile; the runtime
defaults to equal the pinned versions; and the default router, extraction, and
tool-selection texts to hash to the `instructions_sha256` their runs recorded
(the category-fit run hashed its text with the fixture taxonomy rendered in,
so only its identifier is compared). The worker's version set is linked to
EVD-004, and a rollback assembly is not.

Publication. `evaluations.publication` is extended, not replaced. It no longer
imports the SDK or calls `get_client()` from the environment: the tracing
package gains an `EvaluationPublisher` port, implemented by the Langfuse adapter,
and `open_evaluation_publisher`, which validates keys, the loopback endpoint,
and the export bound through `TracingSettings.for_command` and never uses
the cloud default. The command's flag or subcommand is the explicit request, so
the runtime switch is not consulted. `publish_capability_report` keeps its
content and scores. `money-tracker-eval publish-promotion --decision EVD-004`
reads the committed runs, refuses a missing, ineligible, renamed, or
other-weights run, and publishes one new `evaluation-promotion` trace with an
evaluator root for the configuration and one evaluator child per run with its
gates, metrics, dataset, instruction version and hash, selection policy, bar,
and source commit, scored `gate/<suite>`. Evidence goes to the `evaluation`
environment, apart from runtime traces and their retention. Score delivery uses
the SDK's queue, whose flush has no timeout, so it runs on a daemon thread
waited for no longer than the export timeout. Every publication is a new trace,
because replacing one under the same identifier could race Langfuse's
asynchronous deletion.

Nothing model-visible changed and nothing was promoted; the decision log was
not touched.

Real integration test. `test_evaluation_publication.py` publishes the EVD-004
evidence with the runtime switch off into `integration-test`, reads back the
evaluator root with the decision and pinned versions and the six children with
their run identifiers and passing gates, and deletes the trace. A probe showed
that the publication's six scores reach ClickHouse and are deleted with the
trace; this Langfuse answers 404 on its public scores endpoints, so the test
reads observations only.

Validation. `uv run ruff check .`, `uv run ruff format --check .`,
`uv run mypy src` (131 source files), `uv run money-tracker-validate-docs`, and
`git diff --check` pass. `uv run pytest -q` reports 1,282 passed and 157
skipped (25 new unit tests; the real publication test skips without its flag).
The real PostgreSQL suite reports 1,416 passed and 23 skipped, the real
Langfuse suite 15 passed in 76 s, and the real advice HTTP test 1 passed. The
web was not touched. After the runs, and once Langfuse had applied the
asynchronous deletions, the `integration-test`, `evaluation`, `probe-scores`,
and `development` environments held no observation.

### Fifth slice: retention, export, and deletion (2026-09-17)

What Langfuse offers. In Langfuse 4.26.0 OSS a project retention setting does
not exist: the web image lists the `data-retention` entitlement only under the
`self-hosted:enterprise` plan (and the cloud Pro, Team, and Enterprise plans),
`LANGFUSE_INIT_PROJECT_RETENTION` is applied only with that entitlement, and the
worker's retention jobs act only on projects that have one. The public API
offers observations filtered by environment and start time with cursor
pagination, and per-trace `DELETE /api/public/traces/{traceId}`, applied
asynchronously; deleting a trace also removed its scores in ClickHouse.

The procedure. `money_tracker.tracing.retention.TraceStore` and the new
`money-tracker-traces` entry point implement the owner's 30-day decision over
that API with the same loopback-only settings as tracing (the settings
constructor is now `TracingSettings.for_command`, shared with evaluation
publication). `plan` finds the traces of one environment, by default the
tracing environment, whose most recent observation is older than 30 days
(`--older-than-days` or `--before` to choose), keeps whole any trace a retry or
delivery carried past the cutoff, and prints counts, dates, and identifiers
without content. `export` writes those traces, or named ones, one JSON line per
trace with every observation and its content, into a new file created with
`O_EXCL` and mode 0600, refusing a relative path, an existing file, a missing
or group- or world-writable parent, and any path inside the checkout; it prints
counts, size, and SHA-256, never the path, and removes a partial file when the
store fails. `delete` is a dry run unless `--confirm` is given; a confirmed
deletion, by age or repeated `--trace-id`, requests each deletion and re-reads
every trace until it is gone or `--verify-seconds` (600 by default) passes, and
exits 1 if anything remains. Failures are reason codes without URL, key, or
content: exit 1 for configuration, destination, or verification, 2 for an
unreachable store. There is no timer; Step 7 schedules the command.

Exercised. `test_trace_retention.py` writes, through the production tracer,
two traces before a cutoff, one trace on both sides of it, and one after it,
into `integration-test`, then drives `money-tracker-traces` against the running
store: the plan selects the two old traces and neither the spanning nor the
newer one, and counts the spanning trace as kept; the export writes both with
their marker content into a 0600 file whose digest matches the reported one,
without printing its path; the dry run leaves them readable; the confirmed
deletion by age verifies both gone while the spanning and newer traces remain;
and the confirmed deletion by identifier verifies those gone and leaves the
marker unsearchable. Other tests of a run write to the same environment and
their deletions are still pending when this one runs, so its assertions require
its own traces to be selected or excluded rather than an exact set. Alone it
passed in 230 s, most of it waiting for asynchronous deletion.
Against the owner's `development` environment, `plan` and an unconfirmed
`delete` found nothing older than 30 days and changed nothing, and a
non-loopback `LANGFUSE_BASE_URL` was refused with exit 1. Nineteen unit tests
over an in-memory implementation of the observations and deletion contract
cover cutoff and spanning selection, pagination, export refusals and cleanup,
verification with delayed and never-applied deletions, dry-run safety,
identifier selection with missing traces, the 30-day default, and ambiguous
selections.

Backup. The owner's decision is recorded in AD-021, AD-013, arc42, and the
backup, tracing, and security runbooks: the Langfuse volumes are a
reconstructible copy outside the Restic set, so no Restic drill was needed or
run; an export file is owner content outside the backup that the operator keeps
private or removes.

Validation. `uv run ruff check .`, `uv run ruff format --check .`,
`uv run mypy src` (133 source files), `uv run money-tracker-validate-docs`, and
`git diff --check` pass. `uv run pytest -q` reports 1,301 passed and 158
skipped (19 new unit tests; the real retention test skips without its flag).
The real PostgreSQL suite reports 1,435 passed and 24 skipped, the real
Langfuse suite 16 passed in 205 s, and the real advice HTTP test 1 passed. A
first full Langfuse run failed the retention test on an exact-set assertion
that other tests' pending deletions broke; the assertion was corrected as
described above. After the runs and the asynchronous deletions, the
`integration-test`, `development`, `evaluation`, and `retention-probe`
environments held no observation. The web was not touched.

### Sixth slice: degradation in real processes (2026-09-17)

How the failures are produced. The Langfuse SDK keeps one client per project key
per process and ignores the endpoint of any later one, so a pytest process that
has already traced cannot point a second tracer at a failing store. Each
degradation test therefore runs production code in a process of its own: a
`channel_harness` module running the production gateway, ingestor, worker, and
dispatcher composition (only the Bot API answered in-process), the production
`money-tracker-api run`, and the production `money-tracker-worker run`. The
store is stopped (a closed loopback port), slow (a proxy holding every request
6 s, past the export bound, then dropping it), or refusing (every request
answered 503). The Compose stack was never stopped. The export timeout is 1 s.

What must not change, and did not. For each mode: the gateway and worker
delivered the same `/help` answers as an untraced channel over the same
database, answered a model-backed question, and left no pending, retrying,
dead-lettered, or undelivered row; the API served the same advice payload as an
untraced process and exited 0 on SIGINT; the worker service committed its
queued update and exited 0 on SIGINT; and every journal named
`tracing.export.degraded` or `tracing.flush.incomplete` without the store
endpoint, `/api/public`, a traceback, the Langfuse secret key, the database
URL, or the owner's words. `test_degradation.py` holds these nine checks.

| Store (export timeout 1 s) | `/help` update end to end, median of 30, untraced → traced | Flush | API advice, median of 5 | API SIGINT stop | Worker SIGINT stop |
| --- | --- | ---: | ---: | ---: | ---: |
| Running | 80.13 → 83.27 ms (+3.14) | 10.8 ms | 1,175.7 ms | 0.41 s | 0.16 s |
| Stopped | 80.17 → 82.66 ms (+2.49) | 906.4 ms | 1,170.6 ms | 0.31 s | 1.01 s |
| Slow | 79.68 → 81.82 ms (+2.14) | 1,003.5 ms | 1,175.2 ms | 1.31 s | 1.21 s |
| Refusing | 79.46 → 81.71 ms (+2.25) | 1,003.5 ms | 1,172.9 ms | 0.31 s | 0.21 s |

Every exit code was 0 and every answer identical. The worker's stop includes
up to its one-second idle wait.

Two defects found and corrected. First, the flush bound did not hold against a
slow store: the harness flush took 1,987.7 ms with a 1 s timeout, because
OpenTelemetry's `force_flush` waits for an export already in flight before
honouring its timeout. The SDK and a default provider also register exit
handlers that flush again with a thirty-second or unbounded wait. The adapter
now runs every flush on a helper thread it waits for no longer than the export
timeout, builds its provider with `shutdown_on_exit=False`, and releases the
Langfuse resource manager's exit handler at shutdown; after the change every
flush and stop above stayed within the bound. Releasing that handler relies on
a private SDK attribute, recorded as a risk that these tests guard. Second, the
first version of the slow proxy forwarded each held request to the real store,
which left 18 test and measurement traces the tests could not name; they were
removed with `money-tracker-traces delete --environment integration-test
--before ... --confirm`, verified gone, and the proxy now drops held requests.
The 77 measurement traces of the running-store row were likewise deleted and
verified with the same command.

What remains for acceptance. The installed services have not been re-rendered,
switched on, or restarted, because that interrupts the owner's test bot; their
behaviour with `langfuse-web` stopped is observed during acceptance.

Validation. `uv run ruff check .`, `uv run ruff format --check .`,
`uv run mypy src` (133 source files), `uv run money-tracker-validate-docs`, and
`git diff --check` pass. `uv run pytest -q` reports 1,301 passed and 167
skipped (the 9 degradation tests skip without their flag). The real PostgreSQL
suite reports 1,435 passed and 33 skipped, the real Langfuse suite 25 passed in
299 s, and the real advice HTTP test 1 passed. The web was not touched. After
the runs and the asynchronous deletions, the `integration-test`, `development`,
`evaluation`, and `measurement-degradation` environments held no observation.

### Exit-criteria status

| Exit criterion | Status |
| --- | --- |
| Every promoted-workflow LLM call appears with complete input, output, and artifact identity | Met in integration: administrative dispatch, every worker specialist including the resumed category review, and web advice; to be confirmed on the running services at acceptance |
| One owner message produces one correlated end-to-end trace | Partial: gateway, worker, and delivery correlated and proven in-process; the real private test-bot message is open |
| Core behaviour unchanged with Langfuse stopped; added latency within a stated budget | Partial: budget confirmed by the owner; met in real gateway, worker, and API processes against a running, stopped, slow, and refusing store, with a bounded flush and stop; the installed services with `langfuse-web` stopped open until acceptance |
| No credential, token-bearing URL, database URL, or archive path in any trace; no receipt bytes | Met for every traced entry point; the acceptance trace open |
| Trace retention and deletion documented and exercised | Met: 30-day retention, private export, and verified deletion by age and identifier implemented, documented, and exercised against the real store; backup exclusion recorded; scheduling is Step 7 |
| Real integration tests verify emission and a silent disabled path | Met for every traced entry point |
| New ADR, with arc42, operations, and experiment documentation in agreement | Met for the implemented slices |
