# Development and Local Operations

## Prerequisites

- Python 3.12
- Node.js 22 and npm 10 for the Step 5 frontend
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

The upgrade script appends only missing database, llama.cpp, Telegram, receipt,
web, logging, and backup settings, and never changes existing values. It never creates
or copies a bot token.

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
attempts, dead letters, pairing, owner bindings, and pending actions. Migration
`20260910_0006` adds manual outbox replay accounting while preserving each
logical response. Neither stores a raw Bot API update or token-bearing URL.

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

## Local dashboard and API

Build the locked frontend, then run the same-origin production service:

    cd frontend
    npm ci
    npm run typecheck
    npm test -- --run
    npm run build
    cd ..
    uv run --env-file .env money-tracker-api run

The current Step 5 slices serve `http://127.0.0.1:8000/`, the monthly overview,
`/analisi` with day, ISO-week, month, year, custom-range, merchant, and
prior-period analytics, `/movimenti` with the monthly transaction register, audited manual lifecycle
mutations with multi-allocation editing, `/budget` with the monthly budget
planner, `/categorie` with pending proposal approval, active-category reuse,
and cancellation, `/fonti` with source and wallet administration,
`/impostazioni` with fixed profile facts and default-source selection,
`/consigli` with promoted read-only advice, `/scontrini` with retained receipt metadata and its mandatory
review, `/messaggistica` with the durable Telegram queues and their two requeue
commands, and content-free health endpoints. They require exactly one
profile. `/scontrini` shows metadata only; importing an original is still
`money-tracker-admin receipt-store`, and no route returns receipt bytes.
`/messaggistica` shows counts, ages and reason codes only, and needs
`MONEY_TRACKER_TELEGRAM_ENVIRONMENT` to know which bot's rows are its own; the
API reads no Bot API token. The complete configuration, browser protections,
systemd procedure, and real PostgreSQL workflow tests are in
[Local Web Operations](operations/web.md).

Browser acceptance uses Playwright with the system Chrome. The first command
starts its own production API on port 8765 against `money_tracker_test` and the
local model; the second targets the installed service on port 8000 and restarts
it. Prerequisites and coverage are in
[Local Web Operations](operations/web.md#browser-acceptance):

    cd frontend
    npm run build
    npm run test:e2e
    npm run test:e2e:installed

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

## Exercising the Telegram gateway boundary

The accepted gateway and worker entry points expose their command surfaces with:

    uv run --env-file .env money-tracker-telegram --help
    uv run --env-file .env money-tracker-worker --help

Test and production use separate absolute token-file settings and distinct
persistence keys. The selected token file must be a regular owner-owned mode-0600
file. Pairing, production execution, acceptance evidence, and recovery are in
the [Telegram runbook](operations/telegram.md). The opt-in official-protocol
test never accepts the production environment.

## Exercising text dispatch by hand

The `route` command uses the same complete Step 3 dispatcher accepted through
Telegram in Step 4. It builds one normalized work unit, invokes the root router,
and, when `--profile-id` is present, dispatches it to the selected text specialist.
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

    grep -E '^LANGFUSE_INIT_USER_(EMAIL|PASSWORD)=' .env

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

The launcher binds to loopback, requires the configured local API key, which it
hands to llama.cpp through a pipe (`--api-key-file`) so it never appears in a
process listing (set a long random `LLM_API_KEY`; the generated placeholder is
too short to be redacted from traces), offloads
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
are not published, and a missing key or non-loopback `LANGFUSE_BASE_URL`
publishes nothing. Publish the committed evidence of a recorded promotion, so a
promoted configuration in a runtime trace leads to the runs that promoted it,
with:

    uv run --env-file .env money-tracker-eval publish-promotion --decision EVD-004

Trace retention, export, and verified deletion are the `money-tracker-traces`
command described in [LLM Tracing](operations/tracing.md). Its real-store test,
like the publication check below, needs only the observability profile and
takes about four minutes because Langfuse deletes asynchronously:

    RUN_LANGFUSE_INTEGRATION_TESTS=1 LOG_LEVEL=CRITICAL uv run --env-file .env \
      pytest -q tests/integration/money_tracker/tracing/test_trace_retention.py

The publication's real-store check needs only the observability profile:

    RUN_LANGFUSE_INTEGRATION_TESTS=1 LOG_LEVEL=CRITICAL uv run --env-file .env \
      pytest -q tests/integration/money_tracker/tracing/test_evaluation_publication.py

## Application CLI and tracing

With a real inference server running:

    uv run --env-file .env money-tracker \\
      "Help me review this month's spending"

Tracing is off unless `MONEY_TRACKER_TRACING_ENABLED` is true; the generated
Langfuse keys alone do not enable it, and a non-loopback `LANGFUSE_BASE_URL` is
refused. Switch it on for one command:

    MONEY_TRACKER_TRACING_ENABLED=true uv run --env-file .env \
      money-tracker-admin route --text 'Quanto ho speso questo mese?' \
      --minimum-confidence 0.5

Entry points inject the tracer from `money_tracker.tracing.open_tracer()` into
`LocalStructuredOutputModel` and the dispatcher; nothing else imports the
Langfuse SDK. Traces are full fidelity under AD-021 and belong in the local
store only. See [LLM Tracing](operations/tracing.md).

## Application logging

Every entry point emits one JSON object per line on standard error. `LOG_LEVEL`
and `LOG_FORMAT` are in the generated `.env`; override either for one command
without editing the file:

    LOG_FORMAT=text uv run --env-file .env money-tracker-worker process-once
    LOG_LEVEL=DEBUG uv run --env-file .env money-tracker-telegram poll-once

`text` renders the same fields as `key=value` pairs for a terminal, and `DEBUG`
adds the per-step records the default level omits. Machine-readable command
results stay on standard out, so a command can still be piped into `jq` while
its journal goes to standard error:

    uv run --env-file .env money-tracker-backup status 2>/dev/null | jq .

The event vocabulary, the meaning of each level, and the triage queries are in
[Security and Troubleshooting](operations/security-and-troubleshooting.md#reading-the-journal).
When adding code, follow the logging rules in `AGENTS.md`.

## Pull-request development workflow

Deliver every vertical slice through one branch and one pull request. Start
with `git switch main && git pull --ff-only` and create the slice branch before
editing. Roadmap work uses `<type>/step-<number>-<topic>`, with dotted steps
hyphenated; other work uses `<type>/<topic>`. Names are lower-case kebab-case
and Dependabot branches are exempt. Rebase on `main` instead of merging it,
review the diff before each commit, and remove merge, `fixup!`, and `squash!`
commits before review.

Allowed Conventional Commit types are `feat`, `fix`, `docs`, `test`,
`refactor`, `perf`, `build`, `ci`, and `chore`. A commit has one author and no
`Co-authored-by` trailer. There is no numeric subject-length rule: `AGENTS.md`
requires concise imperative subjects, but history establishes no such number.

Run applicable checks before each push. Open the PR with `gh pr create`,
complete its template, report the check state, and stop. Only the owner merges.
Start dependent work only after that merge and a fast-forward of local `main`.
Close a roadmap step in a later PR after owner acceptance, recording evidence
in its step document and updating `docs/roadmap.md`.

### Versioned Git hooks

Install hooks once per clone with `scripts/install_git_hooks.sh`. This
idempotent installer is the only approved way to set `core.hooksPath`.
Pre-commit refuses commits on `main` but permits detached HEAD during rebase.
Commit-msg invokes the packaged policy checker also intended for CI. Pre-push
rejects every remote `refs/heads/main` update, validates branch names, and runs:

    uv run ruff format --check src tests migrations
    uv run ruff check src tests migrations
    uv run mypy src
    uv run pytest -q
    uv run money-tracker-validate-docs
    git diff --check

The 2026-09-18 `main` baseline is 1,302 passed and 167 skipped in about 8
seconds. With the policy tests in the first AD-022 slice, the parallelized
complete hook measured 8.75 seconds with 1,341 passed and 167 skipped
(+39 passed, no skipped delta). The CI helpers of the second slice raise the
baseline to 1,357 passed and 167 skipped (+16 passed), and the guard tests of
the third slice to 1,432 passed and 167 skipped (+75 passed). Record it again
when the baseline changes.

Hooks also run in linked worktrees, where Git exports repository-local
variables such as `GIT_DIR` to them. The development-policy tests remove those
variables before building their temporary repositories; without that, a hook
run in a worktree committed into, branched, and set `core.bare` on the real
repository. Hooks can be bypassed; the `main` ruleset below is the
authoritative boundary.

### Claude Code guardrails

`.claude/settings.json` denies, as glob patterns over each simple command,
`gh pr merge`, `gh repo edit`, `gh repo delete`, `--no-verify` and a
standalone `-n` for `git commit` and `git push`, pushes that name `main` as an
argument or refspec destination, changing `core.hooksPath`, printing `.env`
files, and `gh api` calls that carry an option on a ruleset or branch-protection
endpoint. Its `PreToolUse` hook, `money-tracker-guard-claude-bash`, is the
precise layer: it tokenizes the command like a shell, follows `bash -c`,
`sh -c`, and `eval`, and treats quoted messages and heredoc bodies as data. It
also refuses `-n` inside option clusters such as `-nm`,
`git -c core.hooksPath=...`, a bare `git push` or `HEAD` refspec from `main`,
`--all` and `--mirror`, and every non-GET `gh api` call on rulesets, branch
protection, or the repository settings endpoint. Branch names that merely
contain `main`, such as `feat/step-6-domain-model` or `ci/protect-main`, are
not refused.

Read rulesets with a plain `gh api <endpoint>` or with
`gh api repos/Meguazy/money_tracker/rules/branches/main`; the glob layer
refuses any option on a ruleset endpoint. A commit message containing a
standalone ` -n ` word still matches the glob layer, so write such a message
to a file and commit with `git commit -F <file>`. The guard's text checks for
`gh pr merge`, `--no-verify`, `core.hooksPath`, and `.env` printing apply to
the whole command, heredocs included, because a heredoc fed to a shell runs.

### Protected `main`

`.github/rulesets/main.json` is the versioned copy of the repository ruleset
`protect-main` (id 23686462), applied on 2026-09-19 after the owner confirmed
it. It targets the default branch and has no bypass actors, so it binds
administrators too:

- Changes reach `main` only through a pull request. Zero approvals are
  required, every review conversation must be resolved, and merge, squash, and
  rebase merges remain available.
- `ci-gate` and `pr-metadata` must succeed, reported by GitHub Actions
  (integration 15368), on a head that is up to date with `main`.
- Force pushes to `main` and its deletion are refused.

GitHub adds `require_extra_approval_for_unattributed_changes`, which only asks
for one more approval on an unattributed Copilot pull request and has no effect
with zero required approvals; the file declares it so that it equals the live
ruleset. Verify the effective rules with
`gh api repos/Meguazy/money_tracker/rules/branches/main`. Only the owner
changes the ruleset: change the file in a pull request and, after the merge,
apply it with
`gh api --method PUT repos/Meguazy/money_tracker/rulesets/23686462 --input .github/rulesets/main.json`.

A pull request is *behind* when `main` has moved since its head was built; the
strict policy then blocks the merge until the head contains `main`. Rebase
rather than merging `main` into the branch:

    git fetch origin
    git rebase origin/main
    git push --force-with-lease origin <branch>

The owner can instead choose **Update branch → Update with rebase** on the pull
request. Either way every check runs again, so update a branch only when it is
about to be merged. Merged head branches are deleted automatically once the
owner enables that repository setting; otherwise delete them with
`git push origin --delete <branch>`.

If GitHub Actions cannot run, or a required job is broken by its environment,
nothing can merge. Fix forward in a pull request; only the owner may relax
enforcement, recording why, and must restore it afterwards.

### Local and CI gate correspondence

`.github/workflows/ci.yml` runs on every pull request, on every push to `main`,
and weekly on Sunday at 02:17 UTC. It has no path filter, so its aggregate
check always reports. Each job mirrors a local gate:

| Local gate | CI job | CI specifics |
| --- | --- | --- |
| Ruff format and lint, mypy, `uv build`, docs validation, `git diff --check` | `python-static` | `uv sync --locked`; whitespace is checked over the PR or pushed range |
| `uv run pytest -q` and unit branch coverage | `python-tests` | `--cov-fail-under=73`, the measured 73.29 percent rounded down; the floor may only rise |
| `RUN_POSTGRES_INTEGRATION_TESTS=1 uv run --env-file .env pytest -q` | `postgres-integration` | `application-postgres` from `compose.yaml`; the suite includes the migration round trip |
| Restic and PostgreSQL 17 backup and restore drill | `backup-drill` | Restic 0.18.1 release binary checked against a pinned SHA-256; `postgresql-client-17` from PGDG after checking the signing-key fingerprint |
| `npm ci`, `npm run typecheck`, `npm run test:ci`, `npm run build` | `frontend` | Node from `frontend/.nvmrc`; Vitest writes JUnit |
| `npm run test:e2e:ci` | `e2e-browser` | Playwright's bundled Chromium in the runner's UTC zone, which exercises the Europe/Rome date-time conversion; only the `@local-model` case is excluded |
| Both Compose profiles, image digests, `systemd-analyze verify` | `compose-and-deploy` | Units are rendered against the checkout, and the synced package provides every `ExecStart` executable |
| ShellCheck over `scripts/`, `.githooks/`, and `docker/`; actionlint; zizmor | `shell-and-workflows` | actionlint 1.7.12 with a pinned SHA-256; zizmor 1.30.1 offline over workflows and Dependabot |
| Gitleaks, `pip-audit`, `npm audit --audit-level=high` | `security` | gitleaks 8.30.1 scans `base..head` for a PR and the full history for `main` and the schedule; `pip-audit` covers every locked group |

The full PostgreSQL suite runs at whatever `LOG_LEVEL` the `.env` or shell
sets: the Telegram and worker CLI tests that compare standard error exactly pin
their own level. Evidence recorded before 2026-09-19 used
`LOG_LEVEL=CRITICAL` for the same reason. Every service job generates a
disposable `.env`, waits until PostgreSQL accepts TCP for `money_tracker_test`,
and fails when its JUnit report shows no executed test or a skip citing the
flag the job sets.

`ci-gate` needs all nine jobs, runs even when one fails, and succeeds only when
every result is `success`; a failure, cancellation, or skip turns it red. It is
the only required check of `ci`. `.github/workflows/pr-metadata.yml` is the
second required check: it validates the PR title, the head branch, every commit
in `base..head`, and the template's level-two sections with
`money-tracker-validate-git-policy`, and exempts Dependabot only from the
template sections.

Workflows hold only `contents: read`, check out without persisted credentials,
and pin every action by full commit SHA with its exact version in a comment. No
workflow uses `pull_request_target` or a repository secret. A newer run on the
same PR cancels the older one; runs on `main` and scheduled runs are never
cancelled. The workflows pin uv 0.8.0, the toolchain that writes `uv.lock`;
change both together.

### CI helper commands

- `money-tracker-ci-report --label NAME [--summary-file FILE] [--service]
  [--forbid-skip-reason TEXT ...] JUNIT...` counts the passed, skipped, failed,
  and error cases in JUnit reports, prints one line, and appends a row to the
  job summary. It exits non-zero for an unreadable or empty report. With
  `--service` it also fails when no test executed or when a skip reason
  contains a forbidden fragment, such as the enabling flag the job sets.
- `money-tracker-redact-ci-log --env-file .env --input RAW --output SAFE`
  replaces every credential-named or generated hexadecimal value of the
  disposable `.env`, then every token-shaped run, before failed evidence is
  uploaded. It never prints the evidence.
- `money-tracker-validate-git-policy pull-request-body FILE` names each
  required section of `.github/pull_request_template.md` missing from a PR
  body.

### Reading a red run

1. `gh pr checks <number> --watch` lists every job. Start from the first failed
   job; `ci-gate` only aggregates.
2. `gh run view <run-id> --log-failed` prints the failed steps. The run summary
   page shows every job result and the passed, skipped, failed, and error
   counts of each suite.
3. A failed job uploads its evidence for seven days as
   `<job>-<run-id>`: JUnit and coverage for `python-tests`, redacted service
   logs and JUnit for `postgres-integration`, `backup-drill`, and
   `langfuse-integration`, Vitest JUnit for `frontend`, and Playwright traces,
   screenshots, video, and the redacted PostgreSQL log for `e2e-browser`.
   Download one with `gh run download <run-id> --name <artifact>`. Evidence
   holds only synthetic fixtures and disposable credentials, which are
   redacted.
4. Reproduce with the matching local command, fix the cause, and push grouped
   corrections once; every push consumes minutes. `service suite executed zero
   tests` or `skipped tests because ... was absent` means the job's wiring is
   broken, not the product. Never skip tests, lower thresholds, or add an
   exception merely to turn a check green; document any justified exception
   and its expiry where it is configured.
5. `pr-metadata` reruns when the title or body is edited. A commit violation
   needs a rebase that rewrites the offending commit.

### Gates that remain local

Inference, capability, advice HTTP, model-backed Langfuse, the Playwright
`@local-model` case, `npm run test:e2e:installed`, and real-bot Telegram tests
remain local because they require the promoted GPU model or owner-controlled
services. A slice touching one of those boundaries must run its applicable gate
without mocks and report the exact command and passed/skipped counts. Run local
Playwright E2E and PostgreSQL pytest sequentially because both own
`money_tracker_test`. Model-visible changes also require the evaluation gates;
EVD-004 remains the baseline. The backup drill runs in CI; a workstation without
Restic and PostgreSQL 17 client tools cannot run it, and the CI job is then its
evidence.

### Langfuse workflow

`.github/workflows/langfuse.yml` starts the whole `observability` profile with
disposable credentials and runs the Langfuse gates that need no model,
`tests/integration/money_tracker/tracing/test_evaluation_publication.py` and
`test_trace_retention.py`. It is not a required check. GitHub runs a workflow
triggered only by `workflow_dispatch` and `schedule` from the default branch, so
it cannot run on the PR that introduces it. After merge it runs every Saturday
at 03:43 UTC and on demand with
`gh workflow run langfuse-integration --ref <branch>`. Its first scheduled run
(2026-09-19, run 35431594999) passed 2 tests with none skipped in 379 seconds:
101 to start the profile and 254 for the tests. GitHub started it several hours
after the scheduled time, which scheduled workflows allow.

### Dependabot

`.github/dependabot.yml` proposes grouped updates every Monday morning
(Europe/Rome) for GitHub Actions, uv, npm in `frontend/`, and Compose images,
each with a seven-day cooldown. GitHub Actions share one group. For uv and npm,
minor and patch updates form one group and major updates another, so a
breaking major cannot hold the routine updates back; each group is at most
one pull request a week. Compose images receive digest and minor updates only:
CI starts them on empty volumes, so a green run cannot show that a new major,
such as PostgreSQL 18, opens the workstation's existing data. Major image
upgrades follow the controlled upgrade in
[Container Versions](operations/container-versions.md#controlled-upgrade).
Commit messages start with `ci(deps)`,
`build(deps)`, or `build(deps-dev)`, which the Git policy accepts; Dependabot
branches are exempt from the branch rule and its PRs from the template
sections. A Dependabot PR is reviewed and merged by the owner like any other.
Image proposals follow
[Container Versions](operations/container-versions.md#dependabot-proposals).
Dependabot does not update versions pinned inside workflow steps (uv,
actionlint, gitleaks, zizmor, Restic) or `frontend/.nvmrc`; update those
deliberately together with their checksums. Dependabot alerts and security
updates are repository settings and remain off until the owner enables them.

### GitHub Actions minute budget

GitHub bills each job of a private repository rounded up to a whole minute and
includes 2,000 Linux minutes a month on Free and 3,000 on Pro. The owner
confirmed on 2026-09-19 that the account has GitHub Student, whose Developer
Pack includes Pro while the student status lasts: the allowance is 3,000
minutes, and it falls to 2,000 if the benefit lapses.

The first `ci` run (2026-09-18, cold caches) billed:

| Job | Duration | Billed minutes |
| --- | ---: | ---: |
| `python-static` | 26 s | 1 |
| `python-tests` | 50 s | 1 |
| `postgres-integration` | 91 s | 2 |
| `backup-drill` | 76 s | 2 |
| `frontend` | 84 s | 2 |
| `e2e-browser` | 179 s | 3 |
| `compose-and-deploy` | 13 s | 1 |
| `shell-and-workflows` | 17 s | 1 |
| `security` | 33 s | 1 |
| `ci-gate` | 4 s | 1 |
| `pr-metadata` | 14 s | 1 |

A PR push therefore costs about 16 minutes and a `main` push about 15. The
weekly Langfuse run bills 7 minutes. The jobs that create Dependabot pull
requests do not count toward the allowance, but the CI those pull requests
start does. The projection assumes three owner slices a week with three pushes
each, three Dependabot PRs a week with one push each out of at most six groups,
a `main` push for every merge, and the weekly scheduled `ci` and Langfuse runs:

| Source | Minutes per week | Minutes per month |
| --- | ---: | ---: |
| Slice PR pushes (9 x 16) | 144 | 624 |
| Dependabot PR pushes (3 x 16) | 48 | 208 |
| `main` pushes after merges (6 x 15) | 90 | 390 |
| Scheduled `ci` and Langfuse (15 + 7) | 22 | 95 |
| **Total** | **304** | **1,317** |

That is 44 percent of 3,000 minutes, or 66 percent of 2,000. Metadata-only
reruns after editing a PR cost one minute each. Record new measurements when
the job set changes. At 70 percent of the allowance, propose caching,
consolidation, or schedule changes without removing a blocking control.

## Repeatable checks

Static checks and the service-independent suite:

    uv run ruff format --check src tests migrations
    uv run ruff check src tests migrations
    uv run mypy src
    uv run money-tracker-validate-docs
    uv run pytest -q
    uv run pytest -q tests/unit --cov=money_tracker --cov-report=term-missing \
      --cov-fail-under=73
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

Real Langfuse tracing checks need the observability profile, the local model,
and the isolated test database. They write to the `integration-test` Langfuse
environment and delete the traces they create:

    RUN_POSTGRES_INTEGRATION_TESTS=1 RUN_LANGFUSE_INTEGRATION_TESTS=1 \
      LOG_LEVEL=CRITICAL uv run --env-file .env pytest -q \
      tests/integration/money_tracker/tracing

The channel tests in the same directory drive the production gateway, worker,
and dispatcher composition and answer only the Bot API in-process; the advice
tests start the production `money-tracker-api run` process on a free loopback
port and need the compiled `frontend/dist`. The browser E2E runtime starts its
API with tracing forced off. Every other
test forces tracing off, so a switch set in `.env` never exports synthetic test
messages into the owner's store.

The degradation tests there run the gateway and worker, the production API, and
the production worker service in processes of their own, because the Langfuse
SDK keeps one client per project key per process, against a closed port and a
fault-injection proxy that holds or refuses every request; they never stop a
container.

Pytest runs with `--tb=short` from `pyproject.toml`: real-service fixtures hold
credential-bearing URLs and key pairs, and a long traceback prints a failing
test's arguments. Do not add `--tb=long` or `--showlocals` to a real-service
run.

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
