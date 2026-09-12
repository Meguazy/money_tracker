# Step 5: Dashboard, budgets, analytics, and advice

Part of the [delivery roadmap](../roadmap.md).

Status: **In progress (2026-09-12).** The implemented vertical slices provide
the loopback same-origin host, deterministic monthly overview and transaction
register, accessible React shell, audited manual create, correct, post, and
cancel workflows, the multi-allocation editor for both reconciling dimensions,
the monthly budget planner, and owner review of pending category proposals. The
remaining control-plane views, wider
analytics, advice surface, and complete browser E2E acceptance are still open.
No model, prompt, routing threshold, tool contract, or taxonomy policy changed,
so EVD-004 remains the promoted text baseline.

## Deliverables

- FastAPI same-origin host with React/TypeScript/Vite, TanStack Query, React
  Router, Mantine, and Apache ECharts.
- Overview, transactions, receipt metadata, categories/proposals, monthly
  budgets, funding sources/wallets, inbox/dead letters, health, and settings
  views.
- Manual creation/correction using the same application services as workflows.
- Daily, ISO-week, monthly, annual, and custom-range analytics with purpose and
  personal-fund/benefit breakdowns.
- Read-only spending, budget, wallet, comparison, and advice tools with grounded
  Italian responses.
- Local browser protections: loopback bind, Host/Origin allowlists, closed CORS,
  CSRF control, and no secret-returning endpoint.

## Exit criteria

- Browser E2E tests cover the complete control-plane workflows against real
  application services and PostgreSQL.
- Analytics and budget numbers match deterministic fixtures and never
  double-count receipt parents/lines.
- Advice gates demonstrate 100% numeric grounding and zero mutation access.
- Accessibility checks cover keyboard navigation, form labels, contrast,
  loading/empty/error states, and chart table alternatives.
- Non-loopback or disallowed-origin requests cannot read or mutate data.

## Implementation evidence

### First vertical slice (2026-09-11)

- `money_tracker.web` adds a narrow FastAPI adapter, strict monthly response
  contracts, content-free liveness/readiness probes, and a production CLI. The
  single owner profile is selected at composition time and never accepted from
  HTTP input.
- `ReportingService.monthly_overview` composes the existing posted-payment and
  category-budget reads. Exact EUR minor units are preserved; category and
  funding dimensions reconcile independently, so the response does not add a
  movement, receipt parent, or line twice.
- `frontend/` uses the accepted React/TypeScript/Vite, TanStack Query, React
  Router, Mantine, and ECharts stack. The implemented `/` view shows a selected
  Rome month, personal/benefit funding split, budget positions, explicit
  loading/empty/error states, a keyboard-focusable chart, and an equivalent
  table.
- The HTTP boundary accepts only an IPv4 loopback listener, exact loopback Host
  and Origin allowlists, emits no CORS permission, requires a matching
  HttpOnly-cookie/header token plus an allowed Origin for unsafe methods, and
  sets a restrictive CSP and browser hardening headers. It exposes no
  credential or private receipt path.
- `money-tracker-api.service`, the environment generator/upgrader, and the
  checkout-aware installer provide the host deployment path. Installation
  refuses a missing locked entry point or missing compiled frontend.
- A production-command smoke test on 2026-09-11 started the service at
  `127.0.0.1:8000` against the real application database: liveness and
  readiness returned healthy, the compiled Italian shell loaded, and a
  disallowed Host returned 403. The process then shut down cleanly.
- Focused Python tests pass for deterministic totals, configuration, Host,
  Origin, CSRF, health, static serving, and deployment templates. A real
  PostgreSQL integration test posts an expense through `FinanceService` and
  reads its exact value through the HTTP endpoint. Frontend typecheck, component
  tests, production build, and a runtime dependency audit pass.

### Transaction register and manual lifecycle slice (2026-09-12)

- `ReportingService.transaction_register` reads every lifecycle state for one
  month plus taxonomy and funding-source references. The response retains
  exact dual allocations and optimistic versions while omitting profile,
  idempotency, source-reference, and private-media data.
- The same-origin API adds `GET /api/v1/transactions` and strict create,
  complete-replacement, post, and cancel routes. A narrow web adapter injects
  trusted profile, actor, source reference, aggregate ID, and transport command
  key before calling the existing audited, replay-safe `FinanceService`.
- Unsafe requests exercise the already documented Origin and double-submit
  CSRF policy. Every mutation also requires `Idempotency-Key`; optimistic or
  idempotency conflicts return bounded 409 problems, invalid domain transitions
  return bounded 422 problems, and cancellation preserves history by discarding
  draft/needs-review movements or voiding posted movements.
- The domain now revalidates active owner-scoped category and funding-source
  references when an already posted movement is corrected.
- The React `/movimenti` view provides labelled manual entry, correction,
  registration and cancellation controls; lifecycle, allocation and reference
  facts; loading, empty, failure and retry states; and exact string-to-cent
  conversion without binary floating-point arithmetic. The first editor
  intentionally supported one category and one funding source, while the API and
  register preserved arbitrary reconciled allocation sets. The slice below
  removed that limit.
- Unit/component tests cover the register, strict browser boundary, deep link,
  accessible manual form, exact cents, lifecycle actions, and failure recovery.
  A real PostgreSQL HTTP integration test creates, replays, corrects, posts,
  replays, reads, voids, and verifies the resulting monthly total.

### Multi-allocation editor slice (2026-09-12)

- The `/movimenti` editor now holds both reconciling dimensions as independent
  row lists. The owner adds and removes category and funding-source rows, and
  each dimension reports its own exact sum, its own difference from the total,
  and its own errors, because AD-007 reconciles the two dimensions separately
  rather than against each other.
- Sums use `BigInt` over the already exact parsed cents, so no split is decided
  by binary floating-point arithmetic, and a restored amount is rendered from
  integer digits instead of a division.
- A correction loads every stored allocation. A row whose category the owner
  does not change keeps its `provenance`, `evidence`, and `owner_override`,
  so a workflow classification is not silently relabelled as an owner
  decision. A recategorized row takes owner provenance but keeps its
  `receipt_line_id`: that field records which purchased line the money came
  from, which recategorizing does not change.
- `TransactionCategoryAllocationContract` therefore exposes `receipt_line_id`.
  It is a stable allocation-scoped identifier, not a private media path, and
  the register still returns no profile, source reference, or receipt bytes.
- Editing rows offer only active references. An archived category or funding
  source appears solely in the historical row that already uses it, as a
  disabled option with a named warning, so existing history stays readable
  while a new allocation cannot select a reference the domain refuses when the
  movement is posted.
- The form refuses a duplicate reference, an allocation whose sign differs from
  the total, an unparseable amount, and a dimension left without rows, which a
  `needs_review` draft carrying no resolved category legitimately reaches.
  Native validation reports an empty control, and the alert list reports what
  it cannot.
- Component tests cover the exact two-by-two create, complete correction
  preservation, metadata replacement on a recategorized row, separate
  reconciliation errors, duplicates, wrong signs, invalid amounts, the
  category-less review draft, archived references, keyboard row removal, and
  API failure recovery. A web unit test pins the exact split contract including
  `receipt_line_id`.
- A real PostgreSQL HTTP test creates a two-category and two-source movement,
  reads back both exact dimensions, keeps an unreconciled draft editable while
  refusing to post it, refuses an unreconciled correction of a posted movement,
  rewrites the split into three categories and two sources, and verifies through
  the real `FinanceService` and `PostgresFinanceRepository` that the stored
  allocations and the monthly personal/benefit split match exactly.

### Monthly budget planner slice (2026-09-12)

- The overview has rendered budget positions since the first slice, but nothing
  outside the tests could configure one: no CLI command and no API route reached
  `FinanceAdministrationService`. `/budget` closes that gap, so the budget
  comparison now measures targets the owner actually set.
- `ReportingService.budget_plan` composes the existing posted-spend and budget
  reads. Unlike the overview it also returns each aggregate's stable category,
  explicit overrides, and optimistic version, because the control plane has to
  correct the stored plan rather than only display it. Entries keep the
  overview's ordering, worst position first.
- `create_budget` and `replace_budget` are now replay-safe. They claim the same
  general `finance.idempotency_records` table the movement commands use, keyed
  by a canonical digest of scope, category, recurring target, and overrides.
  Override order does not change the digest, so two orderings of one plan are a
  single decision. This keeps the AD-009 rule that reusing a mutation key
  retries the identical command true for every implemented resource rather than
  only for movements.
- `replace_budget` now raises `AggregateNotFoundError` instead of a bare
  `LookupError`, so the web boundary maps one typed application failure.
- The API adds `GET /api/v1/budgets`, `POST /api/v1/budgets`, and
  `PUT /api/v1/budgets/{category_id}`. A budget is addressed by its stable
  category, so the browser never receives a budget row identifier it has no
  route for, and the responses carry no profile identity. Bounded problems
  distinguish a missing budget, a conflict, and a refused domain rule.
- The React `/budget` view lists each exact position with its remaining amount,
  configures a target for any category that has none, and corrects a stored plan
  under the version it displayed. Month overrides are added and removed as rows,
  amounts convert through exact cents, and the form refuses a non-positive
  target, a month without an amount, and two exceptions for one month.
- Unit tests cover plan ordering and the version a correction must quote, the
  exact wire shape, trusted scope injection, a refused profile field in a
  request body, replayed creation, a reused key with changed content, and
  override-order equivalence. Component tests cover listing, creation,
  correction with stored overrides, validation, keyboard removal, API failure,
  retry, and the empty state.
- Real PostgreSQL tests configure a budget over HTTP, replay it, read the exact
  position for its month and for an overridden month, refuse a key reused for
  other content, refuse a stale version, and verify the corrected plan in the
  database and in the monthly overview. A repository test proves one replayed
  command writes a single audit row.

### Category proposal review slice (2026-09-12)

- `/categorie` makes every pending category proposal reachable from the local
  control plane. Each card names the candidate, optional parent, inclusion
  guidance, rationale, rejected candidates, shortlist, and the exact movement
  waiting for a purpose decision.
- The read model pairs proposal and movement server-side through the trusted
  workflow `source_reference`. The browser receives neither that reference nor
  the profile ID and never supplies an expense ID; missing or ambiguous
  correlation is logged as a stable structured failure and refused.
- `GET /api/v1/category-proposals` returns the pending queue and active taxonomy.
  Strict approve, use-existing, and cancel resources reuse the existing
  `CategoryReviewService`, so approval creates a node and posts atomically,
  reuse posts without taxonomy growth, and cancellation discards the
  never-posted draft without deletion.
- Every HTTP branch requires the proposal-wide `Idempotency-Key` that the
  existing settlement transaction claims. An identical request replays its
  first result, while another branch or a key for another proposal conflicts.
- The page offers the evaluated shortlist and a labelled selector over all
  active categories. Loading, empty, failure/retry and pending-decision states
  are explicit; controls are keyboard accessible and the four-item navigation
  wraps on narrow screens.
- Generating another model candidate remains in the Telegram review workflow.
  The browser can always resolve the review deterministically by choosing an
  active category, but it does not gain an inference dependency or a second
  path that can invent taxonomy.
- Unit tests cover source correlation, cross-workflow refusal, strict wire
  shape, trusted scope, canonical command identity, bounded problems and deep
  linking. Component tests cover proposal facts, approval, shortlist/manual
  reuse, cancellation, keyboard operation, and empty/error recovery.
- A real PostgreSQL HTTP test lists three correlated proposals, approves and
  replays one, reuses an existing category for another, cancels the third, and
  verifies terminal proposal and movement state through the production
  repository. No migration, model-visible contract, prompt, threshold, tool,
  or taxonomy policy changed; EVD-004 remains the promoted baseline.

### Work still required before completion

- Add receipt metadata/review, category rename/archive management, funding
  sources/wallets, messaging operations, and settings views.
- Extend deterministic reads from the current month to day, ISO week, year,
  comparison, and validated custom ranges, retaining purpose and funding
  breakdowns.
- Expose the already promoted read-only advice capabilities without widening
  their mutation authority; rerun and record any evaluation required by a
  changed model-visible contract.
- Add complete browser E2E coverage against real application services and
  PostgreSQL, accessibility/contrast automation, and installed-service browser
  acceptance. Step 5 remains in progress until every exit criterion above has
  that evidence.
