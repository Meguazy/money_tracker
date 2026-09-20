# Data Retention and Secure Deletion

- Status: runtime content retention implemented in Step 7 (2026-09-19); its
  daily timer was installed, enabled, and timer-trigger accepted on the target
  on 2026-09-20
- Decision: [AD-024](../arc42/decisions/AD-024-runtime-content-retention.md)

This runbook says how long each kind of Money Tracker data is kept, what
deletes it, and which copies survive a deletion. Deleting a row or a file is
not the same as erasing its bytes, so every data class below also names its
residual copies.

## Retention inventory

| Data | Owner content | Where | Kept | Deleted by |
| --- | --- | --- | --- | --- |
| Finance records, audit history, idempotency records | Yes | Application PostgreSQL, `finance` | Indefinitely; authoritative | Nothing in V1. Records are voided or archived, never deleted |
| Receipt originals | Yes | Private receipt archive | Indefinitely in V1 | Nothing until Step 6 delivers media deletion |
| Telegram inbox, outbox, and pending actions | Yes | Application PostgreSQL, `messaging` | 30 days after they conclude | `money-tracker-retention`, below |
| Workflow checkpoints | Yes | Application PostgreSQL, `workflow` | 30 days after the thread can no longer resume | `money-tracker-retention`, below |
| Telegram offsets, owner binding, pairing-code hashes | No | Application PostgreSQL, `messaging` | Lifetime of the installation | Nothing; they hold identifiers and hashes only |
| Runtime LLM traces | Yes | Langfuse volumes | 30 days | `money-tracker-traces`; see [LLM Tracing](tracing.md#retention-export-deletion-and-backup) |
| Published evaluation evidence | No; synthetic fixtures and aggregates | Langfuse `evaluation` environment, `out/evaluations/` | Indefinitely | Reconstructible from Git; not deleted by the trace timer |
| Private evaluation data | Possibly | `evals/private/`, `artifacts/evals/` (ignored by Git) | Operator's choice | The operator; see [Experiments](../experiments/README.md) |
| Encrypted backups | Yes | External Restic repository | Seven daily, five weekly, twelve monthly snapshots | `money-tracker-backup create`, after each verified snapshot |
| Finance and trace exports | Yes | Owner-chosen private directory | Operator's choice | The operator |
| Restore-drill targets | Yes | Isolated database and receipt directory | Until the drill is recorded | The operator |
| Application journal | No, by [AD-019](../arc42/decisions/AD-019-structured-application-logging.md) | systemd journal | journald's size and age limits | journald |
| Model digest cache | No | `~/.cache/money-tracker` | Until a file revision changes | Recomputed; safe to remove |
| Telegram chat history | Yes | Telegram servers and the owner's clients | Telegram's policy | The owner, in the Telegram client |

## Runtime content retention

`money-tracker-retention` deletes, in one PostgreSQL transaction:

- consumed or cancelled pending actions that concluded before the cutoff;
- outbox messages sent before the cutoff that no remaining pending action
  refers to;
- completed inbox items once nothing they produced still needs them;
- checkpoint threads whose newest checkpoint precedes the cutoff and which can
  no longer resume: a `router`, `expense`, `read`, or `movement-change` thread
  whose inbox item is completed or absent, or a `category-review` thread whose
  proposal is no longer pending.

The cutoff is 30 days before the run unless `--older-than-days N` names
another whole number of days; one day is the minimum, which keeps Telegram's
24-hour backlog intact.

It always keeps what can still be acted on or investigated:

- an active pending action and the delivered message carrying it, so the
  buttons and reply correlation keep working. When the item that message
  answered is otherwise concluded, the item is deleted and the message is
  detached from it;
- a paused category review and its checkpoint thread;
- inbox and outbox dead letters, responses not yet delivered or retrying, and
  the items they answer;
- any checkpoint thread of a family the command does not recognise.

It never touches finance records, audit history, idempotency records, offsets,
owner bindings, pairing codes, or receipts, so replay safety is unchanged.

### Planning a run

See what a run would delete, changing nothing:

    uv run --env-file .env money-tracker-retention plan

The command executes the same deletion as a confirmed run and rolls it back,
so the plan is exact at that instant. It prints one JSON object:

| Field | Meaning |
| --- | --- |
| `cutoff`, `retention_days` | The instant before which content was selected, and the window it came from |
| `deleted` | Pending actions, outbox messages, inbox items, checkpoint threads, and the checkpoint, blob, and write rows of those threads |
| `detached_outbox_messages` | Delivered messages kept for an active control whose link to a deleted inbox item was cleared |
| `retained_past_cutoff` | Inbox items, outbox messages, pending actions, and checkpoint threads older than the cutoff that the policy kept |
| `checkpoint_store_present` | Whether `money-tracker-db bootstrap` has created the checkpoint tables |
| `dry_run`, `vacuumed` | Whether the transaction was rolled back, and whether the tables were vacuumed afterwards |

It never prints message text, workflow state, bot keys, or the database URL.

A non-zero `retained_past_cutoff` is not an error. Each row has a reason:
resolve inbox dead letters from `/messaggistica` in the dashboard
([Local Web](web.md)) or with `money-tracker-telegram replay`, answer paused
category reviews, and expect delivered summaries with active controls to stay
until their control is used.

### Deleting

`delete` without `--confirm` is the same dry run:

    uv run --env-file .env money-tracker-retention delete
    uv run --env-file .env money-tracker-retention delete --confirm

A confirmed run that deleted anything then runs `VACUUM (ANALYZE)` on the
affected tables. If that fails, the journal records
`runtime_retention.vacuum.failed` at `WARNING` and the result reports
`"vacuumed": false`; the deletion has already committed and autovacuum
reclaims the space later.

Exit code 1 means configuration, the schema revision, or the database refused
the run; 2 means the database was unreachable or another process held a row
for more than five seconds, and the next run retries. Nothing is deleted when
the command fails. Reason codes include `database_configuration_invalid`,
`database_schema_incompatible`, `database_unavailable`, `database_busy`,
`checkpoint_data_invalid`, and `database_operation_failed`.

### Daily schedule

`money-tracker-runtime-retention.timer` runs `delete --confirm` with the
default window every day at 04:45 local time, with up to thirty minutes of
jitter and catch-up after shutdown. The installer renders it and never enables
it. After reviewing a plan, enable and inspect it explicitly:

    ./scripts/install_systemd_units.sh
    uv run --env-file .env money-tracker-retention plan
    systemctl --user enable --now money-tracker-runtime-retention.timer
    systemctl --user list-timers money-tracker-runtime-retention.timer
    systemctl --user status money-tracker-runtime-retention.service
    journalctl --user -u money-tracker-runtime-retention.service -o cat \
      | jq 'select(.event=="runtime_retention.run.completed")'

The completion record carries the same counts as the command's output. A
failed run leaves the oneshot failed and visible; the next daily occurrence
retries the policy. Disable future runs without deleting anything:

    systemctl --user disable --now money-tracker-runtime-retention.timer

Target acceptance on 2026-09-20 first reviewed a zero-item plan. A temporary
runtime-only two-minute schedule then triggered the unchanged production
service; the confirmed run selected zero eligible rows and threads, emitted
only counts, and exited zero. The override was deleted immediately. The timer
remains enabled and active on its checked-in persistent 04:45 schedule with up
to thirty minutes of jitter.

### Consequences for other procedures

- `money-tracker-telegram latency --since` measures only work inside the
  window. Take a latency sample and record it within 30 days.
- Dashboard messaging views and `money-tracker-telegram health` show queue
  state, not history; retention never removes an item they would report as
  pending, retrying, or dead-lettered.
- A checkpoint thread is identified by the contract in
  `money_tracker.workflows.threads`. A new graph family must be added there,
  or its threads are kept forever.

## Secure deletion and residual copies

Deletion in this system is logical. The controls below bound where the bytes
of deleted owner content can still exist.

- **PostgreSQL.** A deleted row leaves its bytes in free space inside table
  pages and in WAL segments until PostgreSQL overwrites them. The retention
  vacuum makes the space reusable promptly; it does not erase it. Do not run
  `VACUUM FULL` routinely to force a rewrite: it locks the tables against the
  gateway and worker, and the old file's blocks are released to the
  filesystem, not overwritten.
- **Backups.** Every Restic snapshot taken before a deletion still contains the
  row in its database dump until that snapshot leaves the seven-daily,
  five-weekly, and twelve-monthly policy, so a deleted message may persist in
  the backup for about a year. A snapshot cannot be edited row by row; the
  only earlier removal is forgetting whole snapshots, which gives up recovery
  points and which the
  [backup guardrails](backup-and-restore.md#destructive-operation-guardrails)
  treat as destructive. V1 does not automate it.
- **Files.** On a journaling filesystem or an SSD, neither `rm` nor `shred`
  guarantees that the old blocks are overwritten. Keep finance exports, trace
  exports, and restore-drill copies on an encrypted volume, delete every copy
  when it is no longer needed, and rely on encryption rather than
  overwriting. The workstation disk is not such a volume today; see the next
  item.
- **Physical medium.** Only full-disk encryption protects deleted database
  pages, WAL, swap, and files at rest, and the same applies to the live
  database, the Langfuse volumes, and the receipt archive. This workstation
  has none: on 2026-09-19 `lsblk -e7 -o NAME,TYPE,FSTYPE,MOUNTPOINT` showed the
  root ext4 filesystem, which holds the home directory and the Docker volumes,
  and the swap partition directly on their partitions with no `crypt` device.
  Anyone who reads the disk can recover deleted and live owner content. No
  command can add it; encrypting the disk is an owner decision recorded in the
  [risks](../arc42/README.md#11-risks-and-technical-debt). Re-run the same
  `lsblk` after any change: a protected filesystem sits beneath a `crypt`
  device.
- **Restore drills.** After recording a drill, drop the isolated database and
  remove its receipt directory. The drill refuses a populated target, so a
  leftover target also blocks the next drill.
- **Journal.** Records contain no owner content by construction, so privacy
  requires no journal retention. journald caps its size by default; to trim
  old records explicitly, run `sudo journalctl --vacuum-time=90d`, which acts
  on every journal on the host.
- **Telegram.** Messages the owner sent and the bot's replies remain in the
  Telegram chat on Telegram's servers and every client. Application retention
  cannot reach them; delete them from the Telegram client if they must go.
- **Receipt originals.** V1 has no product deletion. An original stays in the
  archive and every backup until Step 6 delivers media deletion; deleting a
  file by hand corrupts the archive.

## Verification

    RUN_POSTGRES_INTEGRATION_TESTS=1 uv run --env-file .env pytest -q \
      tests/integration/money_tracker/persistence/test_retention.py

The suite builds messaging state through the production repository and real
checkpoints through the production LangGraph saver. It proves that a plan
changes nothing and matches the confirmed run; that concluded exchanges, dead
letters' delivered notices, and terminal threads of every family go; that an
active control survives, detached, and still resolves through reply
correlation and control revocation; that dead letters, undelivered responses,
recent work, paused reviews, and unrecognised threads stay; that a second run
deletes nothing; that the production CLI prints counts without owner content
or the database URL; and that a mismatched schema revision is refused without
deleting anything. It needs the isolated `money_tracker_test` database only.
