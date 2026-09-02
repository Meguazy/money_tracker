# Money Tracker

Money Tracker is a local-first personal-finance assistant built with Python,
LangChain, LangGraph, llama.cpp, PostgreSQL, and self-hosted observability. The
current repository completes the platform foundation and the Step 2 finance
domain, persistence, private receipt-archive core, and verified encrypted
backup with an isolated restore drill. Telegram, the local web dashboard,
receipt extraction, API/worker media wiring, and backup scheduling remain
planned.

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
