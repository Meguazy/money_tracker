# Local Runtime

- Status: Steps 1–4 runtime accepted; Step 5 API/dashboard implemented and
  accepted as an installed user service on 2026-09-16
- Target platform: Ubuntu workstation, RTX 5070 Ti 16 GB VRAM

## Current executable foundation

[Development and Local Operations](../development.md) contains the verified
commands for:

- locked UV synchronization and mode-restricted environment generation/upgrade;
- a dedicated default-profile application PostgreSQL on loopback;
- Alembic foundation migrations and PostgreSQL LangGraph checkpoint bootstrap;
- the digest-pinned, optional `observability` profile;
- deterministic local evaluation reports and real model capability probes;
- one-turn CLI inference and opt-in real-service tests.

The environment generator refuses to overwrite an existing `.env` and writes a
random 48-character inference key that the trace redactor recognizes; the
upgrade script appends only missing values and never rotates an existing key.
It also adds distinct non-secret Telegram token file paths, an explicit test
environment, and the loopback web boundary. Do not source or print the file during
troubleshooting except for explicitly safe, non-secret keys.

The Langfuse non-cluster ClickHouse repair is implemented. Existing named
volumes were preserved, all analytical migrations completed, and web/worker
health checks pass. The application database remains available when the
observability profile is stopped. Image provenance and upgrade constraints are
recorded in [Container Versions](container-versions.md). Never delete volumes or
force migration versions merely to clear an error.

## Current Step 3 dispatch smoke

`money-tracker-admin route` is the current operator entry point for one
normalized text work unit. Without `--profile-id` it is a database-free routing
diagnostic. With a bootstrapped profile it opens the application PostgreSQL
repository and persistent checkpointer, then calls the single workflow
dispatcher for expense/refund, query/advice, correction/undo, or category
follow-up. Exact commands and required correlation flags are maintained in
[Development and Local Operations](../development.md#exercising-text-dispatch-by-hand).
The promoted read-selection policy is `explicit-finance-scope-v1`. Select
`model-only-v0` for the direct rollback without changing prompts, schemas, or
data.

The command is one-shot and emits safe JSON. It is not the durable worker: it
does not ingest Telegram updates, own an inbox/outbox, retry delivery, or render
owner-facing messages. A model-dependent work unit fails with the launcher hint
when local inference is unavailable; deterministic routing-only input does not
need the endpoint.

## Current and target process topology

Implemented foundation services and accepted future services are distinguished
in this table:

| Service | Host/container | Responsibility | Core? |
| --- | --- | --- | --- |
| Application PostgreSQL | Docker Compose | Implemented finance source of truth, audit, workflow checkpoints, and durable messaging queues | Yes |
| FastAPI/UI service | Host systemd user service | Implemented same-origin health, overview/analytics/register, audited control-plane slices, safe profile settings, promoted read-only advice, and compiled dashboard; real-browser and installed-service acceptance passed, including a real restart | Yes |
| Telegram gateway | Host systemd user service | Accepted authorized long polling, durable ingest, safe file metadata, callback acknowledgement, outbox dispatch | Yes |
| Workflow worker | Host systemd user service | Accepted sequential routing/workflow processing | Yes |
| llama.cpp server | Host systemd user service | Implemented unit for local promoted text inference | Yes for agent work |
| Receipt extractor dependencies | Host process/adapter | Local OCR/VLM selected by evaluation | Yes after Step 6 |
| Receipt archive adapter | Host library/private filesystem | Implemented decoded-image validation and content-addressed original retention; admin CLI wired, API/worker planned | Yes once receipt ingestion is exposed |
| Langfuse stack | Optional Compose profile | AD-021 full-fidelity trace store (administrative dispatch, gateway, and worker traced when switched on) and aggregate experiment scores; outside the backup set, 30-day retention | No |
| Restic backup | Host CLI and persistent systemd user timer | Implemented encrypted authoritative backup, verification, retention, isolated restore drill, and daily scheduling; target timer enabled and timer-trigger accepted, with multi-run retention and clean-machine restore open | Yes once finance data exists |

Python and llama.cpp stay on the host to keep GPU/model paths and loopback
networking explicit. Application PostgreSQL runs separately from the Langfuse
PostgreSQL container. Langfuse is not a startup dependency of any core service.

## Current gateway and startup resilience

The implemented services do not rely on a perfectly ordered boot. The intended
steady-state sequence is:

1. Docker daemon and application PostgreSQL become available.
2. Telegram gateway starts and retries database/network readiness without
   dropping updates.
3. llama.cpp loads the pinned model and exposes capability health.
4. Workflow worker starts/retries and processes durable inbox work one at a time.
5. FastAPI serves liveness, readiness, the dashboard API, and compiled UI.
   Its `Type=simple` unit reports active before uvicorn listens, so dependent
   checks wait for HTTP readiness rather than the unit state.
6. Langfuse starts only when the operator selects development/evaluation mode.

The checked-in systemd user units use explicit working directories and
environment files, bounded stop timeouts, dependency-sensitive restart codes,
owner-only umasks, restricted namespaces and address families, declared
read-only home/system views, and the locked virtualenv entry points. The
read-only views are not in effect under this workstation's user manager, which
cannot create a mount namespace while AppArmor restricts unprivileged user
namespaces; qualifying them is post-V1 work under the owner's bounded-risk
personal release decision. The worker and API also declare a
private `CacheDirectory` for model digests and `~/.local/bin` on `PATH` for the
trace identity of the serving llama.cpp executable.
The llama unit deliberately retains device visibility for local GPU inference, and its launcher hands the inference key to llama.cpp through `--api-key-file` on a pipe, so the key never appears in a process listing; llama.cpp refuses to start if it cannot read it. User lingering or an equivalent boot setup is
required if user services must start before graphical login.

The `money-tracker-telegram` entry point is executable for validation, owner
pairing, one poll, continuous ingress/outbox loops, and operator health, dead
letter, and replay inspection. It has no inference dependency; its persisted
offset resumes after restart. `money-tracker-worker` processes the durable
inbox in a separate process. The real test bot passed complete owner workflows, authorization, latency,
fault injection, and reboot recovery on 2026-09-11.

Checked-in unit templates live in `deploy/systemd/`, and
`scripts/install_systemd_units.sh` renders all of them for the current checkout
into `~/.config/systemd/user/` and reloads the user manager, so compare rendered
and installed files before reinstalling while services run. Removal is `systemctl --user disable --now <unit>`
followed by deleting the rendered file. A real reboot with lingering brought the three Step 4 services up unaided and
drained queued work in order.

## Implemented Step 4 checks and target health model

Step 4 exposes content-free database, queue, offset, outbox, and dead-letter
state through `money-tracker-telegram health`; a real Bot API transport probe
through `money-tracker-telegram validate`; gateway, worker, and model process
liveness through their systemd user units; and llama.cpp readiness through its
loopback `/health` endpoint. The exact command matrix and combined
interpretation are in the [Telegram runbook](telegram.md#inspecting-and-recovering-the-queue).
The Step 5 API now exposes process and database/profile health separately and an allowlisted owner-facing settings snapshot; it
does not aggregate Telegram queue, model, backup, or receipt health yet.
The broader dependency behavior remains:

| Dependency state | Expected behavior |
| --- | --- |
| Application PostgreSQL unavailable | Gateway/worker/API report unhealthy and retry; no in-memory acknowledgement or finance write. |
| Telegram network unavailable | Gateway backs off; locally committed inbox work may continue if no response transport is needed. |
| llama.cpp unavailable | Gateway continues durable ingestion; model-dependent inbox work retries without losing order/idempotency. |
| Langfuse unavailable | All core behavior continues; a traced process loses the affected spans and waits at most the export timeout when it flushes. Measured for the traced administrative dispatch in [LLM Tracing](tracing.md#degradation); real gateway, worker, and API processes against a stopped, slow, and refusing store behaved identically and stopped within the bound, and the installed services answered the owner with `langfuse-web` stopped at the 2026-09-18 acceptance. |
| Receipt extractor unavailable | Receipt remains in review/pending; text and read workflows continue. |
| Backup destination unavailable | Finance runtime continues, but health becomes degraded and the operator is alerted locally; snapshots are not falsely marked successful. |
| FastAPI unavailable | Telegram ingestion/processing continues; dashboard operations wait for API recovery. |

Health endpoints and local status output must distinguish `healthy`, `degraded`,
`waiting dependency`, and `failed/dead-letter` rather than reducing all states
to process liveness.

## Configuration ownership

Current and planned configuration groups:

- local model endpoint/model/capabilities;
- application PostgreSQL credentials and migration state;
- production and test Telegram tokens plus allowed/pairing state;
- private receipt and evaluation data paths; the archive root is configured by
  `MONEY_TRACKER_RECEIPT_ARCHIVE_ROOT` for the administration CLI;
- implemented FastAPI bind, exact Host/Origin, static-root, and CSRF settings;
- optional Langfuse keys/profile plus the explicit `MONEY_TRACKER_TRACING_ENABLED`
  switch and export timeout;
- Restic repository, password source, retention, and staging path.

Secrets remain local, mode-restricted, and excluded from API/UI output. Locale, currency, and timezone remain fixed V1 profile facts; the UI cannot rewrite runtime configuration or infrastructure. Stable
non-secret configuration should migrate toward typed settings and a documented
example without putting user choices into source constants.

## Shutdown and upgrade principles

The complete executable sequence and rollback decision tree are in
[Upgrade and Rollback](upgrade-and-rollback.md). The principles below remain
the constraints every component-specific change must refine:

- Stop ingress or place it in drain mode before a migration that cannot accept
  new writes.
- Let the sequential worker finish or checkpoint the active item.
- Create and verify a pre-upgrade authoritative backup.
- Apply migrations before starting code that requires the new schema. The
  Telegram and worker entry points report `database_schema_invalid` without a
  traceback and stop with exit code 1 until the following succeeds:

      uv run --env-file .env money-tracker-db bootstrap

- Keep the prior pinned application/model/container configuration available for
  rollback when compatible.
- Never upgrade mobile image tags implicitly at boot.
- Drain or migrate interrupted graph threads before incompatible node/state
  changes.

Step-specific upgrade commands must be added only after the corresponding
packaging and migrations exist. An implementing pull request must also state
whether the previous release can operate on the candidate schema and storage;
unknown compatibility is treated as incompatible.
