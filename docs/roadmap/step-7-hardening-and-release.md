# Step 7: Hardening and personal V1 release

Part of the [delivery roadmap](../roadmap.md).

Status: **Planned — next active step (2026-09-19).** Implementation has not
started.

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

## Exit criteria

- Product V1 success criteria and every applicable evaluation gate pass.
- Restore on an empty installation reconstructs database, receipt references,
  retained media, configuration, and integrity evidence.
- Core startup and Telegram recovery succeed without Langfuse.
- No critical/high unresolved correctness, data-loss, authorization, or secret
  exposure finding remains.
- Roadmap, product docs, arc42, ADR implementation status, experiment decision
  log, and operations guides all describe the released system accurately.
