# Operations Guide

- Status: Step 1 platform foundation complete; product operations planned
- Current executable operations: local inference/CLI, dedicated application
  PostgreSQL, migrations/checkpoint bootstrap, optional healthy Langfuse, and
  capability evaluation
- Implemented Step 2 core: authoritative finance schema and transactional
  expense, wallet, budget, pending category-proposal, and receipt-metadata
  repository operations
- Planned operations: Telegram, systemd services, finance backup/restore, web
  API, workflow worker, and receipt storage

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
- [Backup and Restore](backup-and-restore.md): authoritative restore set,
  encryption, retention, verification, and recovery acceptance.
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
  metadata repository operations exist; proposal/receipt lifecycle transitions
  and receipt media storage remain open. No Telegram bot/adapter, finance
  backup, API/dashboard, workflow worker, receipt store, or systemd unit exists
  yet.

## When to update these guides

Update the relevant runbook in the same change that adds or changes a service,
port, credential, file path, systemd unit, health check, migration, retention
rule, backup set, restart behavior, failure mode, or operator command. Move a
procedure from `planned` to `implemented` only after its real integration or
restore verification passes.
