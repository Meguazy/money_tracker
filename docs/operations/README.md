# Operations Guide

- Status: Steps 1–5 complete; Step 5 local dashboard browser-accepted on 2026-09-16;
  Step 5.5 LLM traceability in progress
- Current executable operations: local inference/CLI, dedicated application
  PostgreSQL, migrations/checkpoint bootstrap, optional healthy Langfuse,
  capability evaluation, profile-scoped receipt storage, one-shot Step 3
  dispatch, durable Telegram gateway/worker, pairing,
  health/replay/resend/latency, the local monthly dashboard/API, calendar
  analytics with merchant and prior-period comparison, manual movement lifecycle with multi-allocation editing, monthly
  budget planning, funding-source lifecycle and expiry-aware wallet administration,
  category-proposal review, receipt metadata consultation and its mandatory
  review, browser inspection and requeue of the durable messaging queues, safe
  profile-settings consultation and default-source selection, read-only browser
  advice grounded in promoted deterministic tools, real-browser E2E and WCAG
  A/AA automation against isolated PostgreSQL and the local model, and
  checked-in host service units including the browser-accepted API unit, and
  opt-in full-fidelity tracing of `money-tracker-admin route`, of the Telegram
  gateway and worker, one trace per update, and of web advice into the local
  Langfuse, with evaluation-promotion publication and a 30-day trace retention
  command offering private export and verified deletion
- Implemented Step 2: authoritative finance schema and transactional
  expense, wallet, budget, pending category-proposal, and versioned receipt-review
  repository operations, a safe local receipt-media adapter, and verified
  encrypted backup with an isolated restore drill
- Planned operations: backup and trace-retention scheduling and clean-machine
  restore,
  workflow-worker receipt wiring, receipt extraction, and media deletion

These guides describe accepted operational behavior and the controls future
steps must implement. A section labelled `planned` is not a runnable procedure.
Before executing a command, verify that its roadmap step is complete and that
the referenced file or entry point exists in the repository.

## Runbooks

- [Local Web](web.md): build, loopback API/dashboard, browser controls, health,
  systemd, browser and installed-service acceptance, verification, and
  rollback for the implemented Step 5 slices,
  including merchant and period-comparison analytics, profile settings, and
  funding-source/wallet administration, the metadata-only receipt
  review surface, and the content-free messaging operations surface.
- [Telegram](telegram.md): BotFather preparation, private-owner pairing target,
  long polling, the 24-hour limitation, testing, and token response.
- [Local Runtime](local-runtime.md): current foundation, target process
  topology, startup/recovery, health, and deployment ownership.
- [Container Versions](container-versions.md): immutable image baseline,
  verification evidence, controlled upgrades, and rollback constraints.
- [Backup and Restore](backup-and-restore.md): host prerequisites, backup
  configuration, the implemented `money-tracker-backup` sequence, retention,
  the isolated restore drill, and recovery acceptance.
- [LLM Tracing](tracing.md): what AD-021 traces contain, one trace per
  Telegram update, decision metadata, the explicit switch, identity in
  services, reading a trace, measured degradation, the 30-day retention with
  private export and verified deletion, journal events, and verification.
- [Security and Troubleshooting](security-and-troubleshooting.md): trust
  boundaries, secret handling, logging, incident response, and failure triage.
- [Development](../development.md): commands verified for the current scaffold.

## Operational invariants

- Never run a destructive volume, database, media, or backup command merely to
  clear an error. Identify the exact owner and take a recoverable backup first.
- Application finance data and Langfuse data have different ownership and
  recovery priority.
- Core finance and Telegram behavior must not depend on Langfuse availability.
- Telegram acknowledgement happens only after durable local inbox commit.
- A model outage may delay processing but must not prevent the gateway from
  ingesting available updates.
- Secrets and token-bearing URLs must not appear in shell history, screenshots,
  logs, traces, committed files, or support reports.
- Planned commands must become real, tested commands in the same roadmap step
  that implements their component; update these runbooks then.

## Current known operational state

- The CLI, environment generator/upgrader, llama.cpp launcher, database
  administration CLI, single Step 3 workflow dispatcher, and capability runner
  exist. The earlier optional Langfuse callbacks covered only the scaffold CLI.
  Step 5.5 replaces them with an injected tracing boundary behind
  `MONEY_TRACKER_TRACING_ENABLED`, which traces `money-tracker-admin route`, the
  scaffold, the Telegram gateway and worker, and web advice; the installed
  services run untraced until the switch is set and they are restarted.
- The dedicated application PostgreSQL is healthy, independently persistent,
  least-privileged, and separate from the Langfuse database. It owns the
  authoritative finance schema, persistent workflow checkpoints, and the
  normalized Step 4 messaging tables/repository. The Telegram gateway and
  sequential worker entry points, one-use pairing, health, scoped dead-letter
  replay, Italian response rendering, and host unit templates passed real owner
  workflows, restart/reboot, latency, and fault acceptance on 2026-09-11.
- The FastAPI process, monthly overview and transaction endpoints, compiled
  React overview/register, audited manual lifecycle, multi-allocation editing
  of both reconciling dimensions, replay-safe monthly budget planning, versioned funding-source lifecycle, expiry-aware wallet administration, safe profile-settings consultation and default-source selection,
  deterministic category-proposal review, replay-safe receipt metadata review,
  messaging queue consultation with pinned inbox replay and outbox resend,
  local browser controls, and API
  user-service template are implemented. The receipt surface carries metadata
  only: it never returns an original, its archive key, or a deletion path. The
  messaging surface carries counts, ages, identifiers and reason codes only: it
  never returns a message, a bot key, or a Bot API credential, and it cannot
  send, pair, move the offset, or delete. Read-only browser advice enters the
  promoted advice graph with trusted profile scope and returns only deterministic
  tool evidence; no mutation service is reachable from that composition.
  Real-browser E2E, WCAG A/AA automation, and acceptance of the installed
  `money-tracker-api.service`, including a real restart, passed on 2026-09-16.
- Langfuse 4.26.0 and every dependency are pinned by digest. The non-cluster
  repair and health checks pass with the pre-existing volumes preserved. Under
  AD-021 its volumes may hold owner content, so treat them like the finance
  database; by owner decision they stay outside the backup set and runtime
  traces are kept 30 days.
- Installed model paths remain workstation-owned and outside the repository.
  EVD-004 promotes `text-qwen3-14b` with `Qwen3-14B-Q5_K_M.gguf`, router v6,
  extraction v6, category-fit v6, tool-selection v5, routing confidence 0.5,
  and `explicit-finance-scope-v1`. `model-only-v0` remains the direct
  read-selection rollback; no receipt model is promoted.
- Expense, versioned funding-source, expiry-aware wallet, budget, pending-proposal, and content-idempotent receipt
  metadata operations exist. Receipt review/approval transitions are versioned,
  audited, replay-safe, concurrency-safe, and reachable from `/scontrini`;
  receipt extraction and deletion remain open. The private content-addressed
  receipt adapter is integration-tested through the local administration CLI,
  which remains the only way to import an original.
- `money-tracker-backup` creates verified encrypted snapshots of the
  authoritative database, receipt archive, and runtime configuration, reports
  snapshot age, and restores only into explicitly empty isolated targets. It
  requires Restic and PostgreSQL 17 client tools on the host and points at an
  owner-selected external repository that the environment generator leaves as a
  deliberate placeholder. No scheduling timer, off-device secret check, or
  clean-machine drill exists yet.
- The private test bot passed real owner workflows, unauthorized-user checks,
  five fault injections, p95 latency, and an unattended reboot with all Step 4
  host services. No production bot exists. Media deletion and receipt
  extraction do not exist yet.

## When to update these guides

Update the relevant runbook in the same change that adds or changes a service,
port, credential, file path, systemd unit, health check, migration, retention
rule, backup set, restart behavior, failure mode, or operator command. Move a
procedure from `planned` to `implemented` only after its real integration or
restore verification passes.
