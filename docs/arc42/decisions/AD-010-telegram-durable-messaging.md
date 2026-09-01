# AD-010: Telegram Long Polling and Durable Messaging

- Status: accepted
- Implementation: planned
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
