# Step 7: Hardening and personal V1 release

Part of the [delivery roadmap](../roadmap.md).

Status: **In progress — restore integrity slice implemented
(2026-09-19).**

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

## Exit criteria

- Product V1 success criteria and every applicable evaluation gate pass.
- Restore on an empty installation reconstructs database, receipt references,
  retained media, configuration, and integrity evidence.
- Core startup and Telegram recovery succeed without Langfuse.
- No critical/high unresolved correctness, data-loss, authorization, or secret
  exposure finding remains.
- Roadmap, product docs, arc42, ADR implementation status, experiment decision
  log, and operations guides all describe the released system accurately.
