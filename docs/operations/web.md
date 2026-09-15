# Local Web Operations

- Status: Step 5 overview, manual-movement, multi-allocation editing,
  budget-planning, calendar-analytics, category management/proposal review, funding-source/wallet administration, profile-settings consultation,
  receipt metadata review, and messaging-operations slices implemented;
  complete control plane and release acceptance still in progress
- Binding decision: [AD-009](../arc42/decisions/AD-009-local-web-application.md)
- Delivery evidence: [Step 5](../roadmap/step-5-dashboard-and-advice.md)

The implemented local service serves one compiled React application and its
versioned JSON API from the same origin. It provides the monthly overview and
calendar analytics for deterministic day, ISO-week, month, year, and bounded
custom ranges, the complete monthly transaction register, and manual movement
lifecycle with
multi-allocation editing, monthly budget planning, liveness, readiness, and CSRF
bootstrap endpoints. It also exposes the pending category-proposal queue and
the deterministic approve, reuse-active-category, and cancel decisions, the
versioned category rename/archive surface and taxonomy complexity warnings, the
retained receipt metadata and its mandatory review, and the durable messaging
queues with the two requeue commands. The settings surface exposes the fixed V1 locale, currency, and timezone plus the active default-source choice; only that choice is mutable through the existing funding-source command. Merchant
analytics, explicit period comparisons, advice, and receipt extraction remain
Step 5 or Step 6 work and must not be described as available.

## Ownership and boundaries

`money-tracker-api` is a host process. It owns HTTP composition and browser
policy, but no finance data. `ReportingService` computes exact finance reads; `ProfileSettingsService` validates and projects the safe owner-facing profile subset;
`ExpenseMutationAdapter` translates strict browser commands into the existing
`FinanceService`; `BudgetMutationAdapter` translates budget commands into the
existing `FinanceAdministrationService`; `CategoryManagementMutationAdapter`
exposes versioned rename/archive through `CategoryAdministrationService`
without delete, reparent, or create methods; `FundingSourceMutationAdapter` exposes versioned source lifecycle and immutable manual wallet entries through `FundingSourceAdministrationService`; `CategoryReviewMutationAdapter`
exposes the existing `CategoryReviewService` without accepting a movement
identifier from the browser; `ReceiptReviewMutationAdapter` exposes the
existing `ReceiptMetadataService` and reaches no media store;
`MessagingOperationsAdapter` exposes the existing `MessagingAdministration`
over `PostgresMessagingRepository` and reaches no Bot API transport; and
`PostgresFinanceRepository` owns authoritative storage and audit. The wallet route accepts only opening, credit, and adjustment; debit, refund, and expiry remain system/domain causes and every stored row remains immutable. The React application
owns only ephemeral browser state and receives neither a profile identifier nor
credentials. The settings response contains no database or service URL, token, private path, receipt key, bot scope, or infrastructure detail. Locale `it-IT`, currency `EUR`, and timezone `Europe/Rome` are persisted read-only V1 invariants; there is no generic settings write route. The production build is read-only input to FastAPI; receipt
originals are outside the static root, and no route serves their bytes or their
content digest.

The process requires exactly one bootstrapped profile and refuses to start when
that V1 invariant does not hold. Profile scope is injected during composition,
not taken from query parameters, headers, cookies, or response bodies. The bot
scope behind `/messaggistica` is injected the same way, from
`MONEY_TRACKER_TELEGRAM_ENVIRONMENT`, so the service refuses to start without an
explicit environment. It reads no Bot API token: the messaging surface inspects
and requeues durable rows and never calls Telegram.

## Build and run

Install the locked Python environment and the frontend lockfile, then create the
production assets:

    uv sync --locked --all-groups
    cd frontend
    npm ci
    npm run typecheck
    npm test -- --run
    npm run build
    cd ..

With the application database migrated and exactly one profile present, run:

    uv run --env-file .env money-tracker-api run

Open `http://127.0.0.1:8000/` or `http://localhost:8000/` for the overview;
use `/movimenti` for the register and manual lifecycle, and `/impostazioni` to inspect the fixed V1 profile facts and choose an active default funding source. The editor adds and
removes category and funding-source rows, reconciles each dimension against the
total separately, and shows each sum and its difference in exact cents. An
archived category or funding source can never be selected: it appears as a
disabled option only in the historical row that already uses it. Do not publish
or forward this port. V1 has no login, TLS, LAN listener, or remote-access
threat model.


Use `/fonti` to manage payment resources independently from spending
categories. Creation fixes the economic class and `BalanceMode`; rename changes
only the display label. Set one active source as default before archiving the
old default. The default move quotes both displayed source versions, so a 409
means the snapshot changed and the page must be reloaded.

An untracked source is a label and intentionally has no balance. A ledger source
shows booked value, currently available value, dated remaining lots, and
immutable history. Availability excludes expired credit and consumes the lot
that expires first; credit is usable through its displayed Rome expiry date.
The form permits one first opening, later credits, and signed adjustments. A
negative adjustment asks for confirmation and cannot exceed remaining booked
value; writing down expired credit never restores its availability.
Future entries, a second opening, deletion, and archive of a nonzero or default
wallet are refused. To correct a wallet movement, append an explicit adjustment;
do not edit PostgreSQL or delete history. This surface does not provide income
accounts, transfers, bank synchronization, or statement reconciliation.
Use `/categorie` to rename an active category or archive a leaf. Renaming keeps
the stable identifier used by movements and budgets. Archiving removes the node
from new allocations but keeps historical reports readable. The page asks for
confirmation before archive and disables a root while any active child depends
on it; archive the children first. A 409 means another tab changed the displayed
version: reload before deciding again. Complexity warnings are advisory and do
not weaken the two-level hierarchy or owner-approval rules.

A movement that reaches the editor without a resolved category, such as a draft
left for review, opens with no category row. Add one before saving: the form
refuses an empty dimension, a duplicate reference, an amount whose sign differs
from the total, and any split that does not reconcile.

Use `/categorie` to review proposals that are still pending. The page shows the
proposed category and the safe facts of its associated movement. Approving uses
the proposal, reusing a category applies an existing active category, and
cancelling discards both the proposal and the draft movement. The model-backed
`Proponine un'altra` path remains in the Telegram conversation: this page does
not invoke a model or change the taxonomy.

Use `/scontrini` to see what the private archive retained and to finish the
mandatory review AD-014 requires. Each row shows the display filename, decoded
format, exact size, storage instant and current state. A `stored` receipt is
sent into review, a receipt already in review is approved, and an approved one
offers nothing further. Originals are never shown, linked, downloaded, or
deleted here: the page carries metadata only, and importing an original stays
in `money-tracker-admin receipt-store`. Extraction of the purchase from an
image is Step 6 work and does not exist.

Use `/budget` to set the monthly targets the overview compares against. Until a
target exists there, the overview's budget comparison is empty by design and
every spent category is reported as unbudgeted. A budget has one recurring
target plus optional per-month exceptions and never rolls over: each month
starts from its own target. Budgets are addressed by category, so correcting one
replaces its complete plan under the version the page displayed.

Use `/messaggistica` to see what the Telegram channel is actually doing and to
recover from a failure without opening a terminal. The page shows the selected
bot environment, whether an owner is paired, the persisted offset, the last
poll, active interactions, both queue depths with their oldest waiting item,
and both dead-letter queues with attempt counts, requeue counts and reason
codes. It shows no message: the projections behind it carry no owner text at
all. Rimetti in coda returns a failed work item to the sequential worker with
its source reference intact, so a work unit that already committed a finance
mutation returns its first result rather than applying a second one. Rinvia
starts a fresh bounded delivery cycle for one undelivered response; Telegram
offers no send idempotency key, so an earlier ambiguous send can make the owner
see the same message twice, which the page states before the control. Sending a
message, changing the pairing, moving the offset and deleting a row are not on
this page and are not Step 5 work. Read
[the Telegram runbook](telegram.md) before recovering an old dead letter: a
replayed item delivers whatever answer it produces now, which can reach the
owner as a reply to a question they no longer remember asking.

For frontend development, keep the API on port 8000 and run Vite separately:

    cd frontend
    npm run dev

If an unsafe API route is introduced during Step 5, add
`http://127.0.0.1:5173` to `MONEY_TRACKER_WEB_ALLOWED_ORIGINS` for that local
development session. Production continues to use only the port-8000 origins.

## Configuration

| Setting | Default generated value | Contract |
| --- | --- | --- |
| `MONEY_TRACKER_WEB_HOST` | `127.0.0.1` | IPv4 loopback only; wildcard, LAN, hostname, and IPv6 values are refused. |
| `MONEY_TRACKER_WEB_PORT` | `8000` | Integer from 1 through 65535. |
| `MONEY_TRACKER_WEB_ALLOWED_HOSTS` | `127.0.0.1,localhost` | Non-empty exact host-name allowlist; loopback names only. |
| `MONEY_TRACKER_WEB_ALLOWED_ORIGINS` | the two exact HTTP origins on the configured port | Non-empty exact origins; no path, credentials, query, fragment, wildcard, or HTTPS substitution. |
| `MONEY_TRACKER_WEB_STATIC_DIR` | checkout `frontend/dist` | Absolute path containing `index.html` and `assets/`. |
| `MONEY_TRACKER_TELEGRAM_ENVIRONMENT` | `test` | Already generated for the gateway. The API now reads it too, to scope `/messaggistica` to one bot. It selects no credential here and the web process reads no token file. |

Run `scripts/upgrade_local_env.sh` to append missing web settings without
changing existing values. Review any non-default port consistently across the
listener and allowed origins.

## Current HTTP surface

| Route | Purpose | Content |
| --- | --- | --- |
| `GET /`, `GET /analisi`, `GET /movimenti`, `GET /budget`, `GET /categorie`, `GET /fonti`, `GET /scontrini`, `GET /messaggistica`, `GET /impostazioni` | Compiled React shell | Static application only; every route supports direct browser navigation, reload, and bookmarking. |
| `GET /api/v1/overview/monthly?period=YYYY-MM` | Deterministic monthly overview | Exact posted total, funding class/source split, budgets, and unbudgeted categories. |
| `GET /api/v1/overview/range` | Deterministic calendar analytics | One strict day, ISO-week, month, year, or bounded custom selector; exact posted total, purpose and funding breakdowns, and budget positions only for an exact whole month. |
| `GET /api/v1/transactions?period=YYYY-MM` | Monthly lifecycle register | Every movement state, exact allocations, optimistic versions, and safe category/source references. |
| `POST /api/v1/expenses` | Create a manual draft | Strict complete movement body; returns safe ID, state, and version. |
| `PUT /api/v1/expenses/{id}` | Correct a movement | Strict complete replacement plus `expected_version`. |
| `POST /api/v1/expenses/{id}/post` | Register a draft | Expected version and explicit receipt approval flag. |
| `POST /api/v1/expenses/{id}/cancel` | Cancel without deletion | Discards a draft/needs-review movement or voids a posted movement under its expected version. |
| `GET /api/v1/budgets?period=YYYY-MM` | Monthly budget plan | Configured targets worst position first, their overrides and versions, and the categories still available. |
| `POST /api/v1/budgets` | Configure a category budget | Strict recurring target plus optional month overrides. |
| `PUT /api/v1/budgets/{category_id}` | Replace a budget plan | Strict complete replacement plus `expected_version`. |
| `GET /api/v1/settings` | Safe profile settings | Fixed locale, currency, and timezone plus minimal active default-source choices; no profile ID, credential, URL, path, bot scope, or infrastructure detail. |
| `GET /api/v1/funding-sources` | Source and wallet administration | Active/archived sources, class, immutable balance mode, default flag, version, and for ledgers booked/available balances, expiry lots, and immutable history. No profile or notes. |
| `POST /api/v1/funding-sources` | Create a source | Strict name, funding class, balance mode, and default intent; class/mode cannot later change. |
| `PUT /api/v1/funding-sources/{id}` | Rename a source | Display name plus `expected_version`; stable identity and history remain. |
| `POST /api/v1/funding-sources/{id}/default` | Move profile default | Target version plus previous default ID/version; both rows, profile pointer, claims, and audits commit together. |
| `POST /api/v1/funding-sources/{id}/archive` | Archive without deletion | Expected version; refuses default sources and nonzero booked/available wallets. |
| `POST /api/v1/funding-sources/{id}/wallet-entries` | Append a manual wallet movement | Opening, credit, or adjustment with exact cents, aware time, and optional credit expiry; immutable, replay-safe, and expiry-aware. |
| `GET /api/v1/category-proposals` | Pending category review queue | Safe proposal/movement facts, alternatives, and active categories. |
| `POST /api/v1/category-proposals/{id}/approve` | Approve a proposal | Applies the proposed category to its server-resolved movement. |
| `POST /api/v1/category-proposals/{id}/use-existing` | Reuse an active category | Strict category reference; the movement target is never browser input. |
| `POST /api/v1/category-proposals/{id}/cancel` | Cancel a proposal | Cancels the proposal and its server-resolved draft movement. |
| `GET /api/v1/receipts` | Retained receipt metadata | Display filename, media type, exact byte size, storage instant, review state, and version, newest first. No content digest, no image, no archive path. |
| `POST /api/v1/receipts/{id}/review` | Open mandatory review | Moves a stored receipt to `needs_review` under its displayed version. |
| `POST /api/v1/receipts/{id}/approve` | Approve a receipt | Records explicit owner approval of a receipt already in review. |
| `GET /api/v1/messaging` | Durable messaging state | Bot environment, pairing state, offset, last poll, active interactions, both queue depths and ages, and both dead-letter queues. No bot key, no source reference, no message content. |
| `POST /api/v1/messaging/inbox/{id}/replay` | Requeue a failed work item | Requires the requeue count the page displayed; preserves the source reference and never rewinds the offset. |
| `POST /api/v1/messaging/outbox/{id}/resend` | Retry an undelivered response | Requires the resend count the page displayed; keeps the same logical row and may produce a duplicate physical message. |
| `GET /api/v1/health/live` | Process liveness | Content-free healthy state. |
| `GET /api/v1/health/ready` | Database/profile readiness | Healthy state or bounded problem details. |
| `GET /api/v1/csrf` | Prepare unsafe browser requests | Random double-submit value and HttpOnly same-site cookie. |

Interactive API documentation and the OpenAPI endpoint are disabled. Errors
use bounded problem details and do not include SQL diagnostics, configuration,
paths, owner text, or credentials.

## Browser security contract

- The server configuration refuses every non-IPv4-loopback bind.
- Every request needs an allowed `Host`; this is the DNS-rebinding boundary.
- A present `Origin` must match exactly, including port. No response grants
  cross-origin access.
- Every unsafe method requires an allowed Origin and equal non-empty values in
  the `money_tracker_csrf` HttpOnly cookie and `X-CSRF-Token` header.
- Every mutation additionally requires a non-empty `Idempotency-Key`. Reuse the
  same key only to retry the identical command; a changed command conflicts.
  This holds for movement, budget, funding-source, and wallet writes; the only settings change reuses the funding-source default command: each
  claims the same general command table in the transaction that applies it, so
  a retried request applies once.
  Correction, registration, cancellation, budget replacement, source rename/archive, and default movement also carry the version observed by the browser, so stale tabs receive 409 and must reload
  before retrying.
  Category-review commands use the fixed proposal-wide key
  `category-review:{proposal_id}` already owned by the review service. Retrying
  one decision replays it; changing the decision for that proposal conflicts.
  Receipt-review commands use the fixed transition-wide key
  `receipt-review:{receipt_id}:{expected_version}`, because a receipt is
  reviewed in successive steps and each step is a separate command. Retrying
  one transition replays it; a different decision taken from the same displayed
  state conflicts, and a version another transition already left is refused.
  Messaging requeues use `messaging-replay:{inbox_id}:{replay_count}` and
  `messaging-resend:{outbox_id}:{replay_count}`, where the count is the one the
  page displayed. A requeue claims no finance command record: it commits in the
  messaging schema, and the count in the row itself is the durable pin. Repeating
  one request answers `replayed` and requeues nothing further; every refusal is
  one 409, because an item from another bot environment must not be
  distinguishable from a missing or already recovered one.
- API responses are marked `no-store`. Responses also set a same-origin content
  security policy, deny framing and referrers, and disable MIME sniffing.
- The API has no secret-returning endpoint. Do not add token, database URL,
  private archive path, prompt, or raw message endpoints.

These controls protect the browser boundary from other sites and DNS rebinding;
they do not authenticate other processes running as the workstation owner. A
request for LAN or remote access requires a new threat model and ADR.

## Health and systemd

The checked-in user unit is installed with the other host units:

    ./scripts/install_systemd_units.sh
    systemctl --user enable --now money-tracker-api.service
    systemctl --user status money-tracker-api.service

The installer refuses missing frontend assets. Rebuild before reinstalling or
restarting after a frontend change. The unit restarts after dependency exit
code 2 but not after operator/configuration exit code 1. Inspect bounded service
diagnostics with:

    journalctl --user -u money-tracker-api.service --since today
    curl --fail http://127.0.0.1:8000/api/v1/health/live
    curl --fail http://127.0.0.1:8000/api/v1/health/ready

## Verification and rollback

The isolated real-PostgreSQL HTTP check is:

    RUN_POSTGRES_INTEGRATION_TESTS=1 uv run --env-file .env pytest -q \
      tests/integration/money_tracker/web/test_api.py

This test covers exact monthly reads, the real create/replay/correct/post/
replay/void lifecycle, a multi-category multi-source movement whose exact split
is read back, whose unreconciled draft stays editable but cannot be registered,
whose unreconciled correction of a posted movement is refused, and whose
rewritten split is verified in PostgreSQL, and a budget configured, replayed,
read for an overridden month, refused under a reused key and a stale version,
then verified in the database and the overview. It creates and replays a
source, opens an expiring wallet, refuses an overdrawing adjustment with no
claim or audit, reads booked/available balances, renames, moves the default,
archives the old source, and verifies source versions, profile pointer, ledger,
claims, and audits. It also reads three pending
category proposals, approves one with replay, reuses an active category for
one, cancels one, and verifies every terminal movement/proposal state in
PostgreSQL. It registers two receipts, lists them newest first without their
digests, opens and replays one review, refuses approval of a receipt that never
entered review, approves the reviewed one, refuses a stale version, an unknown
receipt and a non-canonical key, and reads both terminal states back from
PostgreSQL. It drives one update to an inbox dead letter and one notice to a
delivery dead letter, reads both over HTTP without the owner's text or the bot
key, replays the work item, repeats that request and receives `replayed`,
refuses a stale count, an unknown item and a crossed key identically, resends
the response, and verifies through the repository that one requeue happened per
queue and that the recovered work item kept its source reference. It uses
isolated identifiers and cleans up its schemas.

The source-create race check exercises two simultaneous generated candidates
under one idempotency key and verifies that both receive the one stored ID:

    RUN_POSTGRES_INTEGRATION_TESTS=1 uv run --env-file .env pytest -q \
      tests/integration/money_tracker/persistence/test_postgres.py::test_concurrent_source_create_replay_returns_the_stored_identity

A draft may legitimately be unreconciled, so both allocation dimensions are
enforced when a movement is registered, not while it is still being edited.
Registering an unreconciled movement returns a bounded 422 and changes nothing.

To withdraw the web surface, stop and disable only
`money-tracker-api.service`. No migration rollback is needed. Manual movements
already committed through the service are authoritative finance history, and so
are configured budgets, funding-source versions, immutable wallet entries,
category-review decisions, receipt review states, and any requeue already
applied: keep them and the Telegram services running, and correct or cancel them only
through normal audited use cases. Withdrawing the web surface leaves every
retained original untouched, because the service never had access to the
archive, and leaves the Telegram queues operable from
`money-tracker-telegram`, which remains the unattended recovery path.
Re-enable the last known-good build and unit rather than deleting `frontend/dist` while the service is active.
