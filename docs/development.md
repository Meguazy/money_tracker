# Development and Local Operations

## Prerequisites

- Python 3.12
- UV
- Docker with Docker Compose
- NVIDIA CUDA toolkit 12.8 or newer for the RTX 5070 Ti
- An upstream llama.cpp build and a compatible GGUF model

## Python environment

Create the locked environment and generate uncommitted local credentials:

    uv sync
    ./scripts/generate_local_env.sh

UV owns the .venv and uv.lock. Do not install project packages with pip. The
environment generator creates a mode-600, Git-ignored .env containing
independent random credentials and refuses to overwrite an existing file.

## Self-hosted Langfuse

The Compose project follows Langfuse's official self-hosted architecture:
Langfuse web and worker containers, PostgreSQL, ClickHouse, Redis, and MinIO.

    docker compose up -d
    docker compose ps

The first command downloads images when they are unavailable locally. Once
healthy, the UI and API are available at http://localhost:3000. Retrieve the
generated bootstrap login with:

    rg '^LANGFUSE_INIT_USER_(EMAIL|PASSWORD)=' .env

Only ports 3000 and 9090 are bound, both on 127.0.0.1. PostgreSQL, ClickHouse,
Redis, and the worker have no host ports. Langfuse telemetry and the in-app AI
agent are disabled, and no external LLM provider is configured.

Named Docker volumes persist traces and configuration. Stop services without
deleting data:

    docker compose stop

Do not run docker compose down -v unless the local databases and traces should
be permanently erased.

## llama.cpp for the RTX 5070 Ti

Build upstream llama.cpp for Blackwell compute capability 12.0:

    git clone https://github.com/ggml-org/llama.cpp.git
    cmake -S llama.cpp -B llama.cpp/build \
      -DGGML_CUDA=ON \
      -DCMAKE_CUDA_ARCHITECTURES=120 \
      -DGGML_CUDA_FA_ALL_QUANTS=ON
    cmake --build llama.cpp/build --config Release -j

Set LLAMA_SERVER_BIN in .env to llama.cpp/build/bin/llama-server and MODEL_PATH
to a GGUF model. An 8B-14B Q4 or Q5 model is a practical starting point for the
16 GB GPU. Start the server:

    set -a
    source .env
    set +a
    ./scripts/run_llama_server.sh

The launcher binds to localhost, offloads all model layers, enables Flash
Attention, uses the embedded Jinja chat template, and begins with an 8K context.

Ollama remains an OpenAI-compatible fallback:

    LLM_BASE_URL=http://localhost:11434/v1 \
    LLM_MODEL=qwen2.5:14b \
    uv run money-tracker "Suggest three ways to reduce discretionary spending"

## Langfuse tracing

The generated environment provisions the local project with the same
LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY used by the Python SDK.
LANGFUSE_BASE_URL is http://localhost:3000, so traces are sent only to the local
deployment. Removing either project key disables tracing without disabling
inference.

## Tests

Run isolated unit tests:

    uv run python -m unittest discover -s tests/unit -t .

Run integration tests against a real inference service:

    RUN_INTEGRATION_TESTS=1 \
    INTEGRATION_LLM_BASE_URL=http://localhost:11434/v1 \
    INTEGRATION_LLM_MODEL=qwen2.5:14b \
    uv run python -m unittest discover -s tests/integration -t .

Run the complete CLI workflow against a real inference service:

    RUN_E2E_TESTS=1 \
    E2E_LLM_BASE_URL=http://localhost:11434/v1 \
    E2E_LLM_MODEL=qwen2.5:14b \
    uv run python -m unittest discover -s tests/e2e -t .

Real-service tests are opt-in so the unit suite does not silently substitute
mocks when infrastructure is unavailable.

Run the remaining static checks:

    uv run python -m compileall -q src tests
    docker compose --env-file .env.example config --quiet
    git diff --check
