# Backup and Restore

- Status: accepted policy; implementation planned for Roadmap Step 2 and final
  restore qualification in Step 7
- Current finance backup: none; authoritative finance tables now exist, while
  receipt media storage and the required encrypted backup command remain open

Backup is part of the active authoritative finance-data step, not an optional
post-release enhancement. Do not store irreplaceable personal data while this
runbook still lacks a verified executable backup and isolated restore drill.

## Recovery objectives

The owner must be able to reconstruct every posted/draft finance record, audit
history, category/budget/wallet state, messaging idempotency state needed for
safe continuation, and every retained receipt image from an encrypted backup.

Initial policy targets:

- daily scheduled snapshots;
- seven daily, five weekly, and twelve monthly retained snapshots;
- an owner-selected external folder or disk;
- a Restic recovery secret copied outside the workstation;
- restore on an empty local installation as a release gate.

No recovery-point or recovery-time claim should be added until measured by the
implemented backup and restore drills.

## Authoritative restore set

Planned required contents:

1. a consistent logical dump of application PostgreSQL, including migrations,
   finance, audit, messaging state, and workflow checkpoints required for safe
   resume;
2. the content-addressed original receipt archive and required derivatives;
3. stable non-secret configuration and a manifest of expected paths/versions;
4. the private runtime configuration necessary to reconnect services, including
   secrets only inside the encrypted Restic repository;
5. checksums and backup metadata sufficient to detect missing/corrupt media.

The GGUF model may be recorded by filename/hash and reacquired rather than
backed up when license/source and availability make that safe. This choice must
be explicit in the implemented manifest.

## Non-authoritative or separate data

- Langfuse PostgreSQL, ClickHouse, Redis, and MinIO data are observability data
  and may have a separate optional backup policy.
- Python virtual environments, frontend build output, container layers, caches,
  and reproducible dependencies are rebuilt from source/locks.
- Generated evaluation artifacts remain disposable unless a promotion decision
  references them; referenced private artifacts need their own local retention.

Losing Langfuse must not prevent finance restore. Losing application PostgreSQL
or retained receipt data is an authoritative-data incident.

## Planned backup sequence

Roadmap Step 2 must implement and test a real command/timer. Its behavior is
fixed even though no executable command exists yet:

1. acquire a backup operation lock without blocking normal reads;
2. verify target database identity and backup destination;
3. create a consistent `pg_dump` in a protected temporary staging directory;
4. build a manifest with application/migration version, dump checksum, receipt
   counts/hashes, and configuration inventory;
5. ask Restic to back up the dump, manifest, receipt archive, and required
   configuration;
6. verify repository/snapshot success before removing staging data;
7. enforce retention only after a new verified snapshot exists;
8. record success/failure and age for local health reporting without logging
   secrets.

A failed backup does not delete the last good snapshot and must not report
success based solely on a process exit before verification.

## Recovery-secret rules

- Generate a high-entropy Restic password or key through the future setup flow.
- Store the runtime copy mode-restricted and outside Git.
- Keep a second recoverable copy off the workstation and separate from the
  backup disk when possible.
- Never include the recovery secret in the unencrypted manifest, docs, shell
  command line, logs, traces, screenshots, or AI conversations.
- Test access to the off-workstation copy during restore drills.

Encryption without a recoverable key is equivalent to data loss.

## Planned restore sequence

The final runbook must expose concrete, tested commands. The required semantic
sequence is:

1. choose an empty isolated target and record the selected snapshot;
2. install compatible pinned application/database versions without starting
   Telegram ingress or workers;
3. verify the Restic repository and restore the snapshot to protected staging;
4. validate manifest and checksums before importing data;
5. initialize an empty application PostgreSQL and restore the logical dump;
6. restore receipt files to their private target and verify every referenced
   content hash;
7. apply only the documented compatible migration path;
8. run domain/database integrity checks and count comparisons;
9. start API in isolated/read-only verification mode where available;
10. start workers/gateway only after idempotency, pending actions, offsets, and
    owner pairing have been inspected;
11. record the drill outcome without private data in the repository.

Do not restore into a live database or overwrite a receipt directory merely to
test a snapshot.

## Restore acceptance checks

- Database migration/version matches the application.
- Every posted expense satisfies category and payment reconciliation.
- Categories, budgets, funding sources, wallet balances, and audit counts match
  the manifest/expected fixture.
- Every retained receipt reference resolves to the expected hash; no unexpected
  media path is web-accessible.
- Inbox/outbox uniqueness and idempotency constraints remain intact.
- Pending workflows either resume under a compatible graph or are explicitly
  drained/migrated before service start.
- Owner identity and default funding source are present without exposing tokens.
- Core operation starts and passes health checks with Langfuse absent.

## Destructive-operation guardrails

- Never use `docker compose down -v`, volume deletion, database drop, recursive
  media deletion, or Restic snapshot forgetting/pruning without resolving the
  exact target and confirming a verified recoverable snapshot.
- Treat retention pruning as destructive: run it only against the configured
  Money Tracker repository after a successful current backup.
- Media deletion through the product must update references/audit and propagate
  through backup retention according to the documented policy; deleting a file
  manually creates corruption.
- A restore drill uses isolated names and cleans only the data it owns.

## Drill schedule

- Run an initial isolated restore before storing irreplaceable personal data.
- Run again after a migration or material backup-layout change.
- Run periodically during Step 7 operations and after any repository repair.
- Record snapshot ID, application/migration versions, duration, checks passed,
  failures, and follow-up without including private content.
