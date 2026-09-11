# Local Web Operations

- Status: Step 5 first vertical slice implemented; complete control plane and
  release acceptance still in progress
- Binding decision: [AD-009](../arc42/decisions/AD-009-local-web-application.md)
- Delivery evidence: [Step 5](../roadmap/step-5-dashboard-and-advice.md)

The implemented local service serves one compiled React application and its
versioned JSON API from the same origin. It currently provides the monthly
overview plus liveness, readiness, and CSRF bootstrap endpoints. Transactions,
manual mutations, receipts, taxonomy, wallets, messaging operations, settings,
wider date ranges, and advice remain Step 5 work and must not be described as
available.

## Ownership and boundaries

`money-tracker-api` is a host process. It owns HTTP composition and browser
policy, but no finance data. `ReportingService` computes exact reads and
`PostgresFinanceRepository` reads the authoritative application database. The
React application owns only ephemeral browser state and receives neither a
profile identifier nor credentials. The production build is read-only input to
FastAPI; receipt originals are outside the static root.

The process requires exactly one bootstrapped profile and refuses to start when
that V1 invariant does not hold. Profile scope is injected during composition,
not taken from query parameters, headers, cookies, or response bodies.

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

Open `http://127.0.0.1:8000/` or `http://localhost:8000/`. Do not publish or
forward this port. V1 has no login, TLS, LAN listener, or remote-access threat
model.

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

Run `scripts/upgrade_local_env.sh` to append missing web settings without
changing existing values. Review any non-default port consistently across the
listener and allowed origins.

## Current HTTP surface

| Route | Purpose | Content |
| --- | --- | --- |
| `GET /` | Compiled React shell | Static application only. |
| `GET /api/v1/overview/monthly?period=YYYY-MM` | Deterministic monthly overview | Exact posted total, funding class/source split, budgets, and unbudgeted categories. |
| `GET /api/v1/health/live` | Process liveness | Content-free healthy state. |
| `GET /api/v1/health/ready` | Database/profile readiness | Healthy state or bounded problem details. |
| `GET /api/v1/csrf` | Prepare later unsafe browser requests | Random double-submit value and HttpOnly same-site cookie. |

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

To withdraw this first slice, stop and disable only
`money-tracker-api.service`. No migration or finance rollback is needed because
the component adds no authoritative tables and writes no finance state. Keep
the existing PostgreSQL data and Telegram services running. Re-enable the last
known-good build and unit rather than deleting `frontend/dist` while the
service is active.
