# AD-013: Host Runtime and Encrypted Backup

- Status: accepted
- Implementation: Compose topology, encrypted Restic backup/restore, accepted
  Telegram/worker/llama.cpp units, and the browser-accepted FastAPI service unit
  implemented; post-restore semantic verification and the persistent daily
  backup timer are implemented; the target repository, service, and timer are
  installed, enabled, and accepted through a timer-triggered production run;
  multi-run retention and the clean-machine restore remain post-V1 work under
  the owner's 2026-09-24 personal-release decision
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
describe configuration. Qualifying the sandbox by observation is post-V1 work;
the owner must accept the current local-process exposure for personal V1 only.
The same slice gave the worker and API templates a private `CacheDirectory` for
trace identity digests and `~/.local/bin` on `PATH`; they were installed and
the services restarted at the 2026-09-18 acceptance. The llama.cpp launcher now
passes the inference key through `--api-key-file` on a pipe instead of a
command-line argument.

The first Step 7 slice adds a persistent daily user timer over the existing
verified backup command. Its oneshot service keeps the same owner-selected
filesystem authority as the manual CLI because the repository, staging, lock,
archive, and password-file paths cannot be enumerated in a static template.
On 2026-09-20 the owner selected a separate internal 4 TB NVMe shared with
Windows for the target repository. Its NTFS data partition has a stable
owner-masked `fstab` automount. Restic 0.18.1 and PostgreSQL 17.11 clients were
installed, the repository was initialized, and one CLI run plus two production
service runs created verified snapshots. The timer was installed, enabled, and
active; timer-triggered acceptance and multi-run retention evidence were the
next checks. Keeping the repository in the same workstation accepts exposure
to whole-machine loss while separating it from failure of the primary NVMe.

Timer activation passed on 2026-09-20. A temporary two-minute monotonic
override on the installed production timer triggered the unchanged production
service, which created and verified a snapshot and exited zero. The override
was deleted immediately afterwards, and the checked-in daily calendar,
persistence, and jitter were reloaded and confirmed active. Retention evidence
across multiple timer runs remains open.

The off-workstation secret check passed on 2026-09-20. The owner stored the
Restic secret in the authenticated password-manager account used by the Android
phone and opened the synchronized entry there. The local transfer used only a
temporary clipboard, which was cleared and verified empty. The clean-machine
restore must still prove that this recovered value unlocks the repository.

The third Step 7 slice adds a separate read-only restored-data verifier and
runs it at the end of every `restore-drill`. It requires the exact isolated
database identity, application schema revision, and private receipt root; checks
finance reconciliation and profile scope, replay-protection targets, Telegram
offset/queue/action correlations, and retained-media modes and hashes; and emits
only safe counts and stable reason codes. Operators can repeat the same gate
with `money-tracker-backup verify-restore` without Restic tooling. Real
PostgreSQL and filesystem integration evidence covers success plus missing-media
and profile/default-source drift. A clean-machine restore and subsequent core
startup with Langfuse absent remain post-V1 recovery qualification. Earlier
Langfuse-off operation of the installed core services is accepted evidence for
the unchanged personal V1 configuration.

A later Step 7 real-snapshot drill exposed a legacy profile/default-source
pointer mismatch that earlier source checks did not catch. The backup command
now runs the same semantic verifier before the dump and before Restic retention,
so a source already known to be unrestorable is refused without pruning. The
`20260923_0008` migration reconciles the one unambiguous old pointer, and a
real PostgreSQL/Restic test proves refusal leaves the prior snapshot present.
The existing production snapshot was recovered in an isolated database by
applying that migration before semantic verification. On 2026-09-23 the
production deployment repeated that recovery proof on a fresh snapshot while
writers were frozen, migrated the live database, and passed a new isolated
restore drill. The old snapshot is tagged for temporary observation-window
retention. Multi-run retention and the clean-machine drill remain open post-V1.

The tenth Step 7 slice adds the repository-wide
[upgrade and rollback runbook](../../operations/upgrade-and-rollback.md). It
uses the accepted host-process ownership and authoritative backup boundary to
freeze every application writer, create a verified rollback point, migrate
forward, install reviewed units, and reopen ingress only after acceptance.
Prior code or image digests are a rollback only when compatibility is explicit;
otherwise the current state is preserved and the snapshot is restored into an
isolated target before any owner-authorized production cutover. The procedure
adds no new runtime component or automated destructive operation.

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
