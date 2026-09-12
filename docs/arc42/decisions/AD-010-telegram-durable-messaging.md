# AD-010: Telegram Long Polling and Durable Messaging

- Status: accepted; raw-update retention detail superseded by AD-018
- Implementation: PostgreSQL messaging state/repository, Bot API adapter,
  authorized ingress, one-use owner pairing, concurrent gateway loops, outbox
  delivery, the sequential workflow worker, and messaging health/recovery
  reporting implemented and owner-accepted; the same recovery operations are
  also reachable from the Step 5 local control plane
- Date: 2026-09-01

## Context

The workstation has no public server endpoint and is not always powered on.
Telegram is the primary channel and must recover eligible queued messages when
the PC returns online without duplicating financial writes.

## Decision

Use Bot API long polling, not webhooks. Persist every raw update in the
application PostgreSQL inbox and commit it before advancing the Telegram
offset. Deduplicate by `update_id`. A single sequential workflow worker consumes
the inbox; a separate Telegram gateway continues ingesting while inference is
slow or unavailable.

Persist responses in a transactional outbox. Retry transient failures five
times with backoff, then move the message to a visible dead-letter state that
can be replayed. Financial commands carry idempotency keys. Pair one private-chat
owner through a short-lived local code and reject all other users and groups.

[AD-018](AD-018-telegram-processing-transactions.md) replaces raw-payload
retention with minimum normalized fields and refines the transaction boundaries,
strict head-of-line ordering, pending-action scope, replay result, and external
delivery guarantee. Long polling and the other choices above remain accepted.

Migrations `20260909_0005` and `20260910_0006`, with
`PostgresMessagingRepository`, implement the
durable half of this decision: environment-specific offsets, normalized inbox,
claims, retries, dead letters, replay, unique logical outbox, delivery state,
manual delivery replay accounting, hashed one-use pairing, sole-owner bindings
per bot, and scoped pending actions. An explicit outbox resend preserves the
logical row and starts a fresh bounded attempt cycle; because a prior
`sendMessage` result may be ambiguous, the operator must accept the risk of a
duplicate physical Telegram message. Step 5 makes both recovery commands
reachable from `/messaggistica` without widening them: the browser sees the same
content-free projections the CLI prints, states the transition it decided from,
and gains no way to send a message, change the pairing, move the offset, or
delete a row.
`BotApiClient`, `TelegramUpdateIngestor`, `TelegramGateway`, and the
`money-tracker-telegram` production entry point now implement the HTTPS and
process side. Ingress and outbox delivery have independent loops; commit precedes
callback acknowledgement and a later poll. Unit tests cover protocol parsing,
redaction, authorization, and send/commit order, while real PostgreSQL proves
that rejected identities create no inbox rows. The checked-in real Bot API test
has passed `getMe` and backlog-preserving webhook removal without sending
owner content. The exposed diagnostic credential was rotated. Step 4 owner
acceptance then
passed the real workflows, authorization, five fault scenarios, p95 latency,
and unattended reboot recovery. The canonical measurements and limitations are
in the [Step 4 evidence](../../roadmap/step-4-durable-telegram-mvp.md#acceptance-evidence).

## Alternatives considered

- Webhooks require an always-reachable HTTPS endpoint.
- A cloud relay extends retention but violates the selected infrastructure and
  privacy boundary.
- In-memory queues lose work and cannot establish idempotent restart behavior.

## Consequences

- Telegram's maximum 24-hour unconsumed-update retention is an accepted limit.
- A downtime warning can report risk but cannot reconstruct expired updates.
- Duplicate outbound acknowledgements may occasionally be tolerable; duplicate
  financial writes are not.
