# Step 5.5: Full LLM call traceability

Part of the [delivery roadmap](../roadmap.md).

Status: **In progress (started 2026-09-16).** The prerequisites were verified
and [AD-021](../arc42/decisions/AD-021-full-fidelity-llm-traces.md) records the
trace-content decision. The
[first slice](#first-slice-tracing-boundary-and-administrative-dispatch-2026-09-16)
delivers the tracing boundary and traces `money-tracker-admin route`; the
worker, gateway, and web-advice entry points, end-to-end channel correlation,
evaluation linkage, retention, export, deletion, the backup decision, and
owner acceptance remain open, as the
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

### Exit-criteria status

| Exit criterion | Status |
| --- | --- |
| Every promoted-workflow LLM call appears with complete input, output, and artifact identity | Partial: the administrative entry point only; worker and web advice open |
| One owner message produces one correlated end-to-end trace | Open: needs gateway, worker, and commit correlation, then a real message from the private test bot |
| Core behaviour unchanged with Langfuse stopped; added latency within a stated budget | Partial: budget stated and met for the administrative entry point; services open |
| No credential, token-bearing URL, database URL, or archive path in any trace; no receipt bytes | Met for the traced entry point; to be re-proven for each new one |
| Trace retention and deletion documented and exercised | Open: deletion API probed and used by tests; procedure not yet written |
| Real integration tests verify emission and a silent disabled path | Met for the traced entry point |
| New ADR, with arc42, operations, and experiment documentation in agreement | Met: AD-021 and the reconciled documents |
