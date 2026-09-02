# AD-005: Dedicated Application PostgreSQL

- Status: accepted
- Implementation: foundation, finance schema, expense/wallet/budget repository
  operations, and pending-proposal persistence implemented; remaining finance
  adapters and messaging persistence planned
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
repository operations plus taxonomy-scoped pending proposal submission. Expense idempotency, expense/budget optimistic
concurrency, and source-row locking for balance-safe wallet debits are enforced.
Real PostgreSQL tests cover migration, constraints, rollback, restart
persistence, identical retry, stale versions, and concurrent expense and wallet
writers. Proposal resolution, receipt persistence, messaging persistence, and
backup are still open.

## Consequences

- PostgreSQL becomes a required core service and must be backed up and restored.
- Database writes require idempotency keys and explicit transactions.
- Integration tests must exercise a real PostgreSQL server and protocol.
