# LLM Tracing

- Status: Step 5.5 in progress. The tracing boundary is implemented and
  `money-tracker-admin route` is traced; the worker, gateway, and web advice are
  not traced yet
- Decision: [AD-021](../arc42/decisions/AD-021-full-fidelity-llm-traces.md)
- Store: the optional local Langfuse in the `observability` Compose profile

Traces are the owner's complete record of what a model saw and said. They hold
owner messages, prompts, completions, and grounded figures on purpose, which
makes the Langfuse volumes as sensitive as the finance database. The journal
stays content-free; use it for support reports and never attach a trace.

## What is traced today

| Entry point | Traced | Notes |
| --- | --- | --- |
| `money-tracker-admin route` | Yes | One trace per work unit, routing-only or with a profile |
| `money-tracker` scaffold CLI | Yes | Not a promoted workflow; records its single model run |
| `money-tracker-worker` | No | Planned in Step 5.5 |
| `money-tracker-telegram` | No | Planned in Step 5.5 |
| `money-tracker-api` web advice | No | Planned in Step 5.5 |
| `money-tracker-eval` | No runtime traces | `--publish-langfuse` still publishes aggregate capability scores only |

A traced work unit contains a `work-unit` root span, a `dispatch` span with the
complete message, trusted context, versions, and outcome, one span per graph
and node, and one `model:<Contract>` generation per request with every turn, the
strict schema, the completion, the parsed contract or validation summary, token
usage, latency, and the serving artifact's identity.

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

Setting the switch in `.env` also affects the host services once they are
traced. Restarting a service with new code or configuration interrupts the
owner's test bot; announce it first.

## Reading a trace

Open `http://127.0.0.1:3000`, sign in with the generated bootstrap account, and
filter by environment. The trace identifier of a work unit is derived from its
source reference, so an operator who knows the reference can compute it:

    uv run python -c 'from money_tracker.tracing.langfuse import trace_id_for; print(trace_id_for("local-admin:route:REFERENCE"))'

This Langfuse runs in v4 events-only mode, where the legacy traces listing is
unavailable; read observations through the public v2 API with the project keys:

    curl -s -u "$LANGFUSE_PUBLIC_KEY:$LANGFUSE_SECRET_KEY" \
      "http://127.0.0.1:3000/api/public/v2/observations?traceId=TRACE_ID&fields=core,basic,io,metadata,usage"

Load the keys into the shell without printing them, and do not paste the output
anywhere outside the workstation: it contains owner content.

## Degradation

Core behaviour never depends on the store. Measured on the target workstation
for the traced administrative dispatch:

| Condition | Effect |
| --- | --- |
| Store running | About 2.4 ms added per routing work unit (real model: 788.0 ms to 792.1 ms median); exit flush 7–174 ms |
| `langfuse-web` stopped | Identical result and exit code; per-unit cost unchanged; exit flush waits at most the export timeout (959 ms measured, 3.48 s versus 2.13 s end to end); spans dropped |
| Misconfigured tracing | Runs untraced with `tracing.configuration.rejected` |

The budget is at most 25 ms median per work unit while the store runs, and at
most `MONEY_TRACKER_TRACING_EXPORT_TIMEOUT_SECONDS` for a flush in any state.
Spans a stopped store could not take are not retried later.

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
| `tracing.identity.digest_cache_unwritable` | WARNING | Digests are remembered in memory only, as expected under a read-only home |
| `tracing.sdk.reported` | WARNING or above | The Langfuse SDK reported a problem; only its logger name and exception class are kept |

None of these carries a URL, payload, or traceback.

## Retention, export, deletion, and backup

Planned within Step 5.5; no procedure is executable yet. The public API's
`DELETE /api/public/traces/{traceId}` is known to remove a trace asynchronously
and the integration tests use it to delete what they create, but the owner-facing
retention policy, bulk deletion, export, and the decision on whether the
Langfuse volumes enter the encrypted backup set are still open.

## Verification

    RUN_POSTGRES_INTEGRATION_TESTS=1 RUN_LANGFUSE_INTEGRATION_TESTS=1 \
      LOG_LEVEL=CRITICAL uv run --env-file .env pytest -q \
      tests/integration/money_tracker/tracing

This needs the observability profile, the local model, and the isolated test
database. It proves complete emission, absence of forbidden values, a silent
disabled path, and byte-identical model requests, using the `integration-test`
environment and deleting the traces it creates. Do not run it in parallel with
browser E2E, which shares `money_tracker_test`.
