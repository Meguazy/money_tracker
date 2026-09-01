# AD-004: Local-First Privacy with a Telegram Boundary

- Status: accepted
- Implementation: planned
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
