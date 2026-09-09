# AD-013: Host Runtime and Encrypted Backup

- Status: accepted
- Implementation: Compose topology, encrypted Restic backup/restore, and the
  Telegram gateway, workflow worker, and llama.cpp service units implemented;
  the backup timer and its scheduling planned
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
without Langfuse.

Roadmap Step 2 implements `money-tracker-backup`. It creates encrypted Restic
snapshots of a consistent `pg_dump`, the private receipt archive, and the
runtime configuration, guarded by database-identity and receipt-hash preflight,
repository and snapshot verification before staging removal, and the seven
daily/five weekly/twelve monthly retention grouped by host and tag. Its restore
drill reconstructs a snapshot only into an explicitly named empty database and
empty receipt directory, verifying the versioned manifest and every checksum in
staging and again at the destination. Real Restic and PostgreSQL integration
tests cover the create/status/restore cycle through both the service and the
operator CLI.

Host systemd services, daily scheduling, off-device recovery-secret
verification, and the clean-machine restore drill remain planned for Step 7.

## Consequences

- Setup requires systemd unit installation and user lingering or equivalent
  boot activation.
- Core health checks and service dependencies must tolerate delayed Docker,
  network, and model startup.
- Langfuse data may have a separate optional backup policy; it is not part of
  the authoritative finance restore set.
