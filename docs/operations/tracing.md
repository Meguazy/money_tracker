# LLM Tracing

- Status: Step 5.5 in progress. `money-tracker-admin route`, the Telegram
  gateway, and the sequential worker are traced; web advice is not traced yet,
  and the installed services have not been restarted with tracing
- Decision: [AD-021](../arc42/decisions/AD-021-full-fidelity-llm-traces.md)
- Store: the optional local Langfuse in the `observability` Compose profile

Traces are the owner's complete record of what a model saw and said. They hold
owner messages, prompts, completions, and grounded figures on purpose, which
makes the Langfuse volumes as sensitive as the finance database. The journal
stays content-free; use it for support reports and never attach a trace.

## What is traced today

| Entry point | Traced | Trace name | Notes |
| --- | --- | --- | --- |
| `money-tracker-admin route` | Yes | `admin-route` | One trace per work unit, routing-only or with a profile |
| `money-tracker-telegram run`, `poll-once` | Yes | `telegram-update` | One `telegram-ingest` stage per accepted update and one `telegram-delivery` stage per delivery attempt that answers one |
| `money-tracker-worker run`, `process-once` | Yes | `telegram-update` | One `work-unit` stage per claim, in the trace of the update it claims |
| `money-tracker-telegram` operator commands | No | — | `health`, `pair`, `replay`, and the others touch no owner message |
| `money-tracker` scaffold CLI | Yes | — | Not a promoted workflow; records its single model run |
| `money-tracker-api` web advice | No | — | Planned in Step 5.5 |
| `money-tracker-eval` | No runtime traces | — | `--publish-langfuse` still publishes aggregate capability scores only |

A traced work unit holds a `dispatch` span with the complete message, trusted
context, versions, decision metadata, and outcome, one span per graph and node,
and one `model:<Contract>` generation per request with every turn, the strict
schema, the completion, the parsed contract or validation summary, token usage,
latency, the attempt, and the serving artifact's identity.

One owner message in Telegram is one trace, identified from its source
reference `telegram:<bot-key>:<update-id>`:

```text
telegram-update
├── telegram-ingest       gateway, after the batch commits: the update as stored
├── work-unit             worker, per claim: attempts, replays, queue wait, outcome
│   ├── dispatch          message, context, versions, decision metadata, outcome
│   │   ├── graph:router → pre_route, classify → model:RouteClassification
│   │   └── graph:<specialist> → nodes → model:<Contract>
│   ├── render            the exact owner response and its controls
│   └── commit            outbox row, logical key, pending and consumed actions
└── telegram-delivery     gateway, per attempt: delivered text, message identity
```

A retry, a manual replay, or a delivery retry adds another root to the same
trace. The pairing confirmation and downtime notices answer no update and are
delivered untraced; rejected updates and pairing messages open no trace.

### Decision metadata

Every dispatch span carries these flat metadata keys, filterable in the
Langfuse UI:

| Keys | Meaning |
| --- | --- |
| `routing_intent`, `routing_source`, `routing_reason_code`, `routing_confidence` | The routing decision and why |
| `routing_policy_veto` | True when deterministic policy overruled a valid classification |
| `routing_command`, `routing_callback_action` | The channel command or control that decided |
| `specialist`, `outcome_status`, `outcome_clarification_reason` | Which specialist ran and how it ended |
| `extraction`, `category_fit`, `resolution_reason` | Converted model answers and why a movement was not resolved |
| `allocation_funding_basis`, `allocation_category_basis` | `profile_default`, `stated_single`, or `stated_split`; `existing` or `proposal` |
| `movement_target_basis`, `movement_target_id` | Whether routing or a pending action named the corrected movement |
| `read_admitted_tools`, `read_tool_request`, `read_executed_tool`, `read_grounded` | The route's registry, the admitted request, and the grounded result |
| `review_proposal_id`, `review_decision_action`, `review_resumed`, `review_opened` | Category review resumption |
| `thread_ids` | LangGraph threads the work unit used, which join a resumed review to the expense that opened it |

The `work-unit` root adds `work_unit_outcome` (`committed`, `retry_scheduled`,
`dead_lettered`, `lease_lost`), `failure_reason_code`, `attempt_count`,
`replay_count`, `queue_wait_ms`, and `correlation_kind`; the delivery root adds
`delivery_outcome` and `attempt_count`.

Never in a trace: Telegram user, chat, file, or callback query identities, the
raw update, and a pairing code, in addition to the values listed below.

Never in a trace: credentials, token-bearing or database URLs, Telegram
download URLs, raw updates, receipt-archive paths, or receipt bytes. The
Langfuse public key does appear on every span; it is a project identifier that
grants nothing without the secret key.

## Enabling and disabling

Tracing is off by default and project keys alone do not turn it on. The
settings live in `.env`; `scripts/upgrade_local_env.sh` appends the two tracing
keys when missing:

    MONEY_TRACKER_TRACING_ENABLED=false
    MONEY_TRACKER_TRACING_EXPORT_TIMEOUT_SECONDS=2
    LANGFUSE_BASE_URL=http://localhost:3000
    LANGFUSE_TRACING_ENVIRONMENT=development

Start the store first, then switch tracing on for one command without editing
`.env`:

    docker compose --profile observability up -d --wait
    MONEY_TRACKER_TRACING_ENABLED=true uv run --env-file .env \
      money-tracker-admin route \
      --text 'Ho speso 12 euro per i croccantini dei gatti' \
      --minimum-confidence 0.5

The command prints the same JSON either way. With tracing on, the journal shows
`tracing.enabled`; the first traced call after a new model artifact also shows
`tracing.identity.digest.completed` once, because the weights are hashed
(about eight seconds for the promoted GGUF) and then cached under
`$XDG_CACHE_HOME/money-tracker/`.

`LANGFUSE_BASE_URL` must name a loopback host. A remote or missing URL, missing
keys, or an unreadable switch records `tracing.configuration.rejected` with a
reason code at `ERROR` and the command runs untraced.

Setting the switch in `.env` also traces the gateway, worker, and API at their
next start, because every unit reads that file. Restarting a service with new
code or configuration interrupts the owner's test bot; announce it first.

A long-running worker resolves the model identity on a background thread from
start-up. When it meets a model artifact it has no digest for, it hashes the
weights in the background (7.77 s for the promoted GGUF) instead of making the
first work unit wait; calls traced meanwhile carry `digest_pending: true` and
`metadata_complete: false`, and later calls are complete. The digest is
remembered in the unit's `CacheDirectory=money-tracker`, which systemd creates
as `~/.cache/money-tracker` with mode 0700, so a restart reads it in about
11 ms. One-shot commands hash inline instead, so their only call is complete.
The worker and API templates also put `~/.local/bin` on `PATH`, without which
the bare `llama-server` name cannot be hashed after an unattended boot.
Re-render and reinstall the units with `scripts/install_systemd_units.sh`
before relying on either.

## Reading a trace

Open `http://127.0.0.1:3000`, sign in with the generated bootstrap account, and
filter by environment. The trace identifier of a work unit is derived from its
source reference, so an operator who knows the reference can compute it. For
a Telegram update the reference is `telegram:<bot-key>:<update-id>`, and
`money-tracker-telegram dead-letters` prints it for a failed item:

    uv run python -c 'from money_tracker.tracing.langfuse import trace_id_for; print(trace_id_for("telegram:telegram-test:UPDATE_ID"))'

This Langfuse runs in v4 events-only mode, where the legacy traces listing is
unavailable; read observations through the public v2 API with the project keys:

    curl -s -u "$LANGFUSE_PUBLIC_KEY:$LANGFUSE_SECRET_KEY" \
      "http://127.0.0.1:3000/api/public/v2/observations?traceId=TRACE_ID&fields=core,basic,io,metadata,usage"

Load the keys into the shell without printing them, and do not paste the output
anywhere outside the workstation: it contains owner content.

## Degradation

Core behaviour never depends on the store. The owner-confirmed budget is at
most 25 ms median per work unit or advice request while the store runs, and at
most `MONEY_TRACKER_TRACING_EXPORT_TIMEOUT_SECONDS` for a flush in any state.
Spans a stopped store could not take are not retried later. Spans are exported
on the SDK's background thread, so no claim, commit, delivery, or command waits
for the store; a service flushes only after its loop honors a stop request.
Measured on the target workstation:

| Condition | Effect |
| --- | --- |
| Admin routing, store running | About 2.4 ms added per routing work unit (real model: 788.0 ms to 792.1 ms median); exit flush 7–174 ms |
| Admin routing, `langfuse-web` stopped | Identical result and exit code; per-unit cost unchanged; exit flush waits at most the export timeout (959 ms measured, 3.48 s versus 2.13 s end to end); spans dropped |
| Gateway and worker in-process, store running | `/help` update: +0.31 ms ingest, +2.05 ms claim to commit, +0.29 ms delivery (medians of 100); model-backed query: +5.8 and +12.1 ms claim to commit in two runs of 10; flush 6.8–7.6 ms |
| Services with the store stopped, slow, or refusing | Not yet measured on the running services |
| Misconfigured tracing | Runs untraced with `tracing.configuration.rejected` |

Simulate a stopped store only with non-destructive commands, and restore it:

    docker compose --profile observability stop langfuse-web
    docker compose --profile observability start langfuse-web
    docker compose --profile observability ps

Never use `down -v`, delete a volume, or force a migration to clear an error.

## Journal events

| Event | Level | Meaning |
| --- | --- | --- |
| `tracing.enabled` | INFO | The process traces into the named environment |
| `tracing.configuration.rejected` | ERROR | Tracing was requested but cannot be honored; `reason_code` says why |
| `tracing.export.degraded` | WARNING | The exporter could not deliver spans; the store is down, slow, or refusing |
| `tracing.flush.incomplete` | WARNING | A flush hit the export timeout; undelivered spans were dropped |
| `tracing.identity.properties_unavailable` | WARNING | llama.cpp `/props` could not be read; the call is traced with incomplete identity |
| `tracing.identity.digest_cache_unwritable` | WARNING | Digests are remembered in memory only; a unit without its `CacheDirectory` under a read-only home |
| `tracing.identity.digest_failed` | WARNING | A background digest measurement failed; later calls try again |
| `tracing.identity.prewarm_failed` | WARNING | The start-up identity read failed; the first model call reads it again |
| `tracing.observation.propagation_failed` | WARNING | A trace name could not be propagated; the span is still recorded |
| `tracing.sdk.reported` | WARNING or above | The Langfuse SDK reported a problem; only its logger name and exception class are kept |

None of these carries a URL, payload, or traceback.

## Retention, export, deletion, and backup

Decided by the owner on 2026-09-17; the procedures are planned within Step 5.5
and none is executable yet:

- the Langfuse volumes stay outside the encrypted backup set as a
  reconstructible observability copy;
- runtime traces are kept 30 days and may be exported to a private local file
  before they are deleted;
- the deletion is run by hand until Step 7 schedules it.

Langfuse 4.26.0 OSS offers no project retention setting: the `data-retention`
entitlement belongs to the self-hosted Enterprise plan, and
`LANGFUSE_INIT_PROJECT_RETENTION` is ignored without it. The public API's
`DELETE /api/public/traces/{traceId}` removes a trace asynchronously and the
integration tests use it to delete what they create.

## Verification

    RUN_POSTGRES_INTEGRATION_TESTS=1 RUN_LANGFUSE_INTEGRATION_TESTS=1 \
      LOG_LEVEL=CRITICAL uv run --env-file .env pytest -q \
      tests/integration/money_tracker/tracing

This needs the observability profile, the local model, and the isolated test
database. For the administrative command it proves complete emission, absence
of forbidden values, a silent disabled path, and byte-identical model requests.
For the channel it drives the production gateway, worker, and dispatcher
composition, answering only the Bot API in-process, and proves one trace per
update with its three stages, complete calls for every worker specialist
including a resumed category review, absence of forbidden values and Telegram
identities, and a silent disabled channel. It uses the `integration-test`
environment and deletes the traces it creates. Do not run it in parallel with
browser E2E, which shares `money_tracker_test`.
