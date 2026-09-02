# Money Tracker

Money Tracker is a local-first personal-finance assistant built with Python,
LangChain, LangGraph, llama.cpp, and self-hosted observability. The current
repository is an inference scaffold; the approved product architecture adds a
private Telegram channel, durable finance storage, receipt processing, and a
local web dashboard in staged increments.

## Current scaffold quick start

    uv sync
    ./scripts/generate_local_env.sh

After starting a GGUF model with scripts/run_llama_server.sh, run:

    uv run --env-file .env money-tracker "Help me review this month's spending"

Start the dedicated application PostgreSQL and initialize its schemas and
LangGraph checkpoint tables with:

    docker compose up -d --wait application-postgres
    uv run --env-file .env money-tracker-db bootstrap

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
