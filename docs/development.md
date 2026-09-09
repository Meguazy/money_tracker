# Development and Local Operations

## Prerequisites

- Python 3.12
- UV
- Docker with Docker Compose
- NVIDIA CUDA toolkit 12.8 or newer for the RTX 5070 Ti
- An upstream llama.cpp build and a compatible GGUF model for real inference
  and capability checks
- Restic, plus `pg_dump`/`pg_restore` from the same PostgreSQL major version as
  the pinned server, for authoritative backup and its restore drill. See
  [Backup and Restore](operations/backup-and-restore.md).

## Initial setup

Create the locked environment and generate uncommitted local credentials:

    uv sync --frozen
    ./scripts/generate_local_env.sh

UV owns `.venv` and `uv.lock`; do not install project packages with pip. The
generator creates a mode-600, Git-ignored `.env` with independent random
credentials and refuses to overwrite an existing file. Upgrade an existing
environment non-destructively after reviewing its permissions:

    ./scripts/upgrade_local_env.sh

The upgrade script appends only missing database, llama.cpp profile, receipt
archive, and backup settings, and never changes existing values.

## Application PostgreSQL and migrations

Start only the core database, then apply the foundation schemas and initialize
the persistent LangGraph checkpointer:

    docker compose up -d --wait application-postgres
    uv run --env-file .env money-tracker-db bootstrap
    uv run --env-file .env money-tracker-db current

The service binds to `127.0.0.1:5433`, owns an independent persistent volume,
and exposes the non-superuser `money_tracker` role to the application. The
first migration creates the `finance`, `messaging`, and `workflow` schema
boundaries. The Step 2 migrations add authoritative finance tables, deferred
reconciliation constraints, immutable audit/wallet rows, taxonomy bootstrap,
and expense idempotency/version state. Step 4 migration `20260909_0005` adds
normalized Telegram offsets/inbox/outbox, bounded processing and delivery
attempts, dead letters, pairing, owner bindings, and pending actions. It stores
no raw Bot API update or token-bearing URL.

The foundation downgrade is intentionally guarded and the finance migration
refuses destructive downgrade. The command below is now usable only when the
database is still at the empty Step 1 foundation revision; never use it after
Step 2 finance tables have been applied:

    uv run --env-file .env money-tracker-db downgrade-foundation \\
      --confirm-empty-foundation

Never use the guarded downgrade as a production rollback. The messaging
migration also refuses destructive downgrade. Restore a verified backup or
deploy forward when data exists.

Run the durable messaging repository integration suite only against the
guarded local test database:

    RUN_POSTGRES_INTEGRATION_TESTS=1 uv run --env-file .env pytest -q \
      tests/integration/money_tracker/persistence/test_messaging.py

The fixture accepts only `money_tracker_test` on loopback port 5433 and removes
only the schemas it owns.

## Bootstrapping the owner profile

Nothing works without a profile: every finance operation is scoped to one, and
V1 has a single owner. Create it once after the database is migrated, then
configure at least one funding source and mark it the default so an omitted
payment method resolves:

    uv run --env-file .env money-tracker-admin profile-init
    uv run --env-file .env money-tracker-admin funding-source-add \
      --profile-id PROFILE_UUID --name 'Carta' \
      --funding-class personal_funds --default
    uv run --env-file .env money-tracker-admin funding-source-add \
      --profile-id PROFILE_UUID --name 'Buoni pasto' \
      --funding-class benefit --balance-mode ledger

`profile-init` seeds the accepted taxonomy and prints the identifier the other
commands require. It refuses to create a second profile. Use `ledger` as the
balance mode only for a resource whose balance this application should track,
such as a meal-voucher wallet.

Recover the identifier at any time, along with the sources each profile has:

    uv run --env-file .env money-tracker-admin profile-list

`funding-source-add` refuses an unknown profile, a blank name, a name that
repeats an existing source, and a second default.

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

## Exercising text dispatch by hand

There is no bot yet: the Telegram channel, durable worker, and owner-facing
responses are Step 4. The `route` command nevertheless uses the complete Step 3
entry point. It builds one normalized work unit, invokes the root router, and,
when `--profile-id` is present, dispatches it to the selected text specialist.
It prints safe sorted JSON containing the typed route and outcome; it never
echoes the owner message.

Routing-only diagnostics need neither PostgreSQL nor a model for deterministic
input:

    uv run --env-file .env money-tracker-admin route \
      --with-image --minimum-confidence 0.8
    uv run --env-file .env money-tracker-admin route \
      --text '/status' --minimum-confidence 0.8

Free text reaches local inference, so start `scripts/run_llama_server.sh` first.
`--minimum-confidence` remains explicit; the EVD-004 production value is 0.5.
The following only reports routing because it supplies no profile:

    uv run --env-file .env money-tracker-admin route \
      --text 'Ho speso 12 euro per i croccantini dei gatti' \
      --minimum-confidence 0.8

Supply the bootstrapped profile to execute expense/refund, query/advice,
correction/undo, or category follow-up. The command opens the real PostgreSQL
checkpointer and application repository, and all writes use the existing
idempotent services:

    uv run --env-file .env money-tracker-admin route \
      --text 'Ho speso 50 euro al supermercato, 30 con i buoni pasto e 20 con la carta' \
      --minimum-confidence 0.5 \
      --profile-id PROFILE_UUID

    uv run --env-file .env money-tracker-admin route \
      --text 'Quanto ho speso per gli animali questo mese?' \
      --minimum-confidence 0.5 \
      --profile-id PROFILE_UUID

A free-text correction or undo needs the exact movement selected by trusted
runtime context. Without it, deterministic acceptance returns clarification:

    uv run --env-file .env money-tracker-admin route \
      --text 'Correggi la spesa a 15 euro' \
      --minimum-confidence 0.5 \
      --profile-id PROFILE_UUID \
      --latest-movement-id EXPENSE_UUID

When an expense outcome contains a `proposal_id`, dispatch has already opened
that proposal's persistent review checkpoint. Resume it with either a direct
J3 statement and the pending set:

    uv run --env-file .env money-tracker-admin route \
      --text 'Usa la categoria Cibo e accessori' \
      --minimum-confidence 0.5 \
      --profile-id PROFILE_UUID \
      --pending-review-id PROPOSAL_UUID

or an explicit callback. A callback supplies both the action reference carried
by the work unit and the repository-derived pending set:

    uv run --env-file .env money-tracker-admin route \
      --callback-action approve_category \
      --pending-action-id PROPOSAL_UUID \
      --pending-review-id PROPOSAL_UUID \
      --minimum-confidence 0.5 \
      --profile-id PROFILE_UUID

For modify/undo callbacks, `--pending-action-id` is the interface action and
`--latest-movement-id` supplies its trusted target for this development command.
The dispatcher treats neither as model output and never assumes that an action
identifier is itself a movement identifier.

Use `--source-reference` to replay a particular inbound work unit deliberately;
otherwise the command generates a fresh reference. The profile needs at least
one active default funding source. A new movement remains a draft: Step 3 has no
promoted auto-post subset. The result reports any movement/proposal/category
references, admitted read tool and grounded facts, or the stable clarification
reason.

The runtime default is the EVD-004 policy `explicit-finance-scope-v1`, which
acts after model parsing and before the same route registry and safety vetoes.
Return immediately to previous model-only behavior with:

    --read-selection-policy model-only-v0

## Authoritative backup

Point `MONEY_TRACKER_RESTIC_REPOSITORY` at the owner-selected external
repository, create the mode-600 recovery secret, then initialize once and back
up the authoritative database, receipt archive, and runtime configuration:

    uv run --env-file .env money-tracker-backup init
    uv run --env-file .env money-tracker-backup create
    uv run --env-file .env money-tracker-backup status

Every path setting must be absolute. The `create` command verifies database
identity and receipt hashes first, verifies the snapshot before applying
retention, and prints only safe JSON. `restore-drill` reconstructs a snapshot
into an explicitly named empty database and empty receipt directory. Full
configuration, guardrails, and the drill procedure are in
[Backup and Restore](operations/backup-and-restore.md).

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

### Where installed models live

Model weights are workstation-owned and never enter the repository, so only the
layout is recorded here. Installed candidates go under the XDG data directory,
beside the receipt archive that already lives there:

```text
${XDG_DATA_HOME:-$HOME/.local/share}/money-tracker/models/
├── text/<profile>/<weights>.gguf
├── vision/<profile>/<weights>.gguf
│                   └── mmproj-*.gguf
└── shared/<profile>/            weights exercised as both text and vision
```

The profile directory name matches the candidate identifier in the
[shortlist](experiments/local-model-shortlist-2026-09-01.md), so a `MODEL_PATH`
can be traced back to the exact screened artifact and its capability result.
Nothing enforces this layout: it is the convention the environment generator
writes and this guide assumes.

Set `LLAMA_SERVER_BIN` in `.env` to the installed `llama-server` and
`MODEL_PATH` to an installed GGUF. For vision, also set `MMPROJ_PATH` to the
projector shipped for that exact model revision; leave it empty for text-only
runs. Start the server:

    set -a
    source .env
    set +a
    ./scripts/run_llama_server.sh

To measure several candidates rather than run one, `scripts/sweep_models.sh`
owns the whole cycle instead: it resolves each candidate's weights from the
catalog, starts and stops the server around each one, restores the server it
found running, and runs the suites with `--model-profile` so that a forgotten
restart cannot go unnoticed. See
[Experiments](experiments/README.md).

The launcher reads the shell environment rather than `.env`, because it starts
outside the UV entry points that load `.env` themselves. It validates its
inputs before exec and names the exact fix for each failure, since an unset
variable and a leftover placeholder path are otherwise indistinguishable at the
llama.cpp boundary: an unset `MODEL_PATH` reports the sourcing command above, a
`MODEL_PATH` or `MMPROJ_PATH` that names no readable file reports that path, and
an unresolvable `LLAMA_SERVER_BIN` reports the build step.

The launcher binds to loopback, requires the configured local API key, offloads
model layers, enables Flash Attention, uses one inference slot, uses the
embedded Jinja chat template, attaches the optional vision projector, and
defaults to an 8K context with reasoning disabled. Override `LLAMA_PARALLEL` or
`LLAMA_REASONING` only in a recorded experiment. The Qwen3 14B text profile is
approved only with the exact EVD-004 configuration; vision and receipt roles
remain unapproved until their relevant gates pass.

## Capability evaluation

Run the real OpenAI-compatible protocol probes:

    uv run --env-file .env money-tracker-eval capabilities

The suite checks strict nested structured output, forced tool calling,
tool-result continuation, optional image input, leaving an optional field alone
when the message states nothing for it, and answering with a discriminated
union. The last two are screened under two boundary shapes each and take about
seventy seconds together, since they are dataset batteries rather than single
probes. It always writes ignored machine-readable results under
`artifacts/evals/`; an unavailable endpoint or incomplete exact model/build
metadata is an explicit blocking failure. A probe that ran and missed its
pre-registered threshold is reported as `failed` rather than as `error`. Supply
a local image only for a model expected to support vision:

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

Real encrypted backup and restore drills need Restic and matching PostgreSQL
client tools. They create and destroy only their own temporary repository,
staging, and receipt directories, and use the same isolated test database:

    RUN_POSTGRES_INTEGRATION_TESTS=1 \
      RUN_BACKUP_INTEGRATION_TESTS=1 \
      MONEY_TRACKER_TEST_RESTIC_BINARY=/usr/bin/restic \
      MONEY_TRACKER_TEST_PG_DUMP_BINARY=/usr/lib/postgresql/17/bin/pg_dump \
      MONEY_TRACKER_TEST_PG_RESTORE_BINARY=/usr/lib/postgresql/17/bin/pg_restore \
      uv run --env-file .env pytest -q \
      tests/integration/money_tracker/backup

Real root-router checkpoint tests need only the application database; the
deterministic half decides without any inference call:

    RUN_POSTGRES_INTEGRATION_TESTS=1 \
      uv run --env-file .env pytest -q \
      tests/integration/money_tracker/runtime

Add a running local model to also exercise the strict `json_schema` boundary
through the real endpoint:

    RUN_POSTGRES_INTEGRATION_TESTS=1 \
      RUN_INFERENCE_INTEGRATION_TESTS=1 \
      uv run --env-file .env pytest -q \
      tests/integration/money_tracker/runtime

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
