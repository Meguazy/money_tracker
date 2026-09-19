# LLM Tracing

- Status: implemented and owner-accepted on 2026-09-18 (Step 5.5 complete).
  `money-tracker-admin route`, the Telegram gateway, the sequential worker, and
  web advice are traced; the installed services trace into `development` since
  the acceptance
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
| `money-tracker-api` web advice | Yes | `web-advice` | One `advice-request` trace per `POST /api/v1/advice`, derived from a `web:advice:<uuid>` reference the journal's `web.advice.completed` names; other API routes call no model and are untraced |
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

A browser advice request is its own trace: an `advice-request` root with the
message, trusted profile and instant, selection versions, read decision
metadata, and tool facts, over `graph:read`, its nodes, and the
`model:<Contract>` generation. Find it from the `request_reference` of the
`web.advice.completed` journal event.

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
| `promotion_decision`, `promotion_evaluation_runs`, `promotion_model_profile`, `promotion_gguf_sha256` | The decision that promoted this configuration and the run of each suite it rests on; `none` with `promotion_mismatch` naming what differs, such as the `model-only-v0` rollback |

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
| Web advice over HTTP, store running | +2.76 ms median per request against an instant endpoint (41.24 to 44.00 ms, 100 each); +7.62 ms against the promoted model (1,163.26 to 1,170.88 ms, 10 each); SIGINT exit in 0.36–0.46 s with exit code 0 |
| Gateway, worker, and API processes against a stopped, slow, or refusing store (export timeout 1 s) | Identical answers and HTTP payloads, queue drained, no retry or dead letter; `/help` update end to end +2.1 to +2.5 ms median (+3.1 ms with the store running); flush 906–1,004 ms (10.8 ms running); SIGINT exit 0 in 0.31–1.31 s for the API and 0.16–1.21 s for the worker; journal names `tracing.export.degraded` or `tracing.flush.incomplete` without endpoint, traceback, or content |
| Installed services, store running (acceptance) | Installed API +1.4 ms median over 20 alternating pairs with an untraced instance (1,314.6 against 1,313.2 ms), identical payloads |
| Installed services with `langfuse-web` stopped (acceptance) | The owner's message was answered normally, 3.73 s from acceptance to send; worker and API restarts took 0.22 and 0.41 s; journals held only content-free `tracing.export.degraded`; the message's spans were dropped |
| Misconfigured tracing | Runs untraced with `tracing.configuration.rejected` |

The flush bound is enforced by the adapter, not trusted to the SDKs: a flush
runs on a helper thread that the process waits for no longer than the export
timeout, the OpenTelemetry provider has no exit handler, and the Langfuse SDK's
exit handler, which would flush again with a thirty-second or unbounded wait,
is released at shutdown. A store that holds requests otherwise doubled the
bound.

Simulate a stopped store only with non-destructive commands, and restore it:

    docker compose --profile observability stop langfuse-web
    docker compose --profile observability start langfuse-web
    docker compose --profile observability ps

Never use `down -v`, delete a volume, or force a migration to clear an error.

## Journal events

| Event | Level | Meaning |
| --- | --- | --- |
| `traces.plan.completed` | INFO | A retention plan was computed; counts only |
| `traces.export.completed` | INFO | A private export was written; counts and size, never the path |
| `traces.delete.requested`, `traces.delete.verified` | INFO | Deletions were requested and confirmed by re-reading |
| `traces.delete.unverified` | WARNING | Some deletions were not yet applied when verification stopped |
| `traces.export.refused` | ERROR | The export destination was unsafe; `reason_code` says why |
| `traces.store.failed` | ERROR | The store refused or was unreachable; `reason_code` and `transient` |
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

## Evaluation evidence

Published evaluation evidence lives in the `evaluation` environment, not with
runtime traces. `money-tracker-eval capabilities --publish-langfuse` publishes a
screen's aggregate probe outcomes, and

    uv run --env-file .env money-tracker-eval publish-promotion --decision EVD-004

publishes one `evaluation-promotion` trace per call: the promoted configuration
and, for each of the six runs EVD-004 rests on, its gates, metrics, dataset,
instruction version, and source commit, read from `out/evaluations/`. Filter
the store by `promotion_decision` to go from a runtime call to that evidence.
Both commands are their own explicit request, need both project keys and a
loopback `LANGFUSE_BASE_URL`, and wait at most the export timeout.
`publish-promotion` exits 1 when nothing was published; a capability screen
records `langfuse_published: false` in its manifest instead. Scores are stored with the observations, but this
Langfuse's public scores endpoint answers 404, so read them in the UI.

## Retention, export, deletion, and backup

Decided by the owner on 2026-09-17:

- the Langfuse volumes stay outside the encrypted backup set as a
  reconstructible observability copy, so a finance restore restores no trace
  and losing the volumes loses traces only;
- runtime traces are kept 30 days and may be exported to a private local file
  before they are deleted;
- a persistent Step 7 user timer confirms age-based deletion daily; target
  installation and observed execution remain open.

Langfuse 4.26.0 OSS offers no project retention setting: the `data-retention`
entitlement belongs to the self-hosted Enterprise plan, and
`LANGFUSE_INIT_PROJECT_RETENTION` is ignored without it. `money-tracker-traces`
implements the policy over the public API instead. Each run needs the project
keys and a loopback `LANGFUSE_BASE_URL`; the runtime switch is not consulted.

The timer runs the confirmed default age policy daily at 04:15 local time, with
up to thirty minutes of jitter and catch-up after shutdown. Before enabling it,
or before removing a model artifact, see what would go, changing nothing:

    uv run --env-file .env money-tracker-traces plan

It lists, for the tracing environment (`--environment` to choose another), the
traces whose most recent observation is older than 30 days
(`--older-than-days N` or `--before INSTANT` to choose another cutoff), with
their observation counts and dates, and how many traces were kept whole because
a retry or delivery crossed the cutoff. It never prints content.

Optionally export them first. The file holds complete prompts, messages, and
completions, so the command creates it new, with mode 0600, only in an existing
directory no one else can write, and never inside the checkout; keep it on
private storage and never attach it anywhere:

    install -d -m 700 ~/private/money-tracker-trace-exports
    uv run --env-file .env money-tracker-traces export \
      --output ~/private/money-tracker-trace-exports/traces-$(date +%F).jsonl

Each line is one trace with all its observations; the command prints the trace
and observation counts, the size, and the SHA-256 of the file.

Then delete. Without `--confirm` the command repeats the plan with
`"dry_run": true` and deletes nothing:

    uv run --env-file .env money-tracker-traces delete
    uv run --env-file .env money-tracker-traces delete --confirm

A confirmed deletion requests `DELETE /api/public/traces/{traceId}` for every
selected trace, which Langfuse applies asynchronously, then re-reads each trace
for up to `--verify-seconds` (600 by default; one to two minutes were typical
on this workstation) and prints `deleted_verified` and `remaining`. It exits 1
when anything remains readable; run it again later. Delete named traces, such
as smoke or measurement traces, with repeated `--trace-id` instead of an age;
identifiers that no longer exist are reported as `missing_traces`.

Exit code 1 means configuration, an export destination, or verification
failed; 2 means the store was unreachable. Published evaluation evidence lives
in the `evaluation` environment and is reconstructible from Git with
`publish-promotion`, so the default runs never touch it.

The installer renders but does not enable the timer. After reviewing the plan,
enable and inspect it explicitly:

    ./scripts/install_systemd_units.sh
    systemctl --user enable --now money-tracker-trace-retention.timer
    systemctl --user list-timers money-tracker-trace-retention.timer
    systemctl --user status money-tracker-trace-retention.service

The scheduled service runs `money-tracker-traces delete --confirm` with the
default 30-day cutoff and tracing environment. It never exports traces:
automatic full-content export would create another sensitive retention surface.
The same loopback-only configuration and verified asynchronous deletion apply.
If Langfuse is stopped or verification fails, the oneshot fails visibly and the
next daily occurrence retries the policy; finance and messaging remain
unaffected.

Disable future runs without deleting any trace:

    systemctl --user disable --now money-tracker-trace-retention.timer
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
identities, and a silent disabled channel. For web advice it starts the
production `money-tracker-api run` process and proves over real HTTP a complete
trace, absence of forbidden values, a silent disabled API, a byte-identical
selection request, and a clean exit on SIGINT. The retention test writes
traces on both sides of a cutoff and drives `money-tracker-traces` through a
plan, a private export, a dry run, and verified deletions by age and by
identifier; it takes about four minutes because deletion is asynchronous. The
suite uses the `integration-test` environment and deletes the traces it
creates. Do not run it in parallel with
browser E2E, which shares `money_tracker_test`.
