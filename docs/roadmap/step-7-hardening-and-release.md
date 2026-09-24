# Step 7: Hardening and personal V1 release

Part of the [delivery roadmap](../roadmap.md).

Status: **Complete — personal text-first V1 accepted (2026-09-24).**

## Scope change (2026-09-19)

The owner deferred [Step 6](step-6-evaluated-receipt-workflow.md), the receipt
workflow, past the personal V1 release, so this step follows Step 5.5 directly
and releases V1 without receipt extraction. It hardens and releases what Steps
0–5.5 implemented, including the private receipt archive and its metadata
review, which already exist. Receipt download, extraction, preview, line
editing, and media deletion are not part of this step, and no deliverable below
may introduce them. The released V1 scope is the one in the
[product baseline](../product/vision-and-requirements.md).

## Personal V1 release threshold (2026-09-24)

After reviewing the remaining work, the owner chose a shorter, explicitly
bounded personal release rather than delaying V1 for host-level hardening.
The release still requires the promoted text gates, accepted owner workflows,
financial correctness, authorization, healthy core services, a current
verified encrypted snapshot, and its semantic restore into empty isolated
targets. The off-workstation recovery-secret copy must be accessible and
locally compared with the configured secret without entering it in a chat or
repository. A final content-free service, queue, API, and backup check precedes
owner acceptance.

The complete fault matrix, multi-run retention qualification, clean-machine
restore and measured RPO/RTO, and effective filesystem confinement of the
user services move to post-V1 follow-up. They are not passed release gates.
This workstation's internal second disk does not protect against whole-machine
loss; its root and swap are unencrypted; and the user services can access
files as the owner despite declared mount restrictions. On 2026-09-24 the owner
explicitly accepted these specific residual risks for a one-owner local V1,
not for wider deployment. The owner merged scope PR #32 and accepted the
personal release on 2026-09-24.
No global AppArmor relaxation or system-service migration is part of this
release. The [release-readiness checklist](../operations/release-readiness.md)
tracks the accepted boundary and remaining evidence.

Post-V1 follow-up retains the complete fault matrix, observed retention over
multiple scheduled runs, an empty-installation restore using the phone-held
secret with measured RPO/RTO, and effective service filesystem confinement.
The owner must separately schedule that work; closing this step will not mark
those checks as passed. Whole-machine-loss protection and disk encryption also
remain owner decisions before making stronger durability or privacy claims.

The second calendar-triggered backup ran on 2026-09-24 at 11:53 CEST after
workstation startup and exited zero. Its verified snapshot
`3681bda7833b2e95148c41bb595b39bec5adc5a9658cf6e7e0f53c847450c32e`
passed an ordinary restore drill into a new empty database and archive:
revision `20260923_0008`, one profile/default source, 43 expenses, 74 finance
idempotency records, 107 inbox rows, 137 outbox rows, 55 pending actions,
and zero referenced receipt originals. The generated targets were removed.
The pre-upgrade held snapshot remained present after retention; this is not a
seven-daily/five-weekly/twelve-monthly qualification. Bot API validation,
model health, API live/ready, unit state, and zero pending/retrying/dead-letter
queues passed. The owner reported `MATCH` from a local hidden-input comparison
of the phone-held recovery secret with the configured local secret on
2026-09-24; no secret value was shared. The owner then merged scope PR #32.

## Prerequisites

- Steps 0–5.5 are complete.
- Restic and PostgreSQL 17 client tools are installed on the target workstation.
  The daily timer uses them; a post-V1 clean-machine restore will need them on
  its empty target.

## Deliverables

- Daily Restic timer over the Step 2 backup command, a verified current
  snapshot, isolated semantic restore, and an accessible off-device recovery
  secret copy checked locally against the configured secret.
- Export of authoritative finance records and documented portability contract.
- Data/checkpoint/log/trace retention and secure deletion procedures, including
  a schedule for the trace retention command that Step 5.5 left operator-run
  ([AD-021](../arc42/decisions/AD-021-full-fidelity-llm-traces.md)).
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

## Release-readiness audit slice (2026-09-23)

The eleventh Step 7 slice adds the
[release-readiness checklist and threat model](../operations/release-readiness.md).
It names assets, untrusted boundaries, implemented controls, residual physical
and local-process exposure, and the evidence still needed for each release
gate. It audits implemented versus planned claims and corrects stale statements
that daily scheduling is absent or that retention timers still need
installation. No new runtime control or release acceptance is claimed.

The production daily backup timer made a verified catch-up snapshot on
2026-09-23 at 14:00 CEST after workstation downtime. The backup, trace
retention, and runtime-content retention services all exited zero that day.
This is one natural calendar run, not proof of multi-run Restic retention.
The clean-machine restore, effective service sandbox, complete fault matrix,
and final owner acceptance remain open.

## Default-source integrity slice (2026-09-23)

The twelfth Step 7 slice responds to a real isolated restore of the latest
production snapshot. `restore-drill` refused it with
`profile_default_source_invalid`: the profile pointer was null while exactly
one active source was flagged as default. Read-only inspection of the live
database found the same mismatch. Both rows originated on 2026-09-02, before
the current source-creation path updated the profile pointer atomically. No
production data was changed during diagnosis.

Migration `20260923_0008` fills only a missing pointer from the unique active
flagged source and refuses any remaining mismatch transactionally. The backup
command now runs the restored-data semantic verifier on its live source before
dumping or entering Restic retention; an already inconsistent source cannot
produce a new supposedly recoverable snapshot or prune an older one. Real
PostgreSQL tests cover the legacy repair and ambiguous-state refusal. A real
Restic/PostgreSQL test preserves the prior good snapshot when source preflight
rejects drift.

The existing production snapshot was separately restored into a generated
empty database and private temporary archive. Its expected old-revision
refusal was followed by the candidate migration and read-only verification:
revision `20260923_0008`, one active profile, one default source, 43 expenses,
74 idempotency records, 107 inbox rows, 137 outbox rows, 55 pending actions,
and zero referenced receipt originals passed in under one second. Only the
generated database and temporary archive were removed. This proved a recovery
path for that snapshot before production deployment.

## Production integrity repair acceptance (2026-09-23)

The owner authorized the maintenance window after PR #30 merged and all its
required checks passed. API and Telegram ingress, worker, and all three
operational timers were stopped; inbox and outbox had no pending or retrying
work and no dead letters. With writers frozen, the previous reviewed commit
created verified pre-upgrade snapshot
`21f4db2fe7e150ebb3b0651bf9016763f75b4825badded7c32d74325549cc0a7`.
An isolated restore of that exact snapshot reproduced the known
`profile_default_source_invalid` legacy state; candidate migration
`20260923_0008` on the isolated target then passed semantic verification with
the same counts recorded above. No production schema changed before that proof.

The pre-upgrade snapshot was retagged with `step7-preupgrade-hold` to prevent
same-day retention from displacing it; its current Restic ID is
`7ee53ceed3c9d3d842cd3548f38b6f9311792405bde3a2102b92676124d3394f`.
`restic check` passed after the tag change. Production then migrated from
`20260910_0007` to `20260923_0008`. The new source-semantic preflight passed,
and verified snapshot
`77dbf970886319b4fd596c57ccb9d77d43840a6cbbedc1e3338b572c2a1e8c8b`
passed the ordinary isolated `restore-drill` with one active profile, one
default source, 43 expenses, 74 idempotency records, 107 inbox rows, 137
outbox rows, 55 pending actions, and zero referenced receipt originals.
Both temporary databases and archives were removed. API live and ready checks
passed; API, inference, worker, Telegram, and the three previously enabled
timers were active again, with no pending or retrying messages or dead letters.
The pre-upgrade hold remains until the owner accepts an observation window;
do not remove it as a routine cleanup step.

This is a same-workstation deployment and isolated restore, not a clean-machine
restore with the off-workstation secret. Multi-run retention, effective service
confinement, and the complete fault matrix remain post-V1 work under the
2026-09-24 personal-release decision.

## Exit criteria

- Product V1 success criteria and every applicable evaluation gate pass.
- A current encrypted snapshot restores into empty isolated database and
  archive targets with matching semantic counts; the off-workstation secret
  copy matches the configured secret in a hidden-input local check.
- Core startup, API readiness, Telegram queue health, and the already accepted
  Langfuse-off behavior remain valid for the released configuration.
- No unaccepted critical/high correctness, data-loss, authorization, or
  secret-exposure finding remains. The owner explicitly accepted the specific
  host-access and whole-machine-loss risks above for this personal release;
  they remain open follow-ups.
- Roadmap, product docs, arc42, ADR implementation status, experiment decision
  log, and operations guides all describe the released system accurately.

## Owner acceptance and closure (2026-09-24)

The owner chose the bounded personal V1 threshold in PR #32, which merged as
`30836ab24e8e8df7bae8e9dcb746b7451a4ae4dc` with all required checks
green. The owner explicitly accepted the documented same-workstation-loss and
local-access risks and reported `MATCH` from the hidden-input comparison of
the phone-held recovery secret with the configured local secret. No secret was
shared. The final content-free core-health and latest-backup checks above
passed for the unchanged text-first configuration, with schema revision
`20260923_0008` and EVD-004 as the promoted model/prompt baseline. The owner
merged the scope PR after that acceptance. These facts satisfy the narrowed
exit criteria for a one-owner, local-only V1; this separate closure PR records
the Step 7 Complete status. Receipt extraction and the explicit post-V1
hardening follow-ups remain outside this release.
