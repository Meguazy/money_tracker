# AD-009: Local FastAPI and React Web Application

- Status: accepted
- Implementation: version-one finance wire contracts implemented; FastAPI
  adapter, HTTP security, React application, and deployment planned
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
request, response, mutation-header, and problem schemas without claiming a
running HTTP endpoint. Contract tests map real domain expenses and verify UUID
and aware timestamp JSON encoding, integer-only EUR fields, rejection of
floats, naive timestamps, and unknown data, the required canonical idempotency
header, safe problem shapes, and positive aggregate versions.
