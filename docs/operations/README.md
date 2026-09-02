# Operations Guide

- Status: Step 1 platform foundation complete; product operations planned
- Current executable operations: local inference/CLI, dedicated application
  PostgreSQL, migrations/checkpoint bootstrap, optional healthy Langfuse, and
  capability evaluation, plus profile-scoped administrative receipt storage
- Implemented Step 2 core: authoritative finance schema and transactional
  expense, wallet, budget, pending category-proposal, and versioned receipt-review
  repository operations, a safe local receipt-media adapter, and verified
  encrypted backup with an isolated restore drill
- Planned operations: Telegram, systemd services, backup scheduling and
  clean-machine restore, web API, workflow worker receipt wiring, and media
  deletion

These guides describe accepted operational behavior and the controls future
steps must implement. A section labelled `planned` is not a runnable procedure.
Before executing a command, verify that its roadmap step is complete and that
the referenced file or entry point exists in the repository.

## Runbooks

- [Telegram](telegram.md): BotFather preparation, private-owner pairing target,
  long polling, the 24-hour limitation, testing, and token response.
- [Local Runtime](local-runtime.md): current foundation, target process
  topology, startup/recovery, health, and deployment ownership.
- [Container Versions](container-versions.md): immutable image baseline,
  verification evidence, controlled upgrades, and rollback constraints.
- [Backup and Restore](backup-and-restore.md): host prerequisites, backup
  configuration, the implemented `money-tracker-backup` sequence, retention,
  the isolated restore drill, and recovery acceptance.
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
  administration CLI, capability runner, and optional Langfuse callbacks exist.
- The dedicated application PostgreSQL is healthy, independently persistent,
  least-privileged, and separate from the Langfuse database. It now owns the
  Step 2 finance schema, while messaging product tables remain planned.
- Langfuse 4.26.0 and every dependency are pinned by digest. The non-cluster
  repair and health checks pass with the pre-existing volumes preserved.
- Installed model paths remain workstation-owned and outside the repository.
  Three pinned profiles pass the Step 1 capability screen, but no model is
  promoted to a product role.
- Expense, wallet, budget, pending-proposal, and content-idempotent receipt
  metadata operations exist. Receipt review/approval transitions are versioned,
  audited, and concurrency-safe; proposal decisions and receipt extraction or
  deletion remain open. The private content-addressed receipt adapter is
  integration-tested through the local administration CLI.
- `money-tracker-backup` creates verified encrypted snapshots of the
  authoritative database, receipt archive, and runtime configuration, reports
  snapshot age, and restores only into explicitly empty isolated targets. It
  requires Restic and PostgreSQL 17 client tools on the host and points at an
  owner-selected external repository that the environment generator leaves as a
  deliberate placeholder. No scheduling timer, off-device secret check, or
  clean-machine drill exists yet.
- No Telegram bot/adapter, API/dashboard, workflow worker, media deletion, or
  systemd unit exists yet.

## When to update these guides

Update the relevant runbook in the same change that adds or changes a service,
port, credential, file path, systemd unit, health check, migration, retention
rule, backup set, restart behavior, failure mode, or operator command. Move a
procedure from `planned` to `implemented` only after its real integration or
restore verification passes.
