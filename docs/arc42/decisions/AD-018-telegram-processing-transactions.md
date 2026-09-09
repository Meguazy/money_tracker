# AD-018: Telegram Processing Transactions and Ordering

- Status: accepted
- Implementation: messaging contracts/repository, Bot API transport, authorized
  ingress, and independent gateway/outbox loops implemented; worker, rendering,
  pairing administration, operations, and real-bot acceptance pending
- Date: 2026-09-09
- Supersedes: the raw-update retention detail in
  [AD-010](AD-010-telegram-durable-messaging.md); all other AD-010 choices remain
  accepted
- Extends: [AD-005](AD-005-application-postgresql.md) and
  [AD-016](AD-016-explicit-workflow-dispatcher.md)

## Context

AD-010 selects long polling, a PostgreSQL inbox/outbox, and one sequential
worker. It does not define the exact commit boundaries, who owns the Bot API
offset, what ordering means while the head item is waiting for retry, or how a
finance mutation remains recoverable when the process dies before recording its
outbound response.

The existing Step 3 dispatcher is deliberately transport-neutral. Expense
creation, correction, and undo already derive stable command identities from a
channel source reference. Category-review settlement is transactional inside
the finance schema, but a replay currently reports that an already settled
review cannot be settled again instead of necessarily returning its prior
typed result. A Step 4 worker cannot claim crash-safe response recovery until
every mutating specialist has an idempotent or reconstructible terminal result.

Telegram does not offer an idempotency key for `sendMessage`. A process can
therefore lose the response after Telegram accepted a send but before the local
delivery commit. The local system can guarantee one logical outbox message and
zero duplicate finance writes, but it cannot truthfully promise exactly-once
physical delivery at that external boundary.

## Decision

Use these distinct components and narrow interfaces:

- a Bot API transport performs bounded `getMe`, webhook, `getUpdates`, file
  metadata, callback acknowledgement, and send operations and returns typed,
  redacted results;
- an ingestion service authorizes and normalizes supported updates, handles
  one-use pairing before authorization, and supplies one atomic ingest batch to
  the messaging repository;
- the messaging repository owns inbox rows, the next Bot API offset, leases,
  retry/dead-letter state, logical outbox rows, delivery state, pairing state,
  and pending-action correlations;
- one workflow worker maps the oldest eligible inbox record to the existing
  `ChannelMessage`, constructs repository-derived `DispatchContext`, and calls
  only `WorkflowDispatcher.dispatch`;
- a response renderer maps typed outcomes and repository-owned finance display
  data to concise Italian text and typed controls without model or transport
  access;
- an outbox sender delivers repository claims through the Bot API transport;
- administration and health services expose bounded status, retry, replay,
  pairing, readiness, and dead-letter operations without message content or
  Telegram identifiers.

The Telegram gateway process owns both the polling loop and the independent
outbox-delivery loop. They share no in-memory correctness state. The workflow
worker is a separate process and has no Bot API token or Telegram client.

The gateway is the sole owner of the persisted next offset for one configured
bot environment. For each returned batch it performs one database transaction:

1. lock the bot offset row;
2. validate monotonically ordered update identities;
3. authorize and normalize supported updates, or record only a safe rejection
   counter for an unauthorized/unsupported update;
4. insert accepted inbox rows under `(bot_environment, update_id)` uniqueness;
5. set `next_offset` to one greater than the greatest observed update ID;
6. commit.

The next `getUpdates` request is the acknowledgement. No code may advance the
offset outside that transaction. A duplicate returned after an uncertain commit
is absorbed by the inbox uniqueness constraint. Pairing is a special authorized
administrative transition: only a private `/pair` message bearing a valid,
unexpired, one-use code may create the sole owner binding. The code is stored as
a hash; its clear value exists only at generation and owner entry.

Inbox order is ascending Telegram `update_id` within a bot environment. The
single worker may claim only the first non-terminal row. If that row has an
unexpired lease or a future retry time, later rows wait. A completed or
dead-letter row releases the next row. A workflow waiting for an owner is
complete for that inbound item after its pending action and question are
durable, so it does not block later updates.

Claiming increments the attempt count and gives the worker a bounded lease. A
transient failure schedules deterministic bounded backoff. The fifth failed
attempt becomes a visible dead letter. A permanent validation or authorization
failure becomes a dead letter immediately. Manual replay clears only retry
state on the selected safe inbox item, preserves its source reference and
finance command identities, and never rewinds the Telegram offset.

Workflow dispatch and finance mutation cannot share a transaction with the
current independently connected application services. Correctness therefore
uses a recoverable two-phase application protocol:

1. dispatch with source reference
   `telegram:<bot-environment>:<update-id>` and repository-derived trusted
   correlations;
2. require every mutating specialist to return the same terminal result when
   that source reference is replayed, including category-review settlement;
3. in one messaging transaction, persist the typed terminal result, insert the
   uniquely keyed logical outbox response and any pending-action mapping, then
   mark the inbox item complete.

A crash before step 3 re-dispatches the same command. Finance idempotency and
workflow checkpoints return or reconstruct the prior mutation; the outbox
unique key `inbox:<inbox-id>:response:<response-kind>` then creates one logical
response. A finance mutation is never compensated merely because presentation
or delivery failed.

Outbox sending uses a separate lease and five bounded attempts. A successful
Bot API result stores the Telegram message identity and activates any reply
correlation in one transaction. An exhausted delivery becomes a visible outbox
dead letter. An ambiguous network failure may lead to a repeated physical
Telegram message because the external API has no exactly-once contract; the
single logical row, attempt history, and zero duplicate domain effects remain
provable and visible.

Pending actions are profile-, bot-, private-chat-, and owner-scoped. Callback
data carries a bounded action plus an opaque local reference, never a raw
finance identifier. A callback or reply must resolve an active mapping in that
same scope before it can enter `DispatchContext`. Consumption and terminal
outbox creation commit together. Stale, foreign, or already consumed mappings
cannot target another movement or review.

The Step 4 runtime does not require FastAPI. Operator commands and machine-safe
process exit codes provide health/readiness and recovery until the Step 5 local
control plane exists.

## Alternatives considered

- **Hold one PostgreSQL transaction open across model inference.** Rejected
  because a slow or unavailable model would retain locks and couple database
  availability to inference latency.
- **Mark inbox completion before dispatch.** Rejected because a crash would
  silently lose accepted work.
- **Process later eligible rows while the head waits for retry.** Rejected
  because it weakens the explicit sequential ordering contract and can reorder
  correction or undo behind its target.
- **Put Telegram normalization and response rendering in the dispatcher.**
  Rejected because it would violate AD-016 and make transport concepts enter
  workflow/domain code.
- **Add Redis, Celery, or a message broker.** Rejected by AD-005 for the
  single-owner workload.
- **Claim exactly-once Bot API delivery.** Rejected because `sendMessage` has no
  caller idempotency key and an accepted response can be lost in transit.

## Implementation evidence

Migration `20260909_0005` creates the offset, inbox, outbox, pairing, binding,
and pending-action tables with database checks and stable uniqueness. The
transport-neutral `messaging` contracts reject unsafe identifiers, naive time,
unsupported or oversized file metadata, empty work, and overlong callback data.
`PostgresMessagingRepository` owns one transaction per ingest batch or terminal
worker transition, serializes per-bot claims through the offset row, recovers
expired leases, blocks later work behind an ineligible head row, applies the
five-attempt terminal boundary, and preserves source identity during replay.
Unit tests cover the contracts and deterministic retry schedule. Nine
`@pytest.mark.postgres` tests exercise the production adapter over the real
PostgreSQL protocol, including a concurrent two-connection claim.

The production `BotApiClient` uses explicit allowed update types, bounded
connect/read/write timeouts, backlog-preserving webhook removal, plain-text
sending, and safe reason codes that omit API bodies and token-bearing URLs.
`TelegramUpdateIngestor` drops unauthorized and non-private content before
storage. `TelegramGateway` commits a complete observed batch before callback
acknowledgement or another poll, while a second loop leases and delivers the
outbox independently. Thirteen gateway unit tests and one additional real
PostgreSQL authorization test pass. The real test-bot harness is present but
still awaits external credentials and owner interaction.

## Consequences

- Messaging migrations need explicit constraints for environment/update,
  source reference, logical outbox key, pairing, and pending-action scope.
- Category-review replay must be strengthened before the worker can pass its
  crash-window acceptance tests.
- Slow inference never blocks gateway ingestion, but strict head-of-line order
  intentionally delays later work until retry or dead-letter transition.
- Operations can inspect counts, ages, safe identifiers, error codes, and
  dependency state, but not raw Telegram payloads or private message content.
- A physical duplicate Telegram acknowledgement remains a documented external
  transport risk; a duplicate logical response or financial mutation is a
  correctness defect.
