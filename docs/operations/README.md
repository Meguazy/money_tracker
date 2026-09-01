# Operations Guide

- Status: preliminary Step 0 baseline
- Current executable operations: local inference/CLI and the existing Compose
  scaffold described in `docs/development.md`
- Planned operations: application PostgreSQL, Telegram, systemd services,
  finance backup/restore, web API, worker, and receipt storage

These guides describe accepted operational behavior and the controls future
steps must implement. A section labelled `planned` is not a runnable procedure.
Before executing a command, verify that its roadmap step is complete and that
the referenced file or entry point exists in the repository.

## Runbooks

- [Telegram](telegram.md): BotFather preparation, private-owner pairing target,
  long polling, the 24-hour limitation, testing, and token response.
- [Local Runtime](local-runtime.md): current scaffold, target process topology,
  startup/recovery, health, and deployment ownership.
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

- The CLI, environment generator, llama.cpp launcher, and optional Langfuse
  callback integration exist.
- No local model path in the repository is an installed model guarantee.
- No Telegram bot, adapter, application database, finance backup, API,
  dashboard, or systemd unit exists yet.
- The current Langfuse web container has been observed restarting because the
  non-cluster ClickHouse migration lacks `CLICKHOUSE_CLUSTER_ENABLED=false`.
  Roadmap Step 1 owns the non-destructive repair and immutable image pinning.

## When to update these guides

Update the relevant runbook in the same change that adds or changes a service,
port, credential, file path, systemd unit, health check, migration, retention
rule, backup set, restart behavior, failure mode, or operator command. Move a
procedure from `planned` to `implemented` only after its real integration or
restore verification passes.
