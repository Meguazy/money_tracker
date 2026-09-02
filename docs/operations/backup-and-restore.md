# Backup and Restore

- Status: accepted policy; encrypted backup, verification, retention, and an
  isolated restore drill implemented in Roadmap Step 2; scheduling and final
  clean-machine qualification remain Step 7
- Current finance backup: `money-tracker-backup` creates verified encrypted
  Restic snapshots of the authoritative database, receipt archive, and runtime
  configuration, and restores them into explicitly isolated targets

Backup is part of the active authoritative finance-data step, not an optional
post-release enhancement. Run the restore drill below before storing
irreplaceable personal data; the daily timer and clean-machine qualification are
still open.

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

Retention is enforced by the implemented command. Scheduling is not: no timer
exists yet, so snapshots are currently created when the owner runs the command.
No recovery-point or recovery-time claim should be added until measured by the
scheduled backup and a clean-machine drill.

## Host prerequisites

The backup command runs two external executables and refuses to guess their
location. Both settings must name an absolute executable path.

- Restic. Verified against 0.18.1. Install it from the distribution
  (`sudo apt install restic`) or from an upstream release binary.
- `pg_dump` and `pg_restore` from the **same major version as the application
  PostgreSQL server** pinned in [Container Versions](container-versions.md),
  currently 17. An older client refuses to dump a newer server, so the Ubuntu
  24.04 default PostgreSQL 16 client is not sufficient; install the PGDG
  `postgresql-client-17` package.

Verify both before the first backup:

    "${MONEY_TRACKER_RESTIC_BINARY}" version
    "${MONEY_TRACKER_PG_DUMP_BINARY}" --version

## Configuration

`scripts/generate_local_env.sh` writes these settings for a new environment and
`scripts/upgrade_local_env.sh` appends only the missing ones. Every path must be
absolute; the command rejects a relative or ambiguous value before doing work.

| Setting | Meaning |
| --- | --- |
| `MONEY_TRACKER_DATABASE_URL` | Credential-bearing source database URL, never placed in a command line |
| `MONEY_TRACKER_BACKUP_EXPECTED_DATABASE` | Exact database name allowed for backup |
| `MONEY_TRACKER_RECEIPT_ARCHIVE_ROOT` | Private authoritative receipt archive |
| `MONEY_TRACKER_BACKUP_CONFIG_FILE` | Mode-600 runtime configuration included only inside the encrypted repository |
| `MONEY_TRACKER_RESTIC_REPOSITORY` | Owner-selected external repository path |
| `MONEY_TRACKER_RESTIC_PASSWORD_FILE` | Mode-600 recovery-secret file |
| `MONEY_TRACKER_BACKUP_LOCK_FILE` | Host lock preventing overlapping backup or restore |
| `MONEY_TRACKER_BACKUP_STAGING_PARENT` | Protected parent for temporary dumps and manifests |
| `MONEY_TRACKER_RESTIC_BINARY` | Absolute Restic executable |
| `MONEY_TRACKER_PG_DUMP_BINARY` | Absolute `pg_dump` executable |
| `MONEY_TRACKER_PG_RESTORE_BINARY` | Absolute `pg_restore` executable |

The generator leaves `MONEY_TRACKER_RESTIC_REPOSITORY` as an explicit
placeholder. Point it at the owner-selected external folder or disk before the
first backup; the command must not silently back up onto the same device it
protects.

Create the recovery secret once, outside Git, with restrictive permissions:

    install -d -m 700 "$(dirname "${MONEY_TRACKER_RESTIC_PASSWORD_FILE}")"
    umask 077
    openssl rand -base64 48 >"${MONEY_TRACKER_RESTIC_PASSWORD_FILE}"

Then copy that value to an off-workstation location before initializing the
repository. The command refuses a password or configuration file that is not a
private regular file, and it never follows a final symlink to one.

## Authoritative restore set

Each snapshot contains:

1. a consistent custom-format `pg_dump` of application PostgreSQL, including
   migrations, finance, audit, and workflow checkpoints required for safe
   resume;
2. the content-addressed original receipt archive;
3. the mode-restricted runtime configuration, encrypted inside the repository;
4. a versioned `manifest.json` recording application version, database name,
   Alembic revision, dump SHA-256, configuration SHA-256, the absolute source
   paths, and the SHA-256 of every database-referenced receipt.

Messaging inbox/outbox tables and required derivatives are part of the set as
soon as the steps that create them land; the dump already covers whatever the
`messaging` schema contains.

The GGUF model is not backed up. It is recorded outside this set by filename and
hash and reacquired from its original source; the manifest deliberately carries
no model artifact.

## Non-authoritative or separate data

- Langfuse PostgreSQL, ClickHouse, Redis, and MinIO data are observability data
  and may have a separate optional backup policy.
- Python virtual environments, frontend build output, container layers, caches,
  and reproducible dependencies are rebuilt from source/locks.
- Generated evaluation artifacts remain disposable unless a promotion decision
  references them; referenced private artifacts need their own local retention.

Losing Langfuse must not prevent finance restore. Losing application PostgreSQL
or retained receipt data is an authoritative-data incident.

## Implemented backup sequence

Initialize the repository once against an empty path:

    uv run --env-file .env money-tracker-backup init

Create a verified snapshot:

    uv run --env-file .env money-tracker-backup create

The command performs the fixed sequence:

1. validate executables, private secret/configuration files, and repository
   presence, then acquire the exclusive host lock without waiting behind
   another operation;
2. verify that the configured and connected database name is exactly the
   expected one, read the Alembic revision, and verify the on-disk SHA-256 of
   every receipt the database references;
3. create the `pg_dump` as a mode-600 file in a mode-700 private staging
   directory;
4. build the manifest described above and write it mode-600;
5. ask Restic to back up staging, the receipt archive, and the configuration
   file in one snapshot tagged `money-tracker-authoritative`;
6. run `restic check` and require the exact new snapshot to be visible with
   that tag before staging is removed;
7. apply retention only afterwards, as seven daily, five weekly, and twelve
   monthly snapshots grouped by host and tag;
8. print safe JSON with the snapshot ID, receipt count, and database name.

Retention groups by tag on purpose. Restic groups by host and paths by default,
and each run stages its dump under a fresh private directory, so the default
would place every snapshot in a group of its own and never retain-prune.

A failed step raises before the next one and never reports success. Staging is
removed only after verification; the previous good snapshot is never deleted by
a failed run. Errors name only the failing executable, never a command line,
environment, or secret.

Report freshness for local health checks:

    uv run --env-file .env money-tracker-backup status

It prints the newest authoritative snapshot ID, its creation time, and its age
in hours, or `no-snapshot` for an initialized but empty repository.

## Recovery-secret rules

- Generate a high-entropy Restic password as shown above.
- Store the runtime copy mode-restricted and outside Git.
- Keep a second recoverable copy off the workstation and separate from the
  backup disk when possible.
- Never include the recovery secret in the unencrypted manifest, docs, shell
  command line, logs, traces, screenshots, or AI conversations. The command
  passes only the password file path to Restic, never its contents.
- Test access to the off-workstation copy during restore drills.

Encryption without a recoverable key is equivalent to data loss.

## Implemented restore drill

The restore command reconstructs a snapshot only into targets the operator names
explicitly, and only when both are already empty:

    MONEY_TRACKER_RESTORE_DATABASE_URL=postgresql://money_tracker:PASSWORD@127.0.0.1:5433/money_tracker_drill \
    MONEY_TRACKER_RESTORE_EXPECTED_DATABASE=money_tracker_drill \
    MONEY_TRACKER_RESTORE_RECEIPT_ROOT=/absolute/empty/drill/receipts \
      uv run --env-file .env money-tracker-backup restore-drill \
      --snapshot-id SNAPSHOT_ID

Its enforced sequence is:

1. accept only a lowercase hexadecimal snapshot ID and an absolute receipt
   target that is absent or an empty real directory, never a file or symlink;
2. verify tooling and private inputs, then take the same exclusive lock;
3. require the restore database URL to name exactly the expected database and
   to contain no `finance`, `messaging`, or `workflow` schema and no
   `alembic_version` table;
4. restore the snapshot into protected mode-700 staging;
5. require exactly one manifest, a supported manifest version, an application
   version equal to the running one, and canonical absolute paths and hashes;
6. verify the dump and configuration checksums, and every referenced receipt
   hash inside staging, refusing any symlink along a restored source path;
7. import the dump with `pg_restore --exit-on-error` into the empty target;
8. copy receipts to the target as mode-600 files in mode-700 shards and verify
   every referenced content hash again at the destination;
9. print safe JSON with the snapshot ID, receipt count, and database name.

The command therefore cannot restore into a live database, cannot overwrite a
populated receipt directory, and cannot silently accept a snapshot written by a
different application version. Note that step 5 makes a drill fail after an
application version bump: restore with the matching pinned version, as the
sequence below requires.

Real integration coverage lives in
[`tests/integration/money_tracker/backup`](../../tests/integration/money_tracker/backup);
[Development](../development.md) records how to run it.

## Planned clean-machine restore sequence

Step 7 must still qualify a restore onto an empty installation. The required
semantic sequence around the implemented command is:

1. choose an empty isolated target and record the selected snapshot;
2. install compatible pinned application/database versions without starting
   Telegram ingress or workers;
3. run the implemented restore drill against that target;
4. apply only the documented compatible migration path;
5. run domain/database integrity checks and count comparisons;
6. start API in isolated/read-only verification mode where available;
7. start workers/gateway only after idempotency, pending actions, offsets, and
   owner pairing have been inspected;
8. record the drill outcome without private data in the repository.

Do not restore into a live database or overwrite a receipt directory merely to
test a snapshot.

## Restore acceptance checks

- Database migration/version matches the application. The manifest records the
  Alembic revision and the command rejects a foreign application version.
- Every posted expense satisfies category and payment reconciliation.
- Categories, budgets, funding sources, wallet balances, and audit counts match
  the manifest/expected fixture.
- Every retained receipt reference resolves to the expected hash; no unexpected
  media path is web-accessible. The command verifies this in staging and again
  at the destination.
- Inbox/outbox uniqueness and idempotency constraints remain intact.
- Pending workflows either resume under a compatible graph or are explicitly
  drained/migrated before service start.
- Owner identity and default funding source are present without exposing tokens.
- Core operation starts and passes health checks with Langfuse absent.

The checks that read restored finance aggregates are still manual: the drill
verifies identity, integrity, and media, not business reconciliation.

## Destructive-operation guardrails

- Never use `docker compose down -v`, volume deletion, database drop, recursive
  media deletion, or Restic snapshot forgetting/pruning without resolving the
  exact target and confirming a verified recoverable snapshot.
- Treat retention pruning as destructive. The implemented command prunes only
  its own tag, only inside the configured repository, and only after the new
  snapshot has been verified. Do not run `restic forget` by hand instead.
- Media deletion through the product must update references/audit and propagate
  through backup retention according to the documented policy; deleting a file
  manually creates corruption. Product media deletion is not implemented, so no
  snapshot currently needs that propagation.
- A restore drill uses isolated names and cleans only the data it owns. The
  command refuses any non-empty target rather than merging into it.

## Drill schedule

- Run an initial isolated restore before storing irreplaceable personal data.
- Run again after a migration or material backup-layout change.
- Run periodically during Step 7 operations and after any repository repair.
- Record snapshot ID, application/migration versions, duration, checks passed,
  failures, and follow-up without including private content.
