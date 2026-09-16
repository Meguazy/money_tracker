# Step 5.5: Full LLM call traceability

Part of the [delivery roadmap](../roadmap.md).

Status: **In progress (started 2026-09-16).** The prerequisites were verified
and [AD-021](../arc42/decisions/AD-021-full-fidelity-llm-traces.md) records the
trace-content decision. The
[first slice](#first-slice-tracing-boundary-and-administrative-dispatch-2026-09-16)
delivers the tracing boundary and traces `money-tracker-admin route`; the
[second slice](#second-slice-telegram-gateway-and-worker-2026-09-17) traces the
Telegram gateway and the sequential worker as one correlated trace per update.
The owner decided the backup, latency-budget, and retention questions on
2026-09-17. Web advice, degradation measured on the running services,
evaluation linkage, the retention, export, and deletion procedures, and owner
acceptance remain open, as the [exit-criteria status](#exit-criteria-status)
lists.

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

### Exit-criteria status

| Exit criterion | Status |
| --- | --- |
| Every promoted-workflow LLM call appears with complete input, output, and artifact identity | Partial: administrative dispatch and every worker specialist, including the resumed category review; web advice open |
| One owner message produces one correlated end-to-end trace | Partial: gateway, worker, and delivery correlated and proven in-process; the real private test-bot message is open |
| Core behaviour unchanged with Langfuse stopped; added latency within a stated budget | Partial: budget confirmed by the owner; met in-process for the admin, gateway, and worker paths; stopped, slow, and refusing store on the running services open |
| No credential, token-bearing URL, database URL, or archive path in any trace; no receipt bytes | Met for the administrative and channel entry points; web advice and the acceptance trace open |
| Trace retention and deletion documented and exercised | Open: owner decided 30 days and exclusion from backup; procedure and exercise open |
| Real integration tests verify emission and a silent disabled path | Met for the administrative and channel entry points; web advice open |
| New ADR, with arc42, operations, and experiment documentation in agreement | Met for the implemented slices |
