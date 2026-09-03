# Step 4: Durable Telegram MVP

Part of the [delivery roadmap](../roadmap.md).

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
