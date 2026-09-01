# AD-009: Local FastAPI and React Web Application

- Status: accepted
- Implementation: planned
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

## Alternatives considered

- Server-rendered HTML is operationally smaller but less suitable for a rich,
  interactive dashboard.
- Streamlit is useful for experiments but is not the durable product shell.
- LAN or remote access adds authentication, TLS, and network administration not
  required by the owner.

## Consequences

- The repository gains a dedicated frontend toolchain and test boundary.
- API contracts must use stable IDs, ISO timestamps, integer minor units, and
  idempotency keys for mutations.
- Remote access requires a future threat-model update and ADR.
