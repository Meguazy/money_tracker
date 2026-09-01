# Money Tracker

Money Tracker is a local-first personal-finance assistant built with Python,
LangChain, LangGraph, llama.cpp, and a self-hosted Langfuse stack. Inference,
traces, and service data stay on the workstation.

## Quick start

    uv sync
    ./scripts/generate_local_env.sh
    docker compose up -d

After starting a GGUF model with scripts/run_llama_server.sh, run:

    uv run --env-file .env money-tracker "Help me review this month's spending"

## Documentation

- [Development and local operations](docs/development.md)
- [arc42 architecture documentation](docs/arc42/README.md)
- [Repository instructions](AGENTS.md)
