# AD-009: Local FastAPI and React Web Application

- Status: accepted
- Implementation: first FastAPI/React monthly slice, HTTP security, and host
  deployment implemented; remaining Step 5 control plane in progress
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
or application code. Its first endpoint depends only on a narrow
`MonthlyOverviewReader`; production composition supplies `ReportingService` and
`PostgresFinanceRepository`. The adapter owns no data. It injects the only V1
profile from trusted startup state and maps domain money to integer-minor-unit
JSON without exposing profile identity, repository details, private paths, or
secrets.

`ReportingService.monthly_overview` composes existing posted-payment and budget
reads. Its total comes from the mutually exclusive funding split, while budget
positions come from category allocations. The dimensions reconcile to the same
movements independently and are never added together, preserving the AD-007
no-double-count contract.

The FastAPI composition exposes `/api/v1/overview/monthly`, content-free
liveness/readiness, and CSRF bootstrap. Interactive schema endpoints are
disabled. Configuration rejects non-IPv4-loopback listeners, non-loopback Host
values, and non-exact HTTP loopback origins. Middleware validates Host before
routing, rejects any present disallowed Origin, grants no CORS access, and
requires allowed Origin plus equal HttpOnly-cookie/header tokens for every
unsafe method. API responses are `no-store`; all responses receive CSP, no-referrer,
no-sniff, and anti-framing headers.

`frontend/` implements the accepted stack: React and TypeScript built by Vite,
TanStack Query for the monthly server read, React Router for the shell, Mantine
for UI, and ECharts with SVG rendering for the budget comparison. The Italian
view includes an explicit month label, exact EUR rendering, loading, empty, and
error states, keyboard focus, a labelled chart, and an equivalent table. The
compiled `dist` is served by FastAPI on the API origin; arbitrary repository or
receipt files are not mounted.

`money-tracker-api` is a host entry point and
`money-tracker-api.service` is a hardened systemd user-service template. The
environment generator/upgrader owns its non-secret loopback settings, and the
installer refuses missing locked entry points or compiled assets. The local web
runbook records operation and rollback.

Unit tests cover exact contracts, trusted profile scope, deterministic
reconciliation, invalid periods, dependency health, Host, Origin, CSRF, static
serving, and deployment templates. A real PostgreSQL integration test creates
and posts an expense through `FinanceService`, then reads its exact value through
the HTTP route. Frontend typecheck, component tests, production build, and a
runtime dependency audit pass. This is partial Step 5 evidence: the remaining
resources, mutations, range analytics, advice surface, and full browser E2E
acceptance are still required before this decision is fully implemented.