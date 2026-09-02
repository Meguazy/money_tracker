# Development and Local Operations

## Prerequisites

- Python 3.12
- UV
- Docker with Docker Compose
- NVIDIA CUDA toolkit 12.8 or newer for the RTX 5070 Ti
- An upstream llama.cpp build and a compatible GGUF model for real inference
  and capability checks

## Initial setup

Create the locked environment and generate uncommitted local credentials:

    uv sync --frozen
    ./scripts/generate_local_env.sh

UV owns `.venv` and `uv.lock`; do not install project packages with pip. The
generator creates a mode-600, Git-ignored `.env` with independent random
credentials and refuses to overwrite an existing file. Upgrade an existing
environment non-destructively after reviewing its permissions:

    ./scripts/upgrade_local_env.sh

The upgrade script appends only missing Step 1 database and llama.cpp profile
settings and never changes existing values.

## Application PostgreSQL and migrations

Start only the core database, then apply the foundation schemas and initialize
the persistent LangGraph checkpointer:

    docker compose up -d --wait application-postgres
    uv run --env-file .env money-tracker-db bootstrap
    uv run --env-file .env money-tracker-db current

The service binds to `127.0.0.1:5433`, owns an independent persistent volume,
and exposes the non-superuser `money_tracker` role to the application. The
first migration creates the `finance`, `messaging`, and `workflow` schema
boundaries. The active Step 2 migration adds authoritative finance tables,
deferred reconciliation constraints, immutable audit/wallet rows, taxonomy
bootstrap support, and expense idempotency/version state. Messaging tables
remain planned.

The foundation downgrade is intentionally guarded and the finance migration
refuses destructive downgrade. The command below is now usable only when the
database is still at the empty Step 1 foundation revision; never use it after
Step 2 finance tables have been applied:

    uv run --env-file .env money-tracker-db downgrade-foundation \\
      --confirm-empty-foundation

Never use the guarded downgrade as a production rollback. Restore a verified
backup or deploy forward when data exists.

## Administrative receipt storage

Set `MONEY_TRACKER_RECEIPT_ARCHIVE_ROOT` to an absolute private host path. The
environment generator uses the XDG data directory (or `~/.local/share`) and the
upgrade script appends the setting without changing existing values.

After an active profile has been bootstrapped, validate and register one local
original with:

    uv run --env-file .env money-tracker-admin receipt-store \
      --profile-id PROFILE_UUID \
      --file /absolute/path/to/receipt.png \
      --source-reference local:manual-import

The command refuses final input symlinks, directories, unsupported/oversized
images, and unknown profiles. It prints only safe JSON metadata, not file paths
or bytes. Retrying the same content returns the first database record.

## Optional self-hosted Langfuse

The observability stack is separate from the default Compose project. Start it
only for development, tracing, or evaluation:

    docker compose --profile observability up -d --wait
    docker compose --profile observability ps
    curl --fail \\
      'http://127.0.0.1:3000/api/public/health?failIfDatabaseUnavailable=true'

Langfuse web and worker 4.26.0 run with PostgreSQL, ClickHouse, Redis, and MinIO.
The non-cluster ClickHouse setting is explicit, all images are pinned by digest,
and host ports are loopback-only. Versions and the controlled upgrade procedure
are recorded in [Container Versions](operations/container-versions.md).

Retrieve the generated bootstrap login without printing unrelated secrets:

    rg '^LANGFUSE_INIT_USER_(EMAIL|PASSWORD)=' .env

Stop the optional stack while keeping its named volumes:

    docker compose --profile observability stop

Do not run `docker compose down -v` unless every named volume is intentionally
being destroyed and its data is recoverable.

## llama.cpp for the RTX 5070 Ti

Build upstream llama.cpp for Blackwell compute capability 12.0:

    git clone https://github.com/ggml-org/llama.cpp.git
    cmake -S llama.cpp -B llama.cpp/build \
      -DGGML_CUDA=ON \
      -DCMAKE_CUDA_ARCHITECTURES=120 \
      -DGGML_CUDA_FA_ALL_QUANTS=ON
    cmake --build llama.cpp/build --config Release -j

Set `LLAMA_SERVER_BIN` in `.env` to the installed `llama-server` and
`MODEL_PATH` to an installed GGUF. For vision, also set `MMPROJ_PATH` to the
projector shipped for that exact model revision; leave it empty for text-only
runs. Start the server:

    set -a
    source .env
    set +a
    ./scripts/run_llama_server.sh

The launcher binds to loopback, requires the configured local API key, offloads
model layers, enables Flash Attention, uses one inference slot, uses the
embedded Jinja chat template, attaches the optional vision projector, and
defaults to an 8K context with reasoning disabled. Override `LLAMA_PARALLEL` or
`LLAMA_REASONING` only in a recorded experiment. No model is approved until
the relevant evaluation gates pass.

## Capability evaluation

Run the real OpenAI-compatible protocol probes:

    uv run --env-file .env money-tracker-eval capabilities

The suite checks strict nested structured output, forced tool calling,
tool-result continuation, and optional image input. It always writes ignored
machine-readable results under `artifacts/evals/`; an unavailable endpoint or
incomplete exact model/build metadata is an explicit blocking failure. Supply a
local image only for a model expected to support vision:

    uv run --env-file .env money-tracker-eval capabilities \\
      --image /private/path/to/fixture.png

Add `--publish-langfuse` only to copy redacted aggregate scores to the optional
local Langfuse instance. Image bytes, local paths, API keys, and private inputs
are not published.

## Application CLI and tracing

With a real inference server running:

    uv run --env-file .env money-tracker \\
      "Help me review this month's spending"

The generated environment points the SDK at local Langfuse. Removing either
project key disables tracing without disabling inference or database operation.

## Repeatable checks

Static checks and the service-independent suite:

    uv run ruff format --check src tests migrations
    uv run ruff check src tests migrations
    uv run mypy src
    uv run money-tracker-validate-docs
    uv run pytest -q
    uv run pytest -q tests/unit --cov=money_tracker --cov-report=term-missing
    docker compose --env-file .env.example config --quiet
    docker compose --env-file .env.example --profile observability config --quiet
    git diff --check

Real application PostgreSQL integration tests are explicit:

    RUN_POSTGRES_INTEGRATION_TESTS=1 \\
      uv run --env-file .env pytest -q \\
      tests/integration/money_tracker/persistence

Real inference checks remain opt-in and never substitute a mock service:

    RUN_CAPABILITY_INTEGRATION_TESTS=1 \\
      uv run --env-file .env pytest -q \\
      tests/integration/money_tracker/evaluations

    RUN_INTEGRATION_TESTS=1 \\
      INTEGRATION_LLM_BASE_URL=http://localhost:8080/v1 \\
      INTEGRATION_LLM_MODEL=local-model \\
      uv run pytest -q tests/integration/money_tracker/test_llm.py

    RUN_E2E_TESTS=1 \\
      E2E_LLM_BASE_URL=http://localhost:8080/v1 \\
      E2E_LLM_MODEL=local-model \\
      uv run pytest -q tests/e2e
