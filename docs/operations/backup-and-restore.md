# Backup and Restore

- Status: accepted policy; encrypted backup, verification, retention, and an
  isolated restore drill implemented in Roadmap Step 2; daily timer installed,
  enabled, and accepted through a timer-triggered production run in Step 7;
  multi-run retention and final clean-machine qualification remain post-V1 work
- Current finance backup: `money-tracker-backup` creates verified encrypted
  Restic snapshots of the authoritative database, receipt archive, and runtime
  configuration, and restores them into explicitly isolated targets

Backup is part of the authoritative finance-data system, not an optional
post-release enhancement. Run the restore drill below before storing
irreplaceable personal data; timer-to-service acceptance has passed, while
multi-run retention evidence and clean-machine qualification are still open
post-V1 follow-ups under the owner's 2026-09-24 personal-release decision.

## Recovery objectives

The owner must be able to reconstruct every posted/draft finance record, audit
history, category/budget/wallet state, messaging idempotency state needed for
safe continuation, and every retained receipt image from an encrypted backup.

Initial policy targets:

- daily scheduled snapshots;
- seven daily, five weekly, and twelve monthly retained snapshots;
- an owner-selected external folder or disk;
- a Restic recovery secret copied outside the workstation;
- restore into empty isolated database and archive targets as the personal V1
  release gate; rebuild an empty installation as post-V1 recovery work.

Retention is enforced by the implemented command. A persistent user timer now
schedules it daily; the owner must explicitly install, enable, and observe the
first runs after manually verifying the repository.

No recovery-point or recovery-time claim should be added until measured by the
scheduled backup and a clean-machine drill.

## Host prerequisites

The backup command runs two external executables and refuses to guess their
location. Both settings must name an absolute executable path.

- Restic. Verified against 0.18.1, the release the CI drill pins. Ubuntu
  24.04 packages 0.16, so install the upstream binary after checking its
  SHA-256, and point `MONEY_TRACKER_RESTIC_BINARY` at it:

      cd /tmp
      curl -fLO https://github.com/restic/restic/releases/download/v0.18.1/restic_0.18.1_linux_amd64.bz2
      echo "680838f19d67151adba227e1570cdd8af12c19cf1735783ed1ba928bc41f363d  restic_0.18.1_linux_amd64.bz2" \
        | sha256sum --check --strict
      bunzip2 restic_0.18.1_linux_amd64.bz2
      sudo install -m 0755 restic_0.18.1_linux_amd64 /usr/local/bin/restic

- `pg_dump` and `pg_restore` from the **same major version as the application
  PostgreSQL server** pinned in [Container Versions](container-versions.md),
  currently 17. An older client refuses to dump a newer server, so the Ubuntu
  24.04 default PostgreSQL 16 client is not sufficient. Install
  `postgresql-client-17` from PGDG after checking the signing key's
  fingerprint, as the CI job does:

      curl -fsS https://www.postgresql.org/media/keys/ACCC4CF8.asc -o /tmp/pgdg.asc
      gpg --show-keys --with-colons /tmp/pgdg.asc \
        | grep -F ':B97B0AFCAA1A47F044F244A07FCC7D46ACCC4CF8:'
      sudo install -d -m 0755 /usr/share/postgresql-common/pgdg
      gpg --dearmor < /tmp/pgdg.asc \
        | sudo tee /usr/share/postgresql-common/pgdg/apt.postgresql.org.gpg > /dev/null
      printf '%s%s%s\n' \
        'deb [signed-by=/usr/share/postgresql-common/pgdg/' \
        'apt.postgresql.org.gpg] https://apt.postgresql.org/' \
        'pub/repos/apt noble-pgdg main' \
        | sudo tee /etc/apt/sources.list.d/pgdg.list
      sudo apt-get update
      sudo apt-get install --yes --no-install-recommends postgresql-client-17

  Stop if the `grep` prints nothing: the key is not the one PGDG publishes.

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
`messaging` schema contains. Runtime retention deletes concluded messaging rows
and terminal checkpoints after 30 days, but every snapshot taken earlier keeps
them until that snapshot is forgotten; see
[Data Retention](data-retention.md#secure-deletion-and-residual-copies).

The GGUF model is not backed up. It is recorded outside this set by filename and
hash and reacquired from its original source; the manifest deliberately carries
no model artifact.

## Non-authoritative or separate data

- Langfuse PostgreSQL, ClickHouse, Redis, and MinIO data are observability data.
  Under AD-021 they hold complete owner messages and completions. The owner
  decided on 2026-09-17 that they stay outside the encrypted backup set as a
  reconstructible copy: restoring the finance set restores no trace, and losing
  the volumes loses traces only. Runtime traces are kept 30 days and may be
  exported to a private local file first with `money-tracker-traces`, as
  [LLM Tracing](tracing.md#retention-export-deletion-and-backup) describes; an
  export file is owner content outside this backup and the operator's to keep
  private or remove. Do not add the volumes
  to a Restic snapshot by hand: a copy taken while ClickHouse and MinIO write is
  not consistent, and it would keep owner content past its retention.
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
   expected one, read the Alembic revision, verify the on-disk SHA-256 of every
   receipt the database references, and run the restored-data semantic checks
   against the live source in a read-only transaction;
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

The semantic source check was added after a real production snapshot failed
the restore verifier. It refuses a known mismatch before the command reaches
Restic's `forget --prune` stage. It does not pin the later `pg_dump` to the same
read transaction, so deployment acceptance still requires an isolated drill of
the new snapshot.

Report freshness for local health checks:

    uv run --env-file .env money-tracker-backup status

It prints the newest authoritative snapshot ID, its creation time, and its age
in hours, or `no-snapshot` for an initialized but empty repository.

## Scheduled backup

`money-tracker-backup.timer` runs the production `create` command every day at
03:15 in the workstation's local timezone, with up to thirty minutes of jitter.
`Persistent=true` catches up a missed occurrence after the workstation next
starts. The oneshot has a two-hour bound and the command's existing host lock
prevents overlap with a manual backup or restore.

The installer renders the service and timer but deliberately leaves both
disabled. Configure the external repository and recovery-secret file,
initialize the repository, and complete one manual `create` plus `status`
before enabling the schedule:

    ./scripts/install_systemd_units.sh
    systemctl --user enable --now money-tracker-backup.timer
    systemctl --user list-timers money-tracker-backup.timer

Enabling a persistent timer can immediately trigger an occurrence missed since
its last recorded run. Inspect the first result without exposing command
arguments or environment values:

    systemctl --user status money-tracker-backup.service
    journalctl --user -u money-tracker-backup.service --since today
    uv run --env-file .env money-tracker-backup status

The static unit cannot enumerate an owner-selected repository, staging parent,
lock, receipt archive, and password-file location, so it preserves the manual
CLI's owner filesystem authority while using an immutable operating-system
view. A failed scheduled run never reports a snapshot as verified; investigate
and rerun the bounded service after correcting its dependency or configuration.

### Target installation evidence (2026-09-20)

The target workstation uses Restic 0.18.1 and PostgreSQL 17.11 client tools.
The owner selected the separate 4 TB NVMe shared with Windows. Its NTFS data
partition is mounted at a stable path through `fstab` with `ntfs3`, `nofail`,
an on-demand systemd automount, and owner-only file and directory masks. The
repository remains encrypted, but its location inside the workstation does not
protect against theft, fire, or loss of the whole machine.

The initialized repository accepted and verified one direct CLI snapshot and
two snapshots through `money-tracker-backup.service`; the last service run used
the persistent mount and exited zero. `money-tracker-backup.timer` is enabled
and active. At installation its first daily-calendar occurrence resolved to
2026-09-21 at 03:24 CEST; the timer-triggered acceptance below superseded the
wait for that occurrence. Multi-run retention evidence and the clean-machine
restore remain acceptance work.

At 21:55:09 CEST on 2026-09-20, the installed production timer triggered the
unchanged production service under a temporary two-minute monotonic schedule
with no jitter. The service created and verified a snapshot in about three
seconds and exited zero. The temporary drop-in was then removed; daemon reload
and timer restart restored and confirmed the checked-in daily 03:15 calendar,
`Persistent=true`, and up-to-thirty-minute jitter. A following `status` read
the new snapshot. The timer-to-service path is accepted; retention enforcement
across multiple scheduled runs remains open.

After workstation downtime from 2026-09-21 to 2026-09-23, `Persistent=true`
triggered the production daily calendar on 2026-09-23 at 14:00 CEST. The
unchanged service created and verified snapshot
`8d52b52cd5d2d54a730f1fc63ccaf22449e41063a51d7c8028c6dcef2a0eec90`
and exited zero. The same day's trace and runtime-content retention services
also exited zero. This establishes a real calendar catch-up, but only one
post-acceptance daily backup run has been observed; multi-run repository
retention remains open.

On 2026-09-24 at 11:53 CEST, another calendar catch-up created verified
snapshot `3681bda7833b2e95148c41bb595b39bec5adc5a9658cf6e7e0f53c847450c32e`
and the backup service exited zero. Its ordinary isolated `restore-drill`
passed at revision `20260923_0008` with one profile/default source, 43
expenses, 74 idempotency records, 107 inbox rows, 137 outbox rows, 55 pending
actions, and zero referenced receipts. The generated database and archive were
removed. The separately held pre-upgrade snapshot remained present after
retention. Two calendar-triggered runs are not evidence that every 7/5/12
retention bucket has aged into enforcement.

### Legacy default-source recovery (2026-09-23)

An isolated drill of production snapshot
`8d52b52cd5d2d54a730f1fc63ccaf22449e41063a51d7c8028c6dcef2a0eec90`
found `profile_default_source_invalid`. The source database has one null
profile pointer and one active default-flagged source, both dating to the
original 2026-09-02 bootstrap. The current source-creation path updates both
fields, but it cannot retroactively change those rows.

Migration `20260923_0008` fills only that unique missing pointer and
refuses all other drift in one transaction. On 2026-09-23 the real snapshot was
restored into a generated empty database and private temporary archive. The
candidate verifier first reported the expected old schema revision; after
applying the candidate migration to that isolated target, semantic verification
passed with one active profile, one default source, 43 expenses, 74 finance
idempotency records, 107 inbox rows, 137 outbox rows, 55 pending actions, and
zero referenced receipt originals. The temporary database and archive were
removed. This proves recovery of the selected snapshot using the candidate
version; it is not a clean-machine drill and did not use the off-workstation
copy of the secret.

The [upgrade/rollback runbook](upgrade-and-rollback.md) was exercised on
2026-09-23. Writers and timers stopped with no pending or retrying messages or
dead letters. The previous reviewed commit created fresh snapshot
`21f4db2fe7e150ebb3b0651bf9016763f75b4825badded7c32d74325549cc0a7`.
Its isolated restore reproduced the known pointer mismatch; applying
`20260923_0008` only to that target made semantic verification pass with the
same counts above. The snapshot was retagged to
`7ee53ceed3c9d3d842cd3548f38b6f9311792405bde3a2102b92676124d3394f`
with `step7-preupgrade-hold`, and `restic check` passed. The extra tag keeps
this pre-upgrade recovery point in a separate retention group through the
observation window; remove it only after an explicit owner decision.

Production then migrated to `20260923_0008`. Source-semantic preflight and
verified snapshot
`77dbf970886319b4fd596c57ccb9d77d43840a6cbbedc1e3338b572c2a1e8c8b`
passed; that snapshot's ordinary isolated `restore-drill` passed with the same
counts. Both temporary databases and archives were removed. Core services and
the three prior timers resumed. This same-workstation evidence is not the
clean-machine restore or a multi-run retention observation. The old release's
exact schema checks do not accept revision `20260923_0008`, so rollback to old
code is still not an in-place option.

On 2026-09-20 the owner copied the generated recovery secret into the
authenticated password-manager account used by the Android phone and opened
the synchronized entry on that phone. The value crossed only the local
clipboard, which was then cleared and verified empty. Off-workstation access is
therefore accepted; using that copy in the clean-machine restore remains open.

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

### Check the off-workstation copy for the personal V1

On the workstation, open the saved secret on the phone and type it into this
hidden prompt. Do not paste it into a chat, shell command, document, or PR.
The comparison prints only a result and exits nonzero on mismatch. The local
secret has already unlocked the repository for the verified backup and drill;
matching it proves the phone copy is the same value, not that an empty machine
can be rebuilt. Record only `recovery-secret-match` as evidence.

    uv run --env-file .env python -c '
    import getpass
    import hmac
    import os
    import sys
    from pathlib import Path

    if not sys.stdin.isatty():
        raise SystemExit("interactive terminal required")
    try:
        local = Path(os.environ["MONEY_TRACKER_RESTIC_PASSWORD_FILE"]).read_text(
            encoding="utf-8"
        ).rstrip("\r\n")
    except OSError:
        raise SystemExit("configured recovery secret unavailable") from None
    entered = getpass.getpass("Recovery secret from phone: ")
    matched = hmac.compare_digest(entered, local)
    print("recovery-secret-match" if matched else "recovery-secret-mismatch")
    raise SystemExit(0 if matched else 1)
    '

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
9. verify the restored data semantically in one read-only repeatable-read
   transaction, as `verify-restore` below does on its own;
10. print safe JSON with the snapshot ID, receipt count, and database name.

The command therefore cannot restore into a live database, cannot overwrite a
populated receipt directory, cannot silently accept a snapshot written by a
different application version, and cannot report success for a byte-perfect
restore whose business records contradict each other. Note that step 5 makes a drill fail after an
application version bump: restore with the matching pinned version, as the
sequence below requires.

Real integration coverage lives in
[`tests/integration/money_tracker/backup`](../../tests/integration/money_tracker/backup);
[Development](../development.md) records how to run it.

## Implemented restored-data verification

The semantic gate that closes the drill is also an operator command. It needs
neither Restic nor the PostgreSQL client binaries, so it can be repeated on a
restored target at any time:

    MONEY_TRACKER_RESTORE_DATABASE_URL=postgresql://money_tracker:PASSWORD@127.0.0.1:5433/money_tracker_drill \
    MONEY_TRACKER_RESTORE_EXPECTED_DATABASE=money_tracker_drill \
    MONEY_TRACKER_RESTORE_RECEIPT_ROOT=/absolute/empty/drill/receipts \
      uv run --env-file .env money-tracker-backup verify-restore

It reads the named database in a single read-only repeatable-read transaction
and refuses to continue unless the URL names exactly the expected database, the
connected database agrees, and the Alembic revision equals the one the running
application supports. It then checks, failing closed on the first violation:

- profile and default-funding-source consistency in both directions;
- posted and voided expense reconciliation against category and payment
  allocations, including allocation polarity and profile scope;
- wallet-entry and monthly-budget scope, including ledger balance mode;
- every idempotency record resolving to an aggregate in the same profile;
- Telegram offsets ahead of every retained inbox update, outbox/pending-action
  bot-key agreement, owner bindings on active profiles, and pending-action
  targets that still exist in the same profile;
- every non-deleted receipt reference resolving to a regular mode-600 file
  beneath an owner-only archive root whose content hash still matches.

Output and journal records carry only the database name, the schema revision,
stable reason codes, and counts. A failure names a reason code such as
`expense_reconciliation_invalid` or `receipt_checksum_mismatch` and never a
path, an amount, or a category name.

## Planned post-V1 clean-machine restore sequence

The personal V1 accepts an isolated-target drill rather than claiming an empty
installation can be rebuilt. Post-V1 recovery work must qualify that stronger
claim. The required semantic sequence around the implemented command is:

1. choose an empty isolated target and record the selected snapshot;
2. install compatible pinned application/database versions without starting
   Telegram ingress or workers;
3. run the implemented restore drill against that target;
4. apply only the documented compatible migration path;
5. run `money-tracker-backup verify-restore` against that target and compare
   its counts with the expected fixture;
6. start API in isolated/read-only verification mode where available;
7. start workers/gateway only after idempotency, pending actions, offsets, and
   owner pairing have been inspected;
8. record the drill outcome without private data in the repository.

Do not restore into a live database or overwrite a receipt directory merely to
test a snapshot.

## Restore acceptance checks

- Database migration/version matches the application. The manifest records the
  Alembic revision, the command rejects a foreign application version, and
  `verify-restore` pins the restored revision to the running application's.
- Every posted or voided expense satisfies category and payment reconciliation.
  Verified by `verify-restore`.
- Budgets, funding sources, and wallet entries stay inside their profile.
  Verified by `verify-restore`; count comparison against the expected fixture
  stays with the operator, who reads the printed counts.
- Every retained receipt reference resolves to the expected hash; no unexpected
  media path is web-accessible. The command verifies this in staging, again at
  the destination, and once more semantically with file mode and archive
  privacy.
- Idempotency records resolve to a surviving aggregate, and Telegram offsets,
  queue scope, owner bindings, and pending-action targets stay correlated.
  Verified by `verify-restore`.
- Pending workflows either resume under a compatible graph or are explicitly
  drained/migrated before service start. Still manual: workflow checkpoints are
  versioned per graph and carry no cross-record invariant to assert.
- Owner identity and default funding source are present without exposing tokens.
  Verified by `verify-restore`.
- Core operation starts and passes health checks with Langfuse absent. Still
  manual: it requires a running installation, not a restored data set.

Business reconciliation of restored aggregates is therefore executable. What
remains manual is the clean-machine drill around it: using the accepted
off-workstation secret, service startup, workflow-checkpoint disposition, and
recorded duration/RPO/RTO evidence.

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
