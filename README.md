# Money Tracker

Money Tracker is a local-first personal-finance assistant built with Python,
LangChain, LangGraph, llama.cpp, PostgreSQL, and self-hosted observability.
Steps 0–4 are complete: the repository contains the deterministic finance
domain, PostgreSQL persistence, evaluated text workflows, durable private-owner
Telegram channel, encrypted backup, and reboot/fault acceptance. Step 5 is in
progress. Its implemented slices provide a protected loopback FastAPI host,
accessible monthly overview and transaction register, and audited manual
movement creation, correction, registration, and cancellation. The remaining
dashboard views, wider analytics, advice surface, receipt extraction, and
release hardening remain open.

## Current scaffold quick start

    uv sync
    ./scripts/generate_local_env.sh

After starting a GGUF model with scripts/run_llama_server.sh, run:

    uv run --env-file .env money-tracker "Help me review this month's spending"

Start the dedicated application PostgreSQL and initialize its schemas and
LangGraph checkpoint tables with:

    docker compose up -d --wait application-postgres
    uv run --env-file .env money-tracker-db bootstrap

After bootstrapping an active profile, validate and register a local receipt
through the implemented private archive boundary:

    uv run --env-file .env money-tracker-admin receipt-store \
      --profile-id PROFILE_UUID \
      --file /absolute/path/to/receipt.png \
      --source-reference local:manual-import

Send one Italian message through the real root router and see the routing
decision it reaches. A deterministic route needs no model; free text needs the
local server running:

    uv run --env-file .env money-tracker-admin route \
      --text 'Ho speso 12 euro per i croccantini dei gatti' \
      --minimum-confidence 0.8

The same dispatcher now runs behind the accepted durable Telegram gateway and
worker. Pairing, recovery, and real-bot evidence are in the Telegram runbook.

Build and run the implemented local dashboard:

    cd frontend && npm ci && npm run build && cd ..
    uv run --env-file .env money-tracker-api run

Then open `http://127.0.0.1:8000/` for the monthly overview or
`http://127.0.0.1:8000/movimenti` for the transaction register. See the local
web runbook for the exact browser security and systemd contract.

With Restic and matching PostgreSQL client tools installed and an external
repository configured, create a verified encrypted backup of the authoritative
data:

    uv run --env-file .env money-tracker-backup init
    uv run --env-file .env money-tracker-backup create

The repaired Langfuse stack is optional and starts only when selected:

    docker compose --profile observability up -d --wait

Existing observability volumes were preserved during the repair. The CLI
enables tracing whenever both local Langfuse project keys are non-empty; core
database operation does not depend on Langfuse.

## Documentation

- [Documentation map and required reading order](docs/README.md)
- [Product baseline](docs/product/README.md)
- [Delivery roadmap](docs/roadmap.md)
- [arc42 architecture and decisions](docs/arc42/README.md)
- [Experiments and evaluation gates](docs/experiments/README.md)
- [Operations guides](docs/operations/README.md)
- [Current development commands](docs/development.md)
- [Repository instructions](AGENTS.md)
