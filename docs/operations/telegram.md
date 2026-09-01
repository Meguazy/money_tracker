# Telegram Operations

- Status: BotFather preparation is externally available; Money Tracker
  integration, pairing, polling, and commands are planned for Roadmap Step 4
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

## Before Roadmap Step 4

The application cannot yet consume a bot token. Creating the production bot can
wait until Step 4. If the owner reserves a username earlier, keep the token only
in a password manager or another mode-restricted location outside the repository;
do not paste it into issues, prompts, documentation, or the current `.env`
template until Telegram configuration is implemented.

Use a separate bot for real integration/E2E tests so experiments cannot consume
or reply to production updates.

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

Do not send a finance message until the local adapter and owner pairing are
implemented. Sending `/start` early is harmless from Telegram's perspective but
Money Tracker cannot process it.

## Planned Money Tracker configuration

Roadmap Step 4 must add, document, and test all of the following before this
section becomes executable:

- private environment variables for production token and distinct test token;
- a validation operation that calls `getMe` without printing the token;
- removal/check of any existing webhook before enabling long polling;
- explicit allowed update types needed by messages and callback queries;
- a locally generated, expiring, one-use pairing code;
- persisted owner `user_id` and private `chat_id` outside model-controlled data;
- redaction of token-bearing URLs and Telegram identifiers;
- Italian help/status and review commands plus BotFather command registration.

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

## Planned long-polling durability

Telegram supports webhooks and `getUpdates` long polling as mutually exclusive
delivery modes. V1 uses long polling because the workstation has no public HTTPS
endpoint.

The required receive algorithm is:

1. Request the earliest unconfirmed updates, in batches up to the current Bot
   API limit.
2. Insert every raw update into PostgreSQL under a unique `update_id`.
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

The standard hosted Bot API currently documents a 20 MB download limit. Step 4
must check the live API response and enforce conservative configurable limits
instead of assuming that all Telegram media are acceptable.

For each file:

- persist the update/file identifier before download;
- never log the token-bearing download URL;
- verify advertised and decoded type, size, and dimensions;
- hash and store the content outside the web root;
- pass only a private media reference into workflow state and traces.

## Retry and operator recovery

Planned transient failures retry five times with backoff. Exhaustion creates a
dead-letter record visible in the dashboard. Replay preserves the original
idempotency key. A waiting category/receipt decision is not a processing failure
and does not block later unrelated messages.

Outbound replies use a transactional outbox. Telegram does not provide a general
exactly-once `sendMessage` contract, so a rare duplicate acknowledgement may be
tolerated and audited; a duplicate financial mutation is never tolerated.

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
