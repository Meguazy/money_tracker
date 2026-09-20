# Step 7: Hardening and personal V1 release

Part of the [delivery roadmap](../roadmap.md).

Status: **In progress — upgrade/rollback runbook slice implemented
(2026-09-20).**

## Scope change (2026-09-19)

The owner deferred [Step 6](step-6-evaluated-receipt-workflow.md), the receipt
workflow, past the personal V1 release, so this step follows Step 5.5 directly
and releases V1 without receipt extraction. It hardens and releases what Steps
0–5.5 implemented, including the private receipt archive and its metadata
review, which already exist. Receipt download, extraction, preview, line
editing, and media deletion are not part of this step, and no deliverable below
may introduce them. The released V1 scope is the one in the
[product baseline](../product/vision-and-requirements.md).

## Prerequisites

- Steps 0–5.5 are complete.
- Restic and PostgreSQL 17 client tools are installed on the target
  workstation. Their absence today is why the backup drill runs only in CI
  ([risks](../arc42/README.md#11-risks-and-technical-debt)); the daily timer
  and the clean-machine restore need them locally.

## Deliverables

- End-to-end fault matrix across Docker, PostgreSQL, host services, Telegram,
  inference, the receipt archive, Langfuse-off operation, backup, and restore.
- Daily Restic timer over the Step 2 backup command, observed retention
  enforcement across scheduled runs, off-device recovery-secret check, and a
  clean-machine restore drill.
- Export of authoritative finance records and documented portability contract.
- Data/checkpoint/log/trace retention and secure deletion procedures, including
  a schedule for the trace retention command that Step 5.5 left operator-run
  ([AD-021](../arc42/decisions/AD-021-full-fidelity-llm-traces.md)).
- Host-unit sandbox qualified by observing a started unit rather than by its
  exposure score, closing the gap Step 4 measured and Step 5.5 found not in
  effect ([AD-013](../arc42/decisions/AD-013-local-runtime-and-backup.md)).
- Environment generation that writes a random `LLM_API_KEY` long enough for the
  trace redactor, instead of the short placeholder recorded in the
  [risks](../arc42/README.md#11-risks-and-technical-debt).
- Performance profiling on the target workstation and remediation of missed
  warm p95 targets or an explicit revised quality decision.
- Final threat model, upgrade/rollback runbook, operations checklist, and current
  versus planned documentation audit.

## Operations scheduling slice (2026-09-19)

The first Step 7 slice adds checked-in user units for the authoritative backup
and 30-day runtime-trace retention commands. Persistent daily timers schedule
backup at 03:15 and trace retention at 04:15 local time, each with up to thirty
minutes of jitter. The installer renders both service/timer pairs but never
enables them: the owner first initializes and manually verifies the configured
Restic repository, then explicitly enables the timers. Trace retention deletes
only the default runtime environment's age-selected traces, confirms deletion,
and never creates an automatic full-content export.

Fresh environment generation now writes a random 48-character hexadecimal
`LLM_API_KEY`, above the trace redactor's twelve-character threshold. It does
not rotate an existing installation's key.

Unit tests cover the schedules, persistence, bounded commands, installer
rendering, filesystem-authority distinction, and generated-key/redaction
contract. The rendered service and timer set passes `systemd-analyze verify`.
Target-workstation installation, the first observed scheduled runs, retention
evidence across runs, the off-device recovery-secret check, and the
clean-machine restore remain open.

## Finance export slice (2026-09-19)

The second Step 7 slice implements FR-OPS-004 through
`money-tracker-export`. The operator selects one profile and a new private file
outside the checkout. A read-only repeatable-read transaction writes a
version-one JSON Lines manifest plus explicit, revision-bound projections of
the profile, taxonomy, funding sources, receipt metadata, expenses and both
allocation dimensions, wallet history, budgets and overrides, pending category
proposals, and audit history.

The artifact preserves UUIDs, integer minor units, ISO dates and aware
timestamps. It excludes receipt bytes and archive paths, checkpoints, messaging
queues, and transport idempotency records. The command returns safe counts,
byte size, and SHA-256 digest; it never logs owner content or the destination
path. The export is not encrypted and is not a substitute for the authoritative
Restic backup. Historical import remains outside V1.

Unit evidence covers path confinement, private parent and exclusive-file
requirements, deterministic scalar conversion, clock validation, and CLI
parsing. The PostgreSQL integration test builds finance state through production
repositories, exports through both adapter and CLI, and verifies the manifest,
record fidelity, exclusions, digest, file mode, and content-free diagnostics.
The portability contract and operating procedure are recorded in
[AD-023](../arc42/decisions/AD-023-portable-finance-export.md) and the
[data-portability runbook](../operations/data-portability.md).

## Restore integrity slice (2026-09-19)

The third Step 7 slice adds a read-only semantic verifier for an isolated
restored target. The existing `restore-drill` now runs it after database and
media reconstruction, and the operator can repeat it independently with
`money-tracker-backup verify-restore` without Restic or PostgreSQL client
binaries.

The verifier pins the recovered database to the running application's Alembic
revision and reads it in one repeatable-read transaction. It checks profile and
default-source consistency; posted/voided dual-allocation reconciliation,
polarity, and profile scope; wallet and budget scope; finance idempotency
targets; Telegram offsets, queue scope, owner bindings, and pending-action
targets. It then verifies every non-deleted receipt reference against a regular
mode-`0600` content-addressed file beneath a real owner-only archive directory.
Output and journal records contain only schema identity, stable reason codes,
and counts.

Unit evidence covers explicit isolated-target configuration and command
selection. A real PostgreSQL/filesystem integration test exercises both the
service and production CLI, then proves that a missing original and a
profile/default-source mismatch fail closed. The real Restic backup job remains
responsible for exercising the strengthened end-to-end restore path.

This slice advances the clean-machine restore deliverable by replacing its
manual semantic checks with an executable gate. It does not claim the
clean-machine drill itself: target installation, off-device recovery-secret
access, the actual restore on an empty installation, service startup with
Langfuse absent, and recorded duration/RPO/RTO evidence remain open.

## Runtime content retention slice (2026-09-19)

The fourth Step 7 slice implements the data, checkpoint, and log parts of the
retention deliverable and its secure-deletion procedures. The owner decided on
2026-09-19 that concluded runtime content is deleted, not redacted, 30 days
after it concludes, enforced by a daily timer the owner enables
([AD-024](../arc42/decisions/AD-024-runtime-content-retention.md)).

`money-tracker-retention` deletes, in one PostgreSQL transaction pinned to the
supported schema revision, consumed or cancelled pending actions, sent outbox
messages no remaining control refers to, completed inbox items nothing still
needs, and checkpoint threads that can no longer resume. A delivered message
whose control is still active survives, detached from its deleted inbox item,
and keeps working through buttons, reply correlation, and control revocation.
Paused category reviews, dead letters, undelivered responses, and threads of
an unrecognised family are kept, and finance, audit, idempotency, offsets,
bindings, and receipts are never touched. `plan` and an unconfirmed `delete`
roll the same statements back. Output and journal records carry counts only,
including what the policy kept past the cutoff.

Thread identity is now an explicit contract, `money_tracker.workflows.threads`,
used by the dispatcher that writes checkpoints and by retention that reads
them. `money-tracker-runtime-retention.timer` runs the confirmed default daily
at 04:45 with up to thirty minutes of jitter; the installer renders but never
enables it.

The [data-retention runbook](../operations/data-retention.md) inventories every
data class with its retention, deletion mechanism, and residual copies: Restic
snapshots keep deleted rows for up to about a year, PostgreSQL free space and
WAL keep bytes until overwritten, the journal holds no owner content and needs
no privacy retention, and Telegram keeps its own chat history. Checking the
workstation for it found no full-disk encryption: the root filesystem and swap
sit directly on their partitions, so deleted and live owner content remain
readable from the disk. That is recorded as a risk for the owner to decide.

Unit evidence covers the thread contract, the minimum window, clock and URL
validation, the transient unreachable-database path, CLI parsing and
content-free rendering, and the service, timer, and installer. The PostgreSQL
integration suite builds state through the production messaging repository
and real checkpoints through the production LangGraph saver, then proves an
exact rolled-back plan, the deletions and retentions above, surviving control
correlation, idempotent reruns, a content-free CLI, and a fail-closed schema
mismatch. Installing and enabling the timer and observing its scheduled runs
on the target workstation remain open with the other timers.

## Performance profiling slice (2026-09-19)

The fifth Step 7 slice profiles warm text latency on the target workstation
and remediates the one defect the profile exposed. NFR-PER-001 is met without a
revised quality decision.

Two content-free tools make the measurement attributable:

- `money-tracker-telegram latency` keeps the Step 4 measurement of record and
  adds a breakdown of the same warm samples at the worker's completion commit:
  worker side, gateway side, retried samples, and totals per response kind;
- `money-tracker-traces profile` reads observation names and times only
  (`core` and `basic` field groups) and reports, per window, the wait,
  processing, and delivery of every traced Telegram update and the duration of
  every model boundary, graph node, and stage.

Both use the shared nearest-rank convention in `money_tracker.latency`. The
trace stage names are now constants in `tracing.ports`, used by the gateway,
the worker, and the administrative route.

Real traffic since 2026-09-10 first reported a warm p95 of 345,588 ms over 58
warm samples. The breakdown attributed every outlier to the worker side and to the
Step 4 acceptance window of 2026-09-10 and 2026-09-11, whose deliberate fault
injections, stopped worker, and reboot check measure recovery rather than warm
latency. Normal use after acceptance, from 2026-09-12, had a warm p95 of
5,711 ms over 11 warm samples.

The controlled sample ran on 2026-09-19 from 20:14:11 UTC on the RTX 5070 Ti
workstation with the EVD-004 configuration (Qwen3 14B Q5_K_M, router v6,
extraction v6, category-fit v6, tool-selection v5,
`explicit-finance-scope-v1`) and tracing on. The gateway ran `main` at
`f09f47f`. The worker, restarted at 20:26 UTC by the failure described below,
loaded this slice's working tree from before the checkpointer change; its
worker-side edits only name the trace stages through constants. The owner sent
20 text messages one at a time: expenses including a split payment, budget and
spending questions, advice, `/help`, and `/status`, and undid each test
expense.

| Measure | Result |
| --- | --- |
| Samples, warm samples | 20, 19 (first excluded as warm-up) |
| Warm p50 / p95 / maximum | 4,434 / **5,952** / 5,952 ms against 30,000 ms |
| Worker side p50 / p95 | 3,916 / 5,338 ms |
| Gateway side p50 / p95 | 560 / 815 ms |
| Retried samples | 0 |
| p95 by kind | `movement` 5,952 ms (10), `answer` 3,903 ms (7), `help` 1,359 ms, `status` 793 ms |
| Traced updates, button presses included | 31 complete, 0 incomplete; wait p95 964 ms, processing p95 4,626 ms, delivery p95 726 ms |
| Model calls p50 / p95 | extraction 2,034 / 2,928 ms; tool selection 1,325 / 1,521 ms; category fit 950 / 1,257 ms; routing 712 / 1,024 ms |
| Non-model work p95 | commit 11 ms, render 15 ms |

Model calls account for nearly all processing, and the slowest movement stays
five times inside the target, so no latency remediation is warranted. The
headroom is measured with one owner and no concurrent browser advice; the
model already occupies about 14.5 of the GPU's 16.3 GB.

The warm-up sample was the one defect. The application database container had
restarted at 18:51 UTC while the worker kept running, which ended the single
checkpoint connection the worker opened at start-up. The first message after
that failed with `AdminShutdown`, the worker exited and restarted as designed,
and the message stayed leased until its five-minute lease expired: 300,857 ms
of wait in its trace. `open_postgres_checkpointer` now draws from a
`psycopg_pool` pool that checks each connection before use and replaces one the
server ended; connections carry `application_name`
`money-tracker-checkpointer`. A real PostgreSQL test terminates the saver's
session and proves the same thread resumes; with the check removed it fails
with the production `AdminShutdown`.

A worker that crashes in the middle of a work unit for another reason still
leaves that unit leased for five minutes, as AD-018 designs; the fix removes
the routine cause after every database restart.

## Backup target installation slice (2026-09-20)

The sixth Step 7 slice installs the pinned Restic 0.18.1 release and PostgreSQL
17 client tools on the target workstation, after verifying the release digest
and PGDG signing-key fingerprint used by CI. The owner selected the separate
4 TB NVMe shared with Windows as the encrypted repository device. Its data
partition is mounted through `fstab` at a stable host path with an on-demand
`ntfs3` automount, owner-only masks, and `nofail`; the application configuration
points only at a repository directory beneath that mount.

The repository was initialized with a generated owner-only password file. One
manual CLI run and two runs through the installed production systemd service
created and verified authoritative snapshots. The final run used the persistent
mount, exited zero, and `status` reported the new snapshot. The backup timer is
installed, enabled, active, and scheduled for its first automatic occurrence
on 2026-09-21 at 03:24 CEST.

Operational refusal handling now maps configuration and integrity failures to
exit 1 and transient PostgreSQL unavailability to exit 2, without an unhandled
critical record. Handled refusals no longer attach tracebacks that could reveal
a credential-file path. Unit evidence covers missing tooling, missing private
inputs, missing restore settings, dependency classification, content-free
diagnostics, and the absence of critical records.

At installation, timer-triggered acceptance, retention across later scheduled
runs, and the clean-machine restore remained open. Because the repository
device stays inside the workstation, it improves recovery from failure of the
primary NVMe but not from theft, fire, or loss of the whole machine.

## Recovery-secret acceptance slice (2026-09-20)

The seventh Step 7 slice closes the off-workstation recovery-secret check. The
owner saved the generated Restic secret in the authenticated Google Password
Manager account used by the Android phone, then opened the synchronized entry
on that phone. The value was transferred only through the workstation's local
clipboard, never printed or sent through the conversation, and the clipboard
was cleared and verified empty immediately afterwards.

This proves that the copy is accessible independently of the workstation's
local filesystem. It does not replace the clean-machine restore, which must use
the recovered value to unlock the real repository and reconstruct the
authoritative set.

## Backup timer acceptance slice (2026-09-20)

The eighth Step 7 slice observes the installed production backup timer trigger
the installed production service. For bounded acceptance, a temporary drop-in
replaced the calendar with a two-minute `OnActiveSec`, disabled the jitter, and
left the activated service unchanged. At 21:55:09 CEST the timer started
`money-tracker-backup.service`; the command completed in about three seconds,
verified the new authoritative snapshot, and exited zero.

The temporary drop-in was then deleted. After daemon reload and timer restart,
the checked-in daily 03:15 calendar, persistence, and up-to-thirty-minute
jitter were again active; the next occurrence resolved to 03:30:54 CEST. A
fresh `status` reported the timer-created snapshot. This accepts the target
timer-to-service path. Retention enforcement across multiple scheduled runs
and the clean-machine restore remain open.

## Retention timer acceptance slice (2026-09-20)

The ninth Step 7 slice installs, enables, and accepts both production retention
timers on the target workstation. Before enablement, the trace plan found no
runtime trace older than 30 days and the transactional runtime-content plan
found no concluded row or terminal checkpoint older than 30 days. Both plans
therefore selected zero items and changed nothing.

For bounded acceptance, temporary runtime-only drop-ins replaced each calendar
with a two-minute `OnActiveSec`, disabled jitter, and left both activated
services unchanged. At 22:14:11 CEST the trace timer started
`money-tracker-trace-retention.service` and the runtime timer started
`money-tracker-runtime-retention.service`. Both confirmed the 30-day policy,
selected zero eligible items, emitted only content-free counts, and exited
zero. A zero deletion count is expected because the installation has no
eligible content yet; the evidence accepts scheduling and command execution,
not a claim that aged records were deleted.

The runtime drop-ins and their temporary editor files were then deleted. After
daemon reload and timer restart, both units remained enabled and active with
their checked-in persistent daily calendars, 04:15 for traces and 04:45 for
runtime content, and up to thirty minutes of jitter. First naturally scheduled
runs remain observable operational evidence, but target installation and the
timer-to-service paths are accepted.

## Upgrade and rollback runbook slice (2026-09-20)

The tenth Step 7 slice implements the release
[upgrade and rollback runbook](../operations/upgrade-and-rollback.md). It
consolidates the previously distributed application, migration, systemd,
container, model, backup, and incident constraints into one ordered procedure:
classify compatibility, validate the reviewed release, freeze every writer,
create a fresh verified authoritative snapshot, migrate forward, install and
accept the candidate, then reopen Telegram ingress and the schedules.

Rollback is deliberately conditional. A prior application commit or container
digest may return only when compatibility with the current schema and storage
was explicitly proved. Destructive database downgrade remains prohibited. An
incompatible failure preserves the failed state and restores the pre-upgrade
snapshot only into an isolated target for semantic verification; replacing
production storage is a separate owner-authorized cutover because it can lose
every write after the snapshot. Compromised credentials rotate forward, and a
model-visible rollback restores the complete previously promoted
configuration.

The runbook adds no migration, command, or automation and does not claim a real
upgrade drill. Its commands compose already implemented production interfaces,
and its validation is documentation and repository-policy evidence. A future
slice that changes a stateful boundary must add its exact compatibility and
acceptance evidence to the implementing pull request.

## Exit criteria

- Product V1 success criteria and every applicable evaluation gate pass.
- Restore on an empty installation reconstructs database, receipt references,
  retained media, configuration, and integrity evidence.
- Core startup and Telegram recovery succeed without Langfuse.
- No critical/high unresolved correctness, data-loss, authorization, or secret
  exposure finding remains.
- Roadmap, product docs, arc42, ADR implementation status, experiment decision
  log, and operations guides all describe the released system accurately.
