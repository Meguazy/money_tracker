# Telegram Operations

- Status: Bot API adapter, secure configuration, durable polling, private-owner
  filtering, outbox delivery, Italian response rendering, the sequential
  workflow worker, and one-use owner pairing are implemented; messaging health,
  systemd, and real test-bot acceptance remain pending
- Bot created: no
- Production token configured: no
- Test bot configured: no

Telegram is the sole approved external runtime data boundary. Bot messages and
receipt images transit and may be retained by Telegram; bot chats are not
end-to-end encrypted. Local-first refers to processing and application storage,
not to Telegram transport.

Authoritative references:

- [Telegram Bot Features and BotFather](https://core.telegram.org/bots/features)
- [Telegram Bot API](https://core.telegram.org/bots/api)
- [Telegram bot tutorial](https://core.telegram.org/bots/tutorial)

## Current implementation boundary

`money-tracker-telegram` can validate, pair, poll, durably ingest, acknowledge
callbacks, and deliver outbox rows. `money-tracker-worker` processes that inbox
in order and commits owner-facing responses. Run both: the gateway owns the
Telegram connection, the worker owns finance processing, and neither can stand
in for the other.

The path is complete end to end, but nothing in it has been run against a real
Telegram bot yet. Treat the first pairing as the start of acceptance testing,
not as a routine setup step, and use the test bot for it.

Use a separate bot for integration/E2E tests so experiments cannot consume or
reply to production updates.

## Creating a bot with BotFather

These are Telegram operations, not Money Tracker commands:

1. In Telegram, open the verified `@BotFather` account.
2. Send `/newbot`.
3. Choose a human-readable display name.
4. Choose a unique username, 5–32 supported characters, ending in `bot`.
5. BotFather returns an authentication token. Treat it as a password: anyone
   holding it can control the bot.
6. In BotFather settings, disable adding the bot to groups because V1 supports
   one private owner chat only.
7. Optionally set description/about text and a profile image that reveal no
   personal finance information.

Sending `/start` is harmless from Telegram's perspective, but Money Tracker
answers nothing until the bot is paired: an unbound bot rejects every update
without retaining content.

## Implemented gateway configuration

The environment explicitly selects `production` or `test` and supplies two
different absolute credential-file paths:

- `MONEY_TRACKER_TELEGRAM_ENVIRONMENT`
- `MONEY_TRACKER_TELEGRAM_PRODUCTION_TOKEN_FILE`
- `MONEY_TRACKER_TELEGRAM_TEST_TOKEN_FILE`

The gateway reads only the selected regular, owner-owned, mode-0600 file without
following a final symlink. It has no token environment variable and no fallback
between test and production. `scripts/generate_local_env.sh` writes non-secret
default paths and selects test; `scripts/upgrade_local_env.sh` appends only
missing keys.

The production entry point supports:

    uv run --env-file .env money-tracker-telegram validate
    uv run --env-file .env money-tracker-telegram poll-once
    uv run --env-file .env money-tracker-telegram run

`validate` calls `getMe` and emits only environment/status. Gateway startup
also removes any webhook with `drop_pending_updates=false` before long polling.
Only message and callback-query updates are requested. The real credential test
is opt-in and currently unexecuted:

    RUN_TELEGRAM_INTEGRATION_TESTS=1 uv run --env-file .env pytest -q \
      tests/integration/money_tracker/telegram/test_real_bot_api.py

An unpaired bot deliberately acknowledges every observation as rejected and
retains no content, so run `pair` before `poll-once` or `run`. BotFather
command registration remains planned. Italian owner responses and their inline
controls are implemented and produced by the worker.

Candidate UI commands such as `/start`, `/help`, `/status`, and `/cancel` are
product design targets, not current executable entry points. Their final list
must match production behavior and be registered through BotFather or the Bot
API in Step 4.

## Planned owner pairing

1. The local setup operation verifies the configured bot and creates a
   short-lived pairing code.
2. The owner sends `/pair <code>` in the bot's private chat.
3. The gateway atomically binds the Telegram user and chat IDs to the only V1
   profile, invalidates the code, and acknowledges pairing.
4. All other users and group chats are rejected without reading finance data or
   creating workflow work.
5. Re-pairing or owner replacement is a local administrative operation with
   audit; it cannot be initiated solely by an untrusted chat message.

Never use the Telegram chat ID as a LangGraph workflow/thread ID. Each workflow
has its own stable identifier; pending actions map callbacks/replies to that
exact workflow.

## Pairing the owner

The bot has exactly one owner. Until that binding exists the gateway discards
everything it sees, which is what keeps a bot username that anyone can find
from becoming a way into your finances.

1. Make sure the profile exists (`money-tracker-admin` creates it) and the
   database is migrated.
2. Generate a code. It is printed once and stored only as a SHA-256, so if you
   lose it, generate another rather than looking for it:

       uv run --env-file .env money-tracker-telegram pair

3. Start the gateway so it can observe the reply.
4. In the private chat with the bot, send the `send_in_private_chat` line
   exactly as printed, for example `/pair xY3...`.
5. The bot answers that the account is now the owner. That reply comes from the
   outbox, so it also confirms that delivery works.

The code is one-use and short-lived, and generating a new one revokes the
previous. A wrong or expired code binds nothing and is not retained; neither is
a correct one. A bot that already has an owner refuses every other sender, so
recovering from a mistaken binding means deleting the binding row deliberately,
not sending another code.

Never paste a pairing code into a shared channel, an issue, or a log. It is a
short-lived credential for your own finance data.

## Running the two processes

The gateway and the worker are separate processes on purpose. The gateway keeps
accepting Telegram updates while inference is slow or stopped; the worker keeps
processing the durable backlog without holding a Bot API credential.

    uv run --env-file .env money-tracker-telegram run
    uv run --env-file .env money-tracker-worker run

`money-tracker-worker process-once` handles a single eligible work unit and
prints whether one was claimed, which is the safe way to inspect behavior
before running the loop. The worker refuses to start without an owner binding,
because every accepted update belongs to one.

## Implemented long-polling durability

Telegram supports webhooks and `getUpdates` long polling as mutually exclusive
delivery modes. V1 uses long polling because the workstation has no public HTTPS
endpoint.

The required receive algorithm is:

1. Request the earliest unconfirmed updates, in batches up to the current Bot
   API limit.
2. Normalize only supported authorized fields and insert accepted updates under
   a unique bot/update identity; retain counters only for rejected observations.
3. Commit the batch.
4. Only then request the next offset, confirming prior updates to Telegram.
5. Let the separate single-concurrency worker process durable inbox rows.

A crash before commit leaves Telegram responsible for redelivery. A crash after
commit but before confirmation causes a duplicate delivery that the unique
constraint absorbs. Domain commands add their own idempotency key because inbox
deduplication alone is not sufficient proof against replay/resume.

## Offline limitation

The official Bot API states that incoming updates are not kept longer than 24
hours. This is a hard accepted V1 limitation:

- downtime under 24 hours: ingest every update still available and process in
  order after startup;
- downtime over 24 hours: send a possible-loss warning when service resumes;
- never claim to know which expired messages are missing;
- the owner must forward or resend older chat messages manually.

The local durable queue protects messages only after the gateway has received
and committed them. It cannot queue a message locally while the PC is off.

## Receipt files

Step 4 accepts only JPEG, PNG, and WebP metadata up to 20 MiB and persists the
opaque file identities only after private-owner authorization. `getFile`
returns an opaque safe relative path but the gateway does not download media,
construct a token-bearing download URL, or invoke receipt extraction. Download,
decoded-image validation, archive storage, and workflow wiring remain Step 6
work.

## Retry and operator recovery

Inbox and outbox claims persist bounded attempt and retry metadata; the fifth
failed attempt becomes a dead letter. The gateway applies bounded network
backoff and the Bot API `retry_after` hint. The worker classifies its own
failures: an unreachable local model or a losing concurrent mutation is
retried, while a missing record, a rejected domain rule, or a conflicting
logical response becomes a dead letter immediately rather than consuming five
attempts. Operator replay and health commands remain planned, so a dead letter
is currently visible only through the database.

Outbound replies use the transactional outbox and its independent gateway loop.
Telegram does not provide a general exactly-once `sendMessage` contract, so a
rare duplicate physical message may occur after an ambiguous response; a second
logical outbox row or financial mutation is never acceptable.

## Token compromise or rotation

Until Step 4 implements a tested helper, use BotFather's official token rotation
flow and then update only the private local configuration.

1. Stop the future Telegram gateway to avoid ambiguous credentials.
2. Revoke/regenerate the token in BotFather.
3. Store the new token privately; do not print or commit it.
4. Restart and verify `getMe`, webhook state, owner mapping, and backlog behavior.
5. Search redacted logs/artifacts for accidental exposure and rotate again if
   any copy may remain.

If the token ever appears in Git, chat with an AI, a trace, or a public support
artifact, treat it as compromised immediately; deleting the visible message is
not a substitute for rotation.
