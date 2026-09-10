# Step 4: Durable Telegram MVP

Part of the [delivery roadmap](../roadmap.md).

Status: **In progress — every deliverable is implemented and none is
accepted.** Durable persistence, the gateway, response rendering, the
sequential worker, one-use owner pairing, operator health, replay, and resend
commands, and host service units are all in the repository. Step 3 and its EVD-004
baseline remain unchanged. Bot API validation has run against the real test
bot, but no owner-driven workflow has. Acceptance, restart/reboot proof, and
the end-to-end latency measurement remain missing.

## Repository-truth inventory

- `WorkflowDispatcher.dispatch` is the single implemented transport-neutral
  entry point. It covers every Step 3 text specialist and uses independently
  namespaced, PostgreSQL-backed LangGraph checkpoints.
- Application services and `PostgresFinanceRepository` own finance writes,
  audit, optimistic concurrency, command idempotency, category review, and
  reporting. Telegram code must not mutate their tables directly.
- Migration `20260909_0005` creates environment-specific offsets, the
  minimum normalized inbox, bounded leases/retries/dead letters, unique logical
  outbox state, hashed pairing codes, owner bindings, and scoped pending
  actions. Forward-only migration `20260910_0006` adds manual outbox replay
  accounting. `PostgresMessagingRepository` implements their AD-018
  transactions.
- `telegram.config`, `BotApiClient`, `TelegramUpdateIngestor`, and
  `TelegramGateway` now provide separate credential-file selection, official
  HTTPS methods, private-owner filtering, durable long polling, callback
  acknowledgement, and an independent
  outbox loop. The separate sequential worker and Italian response renderer
  consume only durable normalized work.
- `money-tracker-telegram` is the production gateway entry point with
  `validate`, `pair`, `poll-once`, and `run`. `money-tracker-worker` is the
  separate processing entry point with `process-once` and `run`. `health`,
  `dead-letters`, `replay`, and `resend` cover operator inspection and
  recovery for both work and delivery failures.
- `deploy/systemd/` holds user-service templates for the gateway, the worker,
  and llama.cpp, rendered for a checkout by `scripts/install_systemd_units.sh`.
  They use direct locked-virtualenv entry points, checkout-correct documentation
  links, dependency-aware restart codes, and hardened user-service sandboxes. No
  unit has been enabled against a real bot.
- Compose provides application PostgreSQL and optional Langfuse. The three
  systemd unit templates above are checked in; FastAPI is neither installed nor
  required by the Step 4 runtime.
- Thirteen gateway unit tests cover token-file security, Bot API parsing and
  redaction, authorization, commit-before-ack, and outbox send-before-complete.
  One additional real-PostgreSQL test proves that foreign users and groups
  create no inbox rows. The opt-in real test-bot integration test has passed
  `getMe` and backlog-preserving webhook removal; it sends no owner message, so
  it is transport evidence rather than product acceptance.
- Category-review decisions now claim one proposal-wide finance idempotency
  key in the settlement transaction. Identical approval, reuse, cancellation,
  and re-proposal commands return the first stored result; competing branches
  conflict without another finance write.
- `presentation.telegram` renders every dispatch status in Italian and owns the
  bounded callback vocabulary.
- `SequentialWorker` claims the oldest eligible row, resolves its correlation,
  dispatches once, and commits result, response, mapping, and completion in one
  messaging transaction. It holds no Bot API credential.
- `MessagingAdministration` and gateway ingestion implement one-use owner
  pairing. The clear code is shown once to the operator and never stored; the
  message carrying it is never retained.

## Reconciled design

[AD-018](../arc42/decisions/AD-018-telegram-processing-transactions.md)
defines the Step 4 transaction and ordering contract. The component interfaces
are deliberately narrow:

| Component | Interface | Owns |
| --- | --- | --- |
| Bot API transport | Typed validate/webhook/poll/file/send/callback operations | HTTP lifecycle and redacted Bot API errors only |
| Update ingestion | Normalize, authorize, pair, and commit one observed batch | Acceptance policy, not queues or finance data |
| Messaging repository | Ingest batch, claim/complete/fail/replay, claim/send/fail/resend outbox, resolve actions, report health | Offset, inbox/outbox, retries, dead letters, manual replay counts, binding, pairing, pending actions |
| Sequential worker | Process one claimed work unit through `WorkflowDispatcher.dispatch` | Lease lifecycle and typed outcome handoff only |
| Response renderer | Render typed outcomes plus repository-read display data | Italian text and bounded controls, no persistence or transport |
| Gateway runtime | Run independent polling and outbox loops | Process lifecycle; no in-memory correctness state |
| Administration/health | Generate pairing, report health, list dead letters, replay one inbox item, or resend one outbox item | Operator policy and safe output, no private message rendering |

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

## Persistence implementation evidence

- `messaging.contracts` and `messaging.retry` keep queue state transport-neutral,
  serializable, bounded, and free of Bot API credentials or raw updates.
- Migrations `20260909_0005` and `20260910_0006` own six messaging tables
  and manual outbox replay accounting; both refuse destructive downgrade.
  Stable uniqueness covers bot/update, source reference, logical response,
  pairing hash, and the per-bot owner binding.
- `PostgresMessagingRepository` commits ingest-plus-offset and
  result-plus-outbox-plus-action transitions atomically, serializes claims per
  bot, recovers expired leases, and exposes content-free health counts.
- Twenty-seven messaging unit tests and thirteen
  `@pytest.mark.postgres` repository tests passed. The
  PostgreSQL suite uses the real service and protocol; a two-connection race
  proved that only one worker receives the oldest item.

## Workflow replay prerequisite evidence

- Category-review settlement and re-proposal now commit their command claim,
  proposal transition, movement/category changes, and audits atomically.
- The settling handler turns a conflicting command key and a review that is no
  longer pending into the same already-settled question, so a late second
  button ends the thread instead of failing the work unit.
- Forty-seven focused unit tests passed across the review service and graph.
  Nine real-PostgreSQL application tests passed, including identical replay,
  competing-decision conflict, and stable re-proposal reconstruction.

## Response rendering evidence

- Every dispatch status is rendered: movement summaries carrying amount, date,
  purpose, and funding source with `Modifica`/`Annulla`; the paused review with
  only the branches AD-008 still allows; all four read tools; both channel
  commands; and one owner question for every stable clarification code.
- Response content depends only on the outcome and committed state. Live queue
  figures are excluded by design, because a replayed render must reproduce the
  logical response its outbox key already names. Operational status stays with
  the pending administration and health path.
- Twenty-four unit tests passed, including a contract test that drives the real
  ingestion callback grammar with the rendered buttons, and one that fails if a
  new reason code reaches the owner as generic prose.

## Owner posting evidence

- EVD-004 qualified no automatic-post subset, so every movement is created as a
  draft. Until now nothing in the product could record one: `post_expense` had
  no production caller, and the channel offered only `Modifica` and `Annulla`.
  Every Telegram movement was therefore permanently invisible to analytics,
  budgets, and every read tool, which made the Step 4 query flow answer zero by
  construction.
- A draft summary now leads with `Registra`. The control disappears once the
  movement is recorded, because there is nothing left to confirm.
- Posting is an explicit normalized callback action, not a model-visible intent.
  The deterministic pre-router sends `post_expense` through the existing
  movement-lifecycle specialist while retaining the callback action in small,
  serializable graph state. AD-012 therefore continues to reserve the judgement
  that money counts for the owner; the promoted router schema cannot request it.
- `post_expense` became replay-safe like correction and undo, claiming a
  namespaced command key so a redelivered `Registra` returns the first result.
  Real-PostgreSQL tests prove one post audit across two invocations, and that a
  stale control on an already recorded movement writes nothing.
- The active routing suite still names the promoted
  `routing-intent-it-v1.2` dataset. Unit coverage exercises the deterministic
  posting callback and pins every model-visible enum value, so Step 4 adds no
  unevaluated route to the EVD-004 boundary.

## Operations implementation evidence

- `health`, `dead-letters`, `replay`, and `resend` print JSON carrying
  counts, ages, safe identifiers, and reason codes, and no message content.
  Real-PostgreSQL tests prove both failure queues are listable without owner
  text, both manual actions stay inside the selected bot scope, inbox replay
  preserves its finance source identity, and outbox resend keeps the original
  logical row while starting a fresh bounded delivery cycle.
- A real subprocess test drives the installed `money-tracker-telegram`
  entry point against PostgreSQL, proves `dead-letters` omits the stored
  response text, invokes `resend --outbox-id`, and observes the same row
  pending with its replay counter incremented.
- Recoverable and terminal worker failures now create distinct concise Italian
  responses. Their stable outbox identity and retry or dead-letter transition
  commit atomically; repeated attempts reuse one recoverable notice, and no
  internal reason or owner content enters it. Unit tests cover both messages and
  real-PostgreSQL tests prove the terminal notice is claimable with the dead
  letter while the original text is absent.
- Both production entry points catch psycopg schema errors and print only
  `database_schema_invalid`, with no traceback, SQL, URL, or relation metadata.
  Three real-process cases prove the behavior against an isolated empty
  PostgreSQL database and a database stopped at messaging revision 0005; the
  same `health` command was exercised against the current down-level local
  database.
- Both long-running processes classify why they stopped: exit code 1 for
  something an operator must change, 2 for an unreachable dependency. The units
  set `RestartPreventExitStatus=1`, so a missing credential stops visibly
  instead of restarting forever, while a database that starts late converges on
  its own. Eleven unit tests cover the classification and the safety of the
  printed reason.
- Rendered unit files pass `systemd-analyze verify`. Offline security analysis
  reports exposure 3.5 for gateway and worker and 3.8 for llama.cpp; the prior
  templates measured 8.8, 8.8, and 9.2. The installer has been exercised into
  an isolated XDG directory, where every `Documentation=`, environment, and
  executable path resolved. Real manager startup and reboot remain acceptance
  work.
- The gateway warns the owner when it resumes after longer than the Bot API
  keeps undelivered updates. The warning names when it last listened rather
  than an elapsed duration, and is keyed by that instant, so restarting inside
  one outage produces one message. Five unit tests cover the threshold, the
  unpaired case, the never-polled case, and the repeated restart.
- Token rotation stays a manual BotFather procedure by decision: revoking a
  token has no Bot API method behind it, so a helper could automate only the
  file swap while implying the risky half was handled. `validate` verifies the
  replacement before the gateway starts with it.

## Pairing implementation evidence

- `money-tracker-telegram pair` resolves the sole active profile, revokes any
  earlier live code, and prints one short-lived code with the exact message the
  owner must send. Persistence keeps only its SHA-256.
- Ingestion recognizes a private `/pair` before authorization and drops it
  whether the code was right, wrong, or absent, so a typed secret never reaches
  a durable row. A successful pairing counts as an authorized observation that
  produced no work; a failed one counts as rejected.
- Consuming the code, creating the binding, and queueing the owner's
  confirmation commit together, so a paired owner is told that it worked
  without an operator reading the database.
- Two real-PostgreSQL tests take a generated code through the production
  ingestor: one proves the binding, the consumed code, and the deliverable
  confirmation appear while the inbox holds only the owner's ordinary message;
  the other proves a second holder of the same code binds nothing. Thirteen
  unit tests cover code generation, expiry, and every pairing disposition.

## Worker implementation evidence

- The worker translates one claimed row into `ChannelMessage`, derives the
  trusted `DispatchContext` from finance and messaging reads, and calls only
  the dispatch port. An unresolved callback keeps its branch but loses its
  correlation, so routing clarifies instead of applying an old decision to a
  new target.
- Outbox and pending-action identifiers are derived from the inbox row. A real
  PostgreSQL test uses the production dispatcher composition to post a draft,
  kills the process after the finance commit but before the messaging
  transaction, releases the row, and proves the re-dispatched item retains one
  `post` audit mutation and one logical response.
- A `Modifica` callback that lacks replacement details commits its
  clarification with a successor reply mapping for the selected movement. A
  real-PostgreSQL test delivers that question, recreates the repository, and
  proves the Telegram reply message ID still resolves the same movement.
- Six real-PostgreSQL tests cover the complete terminal transaction, immediate
  deliverability of the committed response, the crash window, head-of-line
  ordering behind a retrying row, consumption of an answered correlation, and
  modification follow-up across restart. Twenty unit tests cover translation,
  correlation, failure classification, the bounded attempt budget, and lease
  loss.
- The dispatch assembly is rebuilt per work unit, because the read-selection
  policy receives the active category names at build time and an approved
  review changes them between two messages.

## Gateway implementation evidence

- Production and test select distinct absolute token files; only the selected
  mode-0600 owner file is read, and neither token nor its URL appears in errors.
- `BotApiClient` calls `getMe`, backlog-preserving `deleteWebhook`, long-poll
  `getUpdates`, `getFile`, `sendMessage`, and `answerCallbackQuery` with bounded
  timeouts and typed, redacted failures.
- Ingestion authorizes the persisted private owner before retaining content or
  file metadata, commits all observed dispositions with the next offset, and
  acknowledges callbacks only after that transaction.
- Gateway ingress and outbox loops run independently and have no model,
  LangGraph, or finance-repository dependency. Delivery uses existing durable
  leases and five-attempt/dead-letter transitions.
- `uv run pytest -q tests/unit/money_tracker/telegram` passed 26 tests. The real
  PostgreSQL authorization test passed. The real Bot API test passed `getMe`
  and backlog-preserving webhook removal without owner content; this is
  transport evidence, not Telegram workflow acceptance.

## Deliverables

- Official Bot API adapter using long polling, private-owner pairing, allowed
  update filtering, secure file metadata handling, and redacted diagnostics.
- PostgreSQL inbox/outbox, persisted offset discipline, sequential worker,
  retry/backoff, dead letter, replay, and pending-action mapping.
- Italian message summaries and inline/reply interactions for text expenses,
  category review, record, modify, undo, help, and status.
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
