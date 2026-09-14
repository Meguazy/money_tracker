# AD-009: Local FastAPI and React Web Application

- Status: accepted
- Implementation: monthly overview, calendar analytics,
  transaction/manual-lifecycle, multi-allocation editing, budget-planning,
  category management/review, funding-source/wallet administration, receipt-metadata-review, messaging-operations,
  HTTP security, and host deployment implemented; remaining Step 5 surface in
  progress
- Date: 2026-09-01

## Context

The owner needs a polished dashboard for charts, corrections, budgets,
categories, wallets, settings, and failed-message replay. It only needs to run
on the workstation in V1.

## Decision

Expose a versioned FastAPI REST API and build the dashboard with React and
TypeScript. Use Vite for the frontend build, TanStack Query for server state,
React Router for navigation, Mantine for UI components, and Apache ECharts for
analytics. Serve the production build from FastAPI on the same origin.

Bind to `127.0.0.1`. Do not add a V1 login, but enforce allowed Host and Origin
values, closed CORS, CSRF protection for mutations, and no secret-bearing API
responses.

The Step 2 contract boundary uses the `/api/v1` prefix, exact
`minor_units`/EUR money objects, timezone-aware ISO timestamps, stable UUIDs,
strict unknown-field rejection, and machine-readable problem details. Every
mutation requires a non-empty `Idempotency-Key` header contract; profile scope
is injected from trusted runtime context rather than accepted in request bodies.

## Alternatives considered

- Server-rendered HTML is operationally smaller but less suitable for a rich,
  interactive dashboard.
- Streamlit is useful for experiments but is not the durable product shell.
- Putting idempotency keys in individual request bodies would duplicate a
  cross-cutting transport concern and make mutation contracts inconsistent.
- LAN or remote access adds authentication, TLS, and network administration not
  required by the owner.

## Consequences

- The repository gains a dedicated frontend toolchain and test boundary.
- API contracts must use stable IDs, ISO timestamps, integer minor units, and
  idempotency keys for mutations.
- Remote access requires a future threat-model update and ADR.

## Implementation evidence

The framework-independent `api.contracts` component defines strict Pydantic
finance request, response, mutation-header, and problem schemas. Step 5 adds a
separate `web` adapter so framework and browser policy do not leak into domain
or application code. Reads depend only on a narrow `DashboardReader`;
production composition supplies `ReportingService` and
`PostgresFinanceRepository`. Mutations pass through
`ExpenseMutationAdapter` to the existing `FinanceService`; the adapter injects
the trusted profile, actor, source reference, generated aggregate ID, and
transport command key. It owns no data. Responses map domain money to integer
minor-unit JSON without exposing profile identity, repository details,
idempotency or source references, private paths, or secrets.

`ReportingService.monthly_overview` composes existing posted-payment and budget
reads. Its total comes from the mutually exclusive funding split, while budget
positions come from category allocations. The dimensions reconcile to the same
movements independently and are never added together, preserving the AD-007
no-double-count contract.

Calendar analytics use the pure `domain.periods` component to resolve one
strict day, ISO week, month, year, or inclusive custom date selection into a
half-open timezone-aware instant interval. Calendar boundaries are interpreted
in
`Europe/Rome`, including daylight-saving transitions, and custom intervals
are limited to 366 days. `ReportingService.range_overview` reads posted
movements through the range extension of `DashboardReader`; the repository
owns only the timestamp query, while the application service computes purpose
and funding projections from the same immutable movement set.

Budget positions remain monthly contracts. The range response includes them
only when the selected interval is exactly one complete calendar month,
including a custom selection with those exact boundaries. Day, week, year,
partial-month, and multi-month results return no budget positions rather than
inventing prorated targets. The existing monthly overview and model-visible
monthly reporting tool remain unchanged.

The FastAPI composition exposes `/api/v1/overview/monthly`,
`/api/v1/overview/range`, `/api/v1/transactions`, and strict manual expense
create/replace/post/cancel
resources, monthly budget read/create/replace resources, content-free
liveness/readiness, and CSRF bootstrap. Browser JSON is
validated without coercion; every write requires a separate idempotency header,
uses optimistic concurrency where an aggregate already exists, and maps
application/domain/database failures to bounded problem details. Corrections of
posted movements revalidate active owner-scoped references before persistence.
Interactive schema endpoints are disabled. Configuration rejects
non-IPv4-loopback listeners, non-loopback Host values, and non-exact HTTP
loopback origins. Middleware validates Host before
routing, rejects any present disallowed Origin, grants no CORS access, and
requires allowed Origin plus equal HttpOnly-cookie/header tokens for every
unsafe method. API responses are `no-store`; all responses receive CSP, no-referrer,
no-sniff, and anti-framing headers.

`frontend/` implements the accepted stack: React and TypeScript built by Vite,
TanStack Query for server reads and mutation invalidation, React Router for the
shell, Mantine for UI, and ECharts with SVG rendering for the budget comparison.
Production routes load as independent lazy chunks behind an accessible
`Suspense` fallback. The monthly budget chart loads ECharts only when that
view has configured budgets, and Rollup isolates `zrender` from the chart
engine so every emitted JavaScript chunk stays below the production warning
threshold while the equivalent table remains available in the route chunk.
The Italian overview includes an explicit month label, exact EUR rendering,
loading, empty, and error states, keyboard focus, a labelled chart, and an
equivalent table. The `/analisi` view exposes the calendar selectors, resolved
inclusive dates, exact purpose and funding-source tables, and monthly budget
positions only where that contract applies. It provides loading, empty, error,
retry, keyboard, reload, and bookmark behavior without accepting profile scope.
The `/movimenti` view renders every monthly lifecycle state
and its exact allocations, and exposes labelled create, correct, post, and
cancel controls with retry feedback.

Its editor holds the AD-007 dimensions as two independent row lists that the
owner extends and shortens. Each dimension reconciles against the movement
total on its own and reports its own exact sum, difference, and errors, because
the two dimensions answer different questions and are never added together.
Sums are computed with `BigInt` over already exact parsed cents, and a restored
amount is rendered from integer digits, so no split depends on binary
floating-point arithmetic. A correction loads every stored allocation; a row
the owner leaves classified keeps its provenance, evidence, and override flag,
while a recategorized row takes owner provenance and keeps its receipt line,
which identifies the purchased line rather than the chosen purpose. The
transaction contract therefore exposes `receipt_line_id`, a stable
allocation-scoped identifier that reveals no private media path. Editing rows
offer only active references; an archived category or source stays visible as a
named, disabled option solely in the historical row that already uses it, so
history remains readable while a new allocation cannot select a reference
`require_active_references` would refuse. The compiled `dist` is served by
FastAPI on the API origin; arbitrary repository or receipt files are not
mounted.

Budget resources are addressed by their stable category rather than by the
budget row, so the browser is never handed an identifier it has no route for.
`ReportingService.budget_plan` adds the stored targets, explicit overrides, and
optimistic version that a correction must quote, which the overview
deliberately omits because it reports positions rather than offering edits.
`BudgetMutationAdapter` translates the strict request into
`FinanceAdministrationService`, injecting the trusted profile, actor, and
generated aggregate identifier exactly as the movement adapter does. Wallet
entries remain outside the budget adapter because their balance authority is a
separate application boundary; the dedicated source adapter described below
reaches them without coupling budgets to balances. Budget creation and replacement became
replay-safe in the same change, claiming the general command table described in
AD-005, so this decision's rule that a reused mutation key retries the identical
command holds for every implemented resource instead of only for movements.

The category-review slice composes a dedicated
`CategoryReviewMutationAdapter` over the existing owner-decision service.
`GET /api/v1/category-proposals` exposes proposal and correlated movement facts
plus active reuse choices without profile, source-reference, checkpoint, or
command data. Approve, use-existing, and cancel routes accept no movement ID;
the repository resolves it from the workflow source shared by the proposal and
expense, and the application verifies the association before settlement.
Missing or ambiguous association is a structured, bounded dependency failure,
not a guessed target.

All three mutations require the canonical `category-review:{proposal_id}`
header value. That is the proposal-wide key already claimed in the same
PostgreSQL transaction as proposal, taxonomy, movement, and audit state, so an
identical HTTP retry reconstructs the first result and another branch
conflicts. The React `/categorie` page exposes approval, shortlist reuse,
manual active-category reuse, cancellation, and accessible empty/error states.
Model-backed re-proposal remains in the conversational workflow and no
model-visible contract changes.


The category-management slice adds a separate
`CategoryManagementMutationAdapter` over `CategoryAdministrationService`.
`GET /api/v1/categories` returns active and archived stable nodes, hierarchy,
guidance, aliases, seed provenance, optimistic version, and soft warnings.
Rename accepts only a display name and observed version; archive accepts only
the version. Profile and actor are injected, and neither operation can delete,
reparent, create, or change inclusion semantics.

Both mutations use the common CSRF and idempotency-header boundary. The
repository locks the complete profile taxonomy before revalidating names,
aliases, hierarchy, and active-parent rules, then commits the one-version
replacement, command claim, and audit atomically. The React panel confirms
archive explicitly and disables it for a root with active children. Successful
work invalidates category, proposal, movement, overview, analytics, and budget
reads because each can display a name or active choice. The web component has
no proposal-creation or model dependency, so this surface implements AD-008's
existing policy without changing the evaluated category-fit boundary.

The funding-source slice adds `FundingSourceMutationAdapter` over the dedicated
`FundingSourceAdministrationService`. `GET /api/v1/funding-sources` returns
active and archived source identity, class, immutable balance mode, default
state, and optimistic version. A ledger source additionally returns immutable
entries, booked balance, AD-020 available balance, and remaining dated lots;
an untracked source returns no balance. Profile, command identity, and wallet
notes are omitted.

Strict resources create, rename, make default, archive, and append a manual
opening, credit, or adjustment. They expose no source deletion, wallet-entry
deletion, transfer, income, bank-sync, or general-accounting operation. Source
class and balance mode cannot change after creation. Archive requires an active
non-default source and, for a ledger, zero booked and available balance. The
page confirms default movement and archive explicitly and confirms a negative
adjustment before sending it.

Every command uses the common CSRF and `Idempotency-Key` boundary. Rename and
archive quote the target version; moving the default also quotes the previous
default's ID and version. PostgreSQL serializes lifecycle changes on the profile
and source rows, advances both default flags and the profile pointer together,
and commits claims and audits in that transaction. Wallet append locks the
source, rejects a second opening or an expiry-aware overdraft, and commits one
immutable entry, claim, and audit. Archive rechecks the zero-balance invariant
under the same lock, rather than trusting the earlier browser projection.

The React `/fonti` route labels payment resources separately from spending
categories, displays booked and available values without conflating them,
shows expiry lots and ledger history, and provides loading, empty, retryable
failure, pending, and responsive states. A successful source change invalidates
source, transaction, overview, and analytics queries because all can display a
name or funding allocation. No model-visible contract or receipt boundary is
part of this component.
The receipt slice adds a `ReceiptReviewMutationAdapter` over the existing
`ReceiptMetadataService`. `GET /api/v1/receipts` returns each retained
original's display filename, decoded media type, exact byte size, storage
instant, review state, and optimistic version. It deliberately omits the
content digest: that is the archive storage key under AD-014, no browser route
consumes it, and both decisions are addressed by the stable receipt identifier.
Profile identity and source reference are omitted as elsewhere. The review and
approve resources carry only the version the page displayed, and their command
key is `receipt-review:{receipt_id}:{expected_version}` — the transition, not
the receipt, because a receipt is reviewed in successive steps and each is a
separate decision. Retrying one decision replays it; a different decision taken
from the same displayed state conflicts. The adapter's port exposes the archive
listing and the two transitions and nothing else, so this surface cannot reach
a media store, an extractor, or a deletion path. Every client route is mounted
for direct navigation, so a reload or bookmark of `/movimenti`, `/categorie`, `/fonti`,
`/budget`, `/scontrini`, or `/messaggistica` reaches the same shell.

The messaging slice adds a `MessagingOperationsAdapter` over the existing
`MessagingAdministration`. This is the first control-plane domain that is not
scoped by profile: durable messaging state belongs to one bot environment.
The trusted `MessagingScope` is therefore read from configuration and bound
into the adapter at composition time, and no adapter method accepts a scope, so
the HTTP layer has no argument through which a request could name another bot.
The web process reads no Bot API credential: `MessagingScope.from_env` needs
only the selected environment, and this surface inspects and requeues durable
rows rather than speaking to Telegram.

`GET /api/v1/messaging` returns the bot environment, whether an owner is
paired, the persisted offset, both queue depths with their oldest waiting item,
the active correlation count, whether the downtime gap exceeds the Bot API's
24-hour retention, and both dead-letter queues. It is content-free by
construction: every value is a count, an age, a bounded reason code, or a
stable local identifier. The inbox projection omits the source reference, which
is the finance command identity a replay preserves, exactly as the movement
register and the receipt response omit theirs; the bot key is omitted too,
because the browser never selects a scope.

Replay and resend are requeue commands rather than new financial writes, so
their identity is decided differently from the other resources. They claim no
entry in `finance.idempotency_records`: that table is claimed in the same
transaction as the finance state it protects, and a messaging requeue commits
in the messaging schema through a different repository and connection, so a
claim there would promise an atomicity that does not exist. The durable pin is
instead the row's own `replay_count`, checked and advanced inside the single
messaging transaction that requeues it. The HTTP key names that exact
transition — `messaging-replay:{inbox_id}:{observed_replay_count}` and
`messaging-resend:{outbox_id}:{observed_replay_count}` — and the adapter
refuses any other value. Retrying one decision replays it and reports
`replayed` without a second requeue; a stale tab that already requeued an item
cannot silently requeue it again. No migration was needed.

Every refusal of a requeue is one bounded 409. AD-018 requires an inbox
identifier from another bot environment to be indistinguishable from a missing
or non-dead-letter item, so a foreign, unknown, already progressed, and
non-canonically keyed request all return the same problem.

The `/messaggistica` view shows both queue cards, the persisted offset, the
last poll, the active correlations, and both failure tables with their attempt
counts, requeue counts, and reason codes. It names no message. It warns when the
bot has no paired owner and when the downtime gap means Telegram has discarded
updates, and it states before the resend controls that Telegram offers no send
idempotency key, so a resend can produce a duplicate physical message while no
finance write is repeated.

`money-tracker-api` is a host entry point and
`money-tracker-api.service` is a hardened systemd user-service template. The
environment generator/upgrader owns its non-secret loopback settings, and the
installer refuses missing locked entry points or compiled assets. The local web
runbook records operation and rollback.

Unit tests cover exact contracts including the split allocation shape, trusted
profile scope, deterministic reconciliation, invalid periods, dependency health,
Host, Origin, CSRF, static serving/deep links, strict mutation validation, and
deployment templates, plus budget plan ordering, its correction version, the
exact budget wire shape, trusted scope injection, and a refused profile field in
a budget body, and the receipt wire shape without its archive key, the
transition-wide command identity, the version a decision quotes, and a refused
unknown receipt field. Real PostgreSQL integration tests cover the monthly read, the
complete manual create/replay/correct/post/replay/void HTTP lifecycle, a
multi-category multi-source movement whose rewrite, refused unreconciled
posting, and refused unreconciled correction of a posted movement are verified
against the real `FinanceService` and `PostgresFinanceRepository`, and a budget
configured, replayed, read for an overridden month, refused under a reused key
and a stale version, then verified in the database and in the overview. A real
PostgreSQL receipt test lists two registered originals newest first without
their digests, opens and replays one review, refuses approval of a receipt that
never entered review, approves the reviewed one, refuses a stale version, an
unknown receipt, and a non-canonical command key, and reads both terminal
states back through the production repository. A real PostgreSQL messaging
test drives one update to an inbox dead letter and one notice to a delivery
dead letter through the production repository, reads both over HTTP without the
owner's text or the bot key, replays the work item, repeats that request and
receives `replayed`, refuses a stale count, an unknown item, and a crossed key
with the identical problem, resends the response, and proves through the
repository that one requeue happened per queue and that the recovered work item
kept its source reference. A real PostgreSQL calendar-analytics test posts a
movement split across purpose and funding dimensions, excludes movements on
both sides of an ISO-week boundary, and verifies exact whole-month custom-range
budget aggregation through the production service and repository.

Frontend typecheck, component tests, production build, and a runtime dependency
audit pass. This is partial Step 5 evidence: the settings view, merchant
analytics, explicit period comparisons, advice surface, and full browser E2E
acceptance are still required before this decision is fully implemented.
