# AD-013: Host Runtime and Encrypted Backup

- Status: accepted
- Implementation: Compose topology, encrypted Restic backup/restore, accepted
  Telegram/worker/llama.cpp units, and the browser-accepted FastAPI service unit
  implemented; the backup timer and its scheduling planned
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

Step 4 implements hardened user-service templates for Telegram ingress/outbox,
the sequential workflow worker, and llama.cpp, plus a checkout-aware installer.
It also supplies a tested hidden-input credential installer that atomically
writes only the selected production or test token file without logging its
path or value.
The Python services execute the locked virtualenv entry points directly; all
three use read-only filesystem views, while llama.cpp additionally retains
device visibility for GPU acceleration. Capability-dropping directives are
deliberately absent: an unprivileged user manager cannot apply them, and a
unit that declares them fails to execute with `218/CAPABILITIES` instead of
being hardened. Template rendering passes `systemd-analyze verify` and the
units start under the real user manager; measured exposure of what runs is 7.0
for all three, where the earlier 3.5/3.8 figures scored templates that could
not start. Step 4 reboot qualification passed with lingering: all three services
started unaided and drained queued work in order. Further hardening is Step 7
work that must be qualified by starting a unit rather than scoring it.

Step 5 adds the `money-tracker-api.service` template, direct locked-environment
entry point, loopback settings, and a preflight that requires compiled assets.
Its overview, finance, category, funding-source/wallet, receipt, and messaging
slices have unit and HTTP/PostgreSQL integration evidence. On 2026-09-16 the
unit was installed with the checked-in installer, enabled under the user
manager, and accepted through a real browser: health and readiness, every
direct route, compiled assets, Host, Origin, and CSRF refusals, and readiness
after `systemctl --user restart` replaced the main process. The installer
re-renders every unit, and the Step 4 units it rewrote were byte-identical to
the installed ones. `Type=simple` reports the unit active before uvicorn
listens, so readiness is the HTTP probe rather than the unit state. Daily backup
scheduling, off-device recovery-secret verification, and the clean-machine
restore drill remain planned for Step 7.

A Step 5.5 probe on 2026-09-17 found that the read-only filesystem views are
declared but not in effect: the running worker and a transient unit shared the
host's mount namespace inside a private user namespace, and a write under a
`ProtectHome=read-only` home succeeded, because AppArmor's restriction of
unprivileged user namespaces keeps the user manager from applying
`ProtectHome`, `ProtectSystem`, and `PrivateTmp`. The exposure scores above
describe configuration. Qualifying the sandbox by observation is Step 7 work.
The same slice gave the worker and API templates a private `CacheDirectory` for
trace identity digests and `~/.local/bin` on `PATH`.

## Consequences

- Setup requires systemd unit installation and user lingering or equivalent
  boot activation.
- Core health checks and service dependencies must tolerate delayed Docker,
  network, and model startup.
- Langfuse data is not part of the authoritative finance restore set. After
  AD-021 put owner content in it, the owner decided on 2026-09-17 to keep its
  volumes outside the encrypted backup set as a reconstructible observability
  copy with 30-day trace retention, rather than snapshot ClickHouse, MinIO, and
  the Langfuse PostgreSQL consistently or export traces into Restic.
