# AD-009: Local FastAPI and React Web Application

- Status: accepted
- Implementation: monthly overview, transaction/manual-lifecycle,
  multi-allocation editing, budget-planning, and category-review slices, HTTP
  security, and host deployment implemented; remaining Step 5 surface in progress
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

The FastAPI composition exposes `/api/v1/overview/monthly`,
`/api/v1/transactions`, strict manual expense create/replace/post/cancel
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
The Italian overview includes an explicit month label, exact EUR rendering,
loading, empty, and error states, keyboard focus, a labelled chart, and an
equivalent table. The `/movimenti` view renders every monthly lifecycle state
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
generated aggregate identifier exactly as the movement adapter does; wallet
entries stay outside it because their balance authority is separate and Step 5
exposes no browser route for them. Budget creation and replacement became
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
a budget body. Real PostgreSQL integration tests cover the monthly read, the
complete manual create/replay/correct/post/replay/void HTTP lifecycle, a
multi-category multi-source movement whose rewrite, refused unreconciled
posting, and refused unreconciled correction of a posted movement are verified
against the real `FinanceService` and `PostgresFinanceRepository`, and a budget
configured, replayed, read for an overridden month, refused under a reused key
and a stale version, then verified in the database and in the overview. Frontend
typecheck, component tests, production build, and a runtime dependency audit
pass. This is partial Step 5 evidence: the remaining control-plane resources,
range analytics, advice surface, and full browser E2E acceptance are still
required before this decision is fully implemented.
