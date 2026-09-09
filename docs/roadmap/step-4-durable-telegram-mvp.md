# Step 4: Durable Telegram MVP

Part of the [delivery roadmap](../roadmap.md).

Status: **In progress — design reconciled with repository truth.** Step 3 is
complete and EVD-004 remains the fixed text-workflow baseline. Messaging
persistence, Telegram processes, presentation, systemd units, and real test-bot
evidence do not exist yet and are not claimed by this status.

## Repository-truth inventory

- `WorkflowDispatcher.dispatch` is the single implemented transport-neutral
  entry point. It covers every Step 3 text specialist and uses independently
  namespaced, PostgreSQL-backed LangGraph checkpoints.
- Application services and `PostgresFinanceRepository` own finance writes,
  audit, optimistic concurrency, command idempotency, category review, and
  reporting. Telegram code must not mutate their tables directly.
- The `messaging` schema exists but contains no tables. No inbox, outbox,
  offset, owner binding, pairing, pending action, retry, or dead-letter
  repository exists.
- `httpx` is already a runtime dependency, but there is no Bot API adapter,
  token configuration, gateway, worker, response renderer, or Telegram test
  fixture.
- Administration has profile/funding bootstrap, receipt storage, one-shot Step
  3 dispatch, and backup commands. It has no messaging inspection, pairing,
  replay, health, or service entry point.
- Compose provides application PostgreSQL and optional Langfuse. No systemd
  unit is checked in. FastAPI is neither installed nor required by the Step 4
  runtime.
- Unit, PostgreSQL integration, inference integration, and CLI E2E structures
  exist. Telegram and messaging-recovery markers, fixtures, and suites do not.
- A concrete replay gap remains: category-review settlement rejects an already
  settled proposal but does not yet guarantee reconstruction of the original
  typed terminal result after a crash before outbox creation.

## Reconciled design

[AD-018](../arc42/decisions/AD-018-telegram-processing-transactions.md)
defines the Step 4 transaction and ordering contract. The component interfaces
are deliberately narrow:

| Component | Interface | Owns |
| --- | --- | --- |
| Bot API transport | Typed validate/webhook/poll/file/send/callback operations | HTTP lifecycle and redacted Bot API errors only |
| Update ingestion | Normalize, authorize, pair, and commit one observed batch | Acceptance policy, not queues or finance data |
| Messaging repository | Ingest batch, claim/complete/fail/replay, claim/send/fail outbox, resolve actions, report health | Offset, inbox/outbox, retries, dead letters, binding, pairing, pending actions |
| Sequential worker | Process one claimed work unit through `WorkflowDispatcher.dispatch` | Lease lifecycle and typed outcome handoff only |
| Response renderer | Render typed outcomes plus repository-read display data | Italian text and bounded controls, no persistence or transport |
| Gateway runtime | Run independent polling and outbox loops | Process lifecycle; no in-memory correctness state |
| Administration/health | Generate pairing, inspect/retry/replay/status/readiness | Operator policy and safe output, no private message rendering |

The gateway atomically inserts authorized normalized rows and advances its
environment-specific next offset. The worker claims only the first non-terminal
update, so a future retry time creates intentional head-of-line waiting until
success or dead letter. Dispatch uses
`telegram:<environment>:<update-id>` as its stable source reference. Typed
result, unique logical outbox response, pending-action mapping, and inbox
completion then commit together. Finance writes remain in their existing
application-service transactions and must return the same result under replay.

The gateway process also delivers outbox rows through an independent loop. A
Bot API timeout can make physical delivery ambiguous, so Step 4 guarantees one
logical response and zero duplicate finance writes, not an impossible
exactly-once claim about Telegram. Health output distinguishes database,
transport, worker, outbox, model, downtime risk, and dead letters without
printing private content or identifiers.

## Deliverables

- Official Bot API adapter using long polling, private-owner pairing, allowed
  update filtering, secure file metadata handling, and redacted diagnostics.
- PostgreSQL inbox/outbox, persisted offset discipline, sequential worker,
  retry/backoff, dead letter, replay, and pending-action mapping.
- Italian message summaries and inline/reply interactions for text expenses,
  category review, modify, undo, help, and status.
- Separate production and test-bot configuration and real Telegram integration
  fixtures with owned-data cleanup.
- Host systemd units for Telegram gateway, workflow worker, API when present,
  and llama.cpp; boot recovery and dependency retry behavior.
- Final executable BotFather, pairing, credential-rotation, and downtime guide.

## Exit criteria

- A real private test bot completes text expense, category-review, correction,
  undo, query, and failure-recovery flows through production entry points.
- Fault injection around receive/commit/ack/process/send proves zero duplicate
  finance writes and visible dead letters.
- A real workstation/service restart recovers the eligible backlog in order.
- Unauthorized users/groups receive no finance data and cannot create work.
- The 24-hour limitation and manual-resend recovery are visible in setup and
  operations documentation.

## Milestone

This is the first Telegram-usable alpha. It does not yet include the complete
dashboard or receipt extraction.
