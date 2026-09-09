# Local Runtime

- Status: foundation and Telegram gateway entry point implemented; worker and
  process management planned
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

The environment generator refuses to overwrite an existing `.env`; the upgrade
script appends only missing values, including distinct non-secret Telegram token
file paths and an explicit test environment. Do not source or print the file during
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

The command is one-shot and emits safe JSON. It is not the planned worker: it
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
| FastAPI/UI service | Host systemd | Same-origin REST API and compiled dashboard | Yes after Step 5 |
| Telegram gateway | Host systemd user service | Authorized long polling, durable ingest, safe file metadata, callback acknowledgement, outbox dispatch | Yes after Step 4 acceptance |
| Workflow worker | Host systemd | Sequential routing/workflow processing | Yes after Step 3/4 |
| llama.cpp server | Host systemd | Local text and selected model capabilities | Yes for agent work |
| Receipt extractor dependencies | Host process/adapter | Local OCR/VLM selected by evaluation | Yes after Step 6 |
| Receipt archive adapter | Host library/private filesystem | Implemented decoded-image validation and content-addressed original retention; admin CLI wired, API/worker planned | Yes once receipt ingestion is exposed |
| Langfuse stack | Optional Compose profile | Implemented local traces and aggregate experiment scores | No |
| Restic backup | Host CLI today, systemd timer planned | Implemented encrypted authoritative backup, verification, retention, and isolated restore drill; scheduling planned | Yes once finance data exists |

Python and llama.cpp stay on the host to keep GPU/model paths and loopback
networking explicit. Application PostgreSQL runs separately from the Langfuse
PostgreSQL container. Langfuse is not a startup dependency of any core service.

## Current gateway and planned startup resilience

The implementation must not rely on a perfectly ordered boot. The intended
steady-state sequence is:

1. Docker daemon and application PostgreSQL become available.
2. Telegram gateway starts and retries database/network readiness without
   dropping updates.
3. llama.cpp loads the pinned model and exposes capability health.
4. Workflow worker starts/retries and processes durable inbox work one at a time.
5. FastAPI serves health, operations, and UI when implemented.
6. Langfuse starts only when the operator selects development/evaluation mode.

Systemd units must use narrow users/permissions where practical, explicit
working directories and environment files, restart policies, startup timeouts,
and journald identifiers. User lingering or an equivalent boot setup is
required if user services must start before graphical login.

The `money-tracker-telegram` entry point is executable for validation, owner
pairing, one poll, continuous ingress/outbox loops, and operator health, dead
letter, and replay inspection. It has no inference dependency; its persisted
offset resumes after restart. `money-tracker-worker` processes the durable
inbox in a separate process. The real test bot remains unused, so this is not
yet an accepted product service.

Checked-in unit templates live in `deploy/systemd/`, and
`scripts/install_systemd_units.sh` renders them for the current checkout into
`~/.config/systemd/user/`. Removal is `systemctl --user disable --now <unit>`
followed by deleting the rendered file. A boot/restart recovery test on real
hardware is still outstanding and is part of Step 4 acceptance.

## Target health model

| Dependency state | Expected behavior |
| --- | --- |
| Application PostgreSQL unavailable | Gateway/worker/API report unhealthy and retry; no in-memory acknowledgement or finance write. |
| Telegram network unavailable | Gateway backs off; locally committed inbox work may continue if no response transport is needed. |
| llama.cpp unavailable | Gateway continues durable ingestion; model-dependent inbox work retries without losing order/idempotency. |
| Langfuse unavailable | All core behavior continues without trace callbacks. |
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
- FastAPI bind/allowed Host/Origin/CSRF settings;
- optional Langfuse keys/profile;
- Restic repository, password source, retention, and staging path.

Secrets remain local, mode-restricted, and excluded from API/UI output. Stable
non-secret configuration should migrate toward typed settings and a documented
example without putting user choices into source constants.

## Shutdown and upgrade principles

- Stop ingress or place it in drain mode before a migration that cannot accept
  new writes.
- Let the sequential worker finish or checkpoint the active item.
- Create and verify a pre-upgrade authoritative backup.
- Apply migrations before starting code that requires the new schema.
- Keep the prior pinned application/model/container configuration available for
  rollback when compatible.
- Never upgrade mobile image tags implicitly at boot.
- Drain or migrate interrupted graph threads before incompatible node/state
  changes.

Step-specific upgrade commands must be added only after the corresponding
packaging and migrations exist.
