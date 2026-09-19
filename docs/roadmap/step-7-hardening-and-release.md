# Step 7: Hardening and personal V1 release

Part of the [delivery roadmap](../roadmap.md).

Status: **In progress — operations scheduling slice implemented
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

## Exit criteria

- Product V1 success criteria and every applicable evaluation gate pass.
- Restore on an empty installation reconstructs database, receipt references,
  retained media, configuration, and integrity evidence.
- Core startup and Telegram recovery succeed without Langfuse.
- No critical/high unresolved correctness, data-loss, authorization, or secret
  exposure finding remains.
- Roadmap, product docs, arc42, ADR implementation status, experiment decision
  log, and operations guides all describe the released system accurately.
