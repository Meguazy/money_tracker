# Architecture Decision Index

This index is the source of truth for consequential Money Tracker architecture
decisions. An accepted decision constrains future implementation even when its
implementation state is still `planned`.

| ID | Decision | Status | Implementation |
| --- | --- | --- | --- |
| [AD-001](../README.md#ad-001-out-of-process-inference) | Out-of-process inference | Accepted | Implemented |
| [AD-002](../README.md#ad-002-self-hosted-langfuse) | Self-hosted Langfuse | Accepted | Implemented, healthy and digest-pinned |
| [AD-003](../README.md#ad-003-headless-local-initialization) | Headless local initialization | Accepted | Implemented |
| [AD-004](AD-004-local-first-telegram-boundary.md) | Local-first privacy with Telegram boundary | Accepted | Implemented and owner-accepted against the real private test bot in Step 4 |
| [AD-005](AD-005-application-postgresql.md) | Dedicated application PostgreSQL | Accepted | Foundation, finance schema, expense/wallet/budget, replay-safe correction/undo, pending-proposal, versioned receipt-review, and messaging persistence implemented; remaining adapters planned |
| [AD-006](AD-006-bounded-agent-workflows.md) | Bounded typed agent workflows | Accepted | Checkpointer, bounded workflows, dispatcher, and EVD-004 text promotion implemented |
| [AD-007](AD-007-expense-allocation-model.md) | Expense and dual-allocation model | Accepted | Core domain/schema/repository implemented; remaining adapters planned |
| [AD-008](AD-008-controlled-taxonomy.md) | Controlled two-level taxonomy | Accepted | Policy/seed/schema/bootstrap, pending-proposal submission, and the deterministic category-fit boundary and replay-safe owner decisions implemented |
| [AD-009](AD-009-local-web-application.md) | Local FastAPI and React web application | Accepted | Monthly overview, transaction/manual-lifecycle, and multi-allocation editing slices, HTTP security, and host deployment implemented; remaining Step 5 surface in progress |
| [AD-010](AD-010-telegram-durable-messaging.md) | Telegram long polling and durable messaging | Accepted | Implemented and owner-accepted with recovery, authorization, and latency evidence |
| [AD-011](AD-011-receipt-extraction.md) | Evaluation-gated receipt extraction | Accepted | Private original archive implemented; extraction/review planned |
| [AD-012](AD-012-evaluation-driven-promotion.md) | Evaluation-driven model and prompt promotion | Accepted | Evaluation foundation and EVD-004 Step 3 text promotion implemented |
| [AD-013](AD-013-local-runtime-and-backup.md) | Host runtime and encrypted backup | Accepted | Compose topology, encrypted backup/restore, accepted Step 4 units, and Step 5 API unit implemented; scheduling planned |
| [AD-014](AD-014-private-receipt-archive.md) | Private content-addressed receipt archive | Accepted | Local adapter, admin CLI, versioned review state, filesystem/PostgreSQL integration, and encrypted archive backup/restore implemented; API/worker wiring and deletion planned |

| [AD-015](AD-015-two-stage-category-fit.md) | Two-stage category fit | Rejected | Implemented, measured at 0.7712 against the single call's 0.8693, and rejected; kept behind an evaluation flag so the result stays reproducible |
| [AD-016](AD-016-explicit-workflow-dispatcher.md) | Explicit workflow dispatcher | Accepted | Implemented for all Step 3 text specialists and Step 4 channel presentation |
| [AD-017](AD-017-versioned-read-selection-policy.md) | Versioned deterministic read-selection policy | Accepted | V1 promoted by EVD-004; V0 retained as direct rollback |
| [AD-018](AD-018-telegram-processing-transactions.md) | Telegram processing transactions and ordering | Accepted | Implemented and owner-accepted through production entry points |

## Maintenance rules

- Do not silently contradict an accepted decision in code or documentation.
- Change a decision through a new superseding ADR; preserve the historical ADR.
- Update the implementation column when a roadmap step delivers the decision.
- Record model or receipt-pipeline selections in the experiment decision log
  before creating a superseding or implementation ADR.
