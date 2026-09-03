# Step 5: Dashboard, budgets, analytics, and advice

Part of the [delivery roadmap](../roadmap.md).

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
