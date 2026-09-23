# Personal V1 Release Readiness

- Status: Step 7 assessment in progress; this checklist does not declare V1 ready.
- Scope: the text-first [product baseline](../product/README.md). Receipt
  extraction and media deletion remain deferred to Step 6.
- Decision authority: [Step 7](../roadmap/step-7-hardening-and-release.md) and
  its exit criteria; the owner accepts the release after evidence is complete.

## Threat model

Protected assets are finance and audit records, Telegram pairing and queues,
receipt originals, runtime messages and checkpoints, local LLM traces,
credentials, and the means to recover them. The owner controls the workstation,
private bot, and loopback browser. Telegram traffic, incoming messages and
browser requests, model output, receipt content, imported files, service errors,
and backup paths cross untrusted boundaries. The model is never an
authorization authority. This model covers the deployed single-owner,
local-only V1; LAN access and the deferred receipt workflow need new review.

| Threat and boundary | Implemented control and evidence | Remaining exposure or release action |
| --- | --- | --- |
| Foreign Telegram user or group reads or changes finance state | Private user/chat pairing, authorization before inbox admission, durable offset, idempotent worker; [Telegram runbook](telegram.md) records real test-bot acceptance | No production bot is accepted. Qualify the chosen release bot if different. Telegram retains bot-chat data without end-to-end encryption. |
| Malicious text or model output causes a write or fabricated answer | Typed bounded graphs, deterministic profile context, read-only advice tools, schema and allocation checks, owner approval for category creation; [evaluation gates](../experiments/evaluation-matrix.md) and EVD-004 promotion | Re-run gates after any promoted model, prompt, tool, or policy change. Ordinary text movements remain drafts pending owner action. |
| Browser origin or request bypasses local controls | Loopback listener, exact Host and Origin checks, closed CORS, CSRF, strict shapes, optimistic versions, idempotency; [web runbook](web.md) records installed-browser acceptance | Loopback assumes a trusted local OS user. LAN access needs authentication, TLS, and a new decision. |
| Credential or owner content leaks through diagnostics or traces | Private secret files, content-free structured journal, trace redaction, local Langfuse with 30-day retention; [security](security-and-troubleshooting.md) and [tracing](tracing.md) runbooks | Full-fidelity traces and unencrypted finance exports contain owner content. Export copies remain outside automatic retention. |
| Loss, corruption, or rollback of authoritative data | Encrypted verified Restic snapshots include PostgreSQL, originals, and recovery configuration; isolated restore checks hashes and business invariants; [backup runbook](backup-and-restore.md) | Clean-machine restore with the off-workstation secret and Langfuse-off startup remain gates. The repository is on another disk inside the same workstation, so whole-machine loss remains possible. |
| Local process or physical access reads live or deleted content | Owner-only filesystem modes and service isolation directives; [data-retention runbook](data-retention.md) records residual copies | Mount protections have not taken effect on this workstation. Root and swap are unencrypted. Qualify effective service restrictions and obtain an explicit owner decision on physical-data risk. |
| Failed upgrade leaves incompatible code or storage | Writer freeze, verified snapshot, forward migration, compatibility classification, isolated restore path in the [upgrade runbook](upgrade-and-rollback.md) | A real stateful upgrade needs its own compatibility evidence. Incompatible production cutover requires a separate owner decision. |

No row grants an exception to the Step 7 rule against unresolved critical or
high correctness, data-loss, authorization, or secret-exposure findings. Such a
finding needs an owner-visible decision and evidence before release.

## Operations checklist

| Gate | Current evidence | Next acceptance action |
| --- | --- | --- |
| Scope and promoted text configuration | Step 6 is deferred; EVD-004 promotes the text configuration, and Step 4/5/5.5 owner workflows passed | Re-run affected evaluations only if a promoted boundary changes; record the final V1 version set. |
| Service startup and recovery | Step 4 unattended reboot and five fault injections, and Step 5 installed API acceptance passed | Complete the Step 7 fault matrix across Docker, PostgreSQL, host services, Telegram, inference, archive, Langfuse-off, backup, and restore. |
| Scheduled backup and retention | Timer-to-service paths passed on 2026-09-20. After downtime, the persistent backup timer made a verified snapshot on 2026-09-23 at 14:00 CEST; all three scheduled services exited zero that day | Observe further daily runs and verify seven-daily/five-weekly/twelve-monthly repository retention. One catch-up run is not multi-run evidence. |
| Recovery secret and empty-installation restore | Off-workstation access passed on 2026-09-20; isolated restore and semantic verification have real PostgreSQL/Restic tests | On an empty isolated installation, unlock the real repository with the recovered secret, restore data/media/configuration, verify counts and hashes, then start core services with Langfuse absent. Record RPO/RTO without private content. |
| Effective service confinement | Units start, but the 2026-09-17 probe found mount protections ineffective under this user manager | Observe effective restrictions of each relevant started unit, remediate where feasible, and document residual authority. An exposure score alone is insufficient. |
| Security and documentation | This threat model and audit identify open boundaries | Resolve or explicitly accept release-significant findings; align roadmap, arc42, ADRs, experiments, and runbooks with the tested release. |

Close Step 7 only through a separate acceptance pull request after owner
acceptance. Landing this checklist does not complete the step.

## Current versus planned audit (2026-09-23)

Source, migrations, manifests, and tests support the current runtime described
in [arc42](../arc42/README.md): text workflows, PostgreSQL finance and
messaging state, the loopback control plane, a private receipt archive with
metadata review, verified backup, and scheduled backup/runtime retention.
This audit corrects stale descriptions of daily scheduling and of retention
timers still awaiting installation. It records the first natural calendar
catch-up without claiming multi-run retention.

Still planned for Step 7: multi-run repository retention evidence, clean-machine
restore and Langfuse-off startup, effective host sandbox qualification, complete
fault matrix, and final release acceptance. Receipt extraction and media
deletion remain deferred Step 6 work. No production bot is documented as
accepted.
