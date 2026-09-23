# AD-005: Dedicated Application PostgreSQL

- Status: accepted
- Implementation: foundation, finance schema, expense/wallet/budget repository
  operations, replay-safe correction/undo/category-review/budget/receipt-review/funding-source
  command claims, pending-proposal persistence, receipt review, and durable
  messaging persistence implemented; remaining adapters planned
- Date: 2026-09-01

## Context

The target system needs transactional financial writes, durable Telegram
inbox/outbox records, idempotency, audit history, and persistent LangGraph
checkpoints. The PostgreSQL container currently in Compose is owned by Langfuse
and must not also own application data.

## Decision

Run a dedicated PostgreSQL service for Money Tracker. Keep logical ownership
separate for finance data, messaging records, and workflow checkpoints. Use
schema migrations and repository interfaces; the database is the source of
truth for financial state, while LangGraph checkpoints only own workflow state.

Use the database as the durable queue for the single-user workload. Do not add
Redis, Celery, RabbitMQ, or another broker unless measured load or lifecycle
requirements justify a new ADR.

## Alternatives considered

- SQLite is operationally smaller but creates an early migration and weaker
  concurrency boundary for the API, poller, worker, and checkpointer.
- Reusing Langfuse PostgreSQL couples application availability and migrations
  to an observability component with different data ownership.
- A separate message broker adds operational cost without current throughput
  need.

## Implementation evidence

Roadmap Step 1 provides the independent default-profile Compose service,
persistent volume, loopback port, initialization administrator, and
least-privilege application role. Roadmap Step 2 adds authoritative finance
tables, deferred database invariants, immutable audit and wallet history,
taxonomy/profile bootstrap, and transactional expense, wallet, and budget
repository operations, taxonomy-scoped pending proposal submission, and
content-idempotent receipt metadata registration, plus versioned mandatory
review and owner-approval transitions. Expense/budget optimistic concurrency,
idempotency, and source-row locking for balance-safe wallet debits are enforced.
Correction and undo now use the general command-claim table as well: the claim,
complete aggregate replacement, allocation replacement, and audit event commit
in one transaction, while an identical replay returns the stored result.
Category-review decisions use the same table with one proposal-wide key: the
claim and the selected terminal transition commit together, an identical replay
returns the first movement or successor proposal, and a competing branch
conflicts without a second finance write.
Budget creation and replacement claim that table too, keyed by a canonical
digest of scope, category, recurring target, and overrides, so a retried
browser or administrative request writes one budget and one audit row.
Receipt review transitions are the fourth family. Their key names the receipt
and the version the caller observed, because a receipt is reviewed in
successive steps and each step is its own command; the claim, the state change,
and the audit commit together, so a retry replays and a different decision on
one observed version conflicts. The table is general rather than
expense-shaped, which is why none of these families needed a migration.
Funding-source create, rename, default movement, and archive plus manual wallet
append form another command family. Source lifecycle changes serialize on the
profile and affected source rows; moving the default updates both source
versions and the profile pointer, while archive rechecks zero booked and
available balance. Each command claim, state change, and audit set commits
atomically. Wallet append locks the source, applies AD-020 availability, and
rolls back a rejected overdraft with its claim. A real concurrent repository
test also proves that identical source-create retries with different generated
candidates converge on the one stored identity. No migration was needed because
source versions, wallet history, audit, and the general claim table already
exist. The settings read model adds only `get_profile` to the repository contract, reads the existing fixed profile columns and active source rows, and reuses that same atomic default command for its sole mutable choice; it introduces no schema or second write path.
The merchant and period-comparison range read reuses the existing profile-scoped posted interval query twice, once per non-overlapping bounded interval. Aggregation remains in the application service, so it adds no table, migration, persistence ownership, or write path.
Roadmap Step 4 migration `20260909_0005` adds environment-scoped offsets,
minimum normalized inbox rows, bounded claim/retry/dead-letter state, unique
logical outbox messages, hashed one-use pairing, owner bindings, and scoped
pending actions. Migration `20260910_0006` adds durable manual outbox replay
accounting without replacing existing messages. `PostgresMessagingRepository`
implements the AD-018 transaction boundaries and strict head-of-line claims.
Real PostgreSQL tests cover duplicate ingestion, competing claims, ordered
retries, exhaustion, scoped inbox replay and outbox resend, forward migration,
durable action recovery, environment separation, and pairing.
Existing real PostgreSQL tests continue to cover finance migration, constraints,
rollback, restart persistence, identical retry, stale versions, and concurrent
expense and wallet writers, including concurrent receipt review transitions, a
replayed review command that writes one audit row and one claim, and a rolled
back audit that leaves no claim behind.
Step 7 migration `20260923_0008` repairs a legacy missing profile default-source
pointer only when one active source is already flagged as default. It refuses
other mismatches and has no reverse migration; a real PostgreSQL regression
proves both outcomes. This is a metadata reconciliation of the original
source-selection intent, not a new owner command or financial movement.
Receipt extraction/deletion and remaining transport adapters are still open.

## Consequences

- PostgreSQL becomes a required core service and must be backed up and restored.
- Database writes require idempotency keys and explicit transactions.
- Integration tests must exercise a real PostgreSQL server and protocol.
