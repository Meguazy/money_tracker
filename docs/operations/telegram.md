# Telegram Operations

- Status: implemented and accepted by the owner on 2026-09-11 against the real
  test bot, with all three services under the user manager. Every text
  workflow, owner authorization, the latency requirement, a workstation reboot,
  and five fault injections passed; the evidence is recorded in the
  [Step 4 document](../roadmap/step-4-durable-telegram-mvp.md)
- Bot created: test bot only
- Production token configured: no
- Test bot configured: yes. The exposed diagnostic credential was revoked in
  BotFather and replaced on 2026-09-10; `validate` passed against the
  replacement, and the running gateway's journal contains no token-shaped run
  and no Bot API URL
- Owner paired: yes, one private owner bound through the one-use code

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

The code path exists end to end and the owner has driven every text workflow
through it against the real test bot. Treat pairing as the start of acceptance
testing on a new bot rather than as routine setup, and use the test bot for it.

Both processes classify why they stopped, so a supervisor can tell a problem
that a restart fixes from one that it hides:

| Exit code | Meaning | Correct response |
| --- | --- | --- |
| 0 | The operation finished | Nothing |
| 1 | An operator must change something | Stop and fix; the units do not restart |
| 2 | A dependency was unreachable | Restart when it returns; the units do this |

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

Store or replace a credential through hidden standard input; never put the
token in the command itself:

    scripts/store_telegram_token.sh test

Use `production` only for the separate production bot. The script parses
`.env` as data instead of sourcing it, requires distinct absolute paths,
refuses unsafe existing targets, writes through an owner-only temporary file,
and atomically installs mode 0600 content. It prints neither token nor path.

The production entry point supports:

    uv run --env-file .env money-tracker-telegram validate
    uv run --env-file .env money-tracker-telegram poll-once
    uv run --env-file .env money-tracker-telegram run

`validate` calls `getMe` and emits only environment/status. Gateway startup
also removes any webhook with `drop_pending_updates=false` before long polling.
Only message and callback-query updates are requested. The opt-in real
credential test has passed `getMe` and backlog-preserving webhook removal
without sending owner content:

    RUN_TELEGRAM_INTEGRATION_TESTS=1 uv run --env-file .env pytest -q \
      tests/integration/money_tracker/telegram/test_real_bot_api.py

An unpaired bot deliberately acknowledges every observation as rejected and
retains no content, so run `pair` before `poll-once` or `run`. Italian owner
responses, inline controls, `/help`, and `/status` are implemented and accepted.
`/start` and `/cancel` are not application commands. BotFather menu registration
is optional operator presentation and must match only the implemented commands.

## Pairing protocol

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
everything except a valid private `/pair` attempt, which is consumed without
entering the inbox. This is what keeps a bot username that anyone can find from
becoming a way into your finances.

1. Make sure the profile exists (`money-tracker-admin` creates it) and the
   database is migrated.
2. Generate a code. This also creates the bot's persistence scope, so it works
   on a bot the gateway has never run for. The code is printed once and stored
   only as a SHA-256, so if you lose it, generate another rather than looking
   for it:

       uv run --env-file .env money-tracker-telegram pair

3. Start the gateway so it can observe the reply. After installing the user
   units, use `systemctl --user start money-tracker-telegram.service`; for a
   foreground setup, use the `run` command below.
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

## Correcting a movement

Press `Modifica` on the movement summary. The bot asks which facts should
change; reply directly to that new question with the corrected amount, date,
description, or funding source. The question owns a durable mapping to the
movement selected by the button, so a gateway or worker restart between the
question and the reply does not retarget the correction to a newer movement.
The original summary controls remain valid while the clarification is open.

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

With `MONEY_TRACKER_TRACING_ENABLED=true` both processes trace into the local
Langfuse: each accepted update, each claim with its model calls, and each
delivery that answers it land in one trace named `telegram-update`, derived from
the update's source reference. Traces hold the owner's words and responses, but
never a Telegram user, chat, file, or callback identity, the raw update, or a
pairing code. The switch is on for the installed services since the
2026-09-18 acceptance, where a real test-bot message and its callback each
formed one such trace and the bot answered normally with the store stopped.
See [LLM Tracing](tracing.md).

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

## Inspecting and recovering the queue

Every operator command prints JSON and none of them print message content:

    uv run --env-file .env money-tracker-telegram health
    uv run --env-file .env money-tracker-telegram dead-letters
    uv run --env-file .env money-tracker-telegram replay --inbox-id <uuid>
    uv run --env-file .env money-tracker-telegram resend --outbox-id <uuid>
    uv run --env-file .env money-tracker-telegram latency \
      --since 2026-09-10T08:00:00+00:00

`health` reports queue depths, the oldest waiting item, the persisted offset,
whether an owner is paired, and whether the last poll is old enough that
Telegram may have discarded updates. `dead-letters` lists both failed work
and failed logical responses with identity, attempts, replay count, and reason
code. Use `--inbox-id` to replay work and `--outbox-id` to resend a
response. The environment selected by `.env` scopes both actions; a UUID from
another bot environment is rejected without changing that row.

The Step 5 dashboard shows the same facts and offers the same two recovery
commands at `http://127.0.0.1:8000/messaggistica`, described in
[the local web runbook](web.md). Prefer it for routine checks: it is scoped by
the same configured environment and reaches the same bounded operations. These
commands remain the unattended path, work when the API service is stopped, and
are the only place pairing, latency and `validate` live. One difference is
deliberate: the browser decides from a listing, so it states the requeue count
it displayed and a repeated click reports the requeue as already applied
instead of applying a second one. The command form names an item rather than a
view, so it accepts whichever dead letter that identifier resolves to; run
`dead-letters` immediately before `replay` or `resend` if someone may be
recovering the same queue from the dashboard.
If required relations are missing, `health` exits with code 1 and prints only
`Telegram gateway failed: database_schema_invalid`; apply the checked-in
migrations with `uv run --env-file .env money-tracker-db bootstrap`, then rerun
`health`. This includes a database missing the outbox recovery migration, not
only an empty schema. An unreachable database instead reports
`database_unavailable` with exit code 2 so systemd retries it.

Step 4 readiness remains a set of transport and queue checks distinct from the
Step 5 API liveness/readiness endpoints:

| Condition | Command or signal | Ready interpretation |
| --- | --- | --- |
| Database and schema | `uv run --env-file .env money-tracker-telegram health` | Exit zero; a schema error exits 1 and an unreachable database exits 2. |
| Telegram transport and credential | `uv run --env-file .env money-tracker-telegram validate` | Exit zero after a real Bot API `getMe`; run after rotation, never by exposing the token. |
| Gateway ingestion | `systemctl --user is-active money-tracker-telegram.service` plus advancing `last_poll_at` in `health` | The process is active and successful polls continue. |
| Sequential worker | `systemctl --user is-active money-tracker-worker.service` plus inbox counts/age in `health` | The process is active and an eligible backlog drains in order. |
| Durable outbox | `outbox` in `health` | Pending/retrying counts and oldest age do not grow unexpectedly. |
| Dead letters | `dead_letters` in `health`, then `dead-letters` for safe identities/reasons | Both counts are zero in normal operation; a nonzero count is a visible failure, not healthy work. |
| Local model | `curl --fail --silent --show-error http://127.0.0.1:8080/health` | The llama.cpp health endpoint returns success; its failure does not make Telegram ingestion unready. |

Process liveness alone is never readiness. In particular, an active gateway
with a stale `last_poll_at`, or an active worker with a growing eligible inbox,
needs investigation. The model and worker may be unavailable while the
database and gateway remain ready to commit incoming updates.

Replay preserves the source reference, so the finance command keys the work
unit already derived stay the same. An item that had already committed a
mutation before failing therefore returns its first result instead of applying
a second one. That is what makes replay safe to offer at all, and it is why
replay never rewinds the Telegram offset.

Safe for the ledger is not the same as invisible to the owner. Replay
re-dispatches the work unit, so it also delivers whatever answer that unit
produces now, and the owner receives it with no sign that it belongs to an
older conversation. A stale item can therefore arrive as a reply to a question
they no longer remember asking, or as a refusal describing state that has since
moved on: replaying a settled category review answers that the review is no
longer open. Tell the owner before recovering an old dead letter, or expect to
explain the message afterwards.

`resend` preserves the existing logical outbox row and starts a fresh bounded
five-attempt delivery cycle. Use it only after checking the transport failure:
Telegram has no caller idempotency key for `sendMessage`, so an earlier
ambiguous success can make the owner see the physical response twice. Resending
cannot repeat the finance write.

## Real test-bot acceptance and latency

Do this only with the test bot selected, after rotating the exposed diagnostic
credential, applying migrations, pairing the private owner, and starting the
three host services. Record a UTC start instant immediately before the sample:

    date --utc +%Y-%m-%dT%H:%M:%S%:z

From the paired private account, exercise text expense and `Registra`,
category review across a worker restart, `Modifica` followed by a direct reply
to its question, `Annulla`, query, advice, `/help`, and `/status`. Confirm
that a foreign private account and a group receive no financial response. For
that authorization check, stop the worker briefly, compare `health` before and
after the foreign observations, and require the inbox counts to remain
unchanged; then restart the worker. Repeat an already accepted update only
through the documented replay path and verify that finance and logical outbox
counts do not increase unexpectedly.

Use this lightweight monitor while the owner performs the actions:

    watch -n 2 'uv run --env-file .env money-tracker-telegram health'

After the sample, use the recorded start instant:

    uv run --env-file .env money-tracker-telegram latency \
      --since START_INSTANT

The report measures each successful non-media text input from durable
`accepted_at` to `sent_at`, recorded only after Telegram accepts
`sendMessage`. It excludes failure notices and the first successful workflow
as warm-up, then computes nearest-rank p95. Record the workstation, commit,
Qwen profile/GGUF, prompts, policy, sample count, warm sample count, p95,
maximum, and any retries. Runtime retention deletes concluded inbox and
outbox rows after 30 days, so compute and record the report within that window
([Data Retention](data-retention.md)). The requirement remains p95 at most
30,000 ms. Bot API acceptance is the repeatable owner-visible proxy; actual
client rendering and notification delay are not observable and must be listed
as limitations.

For the workstation restart check, send two distinguishable test messages in a
recorded chat order, stop the worker before it claims them, confirm two pending
items with `health`, reboot once, and confirm the services become active and
the responses arrive in that same order.
Do not repeatedly poll the machine while the owner is performing the reboot.

## Step 4 rollback

Stop ingress and processing first:

    systemctl --user stop money-tracker-telegram.service
    systemctl --user stop money-tracker-worker.service

Preserve the database and token files. Do not downgrade messaging migrations:
their downgrade paths intentionally refuse loss of inbox, outbox, replay, and
pairing history. If the current Step 4 runtime must be withdrawn, keep those
services disabled and deploy the prior reviewed application commit only when it
is schema-compatible. Otherwise restore a verified pre-upgrade backup into an
isolated database, validate it, and then switch the runtime forward. Re-enabling
the gateway resumes from the persisted offset; it must never be reset as a
rollback shortcut. A suspected token compromise follows the rotation procedure
below regardless of application rollback.

## Running as host services

Install the checked-in unit templates for this checkout:

    scripts/install_systemd_units.sh

This renders `deploy/systemd/*.service` with the repository path and direct
entry points from the locked `.venv`, writes them to
`~/.config/systemd/user/`, and reloads the user manager.
It installs without starting, and prints the remaining steps: pair the owner,
enable lingering so the services survive logout, then enable each unit. The
installer refuses a missing `.env` or locked production entry point before
writing any unit.

Create the pairing code, start only the gateway, and send the private pairing
message before starting the worker. Then enable and start all three services
after migration, credential validation, and pairing:

    systemctl --user enable --now money-tracker-llama.service
    systemctl --user enable --now money-tracker-telegram.service
    systemctl --user enable --now money-tracker-worker.service

Normal lifecycle and inspection commands are:

    systemctl --user stop money-tracker-worker.service
    systemctl --user start money-tracker-worker.service
    systemctl --user restart money-tracker-telegram.service
    systemctl --user is-active money-tracker-llama.service
    systemctl --user is-active money-tracker-telegram.service
    systemctl --user is-active money-tracker-worker.service
    systemctl --user status money-tracker-worker.service
    journalctl --user -u money-tracker-worker.service --since today

Repeat the service name as needed for the gateway or llama.cpp. Logs must
contain only the bounded diagnostics described below; never attach an
unreviewed journal to a ticket.

The units are user services because the application owns no system-wide
resource. They set an owner-only umask, drop all capabilities, restrict
namespaces and address families, declare home and the system read-only, and use
the checkout-specific operations file in `Documentation=`. On this workstation
the read-only and private-temporary directives are not in effect: AppArmor's
restriction of unprivileged user namespaces keeps the user manager from giving
a service its own mount namespace, which was observed on 2026-09-17. Do not rely
on them until Step 7 qualifies the sandbox. The worker additionally declares a
private `CacheDirectory` for model digests and puts `~/.local/bin` on `PATH`. Telegram and worker
also hide host devices; llama.cpp retains them for GPU acceleration. They are
not ordered against Docker: a user unit cannot depend on the
system Docker service, so a database that starts late is handled by the exit
codes above instead. The worker is deliberately not ordered after the gateway
either, since their failure domains are separate on purpose.

## Offline limitation

The official Bot API states that incoming updates are not kept longer than 24
hours. This is a hard accepted V1 limitation:

- downtime under 24 hours: ingest every update still available and process in
  order after startup;
- downtime over 24 hours: the gateway warns the owner when it resumes, naming
  when it last listened and asking them to resend; the warning is keyed by that
  instant, so restarting inside one outage does not repeat it;
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
attempts. A recoverable failure queues one stable Italian notice that says an
automatic retry is pending; a terminal failure queues a distinct notice that
asks the owner not to resend while an operator recovers it. Neither notice
contains the reason code or owner content. The state change and notice commit
in one PostgreSQL transaction. Use the implemented `health`, `dead-letters`,
`replay`, and explicit `resend` commands above for recovery.

Outbound replies use the transactional outbox and its independent gateway loop.
Telegram does not provide a general exactly-once `sendMessage` contract, so a
rare duplicate physical message may occur after an ambiguous response; a second
logical outbox row or financial mutation is never acceptable.

## Credentials in logs

The Bot API puts the token in the request path, so any library that logs a URL
logs the credential. Every entry point therefore configures logging through
`configure_logging`, which raises `httpx`, `httpx2`, `httpcore`, and `urllib3` to WARNING
so the request line is never emitted, and installs a filter that redacts
token-shaped runs from every record whatever produced it. The filter reaches
messages, structured fields, and rendered tracebacks, and uncaught exceptions
are routed through logging rather than the interpreter's own printer so that a
crash cannot bypass it.

This was found the hard way: a plain `basicConfig(level=INFO)` enabled INFO for
every logger in the process, and the first real run printed the full Bot API
URL to the terminal. If you see a token in a scrollback buffer, a journal, or a
pasted log from before that fix, treat it as compromised and rotate it.

The transport names the Bot API method it called and a stable reason code, and
never the URL. See
[Reading the journal](security-and-troubleshooting.md#reading-the-journal).

## Token compromise or rotation

Rotation stays a manual BotFather procedure, and no helper will be added.
Revoking a bot token is a BotFather chat operation with no Bot API method
behind it, so a local helper could only ever automate the file swap and would
give the misleading impression that the risky half was handled too. What the
application does provide is verification: `money-tracker-telegram validate`
confirms the new token before you start the gateway with it.

1. Stop the Telegram gateway to avoid ambiguous credentials:
   `systemctl --user stop money-tracker-telegram.service`.
2. Revoke/regenerate the token in BotFather.
3. Store the new token with
   `scripts/store_telegram_token.sh test` (or `production` for the production
   bot); do not print or commit it.
4. Verify the new credential without starting the loops:
   `uv run --env-file .env money-tracker-telegram validate`.
5. Restart the gateway and check `health` for the owner binding and backlog.
6. Search redacted logs/artifacts for accidental exposure and rotate again if
   any copy may remain.

If the token ever appears in Git, chat with an AI, a trace, or a public support
artifact, treat it as compromised immediately; deleting the visible message is
not a substitute for rotation.
