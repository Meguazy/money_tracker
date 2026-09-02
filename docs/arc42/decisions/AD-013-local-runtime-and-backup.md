# AD-013: Host Runtime and Encrypted Backup

- Status: accepted
- Implementation: Compose topology implemented; systemd and Restic planned
- Date: 2026-09-01

## Context

The application must resume automatically when the workstation starts, while
local GPU inference and development remain simple. Financial data and retained
receipt images require recoverable storage.

## Decision

Run the FastAPI service, Telegram gateway, workflow worker, and llama.cpp on the
host under systemd. Run the dedicated application PostgreSQL in Docker Compose.
Keep Langfuse in an opt-in development/evaluation profile rather than the daily
core runtime.

Use Restic for an encrypted daily backup to an owner-selected external folder
or disk. Back up a consistent PostgreSQL dump, receipt archive, and required
configuration. Default retention is seven daily, five weekly, and twelve
monthly snapshots. Keep the Restic recovery secret outside the workstation and
test restore before declaring the personal release ready.

## Alternatives considered

- Containerizing all application and GPU processes increases host networking,
  model mount, and NVIDIA runtime complexity.
- Manual startup can miss the Telegram backlog after power-on.
- Manual exports do not meet the selected durability requirement.

## Implementation evidence

Roadmap Step 1 implements application PostgreSQL as the default Compose service
and isolates the healthy, digest-pinned Langfuse stack behind the
`observability` profile. Core database and migration/checkpoint checks pass
without Langfuse. Host systemd services, authoritative Restic backup, retention,
and restore verification remain planned.

## Consequences

- Setup requires systemd unit installation and user lingering or equivalent
  boot activation.
- Core health checks and service dependencies must tolerate delayed Docker,
  network, and model startup.
- Langfuse data may have a separate optional backup policy; it is not part of
  the authoritative finance restore set.
