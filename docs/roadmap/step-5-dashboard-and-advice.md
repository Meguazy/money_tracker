# Step 5: Dashboard, budgets, analytics, and advice

Part of the [delivery roadmap](../roadmap.md).

Status: **In progress (2026-09-12).** The implemented vertical slices provide
the loopback same-origin host, deterministic monthly overview and transaction
register, accessible React shell, and audited manual create, correct, post, and
cancel workflows. The remaining control-plane views, wider analytics, advice
surface, split-allocation editor, and complete browser E2E acceptance are still
open. No model, prompt, routing threshold, tool contract, or taxonomy policy
changed, so EVD-004 remains the promoted text baseline.

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
  intentionally supports one category and one funding source, while the API and
  register preserve arbitrary reconciled allocation sets.
- Unit/component tests cover the register, strict browser boundary, deep link,
  accessible manual form, exact cents, lifecycle actions, and failure recovery.
  A real PostgreSQL HTTP integration test creates, replays, corrects, posts,
  replays, reads, voids, and verifies the resulting monthly total.

### Work still required before completion

- Add visual editing of multiple allocations, then receipt metadata/review,
  taxonomy proposals, budgets, funding sources/wallets, messaging operations,
  and settings views.
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
