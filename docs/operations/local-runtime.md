# Local Runtime

- Status: Step 1 platform foundation complete; product services planned
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
script appends only missing Step 1 values. Do not source or print the file during
troubleshooting except for explicitly safe, non-secret keys.

The Langfuse non-cluster ClickHouse repair is implemented. Existing named
volumes were preserved, all analytical migrations completed, and web/worker
health checks pass. The application database remains available when the
observability profile is stopped. Image provenance and upgrade constraints are
recorded in [Container Versions](container-versions.md). Never delete volumes or
force migration versions merely to clear an error.

## Current and target process topology

Implemented foundation services and accepted future services are distinguished
in this table:

| Service | Host/container | Responsibility | Core? |
| --- | --- | --- | --- |
| Application PostgreSQL | Docker Compose | Implemented schema/checkpoint foundation; future finance source of truth, messaging, and audit | Yes |
| FastAPI/UI service | Host systemd | Same-origin REST API and compiled dashboard | Yes after Step 5 |
| Telegram gateway | Host systemd | Long poll, durable ingest, file transport, outbox dispatch | Yes after Step 4 |
| Workflow worker | Host systemd | Sequential routing/workflow processing | Yes after Step 3/4 |
| llama.cpp server | Host systemd | Local text and selected model capabilities | Yes for agent work |
| Receipt extractor dependencies | Host process/adapter | Local OCR/VLM selected by evaluation | Yes after Step 6 |
| Langfuse stack | Optional Compose profile | Implemented local traces and aggregate experiment scores | No |
| Restic backup timer | Host systemd | Encrypted authoritative backup and retention | Yes once finance data exists |

Python and llama.cpp stay on the host to keep GPU/model paths and loopback
networking explicit. Application PostgreSQL runs separately from the Langfuse
PostgreSQL container. Langfuse is not a startup dependency of any core service.

## Planned startup order and resilience

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

No systemd unit filenames or installation commands are documented as executable
yet. Step 4/5 must add real checked-in unit templates, installation/removal
instructions, and a boot/restart integration test before updating this status.

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

Planned configuration groups:

- local model endpoint/model/capabilities;
- application PostgreSQL credentials and migration state;
- production and test Telegram tokens plus allowed/pairing state;
- private receipt and evaluation data paths;
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
