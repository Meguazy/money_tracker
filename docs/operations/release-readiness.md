# Personal V1 Release Readiness

- Status: Step 7 personal-release assessment in progress; this checklist does
  not declare V1 ready.
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
| Loss, corruption, or rollback of authoritative data | Encrypted verified Restic snapshots include PostgreSQL, originals, and recovery configuration; isolated restore checks hashes and business invariants; [backup runbook](backup-and-restore.md) | The owner must accept that the same-workstation repository does not survive whole-machine loss. Clean-machine restore with the off-workstation secret moves post-V1; the phone copy must still match the configured secret before release. |
| Local process or physical access reads live or deleted content | Owner-only filesystem modes and service isolation directives; [data-retention runbook](data-retention.md) records residual copies | The owner must accept for personal V1 that mount protections do not take effect and root/swap are unencrypted. Effective service restrictions and disk protection remain post-V1 work, not claims of this release. |
| Failed upgrade leaves incompatible code or storage | Writer freeze, verified snapshot, forward migration, compatibility classification, isolated restore path in the [upgrade runbook](upgrade-and-rollback.md); the 2026-09-23 `0008` deployment passed the fresh-snapshot recovery proof and post-migration drill | Keep the tagged pre-upgrade recovery point through the accepted observation window. Incompatible production cutover requires a separate owner decision. |

The 2026-09-24 personal-release proposal would accept only the explicit
whole-machine-loss and local-access exposures above after owner confirmation. It does not waive an
unknown data-integrity, authorization, credential-leak, or failed-backup
finding. Such a finding still stops release until resolved or separately
accepted by the owner with evidence.

## Operations checklist

| Gate | Current evidence | Next acceptance action |
| --- | --- | --- |
| Scope and promoted text configuration | Step 6 is deferred; EVD-004 promotes the text configuration, and Step 4/5/5.5 owner workflows passed | Re-run affected evaluations only if a promoted boundary changes; record the final V1 version set. |
| Service startup and recovery | Step 4 unattended reboot and five fault injections, Step 5 installed API acceptance, and Step 5.5 Langfuse-off operation passed | Check current API readiness, Telegram queue health, and core unit state. Defer the complete cross-dependency fault matrix. |
| Scheduled backup and retention | Timer-to-service paths passed on 2026-09-20. Calendar catch-ups on 2026-09-23 and 2026-09-24 made verified snapshots; the latest passed an isolated drill and the held pre-upgrade point survived retention | Current backup gate passed. Defer full seven-daily/five-weekly/twelve-monthly qualification. |
| Recovery secret and isolated restore | Off-workstation access passed on 2026-09-20. The repaired production schema produced a snapshot that passed an ordinary isolated drill | Compare the phone copy locally using the [hidden-input check](backup-and-restore.md#check-the-off-workstation-copy-for-the-personal-v1). Defer empty-installation reconstruction and measured RPO/RTO. |
| Effective service confinement | Units start, but the 2026-09-17 probe and 2026-09-24 namespace check found mount protections ineffective under this user manager | Record the accepted personal-release risk; qualify effective restrictions post-V1 before claiming stronger host isolation. |
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

For this narrowed Step 7, the 2026-09-24 scheduled backup and content-free core
health checks passed. The hidden-input phone-secret comparison and final owner
acceptance remain. Multi-run retention, clean-machine restore,
effective host sandbox, and full fault matrix remain open post-V1 rather than
being silently counted as passed. The default-pointer repair is deployed, and
its new snapshot passed an isolated drill. Receipt extraction and media deletion
remain deferred Step 6 work. The private test bot is the accepted personal
channel; no distinct production bot is documented as accepted.
