# AD-004: Local-First Privacy with a Telegram Boundary

- Status: accepted
- Implementation: Bot API transport, separate token-file configuration,
  private-owner authorization, one-use owner pairing, normalized durable
  ingestion, and outbox delivery implemented; real test-bot acceptance pending
- Date: 2026-09-01

## Context

The workstation must retain financial storage, inference, receipt extraction,
and observability. Telegram is nevertheless the primary conversational channel,
so messages and receipt images necessarily traverse Telegram infrastructure.
The previous statement that all prompts remain local is therefore inaccurate.

## Decision

Keep the application local-first and make Telegram the only approved external
runtime data boundary. LLM inference, OCR/VLM processing, application data,
receipt storage, traces, and evaluation artifacts remain on the workstation.
Document that Telegram bot chats are not end-to-end encrypted and that the Bot
API retains unconsumed updates for at most 24 hours.

Authorize exactly one paired Telegram owner. Never send financial content to a
cloud model or OCR provider. Do not place image bytes, bot tokens, or Telegram
file URLs in traces or logs.

## Implementation evidence

The `telegram` adapter reads exactly one explicitly selected production or test
credential from distinct owner-only regular files. The local
`store_telegram_token.sh` helper reads hidden standard input, parses
configuration without executing it, rejects shared or unsafe paths, and
atomically replaces only the selected mode-0600 file. The adapter validates the
credential with `getMe`,
disables webhooks without dropping pending updates, and exposes only bounded
reason codes on failure. Authorization compares private-chat sender and chat
identities with the persisted owner binding before normalized inbox creation.
Foreign users and groups advance the observed offset with counters only. Unit
coverage proves redaction and authorization; the opt-in real Bot API test has
passed against the real test bot without sending owner content. The
credential used during initial diagnostics must be rotated before owner
acceptance because it appeared in terminal output.

## Alternatives considered

- A fully local UI without Telegram would preserve a stricter boundary but
  would remove the required primary channel.
- A cloud relay would extend offline retention beyond 24 hours but would add an
  always-on external financial-data store.
- Optional cloud inference could improve quality but would violate the chosen
  privacy contract.

## Consequences

- Messages older than Telegram's backlog window cannot be recovered by the
  application and must be resent.
- Security and privacy documentation must distinguish local processing from
  Telegram transport and retention.
- Future external integrations require a separate ADR and explicit approval.
