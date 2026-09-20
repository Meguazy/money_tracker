# AD-024: Age-based retention of concluded runtime content

- Status: accepted
- Implementation: implemented in the fourth Step 7 slice; the daily timer was
  installed, enabled, and timer-trigger accepted on the target on 2026-09-20
- Date: 2026-09-19

## Context

Three runtime stores in application PostgreSQL keep owner content for as long
as their rows exist, and until this decision nothing ever removed a row:

- `messaging.telegram_inbox` holds the normalized text of every owner message
  and the workflow result of every processed update;
- `messaging.telegram_outbox` holds every rendered reply, which repeats
  amounts, categories, merchants, and descriptions;
- the LangGraph tables in `workflow` hold every graph thread's state, including
  inline message text, for the router and every specialist.

None of it is authoritative. Finance records, audit history, and the command
idempotency records that make replay safe live in `finance`
([AD-005](AD-005-application-postgresql.md),
[AD-018](AD-018-telegram-processing-transactions.md)). Runtime traces, which
hold the same content, are already kept 30 days
([AD-021](AD-021-full-fidelity-llm-traces.md)). Every copy also enters the
encrypted backup through the database dump
([AD-013](AD-013-local-runtime-and-backup.md)).

Some runtime rows must outlive any window. A paused category review may wait
indefinitely by product decision, and it is resumed from its checkpoint. A
delivered summary's controls stay usable for as long as their pending action is
active, both as buttons and through reply correlation. Dead letters and
undelivered responses are operator work. Telegram redelivers nothing older than
24 hours, and the gateway never requests an update below its durable offset.

## Decision

On 2026-09-19 the owner decided that concluded runtime content is deleted, not
redacted, 30 days after it concluded, and that a persistent daily timer
enforces the policy once the owner enables it.

`money-tracker-retention` implements the policy through
`money_tracker.persistence.retention` in one PostgreSQL transaction, in
dependency order, with a cutoff of now minus the window:

1. consumed or cancelled pending actions that concluded before the cutoff are
   deleted;
2. outbox messages sent before the cutoff that no remaining pending action
   refers to are deleted;
3. an inbox item completed before the cutoff is deleted once every response it
   produced is either deleted or still carries an active control. Such a
   retained response is detached from the item by clearing its `inbox_id`: it
   is kept for its Telegram message identifier and its correlation, never for
   the text that prompted it;
4. a checkpoint thread whose newest checkpoint precedes the cutoff is deleted
   whole, from all three LangGraph tables, when it can no longer be resumed.

Terminality of a thread is read from its identifier, which
`money_tracker.workflows.threads` now defines for both the dispatcher that
writes it and retention that reads it. A source-keyed family (`router`,
`expense`, `read`, `movement-change`) is terminal once no unfinished inbox
item carries its source reference; a thread with no inbox item came from a
local command and has nothing that could resume it. A `category-review` thread
is terminal once its proposal is no longer pending. A thread of any other
family, or without a namespace, is kept, so a new graph fails safe until it is
added to the contract.

Everything else is always kept: active pending actions and their messages,
paused reviews, inbox and outbox dead letters, undelivered or retrying
responses and the items they answer, and inbox items that are not completed.
Retention never touches finance, audit, idempotency records, offsets, owner
bindings, pairing codes, or receipts.

The command refuses a database whose Alembic revision is not the one the
running application supports, and gives up after five seconds on a row another
process holds rather than delay the worker. `plan` and an unconfirmed `delete`
execute the same statements in the same transaction and roll it back, so a plan
is exactly what a confirmed run would delete at that instant. A confirmed run
that deleted anything then runs `VACUUM (ANALYZE)` on the affected tables, so
the space is reclaimed promptly rather than when autovacuum next runs. Output
and journal records carry counts, the cutoff, and flags only, including how
many rows older than the cutoff the policy kept.

`money-tracker-runtime-retention.timer` runs `delete --confirm` with the
default window daily at 04:45 local time, with up to thirty minutes of jitter
and catch-up after shutdown. The installer renders it and never enables it.

Target acceptance on 2026-09-20 reviewed a zero-item plan, enabled the timer,
and used a temporary runtime-only two-minute schedule to trigger the unchanged
service. Its confirmed run selected no eligible rows or threads, emitted only
counts, and exited zero. The override was removed immediately; the enabled,
active timer again uses the checked-in persistent 04:45 calendar and jitter.

## Alternatives considered

- **Redact content and keep the rows.** Keeps identifiers, states, and
  timestamps for diagnosis, but needs a migration that relaxes the constraints
  requiring text, and leaves every row forever. The owner chose deletion.
- **A 7-day or 90-day window.** Seven days leaves little diagnostic history;
  ninety keeps three times as much owner content in the database and every
  backup. Thirty matches the trace store, so one rule covers all
  non-authoritative owner content.
- **An operator-only command.** No new unit, but content grows until someone
  remembers to run it.
- **Treat an active control older than the window as concluded.** Would bound
  every row, but silently revokes a control and a pending review the product
  keeps open indefinitely. Expiring controls is a product decision, not a
  retention side effect.
- **LangGraph `delete_thread`.** The library's own delete runs on the saver's
  autocommit connection, so it could not commit or roll back with the
  messaging deletions, and selecting threads by age still reads the library's
  tables directly. The adapter issues the same three deletes inside its own
  transaction instead.
- **The shallow checkpointer.** Keeps only the latest checkpoint per thread,
  which bounds volume but not age, and changes the resume semantics every
  workflow was evaluated with.
- **A counting query for the plan.** Cheaper to reason about for locking, but a
  second copy of every predicate could drift from the statements that delete.

## Consequences

- Concluded owner content leaves the live database within a day of turning 30
  days old once the timer is enabled. What remains past the window is reported
  as `retained_past_cutoff` and always has a reason: an active control, a
  paused review, a dead letter, an undelivered response, or an unrecognised
  thread family.
- `money-tracker-telegram latency --since` can only measure work inside the
  window. Performance evidence must be collected within 30 days of the sample,
  and the recorded Step 4 evidence is unaffected because it lives in the
  documentation.
- Deletion is logical. Encrypted Restic snapshots keep deleted rows until they
  age out of the seven-daily, five-weekly, and twelve-monthly policy, so a
  deleted message may persist in the backup for about a year. PostgreSQL free
  space and WAL may keep bytes until they are overwritten. Only full-disk
  encryption would protect the medium, as for the database itself, and the
  workstation had none when this decision was taken.
- Selection depends on the LangGraph checkpoint schema: the three table names
  and the `ts` field of each checkpoint. A `langgraph-checkpoint-postgres`
  upgrade must re-run the retention integration test, which writes real
  checkpoints through the production saver.
- A new graph family must be added to `ThreadNamespace`, or its threads are
  kept forever. Retention can therefore never delete a thread it does not
  understand, at the cost of keeping one.
- Duplicate protection is unaffected. Replay safety rests on finance
  idempotency records, which are never deleted, and on the durable offset,
  which is never moved backwards.
