# Money Tracker Architecture

This is the arc42 architecture source of truth for Money Tracker. It documents
both the executable scaffold and the accepted target architecture. Components
marked `planned` do not exist yet; their inclusion records a decision, not an
implementation claim.

Read the [product baseline](../product/README.md), this document, the
[architecture decision index](decisions/README.md), and the
[roadmap](../roadmap.md) together before changing an architectural boundary.

## 1. Introduction and Goals

### Product goal

Money Tracker will be a personal, local-first expense assistant. The owner will
primarily send Italian natural-language messages, and after the personal V1
release receipt images, through a private Telegram bot. The system will
extract, categorize, persist, and analyze expenses and refunds. A local web
dashboard will provide review, corrections, budgets, wallet balances, charts,
and operational controls.

The approved V1 scope is defined in
[Vision and Requirements](../product/vision-and-requirements.md). Steps 0–4 are
complete. EVD-004 promotes Qwen3 14B, router v6, extraction v6, category-fit
v6, tool-selection v5, routing confidence 0.5, and
`explicit-finance-scope-v1`. The durable Telegram runtime passed owner-driven
workflow, authorization, latency, fault, and reboot acceptance on 2026-09-11.

Step 5 is complete. Its implemented slices provide the loopback FastAPI host,
monthly overview, calendar analytics, and transaction register, audited manual
create/correct/post/cancel workflows, the multi-allocation editor for both
reconciling dimensions, the monthly budget planner, versioned category and funding-source/wallet administration, owner review of pending
category proposals, consultation and mandatory review of retained receipt
metadata, browser inspection and pinned requeue of the durable messaging queues, safe profile-settings consultation and default-source selection,
compiled React shell, browser security policy, host unit, and the promoted
read-only advice surface. On 2026-09-16 real-browser E2E against the production
API, PostgreSQL, and the promoted local model, automated WCAG 2.0/2.1 A/AA
checks, and acceptance of the installed user service passed. Step 5.5 is
complete (2026-09-18): [AD-021](decisions/AD-021-full-fidelity-llm-traces.md)
makes runtime traces full fidelity, and every model-calling entry point is
traced. Step 7, release hardening and the personal V1 release, is in progress. On
2026-09-19 the owner deferred receipt extraction, Step 6, after the personal V1
release: V1 keeps the implemented private archive and its metadata review, and
every component below marked as part of the receipt workflow is planned for
that later step rather than for V1.

### Quality goals

| Priority | Goal | Meaning |
| --- | --- | --- |
| 1 | Financial correctness | A retry, resume, or duplicate message must never duplicate a financial write; monetary and allocation invariants are deterministic. |
| 2 | Privacy | Finance storage and AI processing remain local; Telegram is the sole approved external data boundary. |
| 3 | Recoverability | Authoritative data and retained receipt images have encrypted, tested backups. |
| 4 | Explainability | Every categorization, proposal, correction, tool result, and advice figure can be traced to source data and a versioned decision. |
| 5 | Replaceability | Model, receipt extractor, transport, persistence, and UI adapters depend on narrow contracts. |
| 6 | Testability | Deterministic tests and versioned evaluations cover behavior and real external protocols at the appropriate boundary. |
| 7 | Operability | Core services recover automatically after workstation startup and expose actionable health and failure state. |

### Stakeholders

- The owner supplies financial information, approves taxonomy changes, reviews
  receipts, configures budgets and wallets, and operates the workstation.
- Developers and coding agents implement roadmap steps without reopening
  accepted decisions implicitly.
- The operator installs models, Docker services, systemd units, Telegram
  credentials, and backup destinations.

## 2. Architecture Constraints

### Accepted product constraints

- The V1 is single-owner but application records retain a stable profile scope.
- User-facing conversation and UI are Italian; code and technical documents are
  English.
- EUR is the sole operational currency in V1. Contracts retain an ISO currency
  field for future extension.
- Calendar behavior uses `Europe/Rome`; a reporting week starts on Monday.
- The dashboard binds to the workstation loopback interface only.
- The workstation may be powered off. Telegram's unconsumed update backlog is
  accepted as recoverable for at most 24 hours; no cloud relay is planned.
- Receipt images are retained locally until the owner deletes them.
- Advice is on demand. Proactive coaching and budget notifications are outside
  V1.

### Technology and repository constraints

- Python 3.12 and UV define the application runtime and dependency lock.
- LangChain and LangGraph define model and orchestration interfaces.
- Local NVIDIA inference runs out of process behind an OpenAI-compatible HTTP
  contract.
- The target workstation is Ubuntu Linux with an RTX 5070 Ti and 16 GB VRAM.
- PostgreSQL will own authoritative finance data, durable messaging, and graph
  checkpoints; Langfuse stores remain separate.
- React and TypeScript implement the first dashboard slice; FastAPI exposes and
  serves it locally. The rest of the control plane remains Step 5 work.
- Secrets remain in generated, mode-restricted, Git-ignored configuration.
- Python uses Google-style docstrings. Tests and documentation obey `AGENTS.md`.

## 3. Context and Scope

### Business context: target

```text
                         receipt photos and messages
Owner <------------------------------------------------> Telegram Bot API
  |                                                               |
  | localhost dashboard                                           | long polling
  v                                                               v
Web browser -----------------------> Money Tracker <---------- Telegram gateway
                                          |
                                          | local inference and extraction
                                          v
                                Local model/OCR/VLM services
                                          |
                                          | optional local traces/evaluations
                                          v
                                      Langfuse
```

Telegram is external and stores bot-chat data according to Telegram's own
policies. All other finance processing and persistence are local. The bot API
boundary is not end-to-end encrypted and must not be described as local storage.

### Technical interfaces

| Boundary | Protocol | Data | Status |
| --- | --- | --- | --- |
| Owner to CLI | Process arguments/stdin/stdout | One prompt and response | Implemented scaffold |
| Owner to administration CLI | Process arguments/stdout plus private input or output file | Profile-scoped receipt import and portable finance export with safe JSON results | Implemented receipt-store and Step 7 finance-export commands |
| Application to inference | OpenAI-compatible HTTP | Messages, schemas, tool calls, model settings | Implemented basic chat |
| Application to Langfuse | SDK over loopback HTTP/OTLP | Full-fidelity dispatch, graph, node, and model-call observations under AD-021, minus credentials, forbidden URLs, archive paths, and bytes; aggregate capability scores | Tracing boundary implemented behind an explicit switch; `money-tracker-admin route` traced, services not yet |
| Telegram gateway to Bot API | HTTPS long polling and send methods | Updates, callback queries, messages, and bounded file metadata; no Step 4 file download | Implemented and owner-accepted against the real private test bot |
| Browser to application API | Same-origin HTTP JSON | Monthly overview, calendar analytics, transaction register, manual lifecycle, budget planning, category management/review, funding-source/wallet administration, receipt metadata review, messaging operations, safe profile settings, promoted read-only advice, CSRF, and health implemented; receipt extraction remains planned. No route returns receipt bytes or its archive key | Step 5 slices implemented |
| Application to PostgreSQL | PostgreSQL protocol | Checkpoints, authoritative finance aggregates, normalized messaging offsets/inbox/outbox, pairing, actions, retries, and dead letters | Implemented foundation, finance repositories, and Step 4 messaging persistence |
| Application to receipt archive | Private host filesystem | Fully decoded original JPEG/PNG/WebP keyed and verified by content hash; no derivatives | Local adapter/admin CLI and encrypted backup/restore implemented; API/worker media wiring and deletion planned. The control plane reads metadata only and never opens this boundary |
| Backup process to repository | Restic repository protocol | Database dump, receipts and recovery configuration | Implemented with source semantic preflight and isolated restore verification; target repository and persistent daily timer installed, enabled, and timer-trigger accepted; candidate repair of legacy source drift awaits deployment, and multi-run retention observation remains open |

### Out of scope for V1

- Income tracking, complete asset/liability accounts, transfers, bank sync, and
  statement reconciliation.
- Historical import from another application.
- Household or multiuser access.
- LAN or remote dashboard access.
- Cloud LLM, OCR, vision, trace, or finance storage.
- A cloud relay for Telegram updates older than 24 hours.
- Automatic posting of extracted receipts.
- Receipt image download, extraction, preview, line editing, and media
  deletion: deferred on 2026-09-19 to Step 6, after the personal V1 release.
  V1 keeps the private archive, its backup, and its metadata review.

## 4. Solution Strategy

- Preserve the local OpenAI-compatible inference adapter and benchmark local
  models instead of selecting one by reputation.
- Put finance rules in framework-independent domain and application services.
- Use one dedicated application PostgreSQL for transactions, durable inbox and
  outbox, idempotency, audit, and LangGraph checkpoints, with separate logical
  ownership inside it.
- Use a typed root graph that routes to bounded specialist workflows. Keep
  writes deterministic and expose only read tools to query/advice agents.
- Model a purchase once, then reconcile both category allocations and payment
  allocations against its total.
- Classify by spending purpose through a controlled two-level taxonomy and
  require explicit approval for every new node.
- Ingest Telegram updates before acknowledging them and process the durable
  inbox sequentially.
- Select receipt extraction through a local, versioned OCR/VLM experiment and
  require owner preview for every receipt. The workflow is deferred after the
  personal V1 release.
- Serve a React dashboard through same-origin FastAPI on localhost.
- Run host application processes under systemd, keep core PostgreSQL in Docker,
  and start Langfuse only for development and evaluation.
- Promote prompts, models, tools, and extractors only through the evaluation
  gates in [the experiment documentation](../experiments/README.md).

## 5. Building-Block View

### Level 1: target containers and processes

```text
Money Tracker workstation
├── Host processes
│   ├── FastAPI application and compiled React UI        [overview/analytics/movements/budgets/categories/funding sources/receipts/messaging/settings implemented]
│   ├── Telegram gateway                                [implemented; unit template checked in]
│   ├── Sequential workflow worker                      [implemented; unit template checked in]
│   │   ├── typed dispatcher, router, and text graphs  [implemented library]
│   │   └── Italian response renderer                  [implemented library]
│   └── llama.cpp/OpenAI-compatible inference           [scaffolded]
├── Private host data
│   ├── Receipt archive adapter                         [implemented, admin CLI wired]
│   ├── Evaluation data and artifacts                   [implemented foundation]
│   └── Restic staging/configuration and daily timer    [implemented; installed/enabled, timer-trigger accepted]
└── Docker Compose
    ├── Application PostgreSQL                          [implemented finance and messaging core]
    └── Langfuse, PostgreSQL, ClickHouse, Redis, MinIO  [implemented optional]
```

### Current Python scaffold

| Component | Responsibility | Interface/dependencies | Data ownership | Deployment/security |
| --- | --- | --- | --- | --- |
| `cli` | Parse one prompt, invoke the graph, print output, flush traces | Python entry point; graph and `tracing` | No persistent data | Host process; user terminal; traced only when the tracing switch is on |
| `config` | Convert environment variables into immutable model settings | Process environment | Runtime configuration | Secrets are not returned or logged |
| `development` | Enforce branch, commit, PR-range, PR-template, and Claude Bash policy from one tested implementation | Git subprocess boundary, command-line entry points, versioned Git hooks, Claude Code `PreToolUse`, and the `pr-metadata` workflow | No persistent data; policy constants come from `AGENTS.md` and the PR template | Developer checkout and GitHub-hosted runners; the Claude guard parses commands like a shell and refuses hook bypasses, pushes to `main`, and agent writes to rulesets, branch protection, or repository settings; intentional blocks exit 2, internal guard errors fail open, and no command payload is logged |
| `development.ci_reporting` | Count JUnit outcomes into the job summary and fail a real-service job that executed nothing or skipped for its own missing flag | `money-tracker-ci-report`; JUnit XML from pytest, Vitest, and Playwright | No persistent data | GitHub-hosted runners; reads only reports the job produced; logs suite labels and counts, never test content |
| `development.ci_redaction` | Remove generated credentials and token-shaped runs from failed-job evidence before upload | `money-tracker-redact-ci-log`; the disposable `.env` and one evidence file; `logging_safety` | No persistent data; writes one redacted copy | GitHub-hosted runners; never prints evidence; logs only sizes and an error type |
| `main` ruleset | Admit a change to `main` only through a pull request whose `ci-gate` and `pr-metadata` succeeded on a head up to date with `main` and whose conversations are resolved; refuse force pushes and deletion | `.github/rulesets/main.json`, applied by the owner through the REST rulesets API | Repository ruleset `protect-main` (id 23686462) | GitHub; no bypass actors, so it binds administrators; agents are denied writes to it |
| GitHub Actions workflows | Run the hosted gates, aggregate them into `ci-gate`, validate PR metadata, and run the model-free Langfuse gates on a schedule | `.github/workflows/`: `ci.yml`, `pr-metadata.yml`, `langfuse.yml`; `.github/dependabot.yml` | Seven-day failure artifacts only | GitHub-hosted Ubuntu runners; `contents: read`, SHA-pinned actions, no repository secrets, disposable credentials |
| `graph` | Define the initial one-node assistant | LangGraph and `BaseChatModel` | In-memory messages for one invocation | Stateless host process |
| `llm` | Construct an OpenAI-compatible chat adapter | LangChain `ChatOpenAI` | No persistent data | Loopback endpoint by default |
| `tracing` | Own the tracing port injected at entry points, the disabled tracer, the explicit switch and loopback-only settings, the stable trace names, and `open_tracer`, which can resolve model identity in the background for a long-running service | Framework-neutral port; `tracing.langfuse` only when enabled; no domain, application, or workflow dependency | No persistent data | Every entry point that executes a model boundary or a stage of a traced work unit; misconfiguration disables tracing with an `ERROR` record and never stops the process |
| `tracing.langfuse` | Record seeded, named stage spans, dispatch spans, graph and node runs, and one generation per model request in the local Langfuse; publish evaluation evidence | Langfuse SDK and its attribute propagation, its LangChain callback with model runs ignored, and a process-owned OpenTelemetry provider without an exit handler | Spans buffered until the background export or the bounded flush | Only module importing the SDK; loaded lazily; no exception text reaches an SDK context manager; export runs on the SDK's background thread; every flush runs on a helper thread waited for at most the export timeout, and the SDK's unbounded exit handler is released at shutdown |
| `tracing.identity` | Name the serving model artifact with the capability report's fields | llama.cpp `/props` over loopback, reused for 60 s; `evaluations.metadata` assembly | Private revision-keyed digest cache in the unit's `CacheDirectory` or `$XDG_CACHE_HOME/money-tracker` | Digests measured once per file revision, inline for a command and on a background thread for a service, whose calls during a measurement carry `digest_pending`; no path enters a trace |
| `tracing.retention` | Plan, privately export, and delete traces of one environment by age or identifier, keeping a trace whole until its most recent observation passes the cutoff, and verify each deletion by re-reading; read content-free observation times for the profile | Langfuse public observations and trace-deletion API over loopback with `for_command` settings; no SDK | No state; export files are new owner-only files outside the checkout that the operator owns | Operator-invoked through `money-tracker-traces` (`tracing.cli`) or confirmed age deletion from the persistent daily user timer; 30-day default by owner decision; no automatic export; failures are reason codes without URL, key, or content |
| `tracing.profile` | Turn one window's observation names and times into per-name duration distributions and a wait/processing/delivery decomposition of each traced Telegram update | `ObservationTime` values from `TraceStore.timings`, the stage-name constants in `tracing.ports`, and `money_tracker.latency` | No state | Operator-invoked through `money-tracker-traces profile`; the reader requests only the `core` and `basic` field groups, so no input, output, or metadata is fetched or printed |
| `latency` | Own the nearest-rank p50/p95/maximum convention of NFR-PER-001 and its JSON rendering | Standard library only | No state | Shared by the Telegram latency report and the trace profile, so a p95 from the database and one from the trace store mean the same thing |
| `tracing.redaction` | Remove credentials, database and credential-bearing URLs, Telegram URLs, token shapes, archive paths, and bytes from every observation value | Configured secret literals and private roots from the environment; `logging_safety` token patterns | No state | Last line of defence applied by the SDK; components keep forbidden values out by construction |
| `structured_logging` | Configure process logging once, render each record as one JSON object of event name plus named fields, bind per-operation correlation identifiers, and route uncaught exceptions on any thread through logging | Standard library `logging` and `contextvars`; the redaction primitives in `logging_safety`; no domain, repository, or transport import | No persistent data; the journal is diagnostic and is neither authoritative nor backed up | Every host process and CLI; writes to standard error so machine-readable command output on standard out stays usable; credentials are filtered from messages, fields, and tracebacks, and owner content never enters a field |
| `logging_safety` | Own the credential redaction contract and mute the HTTP loggers that would print a token-bearing URL | Standard library `logging` and `re` only | No state | Imported by `structured_logging` and usable by anything that logs |
| `persistence` | Apply versioned migrations, bootstrap persistent LangGraph checkpoints, and implement finance/messaging repositories | Alembic, psycopg, PostgreSQL checkpointer over a checked `psycopg_pool` pool that replaces a session the server ended | Foundation schemas, workflow checkpoints, finance aggregates, and durable messaging state | Host administration CLI and shared host library; least-privilege loopback database role |
| `persistence.export` | Project one profile's authoritative finance state into a versioned portable archive | psycopg read-only repeatable-read transaction; explicit revision-bound projections | No new authoritative state; writes a manifest and typed JSON Lines records to an owner-selected file | Operator-only host CLI; new mode-600 file in an owner-only directory outside the checkout; excludes receipt bytes, checkpoints, messaging state, and credentials |
| `persistence.retention` | Delete concluded runtime owner content older than the retention window: consumed controls, sent messages no control still needs, completed inbox items, and checkpoint threads that can no longer be resumed | psycopg in one transaction with a five-second lock timeout; exact supported schema revision; the `workflows.threads` identity contract; the LangGraph checkpoint tables | No state of its own; deletes messaging and workflow rows, never finance, audit, idempotency records, offsets, owner bindings, pairing codes, or receipts | Operator-invoked through `money-tracker-retention` (`retention_cli`) or confirmed from the persistent daily user timer; 30-day default by owner decision ([AD-024](decisions/AD-024-runtime-content-retention.md)); a dry run rolls the same statements back; output and journal carry counts only |
| `workflows.threads` | Define persistent LangGraph thread identity: the graph family, the reference a thread is keyed by, and which families end with their work unit | Standard library only | No state; the namespace strings are stored in every checkpoint and must never change | Written by the dispatcher and read back by runtime retention; a family missing from the contract is never deleted |
| `domain` | Enforce exact money, expense/refund, dual-allocation, taxonomy, wallet, budget, receipt-metadata, and audit contracts | Pure Python value objects and policies; no framework or infrastructure import | No infrastructure state | Shared host-process library; deterministic validation of untrusted inputs |
| `application` | Coordinate idempotent expense lifecycle and receipt registration/review, audited wallet entries, versioned funding-source lifecycle, replay-safe budget planning, pending proposal submission, owner category decisions, and read-only reporting plus safe profile-settings projection through narrow ports | Finance, administration, proposal, review, reporting, and receipt-metadata repository ports plus clock and ID contracts | Command, concurrency, review-boundary, and audit semantics, not stored state | Shared by CLI, API, and worker; profile scope comes from trusted caller context; node creation is reachable only through an owner decision, and receipt deletion is not exposed |
| `application.profile_settings` | Validate and project the owner-facing V1 profile subset for the settings surface | `ProfileSettingsRepository` reads the active profile and its funding sources; default writes remain in `FundingSourceAdministrationService` | No state; returns fixed locale/currency/timezone facts and eligible active default choices | Shared in-process application service composed into FastAPI; trusted profile scope is an argument from composition, inconsistent default pointers fail closed, and no infrastructure or credential contract exists |
| `workflows` | Own the bounded intent taxonomy, normalized channel work unit, deterministic pre-routing, strict versioned model boundaries, policy vetoes, and post-model read-selection policies, deterministic allocation resolution, draft and correction preparation, stable creation/correction/undo command identities, and the strict-schema retry boundary behind a narrow model port | Pydantic, `zoneinfo`, and pure domain values; no LangGraph, model client, repository, or transport import | Routing destinations, authority classes, stable routing and resolution reason codes, and the product calendar; no persistent state | Shared by the implemented worker; a model can select no deterministic route and supply no profile/timezone/authorization field, no resolved instant, no amount sign, and no funding-source identifier; reason codes carry no owner text |
| `runtime` | Assemble the compiled root-router, expense/refund, correction/undo, category-review, and read graphs; dispatch one normalized work unit to exactly one specialist; adapt the local endpoint to the structured-output port; record dispatches, their deterministic decision metadata, and model requests through an injected tracer | LangGraph, LangChain, application services, the persistent checkpointer, `workflows` contracts, and the `tracing` port (never the SDK) | No domain state of its own; checkpoint contents remain owned by each graph state contract, and tracing adds only non-persisted callbacks | Host administration CLI and worker; dispatcher trusts only injected profile/correlation context and emits no presentation text; the default tracer records nothing |
| `worker` | Claim the oldest eligible durable work unit, translate it into the normalized channel message, dispatch it once, and commit result, response, correlation, and completion together; commit failure notice with retry or dead-letter state; record each claim as the `work-unit` stage of the update's trace | Messaging repository port, finance read ports, a narrow dispatch port, the response renderer, and the `tracing` port | No authoritative state; every identifier it creates is derived from the inbox row so a recovered attempt reproduces the same logical response | systemd host service, separate failure domain from the gateway; holds no Bot API credential and imports no transport type; flushes traces within the export timeout after a stop request |
| `presentation` | Render typed dispatch outcomes, processing failures, committed movement facts, and paused review questions as Italian owner text and bounded controls | Dispatcher outcome contracts, messaging control values, and pure domain values; no model, transport, or repository import | No state; owns the callback code vocabulary and the response-kind names embedded in outbox logical keys | Worker library; content is a function of committed state only, so a replayed render reproduces the same logical response |
| `api.contracts` | Define strict, versioned finance request/response, mutation-header, and problem wire shapes | Pydantic and domain response values; no web framework | No state; stable JSON field semantics only | Shared by the FastAPI adapter; no credentials or private media fields |
| `web` | Compose versioned HTTP reads and writes, static UI serving, health, and browser policy | FastAPI, narrow application ports, typed settings, strict wire contracts, and `web.mutations` | No authoritative data; injects the sole trusted profile and bot scope and owns only HTTP semantics | Host user service on IPv4 loopback; exact Host/Origin, closed CORS, CSRF, CSP, bounded problems, no Bot API credential, and no secret endpoint |
| `web.mutations` | Translate strict browser movement, budget, funding-source/wallet, category-review, receipt-review, and messaging-requeue commands into existing audited use cases | Narrow writer protocols, request contracts, ID port, canonical command hashes, and the trusted messaging scope | No state; owns trusted web actor/source construction, not persistence or lifecycle policy | In-process API adapter; profile, bot scope, and command key are injected, and responses omit all three |
| `backup` | Create verified encrypted authoritative snapshots, report their freshness, and restore only into explicitly empty isolated targets | Restic and PostgreSQL client executables, read-only restored-data verifier, private archive and configuration paths | Snapshot contents and the versioned integrity manifest in the owner repository; no finance state of its own | Host CLI under an exclusive lock; source semantic preflight runs before dump and retention, absolute-path and owner-only input checks apply, recovery secret is passed only by file path, and failures name no argument or environment |
| `recovery` | Verify semantic integrity of an isolated restored database and receipt archive before runtime startup | psycopg read-only repeatable-read transaction, canonical schema revision, and content-addressed receipt files | No state; returns only schema identity and aggregate counts | Host operator command and final stage of `restore-drill`; exact database scope, owner-only media root, content-free failures and journal fields |
| `administration` | Expose bounded local profile bootstrap, funding-source configuration, receipt storage/registration, and one-shot dispatch of every Step 3 text route | Database URL, absolute archive setting, application services, the workflow dispatcher, local model adapter, and `open_tracer` | No independent state; emits safe identifiers, grounded facts, and reason codes only | Host CLI; active-profile preflight for specialists, explicit trusted correlations, no private paths or message content in JSON; the first traced entry point, one trace per work unit |
| `persistence.finance` | Bootstrap profile/taxonomy, persist versioned funding-source lifecycle plus complete expense and budget aggregates, append expiry-aware balance-safe wallet movements, validate/store pending proposals, and register/transition receipt metadata transactionally | psycopg and narrow application repository contracts | Finance tables, allocations, budgets/overrides, wallet ledger, proposals, versioned receipt metadata, audit, and idempotency records | Dedicated application PostgreSQL; no receipt bytes or paths; optimistic versions protect mutable aggregates |
| `media.local` | Decode, validate, atomically retain, and integrity-check private receipt originals | Receipt-media port, Pillow, private filesystem | Original bytes under sharded SHA-256 keys; no database state | Host library outside web roots; `0700` directories, `0600` files, no-follow verified reads; root wired explicitly by admin CLI, API/worker planned |
| `evaluations` | Probe real model protocols, capture exact runtime metadata, score deterministically, collect owner usefulness labels, optionally publish aggregates and a recorded promotion's committed evidence | OpenAI-compatible HTTP, local files, the `tracing` publication port with loopback-only settings (never the SDK directly), and a temporary standard-library loopback HTTP interface | Ignored local run artifacts and browser-local draft scores, plus published aggregate scores keyed by suite and by the model that produced them; no domain authority | Host CLI; the review page binds only to `127.0.0.1`, uses a per-run submission token and no external assets, and excludes secrets, image bytes and paths |
| `evaluations.catalog` | Read the declared local model candidates, name the model behind a score, and refuse a run that is not measuring the candidate it declared | Committed candidate YAML, local weights paths, and the inference server's own report of what it loaded | No state; the committed catalog is the source of truth for what a candidate is | Host CLI; never downloads, installs, or selects weights, and a candidate it cannot pin by hash is refused at load time |
| `workflows.wire` | Hold the union-shaped schemas a local model is asked to fill and convert them purely to the domain contracts | Pydantic and the `workflows` contracts; no model client or transport | No state; conversion only | Shared by every model boundary; an unstated field has an explicit variant, so a fabricated value has no shape and a split that does not reconcile is refused where it can still be repaired |
| `workflows.two_stage` | Compose the heading and level boundaries into one category judgment, choosing the subtree before the level | The two instruction catalogs, the bound wire schemas, and the narrow model port | No state; a pure composition over two calls | Evaluation only. AD-015 is rejected on its measurement, and this component is kept so that result stays reproducible; no runtime path uses it |
| `workflows.promotion` | Record each promotion as code: decision, promoted model and weights digest, pinned prompt, threshold, and policy versions, and the published run of every suite it rests on; tell whether an assembly's versions are that configuration | Pure data and comparison; no import beyond the standard library | No state; the decision log and committed runs stay authoritative, and a unit test holds the record to both and to the runtime defaults | Read by `runtime.trace_metadata` to link every runtime trace to its promotion and by `evaluations.publication` to publish the evidence; changes nothing a model is asked |
| `workflows.prompts` | Load, validate, and version the instruction catalogs each boundary is given | JSON catalogs under `prompt_data/`, the intent taxonomy, and the tool registry | No state; catalogs are read-only data | Vocabularies are derived from the taxonomy and registry, so a stale catalog is refused at load time; nothing is promoted |
| `telegram.config` | Select one test/production bot and securely read its distinct owner-only token file | Process environment, private filesystem, messaging environment enum | No authoritative state; credential exists only in process memory | Host gateway; absolute path, owner UID, regular no-follow file, and mode 0600 required |
| `telegram.transport` | Call official Bot API methods and reduce responses to typed values | `httpx`, bounded timeouts, messaging outbox contracts | No durable state | Host gateway only; every exception is replaced by a content-free reason code |
| `telegram.ingestion` | Authorize one persisted private owner and normalize a complete observed batch | Telegram transport values and `MessagingRepository` | Acceptance policy only | Runs before persistence; groups/foreign users retain counters only and never trigger file lookup |
| `telegram.gateway` | Run independent long-poll and outbox-delivery loops; record each committed update and each delivery that answers one as stages of that update's trace | Transport, ingestion service, repository, clock, retry policy, and the `tracing` port | No in-memory correctness state | Production `money-tracker-telegram` entry point; no LangGraph, model, or finance repository dependency; user service unit checked in; each loop opens its own spans on its own thread, and no Telegram user, chat, file, or callback identity enters a trace |
| `development` | Validate canonical documentation and privacy-ignore invariants | Repository Markdown/YAML/filesystem | No persistent data | Service-independent development command |

### Human usefulness review runtime

After the advice runner completes every model call, it gives the evaluation-only
review component the 52 synthetic non-injection requests and their structured
results in memory. The component serves a one-session browser interface from the
same host process on IPv4 loopback. The browser keeps an isolated draft under the
run ID and returns a complete 1–5 score map with the per-process token. The server
rejects partial, unknown, and out-of-range entries, shuts down after the accepted
submission, and returns the scores to the runner. The runner then computes the
median and writes `human-usefulness-review.json` beside that exact raw run. This
interface has no product route, domain authority, repository access, external
asset, or persistent listener.

### Model-facing boundary shapes and instruction catalogs

Two components inside `workflows` exist because of what the Step 1 capability
screen measured about the local candidate, and they are worth naming as
building blocks rather than as implementation detail.

`workflows.wire` holds the schemas a model is actually asked to fill, and
nothing else. `workflows.contracts` says what a boundary means; `wire` says how
a local model is asked for it, and converts to the contract purely and totally.
The two differ for one measured reason: the screened candidate cannot reliably
leave an optional field alone, and it can answer a discriminated union cleanly.
Every field a message may leave unstated is therefore a union whose "not
stated" variant carries no payload, so inventing a merchant, a month, or a
payment method is not a mistake the decoding grammar can express. Cross-field
rules that a validator used to enforce after the fact are carried by the shapes
too: a single payment method and a split are separate variants, so an
unquantified split has no form, and a split whose parts do not add up to the
stated total is refused while the boundary's one corrective retry is still
available. The component owns no policy: what an absent value means is still
decided by the deterministic resolvers.

`workflows.prompts` reads versioned instruction catalogs from
`workflows/prompt_data/<boundary>.json`, one file per boundary, each holding
every version of that boundary's text with the note recording why the version
exists and what its run measured. Prompts are data that changes far more often
than the code reading them, and a boundary accumulates versions because an
evaluation compares them. Two vocabularies stay derived rather than retyped:
the routable intents come from the taxonomy and the read tools from the
registry, and a catalog whose vocabulary no longer matches is refused at load
time rather than quietly offering a route that does not exist. Nothing in a
catalog is promoted; every identifier carries a `candidate-` prefix.

### Planned domain and application blocks

| Component | Responsibility | Narrow interfaces/dependencies | Data ownership | Deployment/security |
| --- | --- | --- | --- | --- |
| Finance domain (core implemented) | Enforce money, expense/refund, allocation, budget, category and wallet invariants | Pure value objects and domain policies | No infrastructure state | Python application; no framework imports |
| Application services (expense/wallet/budget/funding-source, proposal submission and settlement, and replay-safe receipt registration/review implemented) | Execute validated commands and queries transactionally | Narrow repository ports, clock, ID and future media ports | Command/content idempotency, versioning, review boundary, and audit semantics | Implemented FastAPI and worker host processes; profile scope injected; the receipt metadata lifecycle is control-plane reachable while the extracted-draft preview remains workflow-owned |
| Finance repositories (expense/wallet/budget/funding-source/proposal/versioned receipt metadata implemented) | Persist authoritative domain records and analytics projections | PostgreSQL protocol through explicit repository contracts | Finance schema implemented; receipt deletion open | Dedicated application database; least-privilege credentials; optimistic versions and source locks protect concurrent mutations; no media payloads |
| Messaging persistence (implemented) | Receive, order, lease, retry, dead-letter, replay, correlate, and complete transport work | `MessagingRepository` port, psycopg, and PostgreSQL | Minimum normalized inbox data, offsets, typed results, logical outbox, pairing, pending actions, and attempt metadata; concluded rows are deleted 30 days after they conclude (AD-024) | Shared gateway/worker/admin adapter; no raw update payload, token, or token-bearing URL in rows |
| Workflow runtime (dispatcher, router, and Step 3 text specialists implemented) | Route one normalized work unit and execute exactly one bounded specialist | Typed dispatcher outcome, LangGraph, model gateway, application services, checkpointer | Small per-graph versioned workflow state only; authoritative domain data stays in repositories | Administration CLI and sequential worker; no media, credentials, or service objects in checkpoints |
| Receipt service | Validate media, invoke extractor, reconcile lines, prepare review | Implemented media-store port, planned extractor, finance service | Receipt metadata, extraction versions, review state | Private filesystem; untrusted image boundary; orchestration planned for Step 6, deferred after V1 |
| Analytics service | Compute time, category, budget and funding summaries | Read repositories and deterministic calendar policies | No separate authoritative data | FastAPI and read-only agent tools |
| Backup, recovery verifier, and scheduler (implemented) | Create consistent encrypted backups, enforce retention, restore isolated targets, and verify recovered business/runtime integrity | `pg_dump`, Restic, psycopg read-only verification, receipt/configuration paths, persistent systemd user timer | Backup snapshots in owner repository; no application state | Manual command or daily oneshot on the host; target timer-trigger accepted against a separate internal NVMe; multi-run retention evidence and clean-machine qualification remain open |

### Current and planned adapters and presentation blocks

| Component | Responsibility | Interfaces/dependencies | Data ownership | Deployment/security |
| --- | --- | --- | --- | --- |
| Workflow dispatcher, root router, and text specialists (implemented) | Route one intent and coordinate expense/refund, correction/undo, category review, query, or advice | Typed normalized work unit/outcome, versioned schemas, scoped tool registries, trusted correlations | Namespaced checkpoint references and workflow IDs; no domain ownership | Administration CLI and worker; mutation paths call replay-safe application services |
| Telegram Bot API transport (implemented) | Validate the bot, long poll, obtain file metadata, acknowledge callbacks, and deliver outbox messages | Bounded HTTPS client and messaging services | No authoritative finance data or correctness state | Gateway host service; separate production/test token selected from a private environment file |
| FastAPI adapter (partial) | Expose monthly and calendar-range overviews with merchant and prior-period comparison, transaction register, manual lifecycle, budget planning, category and funding-source/wallet administration, pending category review, receipt metadata review, messaging operations, safe profile settings, promoted advice, health, CSRF bootstrap, compiled UI, and later REST resources | Narrow advice, reporting, finance, budget, category, funding-source, receipt-review, messaging-operations, and profile-settings ports | No independent data | Implemented on loopback with Host/Origin, CSRF, idempotency for writes, optimistic concurrency, proposal-wide and transition-wide command identity, and bounded errors |
| Browser advice composition (implemented) | Enter the promoted advice read graph directly, map only deterministic tool facts to a strict HTTP union, and record each request as one `web-advice` trace | `AdviceWorkflow`, active taxonomy/source configuration reads, `ReportingService`, clock, promoted model adapter and selection policy, and the `tracing` port | No state; no checkpointer and no mutation service in its object graph | In-process in `money-tracker-api`; trusted profile/time/vocabulary injection, CSRF-protected read-only POST, content-free logs carrying only a request reference, no prompt/completion/profile/secret exposure over HTTP; the trace holds the request and selection call under AD-021 |
| React dashboard (partial) | Display the monthly overview, calendar analytics with merchant and period comparison, transaction register, budget planner, categories, funding sources with tracked-wallet balances/history, retained receipt metadata, durable messaging state, fixed profile facts, and promoted tool-grounded advice; edit the multi-allocation manual lifecycle, monthly budgets, deterministic category and source lifecycle, manual wallet entries, the mandatory receipt review, and both queue requeues; choose the active default funding source while locale, currency, and timezone remain read-only | Same-origin REST API | Browser session/UI state only | Route-split compiled static assets with the chart engine isolated from its renderer; no secrets, infrastructure configuration, receipt images, prompts, completions, or original message reflection; accessible loading, empty, insufficient, clarification, error/retry, lifecycle history, responsive navigation, chart/table alternatives, and named keyboard-focusable table scroll regions; real-browser WCAG A/AA acceptance |
| Local model gateway | Provide typed chat, schema and tool capabilities | OpenAI-compatible local endpoint | No persistent domain data | Replaceable host inference process |
| Calendar period resolver (implemented) | Resolve strict day, ISO-week, month, year, and bounded inclusive custom selections plus their deterministic predecessors into half-open query intervals | Pure domain date values used by reporting and the web adapter; no framework or repository dependency | No persistent data | In-process domain component; Europe/Rome calendar semantics, DST-safe boundaries, and a 366-day custom-range ceiling |
| Receipt extractor adapter | Produce a versioned structured receipt draft | Selected local OCR/VLM pipeline | No authoritative data until validated | Local-only; selection remains evaluation-gated; planned for Step 6, deferred after V1 |
| Langfuse adapter (`tracing.langfuse`, implemented) | Record full-fidelity AD-021 traces; publish aggregate capability scores and promotion evidence through `evaluations.publication` | Local Langfuse SDK behind the tracing port | Observability copies, not source of truth; owner content with ledger sensitivity; excluded from backup and kept 30 days by owner decision | Optional Compose profile; administrative dispatch, gateway, worker, and web advice traced |

### Step 4 messaging boundaries

AD-018 divides the Step 4 runtime into seven replaceable blocks. The contracts,
retry policy, PostgreSQL repository, ingestion service, Bot API transport,
gateway process, response renderer, sequential worker, pairing, and the
administration and health commands are all implemented:

| Component | Responsibility | Interface/dependencies | Data ownership | Deployment/security |
| --- | --- | --- | --- | --- |
| `messaging.contracts` | Define normalized inbox, outbox, pairing, pending-action, retry, dead-letter, requeue, and health values, and the single `telegram:<bot-key>:<update-id>` source-reference derivation that outbox claims also carry | Pure Python contracts and time/identifier values | No infrastructure state | Shared host library; contains no Bot API token or raw update |
| `messaging.scope` | Select one bot environment and derive the stable key that scopes its durable rows | One process environment variable and the environment enum | No state; the derivation is the single source of the bot key | Every messaging process, including the web host, which must know its scope without holding a credential |
| `messaging.errors` | Name the failures the durable messaging port may raise | Nothing but the standard library | No state | Imported by callers that depend on the port rather than on PostgreSQL |
| `messaging.service` (ingestion, pairing, worker, and operator surface implemented) | Authorize/normalize ingestion, consume one-use pairing, coordinate claim/dispatch/retry/replay/delivery, compose the content-free operations snapshot, and summarize text latency | Messaging repository port, response renderer, dispatch port, and durable acceptance/send timestamps | Application policy only, including which requeue transitions a caller may pin | Gateway/worker/admin/API host processes; owner scope comes only from binding records, pairing codes are never retained in clear, and no output contains messages or identities |
| `persistence.messaging` | Implement the AD-018 transactions and uniqueness/lease rules | psycopg and the dedicated application PostgreSQL | Entire `messaging` schema | Least-privilege application role; content is private and included in authoritative backup |
| `telegram.transport` | Implement the official HTTPS Bot API methods with bounded timeouts and redacted failures | `httpx` and configured Bot API base URL/token | No durable state | Gateway only; production/test configuration is mutually exclusive |
| `telegram.gateway` | Run independent ingress and outbox loops, each tracing its own stage of an update | Bot API transport, messaging service, and tracing port | No in-memory correctness state | systemd host service; no model or finance repository dependency |
| `worker` (implemented) | Translate one claimed row to `ChannelMessage`, construct `DispatchContext`, call the Step 3 dispatcher, and finalize messaging state, tracing the claim as one stage of the update | Messaging/finance read ports, a narrow dispatch port, and the tracing port | No authoritative state; derives outbox and pending-action identity from the inbox row so a recovery reproduces them; a modification clarification creates a durable successor reply mapping for the selected movement | systemd host service; no token or Telegram client |
| `presentation.telegram` (implemented) | Render typed workflow outcomes, controls, and the pairing confirmation in Italian | Dispatch outcome plus bounded display projections | No state | Worker and gateway library; never exposes tool names, raw evaluation JSON, or internal IDs; content depends only on committed state so a replayed render is identical |
| `store_telegram_token.sh` (implemented) | Install or rotate exactly one selected Bot API credential | Hidden standard input and non-executed `.env` path settings; no Bot API call | Atomically replaces the selected owner-only token file | Manual host operation; refuses shared paths, unsafe targets, foreign ownership, and non-private configuration, and prints neither path nor value |

The gateway and worker have separate failure domains. The gateway remains able to
commit updates when llama.cpp or any workflow specialist is unavailable. The
worker never imports a Telegram transport type; the normalized
`ChannelMessage` is the only value crossing into the Step 3 workflow boundary.

Detailed finance concepts and the initial taxonomy live in
[Domain and Taxonomy](../product/domain-and-taxonomy.md).

### Step 3 evaluation corrections (2026-09-08)

The current corrective experiment is recorded in
[contract corrections](../experiments/text-workflow-fixes-2026-09-08.md).
The read-only reporting service now returns per-source net posted payment
consumption beside funding-class totals (read-result contract v2). It calls the
pure domain `posted_funding_source_spend` operation over profile/period-scoped
repository records. Refunds reduce consumption; unposted records are excluded.
Sources with zero consumption remain visible, including historical archived
sources. No identifier is exposed in the answer and no ledger balance is inferred.
This extends existing domain/application/runtime components, owns no new data,
and runs in the existing host process against application PostgreSQL.

Selection v6 uses versioned descriptions of those outputs, the existing budget
survey's missing-budget data, and explicit refusal of balance requests. Query
still excludes the survey; advice still excludes the single-budget lookup.
Category v7 renders explicit parent paths, independent of repository row order.
The administrative expense path and evaluations share candidate definitions.
That corrective snapshot was experimental. EVD-004 now promotes the selected
configuration; all movements still remain drafts because no auto-post subset
qualified.

## 6. Runtime View

### Current CLI turn

1. The CLI reads a prompt and opens the tracer the switch selects.
2. The graph receives a human message and prepends its system contract.
3. The model adapter calls the configured local inference API.
4. The graph returns one assistant message; the CLI prints it and flushes traces
   within the export bound.

No state survives the process and no financial write occurs.

### Current traced dispatch

AD-021 governs what is recorded; this is the sequence for `money-tracker-admin
route`. The worker runs the same steps 3 and 4 inside each claim, as the traced
Telegram update below describes.

1. The entry point calls `open_tracer()`. With the switch off it receives the
   disabled tracer, the SDK is never imported, and every later step records
   nothing.
2. It opens the `work-unit` root span of a trace named `admin-route`, whose
   identifier is derived from the work unit's source reference, and injects the
   tracer into `LocalStructuredOutputModel` and the dispatcher together with
   the version set it composed them with.
3. `WorkflowDispatcher.dispatch` opens a `dispatch` span holding the complete
   message, trusted context, and version set. Each graph invocation receives
   its thread identifier plus the tracer's callbacks only, so checkpoints are
   unchanged; the callback records the graph and its nodes and ignores chat
   model runs.
4. Inside a node, the adapter builds the turns and the strict
   `response_format` exactly as before, then opens a `model:<Contract>`
   generation carrying them, the chat model's allowlisted parameters, and the
   serving artifact's identity. The request is sent unchanged; the raw
   completion, provider metadata, token usage, and, only when recording, the
   parsed contract or validation summary are attached. An endpoint failure is
   recorded as a reason code and exception class and propagates unchanged.
5. The dispatch span records the typed outcome, the root span records the
   command result, and the entry point flushes within the export timeout before
   exiting. A stopped store costs that bound and nothing else.

### Current traced Telegram update

AD-021 governs the content; this is how one owner message becomes one trace
across two processes. Every stage derives the trace identifier from the
update's source reference `telegram:<bot-key>:<update-id>` and propagates the
trace name `telegram-update`, so the stages join whichever reaches the store
first.

1. The gateway's ingress thread polls, and the ingestor commits the batch. For
   each accepted update the thread then opens and closes a `telegram-ingest`
   root span with the normalized update, the batch counters, the poll and
   commit durations, and the callback acknowledgement result. Rejected and
   pairing updates, and every Telegram user, chat, file, and callback identity,
   stay out.
2. The worker claims the inbox row and opens a `work-unit` root span with the
   inbox identity, attempts, replays, and queue wait, and records what a
   callback or reply resolved to.
3. Inside it the dispatcher's `dispatch` span, graphs, nodes, and generations
   are recorded exactly as in the traced dispatch above, plus the decision
   metadata read from the typed decision and final graph states.
4. A `render` span records the owner response and controls, and a `commit`
   span the outbox row, logical key, pending action, consumed action, and
   revocations of the terminal transaction. A failure instead records the retry
   or dead-letter transition and its reason code on the root; a lost lease is
   recorded without failing it.
5. The gateway's outbox thread claims the response, whose claim carries the
   inbox row's source reference, and opens a `telegram-delivery` root span with
   the delivered text, the attempt, and the Telegram message identity or the
   transport reason code. Work that answers no update is delivered untraced.

Retries, replays, and delivery retries add further roots to the same trace.
Spans are exported on the SDK's background thread, so no claim, commit, or
delivery waits for the store; both processes flush within the export timeout
after their loops honor a stop request.

### Current finance repository mutations

1. A trusted caller supplies profile-scoped domain objects and non-sensitive
   actor/reason audit metadata to an application service.
2. Expense commands validate the complete aggregate, then create idempotently
   or replace only the caller-observed version.
3. A wallet command locks its ledger funding source, reconstructs booked and
   AD-020 available balance, and rejects an expiry-aware overdraft before
   appending anything.
4. A budget command creates version one or replaces the complete recurring
   target and override set only at the expected version.
5. The repository commits the domain mutation and immutable audit event in one
   PostgreSQL transaction; any validation or concurrency failure rolls both
   back.

Expense, budget, and funding-source/wallet operations are reachable through
the profile-bound local API; administration CLI paths retain their narrower
operator scope. Integration tests exercise the production services through both
HTTP and the real PostgreSQL protocol.

### Current pending category proposal submission

1. A caller supplies a domain-validated proposal in `pending` state with its
   source workflow, parent, alternatives, rationale, and exclusions.
2. The repository checks that the parent is an active root, every alternative
   is active in the same profile, and the normalized name matches no active
   category or alias.
3. Proposal and immutable creation audit commit in one transaction; any failed
   scope or duplicate check writes neither.
4. No category is created and no proposal decision is accepted by this service.
   Settling a review is a distinct authority behind its own port.

### Current owner category decision

The only path that can extend the taxonomy. It settles a review the owner has
actually seen and always reaches one terminal state.

1. `Approva` builds the proposed node, validates it by extending the real
   taxonomy aggregate, attaches the whole signed total to it, and posts the
   movement. Node creation, review settlement, movement replacement, and both
   audits commit in one transaction, so no state exists in which a category was
   created for a movement that never received it.
2. `Usa una delle categorie esistenti` and `Usa la categoria X` share one
   operation. Both resolve through the active taxonomy's names and aliases,
   never an identifier a caller chose, create no node, and record
   `owner_override` on the stored allocation.
3. `Proponine un'altra` refuses the candidate and opens a successor carrying
   every name already refused in this review. The movement is untouched. The
   loop is capped, after which the review offers existing categories and manual
   entry instead of another candidate.
4. `Annulla` settles the review with no node and discards the draft. Discarding
   is distinct from voiding: voiding reverses a movement that already counted
   and stays reconciled, while a cancelled draft never counted and has no
   purpose dimension.
5. Every branch shares one proposal-wide idempotency key. Its claim commits
   with settlement, movement/category changes, and audits; an identical replay
   returns the first stored result while a competing branch conflicts. Posting
   here is not the gated auto-post policy, which decides whether to post
   *without* asking; this path posts only what the owner explicitly authorized.
6. Allocations created here carry an owner provenance distinct from the
   category-fit boundary's versioned one, so stored history says whether a
   human or a prompt chose the purpose.

### Current paused owner category review

The first workflow that deliberately stops and waits for a human. It is a
two-node loop over the persistent checkpointer.

1. The asking node renders the question from the authoritative stored proposal,
   then pauses. The question names the candidate, its parent, the rationale,
   and the reuse shortlist; it names categories and never identifies them, so a
   channel sends back a branch rather than an identifier. It also states
   whether the candidate loop may still offer another name.
2. The settling node applies the branch through the owner decision service and
   decides whether the review is over. Approval, reuse, and cancellation end
   it; a re-proposal opens a successor and the loop pauses again.
3. Only reads happen before the pause, and every write lives in the settling
   node. LangGraph replays an interrupted node from its start, so a single node
   holding both would replay its settlement the second time around the loop,
   when the cached first interrupt returns immediately.
4. Replay safety does not rest on the topology alone. The application service
   checks the durable proposal command before mutable review state: identical
   approval, reuse, cancellation, or re-proposal reconstructs its first result,
   and another branch cannot reuse that command identity.
5. A decision that cannot be applied keeps the review open and pauses again
   carrying a stable reason: an unresolvable name, an empty reuse shortlist, a
   capped loop, or a replacement the taxonomy refused. Nothing is written on
   those paths.
6. A re-proposal re-invokes the purpose boundary with every refused candidate
   excluded. It classifies the stored movement's own description rather than
   the original owner message, because a review may wait indefinitely and the
   paused checkpoint should hold no raw owner text it does not need.
7. Resume payloads arrive from outside the graph and are validated, not
   trusted. A branch outside the review taxonomy, a named reuse without a name,
   and a name riding on a branch that ignores it are all refused.

An unrelated message never reaches this graph: deterministic acceptance already
requires a category follow-up to correlate with exactly one pending review, so
the paused thread simply keeps waiting while the message is routed on its own.

### Current read-only question and advice routes

The bounded agent loop AD-006 permits, and its bound is the point: the model
makes exactly one decision, and everything after it is deterministic.

1. Each read-only route owns a scoped registry of named tools. The two
   registries differ, so the advice survey is unreachable from a question and
   the single-budget lookup is unreachable from advice. No mutating,
   deterministic, or inert route owns a registry at all, so those routes have
   no tool to draw.
2. The offered vocabulary is rendered from the registry rather than retyped, so
   a model is never offered a tool deterministic acceptance would refuse.
3. The selection boundary is strict, versioned independently, and exposes no
   profile, identifier, amount, funding source, or free-form query. There is
   deliberately no generic SQL tool in the vocabulary at all.
4. A versioned pure policy may repair only an enumerated semantic scope from
   normalized text and trusted active display names. EVD-004 promotes
   `explicit-finance-scope-v1`; `model-only-v0` preserves the model request and
   remains the direct rollback.
5. Deterministic acceptance admits the resulting tool against the registry of
   the *routed* intent, taken from trusted context rather than from the model,
   so neither model nor policy can widen the route scope.
6. The tool then executes against the domain read models over stored records.
   A model supplies a tool name, a category the owner named, and at most a
   calendar month; it supplies no figure, sees no figure, and can reach no node
   that would let it claim one.
7. Absence is reported rather than smoothed. An unconfigured budget is stated
   as missing, posted spend with no budget to compare against is named, and an
   unresolvable category becomes a question instead of a zero.
8. The reporting service depends on a port that exposes only reads, so a
   mutation is absent from the object graph these nodes can reach rather than
   merely disallowed by convention.

Ordering the advice survey worst-position-first is deterministic here rather
than left to a model's sense of severity, which is what J8 requires when it
says exceeded and at-risk budgets come first.

### Current receipt metadata registration and review state

1. A caller supplies domain-validated metadata only after the local media-store
   port has accepted and identified decoded content.
2. The service accepts only the initial `stored` state and never receives bytes
   or filesystem paths.
3. PostgreSQL creates the first row for `(profile_id, content_sha256)` with its
   audit. An identical retry returns that row and writes no second audit.
4. A retry claiming a different byte size or media type fails; a failed audit
   rolls back the new metadata row.
5. The application permits only `stored` to `needs_review` to `approved`;
   direct approval and all transitions from terminal states fail.
6. Each review transition increments an optimistic version, claims a command
   key derived from the receipt and the observed version, and commits its
   immutable audit atomically. A repeated command replays its result; a
   different decision on one observed version conflicts; concurrent distinct
   writers have exactly one winner.

The media adapter is wired to the local administration CLI, which imports an
original, and the metadata lifecycle is wired to the control plane, which reads
it and completes the mandatory review. No entry point reads, serves, or deletes
an original. Extraction and the product draft-preview workflow remain planned.

### Current browser receipt metadata review

1. The browser loads every retained receipt of the trusted profile through one
   narrow control-plane adapter over `ReceiptMetadataService`.
2. The response carries display filename, decoded media type, exact byte size,
   storage instant, review state, and optimistic version. It omits the content
   digest, which is the archive storage key, together with profile identity and
   source reference. No route returns, links to, or streams an original.
3. A review or approval request carries only the version the page displayed.
   The HTTP command key is `receipt-review:{receipt_id}:{expected_version}`,
   the same key the application transaction claims, and the adapter refuses any
   other value.
4. The service resolves a replay before reading the current row, so a retry
   returns the first result rather than depending on state that attempt already
   advanced. The domain then decides the transition: approving a receipt that
   never entered review, or quoting a version another transition already left,
   is a bounded rejection that writes nothing.
5. The React page lists the archive newest first with explicit loading, empty,
   failure and pending states, and offers exactly one decision per state. A
   successful decision invalidates the archive read.
6. The web object graph contains no media store, extractor, or deletion path,
   so this surface cannot widen beyond metadata consultation and the two
   audited transitions without a new component and a new decision.

### Current browser messaging operations

1. The browser loads one bot's durable state through a control-plane adapter
   whose scope is bound at composition time. The trusted `MessagingScope` comes
   from configuration, no adapter method accepts a scope, and the web process
   holds no Bot API credential.
2. `MessagingAdministration` composes one content-free snapshot: environment,
   whether an owner is paired, the persisted offset, both queue depths with
   their oldest waiting item, active correlations, whether the downtime gap
   exceeds the Bot API's 24-hour retention, and both dead-letter queues.
3. Every value crossing the boundary is a count, an age, a bounded reason code,
   or a stable local identifier. The dead-letter projections carry no owner text
   at all, so the same facts are safe in a page and in a journal. The response
   also omits the bot key and the inbox source reference.
4. A requeue request carries only the `replay_count` the page displayed. Its
   HTTP command key names that transition, `messaging-replay:{inbox_id}:{count}`
   or `messaging-resend:{outbox_id}:{count}`, and the adapter refuses any other
   value.
5. The repository locks the row, applies the requeue only from the pinned
   state, and reports a repeat of that same transition as already done rather
   than requeuing twice. Every other outcome is one bounded conflict, so a
   foreign, missing, already progressed, or wrongly keyed request cannot be told
   apart.
6. Replay preserves the source reference, so a work item that already committed
   a finance mutation returns its first result. Resend keeps the same logical
   response row and starts a fresh bounded delivery cycle, which the page warns
   can produce a duplicate physical Telegram message.
7. The web object graph contains no Bot API transport, no pairing mutation, no
   offset control, and no deletion path, so this surface cannot widen beyond
   consultation and the two requeue commands without a new component.

### Current local receipt-media storage

1. Untrusted bytes are rejected above 20 MiB before decode.
2. Pillow fully verifies and decodes only single-frame JPEG, PNG, or WebP under
   40 megapixels and 10,000 pixels per side.
3. The adapter derives SHA-256 and atomically links a synced mode-`0600`
   temporary file into a mode-`0700` digest shard without overwriting.
4. Concurrent identical writes converge on the same file. Reads accept only a
   digest, refuse symlinks, reapply the byte limit, and verify content hash.
5. The receipt-metadata service then registers safe facts in PostgreSQL; media
   bytes and paths never enter rows, checkpoints, or traces.

### Current local routing command

1. `money-tracker-admin route` builds one channel message from flags and the
   current instant, and takes the trusted routing context from flags rather
   than the database, so the command needs no active profile and reaches no
   repository.
2. It requires an explicit confidence bar, because no calibrated production
   value is promoted, and rejects a bar outside the unit interval before
   spending an inference call.
3. A deterministic route completes with no model involved. Only free text
   reaches the local endpoint, and an unreachable endpoint names the service to
   start instead of surfacing a transport traceback. It is never converted into
   a routing decision.
4. Stdout contains sorted JSON with intent, deciding half, justification code,
   confidence, and any correlated command or reference. The owner message is
   not echoed, because a routing decision carries no message content.

The command keeps no thread. Routing alone writes nothing, but supplying
`--profile-id` continues a movement route into the expense workflow, which
stores a draft through the application services or reports the reason it
clarified. Stdout then also carries the outcome, the stored draft identifier,
any pending candidate, and the clarification reason.

### Current administrative receipt import

1. `money-tracker-admin receipt-store` loads the database and absolute archive
   settings and confirms the active profile before media mutation.
2. It refuses a final input symlink, directory, missing file, or payload above
   the byte limit and validates display filename/source metadata.
3. The existing media and metadata services store/register idempotently.
4. Stdout contains sorted JSON with receipt ID, hash, MIME, size, and status;
   input/archive paths and bytes are omitted.


### Current deterministic root routing

The router has two halves with different reliability requirements. The
deterministic half decides from facts the channel already stated; the model half
is a nondeterministic suggestion that deterministic policy then re-checks.

1. A transport adapter normalizes one durable inbound record into a channel
   message carrying a source reference, an aware receipt timestamp, optional
   text, an optional explicit interface decision with its pending-action
   reference, an optional free-text reply reference, and whether image media is
   attached. It carries no transport credential, raw update payload, or bytes.
2. Deterministic pre-routing applies a total precedence order. An explicit
   interface decision wins because the owner already chose. Its normalized
   callback action remains separate from the intent enum exposed to the model;
   in particular, `post_expense` reaches the movement-lifecycle specialist
   without changing the EVD-004 router schema. Image media
   outranks its own caption, so a caption can never turn a review-only receipt
   into a posting path. A supported command outranks free text. Blank remaining
   input is clarified without spending an inference call.
3. A callback that names no pending action is clarified rather than applied to
   whichever action happens to be pending.
4. Remaining free text is classified by the model against a strict versioned
   schema that carries an intent, a diagnostic confidence, a short verbatim
   evidence span, and explicitly declared unknowns. The schema exposes no
   profile, timezone, funding-source, authorization, or idempotency field and
   rejects the two deterministic-only routes. This declared-unknown channel
   belongs to routing alone: the extraction boundary deliberately has none.
5. Deterministic acceptance then vetoes an impossible route: a category
   follow-up requires exactly one correlated pending review, a correction or
   undo requires a movement the runtime resolved, and every decision must meet
   an explicitly supplied confidence bar. Each veto falls back to
   clarification, which asks instead of writing.
6. The resulting decision carries only small serializable values and stable
   identifier references, plus a machine-readable justification code that
   contains no owner text.

The compiled LangGraph router, specialists, persistent review interrupt, and
explicit dispatcher are implemented. EVD-004 promotes Qwen3 14B, router v6,
and confidence 0.5. The framework-independent router policy still imports no
model client, repository, or transport module.

### Current extraction boundary and deterministic resolution

1. The extraction schema carries a direction word, a positive amount magnitude,
   the owner's verbatim temporal words, a description, optional merchant
   metadata, funding portions as the owner named them, and an evidence span. It
   carries no currency, because V1 operates in one currency the model has no
   basis to supply, and no abstention channel.
2. The boundary offers the model no way to declare its own uncertainty. Every
   aspect it might abstain on is already detectable deterministically, and the
   deterministic side knows strictly more: it holds the profile's configured
   funding sources and the Italian date forms the resolver reads, neither of
   which the model is shown. Omission therefore carries meaning: an absent
   amount is unresolvable, while an absent time, merchant, or funding portion
   is the documented default.
3. A split payment must quantify every portion and name distinct sources,
   because an unquantified split could never reconcile against the total.
4. Deterministic resolution converts the untrusted extraction into domain
   values: the amount sign from the stated polarity, and the occurrence instant
   from the temporal words against the message timestamp in the product
   calendar. Same-day, previous-day, counted day and week offsets, named past
   weekdays, and explicit Italian dates are supported; a bare weekday, an
   unreadable phrase, and a day after the message are refused.
5. The message wall-clock time is preserved so a named day stays inside its
   intended Rome day. A wall time inside the spring-forward gap normalizes to
   the equivalent instant after the transition.

6. Payment allocation resolves each named portion against the profile's
   configured active sources, or fills an omitted method from the configured
   default. Three shapes are supported: no stated funding, one named source
   carrying the whole total, and a split that must sum exactly to the total.
   Every allocation carries the total's sign, so a refund returns value to its
   sources instead of inverting one dimension against the other.
7. Each failure reports a stable resolution reason code, so the workflow can
   ask the one question that matches the failing step: an unstated amount, an
   unreadable date, a missing default source, unresolved funding words, two
   portions naming one source, or a split that does not reconcile.

8. Category fit is one judgment with two outcomes. An existing outcome names an
   active category, resolved through the taxonomy's names and aliases. A
   proposal outcome carries a candidate name, description, rationale, optional
   parent, and the active categories it examined; deterministic assembly checks
   every one of those words against the taxonomy, refuses a name that
   duplicates an active node or was already rejected in this review, and holds
   the two-level limit before a node could exist. Nothing here creates a
   taxonomy node: a proposal exists only for the owner-approval interrupt.
9. The purpose dimension is one allocation carrying the whole signed total,
   because a text movement has a single purpose; multi-purpose splitting is a
   receipt-line concern. The allocation records its classification origin and
   whether the owner forced the category over the workflow's judgment.

Both resolved dimensions are verified against the real domain aggregate, which
independently enforces non-zero rows, unique identifiers, matching polarity,
and exact reconciliation. The graph node, prompts, and owner-approval interrupt
that would turn a resolved draft into a stored expense remain open.

### Current draft movement assembly

1. Deterministic assembly takes one extraction and one purpose judgment plus
   trusted identity, profile scope, taxonomy, and funding sources, and returns
   either a complete draft or the stable reason selecting the question to ask.
2. The occurrence instant, signed amount, funding dimension, and purpose
   dimension are resolved by the implemented resolvers before any aggregate
   exists, so an unreadable date or an unresolved funding source never produces
   a partially built movement.
3. Every movement is created as a draft, and an explicit owner `Registra`
   records it. `Registra` is a deterministic callback action carried by the
   correction route into the shared lifecycle specialist; it is not a
   model-routable intent. That control is the only channel path to posting,
   because
   EVD-004 qualified no automatic-post subset and a draft nobody records stays
   invisible to every report.
   The auto-post eligibility policy is calibrated on the evaluation set and
   none is promoted, so no text message may post itself.
4. When the purpose needs a new category, the draft is still assembled with its
   funding dimension resolved and its purpose dimension deliberately empty,
   accompanied by a pending proposal. AD-008 lets only owner approval extend
   the taxonomy, and the domain admits a draft without a purpose while
   refusing to post one.
5. The creation key is derived from the inbound record and the request digest
   from the request's own semantics, so a redelivery replays into the first
   movement while a changed request on the same record is refused as a
   conflict.

Real PostgreSQL tests store a draft against a seeded profile's taxonomy and
funding sources, replay one inbound record into exactly one movement, refuse a
changed request on that record, and keep a pending proposal alongside its draft
without extending the taxonomy.

### Current expense and refund workflow

1. A routed movement message enters a specialist graph with the trusted profile
   scope injected by the runtime. No model-facing schema has a field for that
   scope, so a model cannot influence whose data is read or written.
2. The extraction boundary reads the movement, and routing and extraction judge
   the direction of money independently. A disagreement clarifies rather than
   picking one, because recording a purchase as a reimbursement is the kind of
   ambiguity the product baseline forbids reaching a write.
3. The purpose boundary receives the owner's own active category names rendered
   from their taxonomy at call time, so a model can only name a category the
   owner actually has.
4. Deterministic assembly then either produces a complete draft or reports the
   stable reason that selects the question to ask. Only then do the application
   services store the draft, and a pending candidate is submitted through the
   existing proposal port.
5. Each boundary's answer is carried in the state as its own wire form, so a
   resumed thread never re-asks the model, and the stored movement is
   referenced by identifier only.
6. Every node either advances or records a clarification, so a run stores a
   complete draft or stores nothing.

Funding words are resolved during assembly, after the purpose boundary, so a
message naming an unresolvable funding source still costs one purpose call. That is
a deliberate trade: every write-deciding rule stays inside one atomic function,
and the latency target applies to successful runs.

### Current correction and undo specialists

1. Both routes receive the exact movement identifier resolved by trusted
   runtime context. A missing or foreign target returns
   `no_correctable_movement` before a model call or financial write.
2. Correction reuses the evaluated extraction and category-fit boundaries. A
   pure workflow function resolves their output into a complete replacement;
   omitted occurrence, merchant, and funding values preserve stored data. A
   changed total scales a single existing payment allocation, while a
   multi-source payment must be restated rather than guessed.
3. A replacement category must resolve to an active stable identifier. A
   proposed category stops with `category_approval_required`; correction
   cannot bypass AD-008's owner-only taxonomy authority.
4. Undo has no model node. It loads the trusted target and calls the application
   void operation, preserving the aggregate and audit history.
5. The `apply_correction` and `apply_undo` nodes derive distinct command keys
   from the durable source reference. PostgreSQL claims each command in the
   same transaction as the aggregate replacement and audit event. A replay,
   including a fresh graph invocation after a void reached terminal state,
   returns the first result and creates no second write.
6. Checkpoints contain only the normalized message, profile and movement
   identifiers, boundary JSON, outcome, and reason codes. Authoritative
   movement data is reread from the repository.

The specialists run in the host workflow process and own no data. They depend
on `FinanceService`, the narrow profile configuration reader, the structured
model port for correction only, and application PostgreSQL. Real PostgreSQL
integration tests cover a completed correction, missing-target clarification,
void, and a fresh replay with one resulting audit mutation.

### Current compiled root router

1. A caller builds the initial state from one normalized channel message and
   the trusted routing context, then invokes the compiled graph under a thread
   identifier so the checkpointer can resume it.
2. The `pre_route` node decides from stated channel facts alone. When it
   decides, a conditional edge ends the graph, so an explicit interface
   decision, an attached image, a supported command, and blank input never
   reach an inference call.
3. Only unresolved free text continues to `classify`, which asks the model
   through the strict boundary and then re-imposes deterministic policy on the
   answer with the confidence bar the caller stated.
4. An exhausted retry budget is not an error path: the node converts it into a
   clarification decision carrying the `invalid_model_output` reason, because
   AD-006 requires routing to ask rather than loop or fail.
5. The persistent PostgreSQL checkpointer stores only the versioned state
   contract, so a resumed thread reconstructs its message, trusted context, and
   decision from JSON primitives and stable string references.

Both the instruction text and the confidence bar are required arguments with no
defaults, so the assembly cannot imply a production configuration for either.
The graph holds no routing policy of its own; every decision it makes is
implemented and tested in the framework-independent component.

The router remains independently testable and emits no owner response. The
explicit dispatcher below now connects it to the implemented specialists; no
channel or worker process exists yet.

### Current workflow dispatcher

1. `WorkflowDispatcher.dispatch` is the single transport-neutral call. It takes
   a normalized `ChannelMessage` plus a `DispatchContext` containing trusted
   profile and correlation facts, invokes routing once, and returns a typed
   `DispatchOutcome` rather than exposing a graph state mapping.
2. An explicit intent switch sends expense/refund, query/advice,
   correction/undo, and category follow-up to their independently compiled
   specialists. Clarification terminates safely. Receipt, command, non-finance,
   and unsupported intents remain typed handoffs because their behavior belongs
   to later roadmap steps.
3. Each graph keeps its narrow state schema and a namespaced stable thread ID.
   No union graph state carries unrelated boundary answers, and domain records
   stay in PostgreSQL behind application services.
4. A movement that creates a category proposal opens the persistent review
   checkpoint before the dispatcher returns. A later correlated decision
   resumes that exact proposal. Direct Italian J3 statements use a bounded pure
   parser; text that does not explicitly name a branch cannot settle a review.
5. Category callbacks must still be present in the trusted pending-review set.
   Movement callbacks resolve through a trusted pending-action-to-movement map;
   the callback identifier is never guessed to be the movement identifier.
6. The component owns orchestration, no data or presentation. It runs in the
   host administration CLI now; Step 4's durable worker will construct context
   from its repositories and call the same operation.

[AD-016](decisions/AD-016-explicit-workflow-dispatcher.md) records why this is
explicit composition rather than a LangGraph containing every specialist as a
subgraph. The choice keeps checkpoint lifecycle, replay behavior, and state
migration local to each bounded workflow.

### Current version-one finance wire contracts

1. The web transport adapter validates the canonical `Idempotency-Key` header
   separately from a strict expense request body; unknown fields and a
   caller-supplied profile scope are rejected.
2. Exact signed cents, EUR, aware occurrence time, stable UUID references, and
   non-zero movement semantics are validated before application dispatch.
3. Register reads map real domain expenses to stable IDs, ISO timestamps,
   lifecycle status, allocations, and optimistic versions. Mutation responses
   return only identity, lifecycle, and version; neither shape carries a command
   key, trusted profile, source reference, repository detail, media path, or
   secret.
4. Failures use a bounded problem-details schema and the
   `application/problem+json` media type.

### Current local overview and movement requests

1. The browser requests the compiled shell and then an exact `YYYY-MM` overview
   or movement register from the same origin.
2. Middleware rejects an unapproved Host or present Origin before routing. An
   unsafe method additionally needs an allowed Origin and matching CSRF cookie
   and header.
3. Runtime composition selects the sole profile and supplies the narrow
   `DashboardReader` and movement adapter; no HTTP value can change owner scope.
4. `ReportingService` reads posted overview facts or every monthly lifecycle
   state plus editor references from PostgreSQL. Both responses carry exact EUR
   minor units, and the overview dimensions reconcile independently.
5. A create, complete replacement, post, or cancel request is validated
   strictly, bound to an idempotency key and observed version where applicable,
   and delegated to `FinanceService`. Persistence changes aggregate and audit
   state atomically; cancellation keeps the record as discarded or voided.
6. TanStack Query renders Italian loading, error, empty, and result states and
   invalidates both monthly reads after a successful write. ECharts and its
   adjacent table expose the same overview values; the movement table retains
   complete lifecycle and allocation facts.

The overview and movement adapters own no authoritative state, model call, or
receipt media access. Real-browser E2E drives this sequence through the
production entry point and PostgreSQL, including a correction, a cancellation,
and the overview returning to zero.

### Current browser advice

1. `/consigli` sends a bounded message to `POST /api/v1/advice`. POST keeps the
   owner text out of URL and access metadata; CSRF applies because the method is
   unsafe, while no `Idempotency-Key` is accepted because no mutation occurs.
2. The adapter enters the existing advice read graph with trusted intent. It
   composes the promoted model adapter, `ReportingService`, configuration
   reads, and a clock, with no dispatcher, checkpointer, or mutation service.
3. Tool-selection v5 and `explicit-finance-scope-v1` choose only among
   `budgets_at_risk`, `category_spending`, and `funding_class_split`; the direct
   category-budget lookup remains query-only.
4. Profile, current `Europe/Rome` month, active category names, and active
   funding-source names come from runtime composition and current configuration,
   not from an HTTP field or model output.
5. The model chooses scope once and supplies no figure. Registry acceptance,
   classification repair, and `ReportingService` execution are deterministic;
   the HTTP evidence union validates and publishes only the resulting tool JSON.
6. An empty budget survey becomes explicit `insufficient_data`; a category
   result says no threshold exists; a funding result says consumption is not a
   wallet balance. The promoted registry has no historical-comparison advice
   tool, so the page links to `/analisi` instead of inventing a percentage.
7. React renders initial, loading, clarification, insufficient-data,
   dependency-error/retry, and answered states. Tables expose every numeric
   evidence item directly. Advice owns no query cache, so finance writes do not
   invalidate or silently rerun a prior owner question.
8. Responses and logs omit the request, prompt, completion, profile, merchant
   and category fields from logs, amounts from logs, credentials, URLs, private
   paths, receipt media, and infrastructure details. No advice write exists.
9. With tracing switched on, the request is one trace named `web-advice`,
   derived from a `web:advice:<uuid>` reference generated for that request: an
   `advice-request` root with the message, trusted profile and instant, the
   selection versions, the read decision metadata, and the tool facts, over the
   read graph's node spans and its `model:<Contract>` generation. The graph still
   sees the fixed `web:advice` source reference, and an untraced request invokes
   it without any configuration. The journal's `web.advice.completed` names the
   request reference, so an operator can find the trace without owner content.

### Current merchant and period comparison analytics

1. The web adapter resolves one current day, ISO week, month, year, or bounded
   custom interval and asks the domain for its only V1 predecessor. Named
   selectors keep their calendar unit; a custom selector uses the immediately
   adjacent earlier block with the same inclusive Rome-day count. An
   unrepresentable predecessor is refused before any repository read.
2. `ReportingService.range_overview` performs two bounded posted-only reads
   through the existing repository port. Current and previous payment
   allocations produce exact signed net totals; their difference is the
   monetary delta. Relative variation is integer basis points over the absolute
   previous total, rounded half away from zero, or explicitly undefined when
   the previous total is zero.
3. The current movement set is also grouped by optional parent merchant
   metadata after Unicode NFKC, whitespace collapse, and case folding. Null and
   blank values share one missing bucket. Every posted parent contributes its
   signed total once; allocations and receipt lines never enter this grouping,
   so merchant, purpose, and funding views reconcile independently and are
   never summed together.
4. Budget positions keep the existing exact-whole-month rule and are neither
   prorated nor part of the prior-period comparison. No analytics write,
   migration, or new data-owning component exists.
5. React renders both projections with keyboard-focusable lazy ECharts views
   and equivalent exact tables. The missing merchant and zero-baseline reason
   are explicit; loading, empty, dependency failure, retry, direct navigation,
   and bookmark behavior remain available. Mutations that can change the facts
   invalidate the range query.
6. Profile scope remains injected by composition. Responses and logs exclude
   profile scope, merchant and description content from logs, monetary amounts
   from logs, credentials, URLs, tokens, private paths, receipt content, and
   infrastructure detail. The model-visible monthly read and advice tools are
   unchanged, so AD-012 and AD-017 evaluation gates are not triggered.

### Current browser category-proposal review

1. The browser loads every pending proposal, the current active taxonomy, and
   the movement each review blocks through one profile-scoped control-plane
   adapter.
2. PostgreSQL correlates proposal and movement through the trusted source
   reference written by the expense workflow. The service requires one exact
   match; missing or ambiguous state emits a structured error with identifiers
   and counts only. The response omits profile, source reference, channel data,
   and command records.
3. The browser sends only a proposal ID and, for reuse, a current category
   display name. It never supplies the movement ID. `CategoryReviewService`
   rechecks that proposal and movement share profile and source before applying
   approval, reuse, or cancellation.
4. Every branch requires the canonical proposal-wide HTTP idempotency key that
   the existing review transaction claims. Approval creates a taxonomy node
   and posts the correlated movement atomically; reuse resolves an active name
   or alias and posts without node creation; cancellation terminates the
   proposal and discards the never-posted movement.
5. The React page offers the stored shortlist and a labelled selector over all
   active categories, with loading, empty, retryable failure and pending-action
   states. Successful decisions invalidate proposal, movement, overview, and
   budget reads.
6. A request for another model candidate remains in the persistent Telegram
   workflow. The web object graph contains neither a model nor a proposal
   generator; manual active-category reuse provides a deterministic browser
   resolution without widening taxonomy authority or changing a model-visible
   contract.

The application service and PostgreSQL own decision and audit semantics. The
FastAPI adapter owns strict JSON, CSRF, bounded problems, and trusted profile
injection; React owns ephemeral selection state only.

### Current browser category administration

1. The browser reads all active and archived nodes through a dedicated
   category-management adapter. The response includes stable identity,
   hierarchy, guidance, aliases, seed provenance, optimistic version, and soft
   complexity warnings, but no profile or command data.
2. Rename and archive call a separate `CategoryAdministrationService`; they do
   not share proposal-creation authority. The domain rebuilds the whole
   taxonomy and refuses ambiguity, archived-node transitions, or an archived
   parent with an active child. IDs, parents, guidance, aliases, and history do
   not change.
3. PostgreSQL locks all profile nodes in stable order, checks the displayed
   version, and commits replacement, idempotency claim, and audit in one
   transaction. Bootstrap validates immutable seed semantics without reverting
   an owner-managed display name or archive state.
4. FastAPI injects profile and actor, accepts no delete/reparent/create field,
   and applies the same CSRF, idempotency, bounded-problem, and `no-store`
   policy as other mutations.
5. React asks for archive confirmation, prevents archiving a root with active
   children, and invalidates every category-dependent read after success. It
   owns only edit and confirmation state.


### Current browser funding-source and wallet administration

1. The browser loads all active and archived payment resources for the trusted
   profile. The response exposes stable source identity, economic class,
   immutable `BalanceMode`, default state, and optimistic version. It returns no
   profile, command identity, or wallet note.
2. An untracked source has no balance projection. For a ledger source the
   application reads immutable entries once and computes booked, available,
   and remaining dated lots at one shared observation instant using AD-020's
   Europe/Rome first-expiry-first policy.
3. Create, rename, default, archive, and manual opening/credit/adjustment use a
   dedicated application service. FastAPI injects profile and actor, validates
   strict JSON, CSRF, and `Idempotency-Key`, and maps stale source/default
   snapshots to bounded conflicts. No route deletes a source or entry, changes
   class/mode, transfers funds, or reaches banking.
4. PostgreSQL serializes source lifecycle on the profile and affected source
   rows. Moving the default advances old and new sources plus the profile
   pointer with both audits; archive rechecks zero booked and available balance
   under the same source lock used by append. A wallet append validates the
   complete effective history and commits its claim, immutable row, and audit
   atomically.
5. React labels booked and spendable values separately, shows dated lots and
   history, confirms default/archive/negative-adjustment actions, and disables
   archive for the default or a nonzero wallet. Success invalidates source,
   movement, overview, and analytics queries.

The source service owns lifecycle and wallet policy; the web adapter owns only
trusted translation and HTTP semantics; the browser owns ephemeral editor and
confirmation state. Spending categories remain a separate purpose dimension.
### Current browser profile settings

1. FastAPI invokes `ProfileSettingsService` with the sole profile identifier injected at composition. No query, header, cookie, body, or response field can select another profile.
2. The service reads the persisted profile and active funding sources through `ProfileSettingsRepository`, then requires the profile pointer and the single active default flag to identify the same source. An empty initial profile is valid; inconsistent state fails closed before serialization.
3. `GET /api/v1/settings` exposes only `it-IT`, `EUR`, `Europe/Rome`, the minimal current default, and minimal active choices. Profile identity, wallet history or balances, owner notes, database and service URLs, credentials, tokens, private paths, bot scope, receipt keys, and infrastructure details are absent by contract.
4. Locale, currency, and timezone are immutable V1 domain and database invariants, so there is no generic settings mutation. Choosing a default reuses `FundingSourceAdministrationService` and `POST /api/v1/funding-sources/{id}/default`; strict JSON, CSRF, idempotency, target and previous-default versions, profile/source locks, atomic pointer/flag replacement, and both audits stay in one implementation.
5. React owns only selection, confirmation, and feedback state. It renders loading, read-only facts, actionable empty, retryable failure, and mutation success/failure states, then invalidates every dependent query after a confirmed default change.

This read model is a real security and application boundary, not a second owner of profile lifecycle. Remote access, credentials, pairing, destructive operations, and infrastructure configuration remain absent; categories remain distinct from payment sources.

### Current authoritative backup and restore drill

1. `money-tracker-backup create` validates executables and owner-only
   secret/configuration files, then takes an exclusive host lock instead of
   queueing behind another operation.
2. It requires the connected database name to equal the configured expected
   name, reads the Alembic revision, and re-hashes every receipt the database
   references before anything is written.
3. A mode-600 `pg_dump` and a versioned manifest of application version,
   revision, dump/configuration checksums, and receipt hashes are staged in a
   private mode-700 directory.
4. Restic backs up staging, the receipt archive, and the runtime configuration
   as one tagged snapshot. Repository check and exact snapshot verification run
   before staging is discarded.
5. Retention applies only afterwards, grouped by host and tag so that
   per-run staging paths cannot defeat the daily/weekly/monthly policy.
6. `money-tracker-backup status` reports the newest snapshot and its age for
   local health checks.
7. `money-tracker-backup restore-drill` accepts a hexadecimal snapshot and
   requires both an empty isolated database and an absent or empty real receipt
   directory. It verifies manifest compatibility and every checksum in staging,
   refuses symlinked restored paths, imports the dump, then re-verifies each
   receipt hash at the destination.
8. The restore then runs the same read-only semantic gate exposed independently
   as `money-tracker-backup verify-restore`: schema identity, finance
   reconciliation/scope, replay targets, Telegram offset/queue/action
   correlations, and every non-deleted receipt's mode and content hash must all
   pass before success is reported.

Credentials reach subprocesses only through libpq environment variables and the
Restic password-file path. Failures name the failing executable alone. A
persistent daily user timer invokes the same command at 03:15 local time with
up to thirty minutes of jitter after explicit owner enablement. The target
timer is installed, enabled, and active after successful production-service
runs. A temporary two-minute override proved the timer-to-service path, then
was removed and the daily calendar restored; multi-run retention evidence
remains open. Business reconciliation is now
a repeatable executable check. Off-workstation access to the recovery secret
passed from the owner's authenticated Android password manager; the
clean-machine environment, service startup, and measured recovery acceptance
remain open.

### Current portable finance export

1. `money-tracker-export` requires one explicit profile UUID and a new absolute
   destination outside the checkout whose parent is owned by the invoking user
   and has no group or other permissions.
2. The adapter opens a read-only repeatable-read PostgreSQL transaction, checks
   the exact supported Alembic revision, and selects every field through
   explicit, deterministically ordered projections rather than `SELECT *`.
3. It writes a version-one JSON Lines manifest followed by typed profile,
   taxonomy, funding-source, receipt-metadata, movement, allocation, wallet,
   budget, proposal, and audit records. UUIDs, dates, timestamps, integer minor
   units, booleans, and nulls keep portable JSON representations.
4. Receipt bytes and archive paths, workflow checkpoints, messaging state, and
   transport idempotency records stay outside the export. Historical import is
   outside V1.
5. The file is created exclusively with mode 600, synchronized before success,
   and removed if writing fails. Standard output contains only safe counts,
   format version, profile identifier, byte count, and SHA-256 digest; neither
   owner content nor the output path enters the journal.

The export is a portability artifact, not a recovery backup. It is not
encrypted and contains owner finance content, so the operator protects and
deletes it explicitly. The encrypted Restic snapshot remains the recovery
mechanism.

### Current runtime content retention

1. `money-tracker-retention` computes the cutoff as now minus the window, 30
   days unless the operator names at least one whole day, and opens one
   PostgreSQL transaction whose lock waits end after five seconds.
2. It refuses a database whose Alembic revision is not the supported one.
3. It deletes consumed or cancelled pending actions that concluded before the
   cutoff, then sent outbox messages no remaining pending action refers to.
4. It clears the inbox link of any sent message still carrying an active
   control whose inbox item is otherwise concluded, then deletes completed
   inbox items that no outbox message refers to any more. Dead letters and
   items with undelivered or retrying responses stay whole.
5. If the checkpoint tables exist, it selects threads whose newest checkpoint
   precedes the cutoff and whose family, read through `workflows.threads`, can
   no longer resume: a source-keyed thread whose inbox item is completed or
   absent, or a category review whose proposal is not pending. It deletes each
   such thread from all three LangGraph tables.
6. It counts the rows older than the cutoff that it deliberately kept, then
   commits, or rolls back for `plan` and an unconfirmed `delete`, so a plan
   reports exactly what a confirmed run would delete.
7. After a confirmed run that deleted anything it runs `VACUUM (ANALYZE)` on
   the affected tables; a failure there is a warning, because the deletion has
   already committed.

The persistent daily user timer runs the confirmed default at 04:45 local time
with up to thirty minutes of jitter after explicit owner enablement. Replay
safety is unaffected: finance idempotency records and the durable offset are
never touched. The encrypted Restic snapshots keep deleted rows until they
age out of the backup policy.

### Current text expense turn

1. A normalized channel message receives a source and command idempotency key.
2. The root router produces a typed intent decision.
3. The expense workflow extracts amount, time, description, merchant and payment
   allocations, using the profile's default funding source when omitted.
4. Deterministic validation rejects malformed money, missing invariants, and
   ambiguous data before any write.
5. Category resolution selects an adequate existing node or persists a proposal
   and interrupts the workflow.
6. Every resolved movement remains a draft because EVD-004 promoted no
   automatic-post subset. Unresolved input remains in review and asks a targeted
   question.
7. The owner receives a summary and may explicitly post, modify, or cancel it.

### Current conversational category review

1. The workflow persists a proposal and emits a serializable review payload.
2. The owner approves it, requests the best existing category, names an existing
   category, requests another proposal, or cancels.
3. The inbound reply maps to the exact pending workflow and resumes its
   checkpoint.
4. Creation and expense posting occur only after the interrupt in an idempotent
   database transaction.
5. The implemented re-proposal path excludes candidates rejected in the
   current review; after
   three rejections the interface requires existing-list or manual resolution.

### Planned receipt review

This sequence belongs to Step 6, which the owner deferred after the personal V1
release on 2026-09-19. The V1 runtime stops before step 2: an image is routed
deterministically to the `receipt` intent, which answers in Italian that
photographed receipts are not handled yet, and nothing is downloaded.

1. Telegram media metadata is durably ingested before processing.
2. The worker downloads and validates the image, hashes it, and stores it outside
   the web root.
3. The selected local extractor emits a versioned draft with source evidence and
   confidence.
4. Deterministic reconciliation checks total, receipt lines, category
   allocations, payment allocations, discounts, and rounding.
5. The owner reviews the complete draft through Telegram or the dashboard.
6. Only an approved, reconciled draft is posted; otherwise it remains in review.

### Telegram recovery and processing (implemented and accepted)

The migration/repository, production gateway, response renderer, sequential
worker, pairing, and host units implement every step below. Owner workflows,
fault injection, latency, restart, and unattended reboot acceptance passed
against the real private test bot on 2026-09-11.

0. An operator generates one short-lived code and the owner sends it as a
   private `/pair`. The code is consumed, the sole owner binding is created,
   and a confirmation is queued in the same transaction; the message carrying
   the code is never retained.
1. The gateway requests the environment's persisted next offset through long
   polling and filters authorization and supported update types before work.
2. One PostgreSQL transaction inserts minimum normalized accepted updates under
   `(bot_environment, update_id)` uniqueness and advances the offset beyond
   every observed update. The next poll acknowledges only that commit.
3. The worker leases only the oldest non-terminal record. A future retry or
   live lease deliberately blocks later rows; an owner-waiting workflow is
   complete once its question and pending action are durable.
4. The worker translates the row to `ChannelMessage`, builds trusted
   correlations from repositories, and calls only `WorkflowDispatcher.dispatch`.
   If `Modifica` needs replacement details, its clarification commits a new
   pending mapping; delivery binds that question's message ID to the movement,
   so a reply after restart cannot drift to a newer write.
5. Finance writes use their existing replay-safe application transactions. The
   typed result, unique logical outbox row, pending action, and inbox completion
   then commit in one messaging transaction. A crash between them redispatches
   the same source identity and reconstructs the prior mutation result. The
   rendered response must reconstruct too, so its text and controls are derived
   only from the outcome and committed state; live queue figures would make a
   recovery collide with its own first response.
6. A retry or dead-letter transition atomically creates a stable Italian owner
   notice without its internal reason code. Five transient failures lead to a
   visible dead letter. Manual replay is constrained by the configured bot
   environment plus inbox ID, keeps the source identity, and never rewinds the
   Telegram offset.
7. The independent outbox loop records delivery attempts and Telegram message
   correlation. A lost Bot API response may duplicate physical delivery, but
   cannot create a second logical response or finance mutation. An exhausted
   response is listed without content and can be explicitly resent by its
   bot-scoped outbox ID. This retains the logical row, records a replay cycle,
   and starts a fresh bounded attempt budget; the operator accepts that an
  ambiguous earlier send may make the physical message appear twice.

NFR-PER-001 is measured from the durable inbox acceptance instant through the
successful Bot API send commit. The operator selects an explicit time window;
the first successful non-media text workflow is excluded as warm-up and the
remaining sample uses nearest-rank p95. Telegram client rendering time is not
observable through the Bot API and is recorded as a limitation rather than
silently included in the claim.

Since Step 7 the report also splits each warm sample at the worker's
completion commit, into the worker side (acceptance to completion) and the
gateway side (completion to send), counts retried samples, and groups totals by
response kind. When tracing is on, `money-tracker-traces profile` refines the
worker side from the stage spans of each traced update, into wait before the
last attempt, the attempt itself, and delivery, and reports the duration of
every model boundary, graph node, and stage. It reads names and times only.
Both use the nearest-rank convention in `money_tracker.latency`.

### Planned insight request

1. The router selects a spending, budget, wallet, or advice workflow.
2. A bounded agent receives only the read tools needed for that route.
3. Tools compute amounts, periods, budget status, and comparisons
   deterministically from posted allocations.
4. The final answer may narrate only facts returned by tools and states its
   period and interpretation of `too much`.

### Current pull-request CI run

1. A pull request is opened or updated. `ci` and `pr-metadata` start on
   GitHub-hosted runners with a read-only token; a newer run on the same PR
   cancels the older one.
2. `pr-metadata` checks out the PR head and runs `git_policy` over the title,
   the branch, every commit in `base..head`, and the body's template sections.
3. The nine `ci` jobs run in parallel. Static, unit, frontend, deployment,
   shell and workflow, and security jobs need no service. `postgres-integration`,
   `backup-drill`, and `e2e-browser` generate a disposable `.env`, start
   `application-postgres` from `compose.yaml`, wait for TCP readiness, and run
   the real suites against `money_tracker_test`.
4. Each suite's JUnit report passes through `ci_reporting`, which appends its
   counts to the job summary and fails a service job that executed no test or
   skipped for its own missing flag.
5. On failure, `ci_redaction` copies service logs and reports without generated
   credentials, and the job uploads that evidence for seven days.
6. `ci-gate` reads every job result and succeeds only when all nine succeeded.
   `main` pushes and the weekly schedule rerun the workflow, with gitleaks over
   the full history.
7. The `protect-main` ruleset lets the owner merge only when `ci-gate` and
   `pr-metadata` succeeded on a head that contains the current `main` and every
   conversation is resolved; otherwise GitHub reports the pull request as
   blocked.

## 7. Deployment View

### Current deployment

```text
Ubuntu workstation
├── systemd user services: accepted API, Telegram, worker, llama.cpp units
├── persistent backup timer [implemented; target enabled, timer-trigger accepted]
├── persistent trace/runtime-retention timers [implemented; target enabled and timer-trigger accepted]
├── money-tracker-api -> 127.0.0.1:8000  [implemented and browser-accepted]
├── compiled React assets served by money-tracker-api
├── UV-managed Python CLIs
├── optional llama-server on 127.0.0.1:8080  [not installed/configured here]
├── private receipt archive and Restic staging  [owner-configured absolute paths]
├── external Restic repository                  [owner-selected; daily timer checked in]
└── Docker Compose
    ├── application-postgres on 127.0.0.1:5433  [default, healthy]
    └── observability profile                    [optional, healthy; AD-021 trace store]
        ├── Langfuse web on 127.0.0.1:3000
        ├── Langfuse worker
        ├── PostgreSQL
        ├── ClickHouse
        ├── Redis
        └── MinIO API on 127.0.0.1:9090
```

Application and Langfuse PostgreSQL have separate roles, databases, lifecycles,
and named volumes. The non-cluster ClickHouse setting and digest-pinned images
are explicit. The repair retained the pre-existing observability volumes; all
profile services pass their health checks.

Tracing is switched per process by `MONEY_TRACKER_TRACING_ENABLED`, default
false, with the Langfuse keys and loopback base URL from `.env`; every host unit
reads that file, so the switch set there affects the gateway, worker, and API at
their next start. A traced process keeps a private digest cache of model
artifacts (mode 0700/0600) and reads llama.cpp `/props` with the inference key.
The API opens its tracer with background identity, like the worker, and flushes
it after uvicorn stops. Since 2026-09-18 `.env` sets
`MONEY_TRACKER_TRACING_ENABLED=true`, so the installed gateway, worker, and API
trace into the `development` environment, and the re-rendered units are
installed. The llama.cpp launcher hands the inference key to the server
through `--api-key-file` on a pipe rather than `--api-key`, so no process
listing shows it. The worker and API templates declare
`CacheDirectory=money-tracker`, which is
`~/.cache/money-tracker` for a user unit and stays writable under a read-only
home, and put `~/.local/bin` on `PATH` so the executable digest resolves after
an unattended boot; installed units change only when re-rendered. Since AD-021
the Langfuse volumes hold owner content, so their exposure matches the
application database; by owner decision they stay outside the authoritative
backup set as a reconstructible copy kept 30 days.

The units' mount-namespace directives are declared but not in effect on this
workstation: with unprivileged user namespaces restricted by AppArmor, the user
manager starts each service in its own user namespace but the host's mount
namespace, so `ProtectHome=read-only`, `ProtectSystem=strict`, and
`PrivateTmp=true` do not restrict it. This was observed on 2026-09-17 by
comparing namespaces and writing under a protected home from a transient unit;
qualifying the sandbox is Step 7 work.

### Target deployment

```text
Ubuntu workstation
├── systemd host services
│   ├── money-tracker-api       -> 127.0.0.1, same-origin API and UI [Step 5]
│   ├── money-tracker-telegram  -> Telegram HTTPS API
│   ├── money-tracker-worker    -> application PostgreSQL and local models
│   └── llama-server            -> 127.0.0.1 OpenAI-compatible API
├── private application data
│   ├── receipt archive
│   └── private evaluation artifacts
└── Docker Compose
    ├── default service: application PostgreSQL and persistent volume
    └── observability profile: Langfuse stack and independent volumes

External backup folder/disk
└── encrypted Restic repository
```

`deploy/systemd/` holds checked-in user-service templates for the API, gateway,
worker, llama.cpp, authoritative backup, trace retention, and runtime content
retention, plus persistent daily timers for the last three. They are rendered
for a checkout by
`scripts/install_systemd_units.sh`. They are user services because the
application owns no system-wide resource, and they are not ordered against
Docker because a user unit cannot be: a database that starts late is handled by
process exit codes instead. Exit code 1 means an operator must change something
and the unit stops;
this includes `database_schema_invalid` when required migrations are absent.
Exit code 2 means a dependency was unreachable and the unit restarts. The
Python entry points replace psycopg diagnostics with stable reason codes.
The three Step 4 units passed enabled-service and reboot acceptance. The API
unit passed installed browser acceptance on 2026-09-16, including readiness
after a real `systemctl --user restart`; because it is `Type=simple`, the unit
reports active before uvicorn listens, so readiness is the HTTP probe.

Systemd user services use lingering, or an equivalent host setup, to recover
without a manual application start. Each service tolerates delayed database,
network, and inference readiness through health checks and retry policies.

Step 4 installed and accepted the Telegram gateway, workflow worker, and
llama.cpp units. The first Step 5 slice adds the API unit and compiled-asset
preflight without coupling Telegram recovery to dashboard availability.

Step 7's [upgrade and rollback runbook](../operations/upgrade-and-rollback.md)
coordinates these deployment owners without adding an orchestrator. It freezes
gateway, API, worker, and operational timers; creates a verified authoritative
snapshot; migrates forward; installs the reviewed units; accepts readiness;
and reopens ingress last. Returning to prior code or image digests requires
explicit schema/storage compatibility. An incompatible rollback restores only
to an isolated target until the owner authorizes a separately reviewed
production cutover.

The developer checkout also contains versioned Git hooks and shared Claude Code
settings. The hooks run through the UV-managed application environment and are
installed per clone; the Claude hook invokes the same packaged development
boundary. Neither is deployed with the runtime services or owns application
data.

GitHub-hosted Ubuntu runners execute the AD-022 workflows. Each job starts from
a clean runner, installs the locked environments and SHA-256-pinned tools, and
creates only disposable services and credentials; nothing reaches the
workstation, its services, or owner data. Failed evidence stays in GitHub for
seven days. The `protect-main` repository ruleset enforces the merge boundary
on GitHub itself.

## 8. Cross-Cutting Concepts

### Money and time

- Monetary values use integer minor units plus ISO currency; binary floats are
  prohibited at storage and public interfaces.
- A model states an amount as a positive magnitude and a direction word. The
  sign the domain expects is applied deterministically, so a stray minus sign
  cannot turn a purchase into a reimbursement.
- Both category and payment allocations reconcile exactly with the parent total.
- Payment reconciliation is checked during deterministic resolution, not at the
  model boundary. An unquantified split is a schema violation because it could
  never reconcile, but portions that simply do not add up are a statement by
  the owner: they become one targeted question instead of counting against
  output-schema validity.
- The occurrence time, not processing time, controls reports and budgets.
- Relative Italian dates resolve against the source message timestamp in
  `Europe/Rome`. The implemented resolver is deterministic and pure: a model
  reports the owner's temporal words verbatim and never an instant, because
  timezone and daylight-saving arithmetic must be exactly reproducible across a
  retry, a resume, and an evaluation run.
- Silence and abstention are distinct. A message with no temporal words
  describes a movement that just happened; words that are present but
  unreadable are an error that asks the owner, never a substituted default.
- A resolved occurrence day is never after the message that reported it.
- Dashboard date-time fields show and accept `Europe/Rome` wall time in any
  browser time zone. The frontend converts explicitly between the instant and
  Rome wall time: a wall time repeated when summer time ends resolves to its
  first occurrence, and one skipped when it starts moves forward by the skipped
  hour. A correction that leaves the field untouched keeps the recorded instant.

### Identity, authorization, and idempotency

- One paired profile owns all V1 data. Profile identifiers come from trusted
  runtime context, never model-generated tool arguments. The implemented router
  model boundary has no field in which a model could supply one.
- Routing authority is partitioned in code. Every intent belongs to exactly one
  authority class, so a question, an image, or non-finance text cannot be
  answered by a financial write path, and a model cannot claim a route that
  must stay reproducible.
- A correlation target is never inferred. A review follow-up, correction, or
  undo uses a pending-action or movement reference the runtime resolved, and an
  uncorrelated or ambiguous case is clarified.
- A funding source is named by the owner and resolved deterministically against
  the profile's configured active sources. The model supplies words, never an
  identifier, and it cannot select the default. Matching is exact on the folded
  configured name with no synonym guess, because silently spending a benefit
  balance instead of personal funds is worse than asking one question.
- A category is likewise named and never identified. Resolution happens against
  the profile's active taxonomy, so a judgment cannot reference a node that
  does not exist, and stored history is anchored to stable identifiers rather
  than display names. Every category allocation records the boundary version
  that classified it.
- Version-one HTTP mutation contracts carry that key only in the required
  `Idempotency-Key` header; request bodies cannot shadow it.
- Every external update and financial command has a stable idempotency key.
  Movement creation, correction, and undo derive separate namespaced keys from
  the durable inbound record, so a redelivery claims the same key and the
  repository returns the first result. The accompanying request digest covers
  what the owner asked for
  and excludes lifecycle status, version, and generated identifiers, and
  normalizes the occurrence instant to UTC so the same moment expressed with a
  different offset matches. A redelivery must therefore replay the stored
  record unchanged, its channel timestamp included.
- An owner category decision keys on the proposal rather than on the inbound
  record, because approval, reuse, cancellation, and re-proposal are mutually
  exclusive answers to one question. The identical branch replays into its
  first movement or successor proposal, while a competing branch conflicts
  without a second finance write.
- A receipt review decision keys on the transition rather than on the receipt,
  because a receipt is reviewed in successive steps and each step is a separate
  command. The key names the receipt and the version the caller observed, so a
  retry of one decision replays it while a different decision taken from the
  same displayed state conflicts.
- Durable messaging state is scoped by bot environment rather than by profile.
  The scope is bound at composition time from configuration and no HTTP
  argument accepts one, so a request cannot name another bot.
- A messaging requeue is not a financial write and claims no entry in the
  general command table: that table is claimed in the same transaction as the
  finance state it protects, while a requeue commits in the messaging schema
  through a different repository. Its durable pin is the row's own
  `replay_count`, checked and advanced inside the one transaction that requeues
  it, and its HTTP key names that exact transition. A repeat of the same
  transition is reported as already applied instead of requeuing twice.
- Callback and free-text follow-up map to explicit pending actions and workflow
  IDs instead of assuming one global chat thread.

### Local web browser security contract (implemented surface)

- Typed configuration accepts only an IPv4 loopback listener, exact loopback
  Host names, exact HTTP origins, and an absolute compiled-asset directory.
- Host validation occurs before routing to block DNS rebinding. A present Origin
  must match exactly, and the service emits no CORS permission headers.
- Unsafe methods require an allowed Origin plus equal random values in an
  HttpOnly, same-site cookie and the `X-CSRF-Token` header. Every mutation
  additionally requires `Idempotency-Key`; movements and budgets use
  their command keys and optimistic versions, category management quotes the
  displayed node version, category decisions require the proposal-wide key
  already claimed by the review transaction, receipt
  review decisions require the transition-wide key that names the receipt and
  the version the page displayed, and messaging requeues require the
  transition-wide key that names the queue, the item, and the requeue count the
  page displayed.
- API responses are `no-store`. CSP restricts content and connections to the
  same origin; framing, referrers, and MIME sniffing are disabled. Interactive API schema endpoints are absent.
- Static serving exposes only the compiled index and asset directory. Every
  client route is mounted so a reload or bookmark reaches the same shell. The
  API returns no credential, profile identity, bot key, receipt path or content
  digest, owner text, or raw database diagnostic, and no route returns receipt
  bytes or Telegram message content.
- V1 intentionally trusts other processes running as the workstation owner.
  LAN or remote access requires authentication, TLS, a new threat model, and an
  ADR rather than a wider allowlist.

### Telegram security and recovery contract (implemented and accepted)

- Production and test bot environments have separate tokens and offset rows. A
  process selects exactly one environment and never falls back to the other.
  The credential installer accepts hidden standard input, treats `.env` as
  data, refuses shared/unsafe paths, and atomically writes a mode-0600 file
  without printing its value or location.
- Authorization happens before inbox creation, media lookup, workflow dispatch,
  or finance reads. Unsupported and unauthorized updates advance the observed
  offset without retaining private payload content.
- The sole owner binding requires an unexpired one-use hash-verified pairing
  code in a private chat. Groups, channels, and foreign users cannot create
  workflow work or receive finance content.
- The gateway stores only normalized fields required for recovery. Bot tokens,
  token-bearing URLs, raw updates, usernames, and unnecessary file metadata are
  absent from rows, logs, traces, fixtures, and health output.
- Persisted offset recovery never rewinds domain work. Replay selects an inbox
  item only within the configured bot environment and preserves its source
  identity; it does not alter Telegram
  acknowledgement state.
- A measured gap of more than 24 hours marks health degraded and queues a
  generic possible-loss warning. Recovery can request manual resend but cannot
  identify or reconstruct expired updates.
- Leases recover abandoned processing and delivery claims after process death.
  Five failed attempts produce visible inbound or outbound dead letters; inbox
  retries and terminal failures create idempotent owner notices atomically; safe
  operator actions are inspect, retry eligibility, or explicit replay.
- The authoritative backup includes messaging state through the PostgreSQL dump.
  No separate queue snapshot or in-memory state is required for recovery.

### Configuration and secrets

- Environment files remain mode-restricted and ignored by Git.
- Telegram tokens, database credentials, Restic secrets, and signed media URLs
  are redacted from logs, errors, traces, and frontend responses.
- Production prompts, schemas, taxonomy seeds, and tool definitions are
  versioned in code. Langfuse cannot silently change runtime contracts.

### Privacy and untrusted content

- Telegram transport is an accepted external boundary; local-first must not be
  described as end-to-end local communication.
- Receipt pixels, OCR text, captions, and user prompts are untrusted data, never
  system instructions.
- Validate content type, decoded image limits, dimensions, and storage paths.
- Do not trace image bytes or download URLs.
- Routing justification codes are stable machine-readable values chosen so that
  routing telemetry cannot leak message content. Command arguments, including a
  one-use pairing code, are not carried in a routing decision.
- The implemented local archive accepts at most 20 MiB, 40 megapixels, and
  10,000 pixels per side; it rejects animation and unsupported decoded formats.

### Persistence and consistency

- PostgreSQL owns authoritative state. Workflow checkpoints and Langfuse traces
  are supporting records, not finance ledgers.
- A long-lived process never holds one PostgreSQL session for its lifetime. The
  checkpoint saver draws from a pool that checks each connection before use,
  so a database restart costs the next work unit a reconnect rather than a
  crash and a leased item; repositories open a connection per operation. Concluded messaging rows and
  terminal checkpoints are removed by age; authoritative records never are.
- Implemented expense, wallet, and budget mutations share a transaction with
  their audit event. Future channel mutations also include their outbox record.
- Ledger wallet appends lock the funding source and validate AD-020 available
  balance before append. Funding-source lifecycle changes lock the profile and
  affected sources; archive rechecks zero booked and available balance, while
  default movement updates both source versions and the profile pointer. Budget
  and expense replacements compare versions.
- Categories and financial history use stable IDs. Archive and void replace
  destructive deletion for referenced records.

### Observability and evaluation

- Daily operation works when Langfuse is stopped.
- One Telegram update is one trace across the gateway and worker processes,
  joined by the identifier derived from its source reference rather than by
  propagated context, so neither process depends on the other to trace.
- [AD-021](decisions/AD-021-full-fidelity-llm-traces.md) makes runtime traces
  full fidelity: complete prompts, owner messages, repair turns, completions,
  parsed contracts, decision context, model artifact identity, and the
  prompt, contract, and policy versions in force. They never carry credentials,
  token-bearing or database URLs, receipt-archive paths, or receipt bytes, and
  they go only to the loopback Langfuse behind an explicit switch.
- The trace store and the journal stay separate. Traces never enter the journal
  or a support report; tracing SDK log records are rewritten into content-free
  `tracing.*` events.
- Tracing never changes a model request or a checkpoint. It enters through a
  port injected at entry points, and its faults and a stopped store cost at most
  the bounded flush.
- A promoted configuration is traceable to the runs that promoted it. Every
  runtime version set names the `promotion_decision` and its run identifiers
  when its composed versions match the code-owned promotion record, and
  `money-tracker-eval publish-promotion` publishes that decision's committed
  evidence into the `evaluation` environment under the same keys.
- Deterministic evaluators guard monetary, schema, routing, tool, reconciliation,
  and idempotency behavior. Human or model review supplements subjective quality.
- Private receipt and finance datasets never enter Git.

### Structured logging

AD-019 makes the operational journal a first-class surface, separate from
tracing and from the exit-code contract AD-018 defines for supervisors.

- Every process writes one JSON object per line to standard error through
  `money_tracker.structured_logging`. Standard out stays reserved for the
  machine-readable results several commands print as their output contract.
- A record is a stable event name plus named fields. The name never carries a
  value, so events stay countable and the fields stay deliberate.
- Levels have fixed meanings: `WARNING` is a degraded or rejected outcome the
  system handled itself, `ERROR` is a failure that needs the owner, and
  `CRITICAL` is reserved for a condition that ends the process or puts
  authoritative data in doubt.
- `log_context` binds correlation identifiers to an operation through
  `contextvars`, so a record emitted deep in dispatch or persistence carries
  the work unit that caused it. A binding does not cross into a thread the
  operation did not start, which is why each long-running loop binds its own.
- Uncaught exceptions on any thread are routed through logging rather than the
  interpreter's printer, so a crash is formatted and redacted like every other
  record instead of reaching the journal unfiltered.
- Owner message text, receipt bytes, model completions, category and merchant
  names, monetary amounts, credentials, and database URLs never enter a field.
  Records carry identifiers, stable codes, enum values, counts, versions, and
  durations. Credential redaction applies to messages, fields, and rendered
  tracebacks, and is a defence against tokens only: it cannot recognize owner
  text.
- A shared failure funnel is the place the record is written, rather than each
  of its callers. The web boundary builds every problem response in one
  function, and every database failure of that boundary in one more, so a
  rejected request is recorded with its status, stable code and route, and an
  unavailable database is recorded with the traceback that is the only
  explanation the response deliberately withholds.
- Records from the Langfuse and OpenTelemetry SDK loggers are held at `WARNING`
  or above and rewritten into content-free `tracing.export.degraded` and
  `tracing.sdk.reported` events, with export failures at `WARNING`, because the
  SDKs interpolate endpoint URLs, transport errors, and at `DEBUG` whole traced
  payloads.
- The journal explains behavior. PostgreSQL owns state and the audit trail, and
  backup does not cover logs.

### Error handling

- Expected domain failures return typed, user-actionable outcomes.
- Infrastructure failures remain distinguishable for retry and dead-letter
  handling; they are not converted into fabricated assistant explanations.
- Strict constrained decoding requires every schema property to be present, so
  a boundary schema marks them all required and expresses optionality as an
  admitted null. An optional-in-schema property is not enforced by the decoding
  grammar, and a model that omits one turns an otherwise correct answer into a
  schema failure.
- A model boundary therefore validates shape, not cross-field exclusivity. When
  a model both supplies a value and flags it unresolved, the deterministic layer
  prefers the flag, because a flag leads to a question while a guess would be
  stored.
- Operational constants never enter a model boundary. The V1 currency is one:
  the money contract enforces it, so restating it would be configuration the
  model has no basis to supply and no freedom to vary.
- Structured-output parsing gets one corrective retry, then clarification. The
  implemented boundary expresses that budget as straight-line code rather than
  a loop, so it structurally cannot retry twice, and a persistent failure
  reports only the attempt count. Neither the rejected completion nor the owner
  message enters an exception message, because those reach logs and traces
  while the repair turn does not.
- A repair states only the rejected field paths and reasons, not the rejected
  values, so the retry introduces no untrusted content beyond the prior
  completion, and the quoted completion is truncated so a runaway output cannot
  outgrow the context window.
- Receipt or payment mismatches remain visible drafts.

### Backup and retention

- Encrypted Restic backups include a consistent application database dump,
  retained receipt files, and required recovery configuration. The command is
  implemented and verified; the daily timer is installed and timer-trigger
  accepted, with multi-run retention evidence still open. Step 7 source
  semantic preflight now refuses known inconsistent finance or messaging state
  before snapshot creation or pruning. Production migrated to `20260923_0008`
  on 2026-09-23; its new snapshot passed an isolated drill. The pre-upgrade
  snapshot is held separately until the observation window is accepted.
- Snapshot retention is seven daily, five weekly, and twelve monthly, grouped by
  host and tag rather than by per-run staging paths.
- Every snapshot carries a versioned manifest of application version, migration
  revision, dump/configuration checksums, and referenced receipt hashes.
  Integrity is verified before backup, in restore staging, and again at the
  restore destination.
- Restore refuses a populated database or receipt directory and a snapshot from
  a different application version, so a drill cannot damage live data.
- The recovery secret has an off-workstation copy and is passed to Restic only
  as a file path. A tested restore is a release gate; the isolated drill passes
  and the clean-machine drill remains open.
- Langfuse is not authoritative and stays outside the encrypted backup set by
  owner decision (2026-09-17): its volumes are a reconstructible observability
  copy whose loss loses traces, never finance data. AD-021 puts owner content
  in it, so runtime traces are kept 30 days and may be exported to a private
  local file first with `money-tracker-traces`, which deletes by age or
  identifier and verifies each deletion; Step 7 schedules age deletion daily.
- Concluded runtime content in application PostgreSQL, meaning Telegram inbox
  and outbox rows, consumed controls, and terminal checkpoint threads, is
  deleted 30 days after it concludes by `money-tracker-retention` and its daily
  timer ([AD-024](decisions/AD-024-runtime-content-retention.md)). Active
  controls, paused reviews, and dead letters are kept until they conclude.
- Deletion is logical. A deleted row survives in every Restic snapshot taken
  before it, for as long as that snapshot is retained, up to about a year
  under the monthly policy; free database pages and WAL may hold its bytes
  until overwritten. Only full-disk encryption protects the medium. The
  [data-retention runbook](../operations/data-retention.md) lists every data
  class, its retention, and its residual copies.

### Data portability

- The implemented profile-scoped export is the stable machine-readable
  portability boundary defined by
  [AD-023](decisions/AD-023-portable-finance-export.md), not a PostgreSQL dump.
- A manifest pins the format and database revision; additive or breaking
  contract evolution requires an explicit exporter version and documentation.
- Export files contain owner finance content and are intentionally outside the
  journal, checkout, and automatic backup set. They are private but not
  encrypted; custody and deletion belong to the operator.
- Import is outside V1. A future importer must validate the manifest and all
  domain invariants rather than replaying rows directly into tables.

### Testing

- Unit tests exercise production domain behavior and replace only genuine
  external boundaries when isolation is the goal.
- Integration tests use real PostgreSQL, inference, Langfuse, Telegram, and
  media protocols for the components in scope.
- End-to-end tests traverse real user entry points and clean isolated persistent
  records.
- Browser E2E uses Playwright and headless Chrome against the production
  `money-tracker-api run` entry point, the compiled build, a guarded
  `money_tracker_test` database seeded through production services, and the
  promoted local model; in CI the browser runs in UTC. Axe enforces WCAG
  2.0/2.1 A and AA rules on every route and on populated, advice, and hovered
  states. A second configuration accepts the installed systemd user service
  without replacing systemd.
- Repository policy is executable code under `development` and is unit-tested
  with real temporary Git repositories. Versioned hooks and the `pr-metadata`
  workflow call that implementation.
- Hosted CI mirrors repeatable local checks, including real PostgreSQL, the
  Restic backup and restore drill, and Playwright without the model case.
  Service jobs fail when a suite executed nothing or skipped for its own
  missing flag. Model/GPU, real Telegram, installed-systemd, and model-backed
  Langfuse evidence stays local and becomes mandatory when a slice changes the
  corresponding boundary.
- Agent quality experiments complement rather than replace correctness tests.

## 9. Architecture Decisions

The full accepted decision set and maintenance policy are indexed in
[Architecture Decisions](decisions/README.md).

### AD-001: Out-of-process inference

- Status: accepted
- Implementation: implemented

Use an OpenAI-compatible local inference server rather than Python CUDA,
Transformers, or `llama-cpp-python` dependencies.

Consequences include a small Python environment, direct llama.cpp tuning for
the GPU, an independently operated inference process, and configuration-based
replacement with another compatible server.

### AD-002: Self-hosted Langfuse

- Status: accepted
- Implementation: implemented, repaired and digest-pinned

Operate Langfuse locally with telemetry disabled and keep tracing optional.
Prompts, traces, scores, and service storage remain on the workstation. Core
finance operation must not depend on Langfuse availability.
[AD-021](decisions/AD-021-full-fidelity-llm-traces.md) extends this decision
with what product traces contain and how the tracing boundary enters the
inference path.

### AD-003: Headless local initialization

- Status: accepted
- Implementation: implemented

Generate credentials locally and use the same project keys for Langfuse
initialization and SDK configuration. Never overwrite or commit the sensitive
environment file.

### Partially implemented accepted decisions

- [AD-005: Dedicated application PostgreSQL](decisions/AD-005-application-postgresql.md):
  service, migration boundary, checkpoints, finance schema, expense, wallet,
  budget, pending-proposal, replay-safe versioned receipt-review, and messaging
  persistence implemented; remaining adapters planned.
- [AD-007: Expense and dual-allocation model](decisions/AD-007-expense-allocation-model.md):
  domain, schema, expense/wallet/budget persistence, reconciliation, and
  reports implemented; remaining adapters planned.
- [AD-008: Controlled two-level taxonomy](decisions/AD-008-controlled-taxonomy.md):
  policy, accepted stable seed, schema, bootstrap, taxonomy-scoped pending
  proposal submission, the deterministic category-fit boundary, the owner
  decision service, and the persistent review workflow implemented.
- [AD-006: Bounded typed agent workflows](decisions/AD-006-bounded-agent-workflows.md):
  persistent checkpoint adapter, bounded intent taxonomy with partitioned
  authority, deterministic pre-routing, and the strict versioned router model
  boundary, the extraction and category-fit boundaries, deterministic
  dual-allocation resolution, the expense and refund specialist graph, and the
  persistent owner category review interrupt, scoped read-only query/advice
  routes, correction/undo specialists, explicit dispatcher, and EVD-004 prompt,
  model, policy, and confidence promotion implemented.
- [AD-012: Evaluation-driven model and prompt promotion](decisions/AD-012-evaluation-driven-promotion.md):
  capability runner, artifact contract, immutable suite evidence, and the
  EVD-004 Step 3 text-role promotion implemented; later roles remain gated.
- [AD-013: Host runtime and encrypted backup](decisions/AD-013-local-runtime-and-backup.md):
  Compose topology, encrypted Restic backup, verification, retention, and the
  isolated restore drill with semantic verification, accepted Step 4 host
  units, the browser-accepted Step 5 API unit, and the Step 7 daily backup
  timer implemented and timer-trigger accepted; multi-run retention evidence
  and the clean-machine restore remain planned.
- [AD-011: Evaluation-gated receipt extraction](decisions/AD-011-receipt-extraction.md):
  private original-media boundary and the owner-facing metadata lifecycle
  implemented; extractor evaluation and the extracted-draft preview workflow
  planned for Step 6, deferred after the personal V1 release.
- [AD-009: Local FastAPI and React web application](decisions/AD-009-local-web-application.md):
  strict version-one finance wire contracts, monthly overview, calendar
  analytics including merchant and prior-period comparison, manual lifecycle,
  multi-allocation editing, budget planning, category management/review,
  funding-source/wallet administration, profile settings, receipt metadata
  review, promoted read-only advice, HTTP security, and host deployment
  implemented and accepted through real-browser E2E, WCAG A/AA automation, and
  the installed user service.
- [AD-020: Wallet availability and expiry projection](decisions/AD-020-wallet-availability-and-expiry.md):
  deterministic Europe/Rome first-expiry-first availability, immutable booked
  history, source lifecycle service, transactional locks, and local control-plane
  administration implemented.
- [AD-014: Private content-addressed receipt archive](decisions/AD-014-private-receipt-archive.md):
  safe local adapter, replay-safe versioned review state, and real
  filesystem/PostgreSQL integration implemented; administration CLI wiring,
  encrypted archive backup/restore, and browser metadata review implemented;
  API/worker media wiring and deletion planned for Step 6, deferred after the
  personal V1 release.
- [AD-016: Explicit workflow dispatcher](decisions/AD-016-explicit-workflow-dispatcher.md):
  transport-neutral typed outcome, explicit router-to-specialist composition,
  trusted action correlation, administration CLI wiring, durable worker, and
  channel presentation implemented.
- [AD-017: Versioned deterministic read-selection policy](decisions/AD-017-versioned-read-selection-policy.md):
  promoted V1 policy implemented between model parsing and registry acceptance;
  V0 remains the direct configuration rollback.
- [AD-018: Telegram processing transactions and ordering](decisions/AD-018-telegram-processing-transactions.md):
  reconciled Step 4 persistence, Bot API transport, authorized ingestion,
  persisted-offset runtime, outbox delivery, Italian response rendering, the
  sequential worker, one-use owner pairing, and operator
  health/replay/resend/latency commands and real-bot acceptance are implemented.

### Implemented and accepted Telegram decisions

AD-004, AD-010, and AD-018 have persistence, gateway, pairing, worker,
presentation, operator-command, real-bot, fault, latency, and reboot evidence.

### Implemented cross-cutting decisions

[AD-021](decisions/AD-021-full-fidelity-llm-traces.md) is implemented and
owner-accepted (2026-09-18): every model-calling entry point is traced into the
local Langfuse behind an explicit switch, one Telegram update is one trace
across the gateway and worker, promoted configurations link to their runs, and
traces are kept 30 days outside the backup set with a verified deletion
command, which an installed and enabled Step 7 daily timer now schedules; its
timer-to-service path was accepted on the target workstation on 2026-09-20.

AD-019 is implemented across every entry point, adapter, application service,
and persistence boundary. It supplements rather than replaces the AD-018 exit
codes: a supervisor still distinguishes a configuration failure from a
dependency failure without reading logs, while the journal explains what a
still-running process did and why.

[AD-022](decisions/AD-022-pull-request-workflow.md) is implemented: the shared
policy checker, local Git hooks, Claude Code guardrails, PR template,
development workflow, hosted CI with `ci-gate` and `pr-metadata`, the scheduled
Langfuse workflow, Dependabot, and the `protect-main` ruleset, active since
2026-09-19. A throwaway pull request proved that failing required checks block
the merge.

[AD-023](decisions/AD-023-portable-finance-export.md) is implemented: the
operator-only CLI exports one profile's authoritative finance records from a
consistent snapshot into a versioned, private JSON Lines artifact with a
digest and without receipt bytes or runtime state.

[AD-024](decisions/AD-024-runtime-content-retention.md) is implemented: the
operator CLI and its daily timer delete concluded Telegram messages, consumed
controls, and terminal checkpoint threads 30 days after they conclude, in one
transaction whose dry run rolls back, while active controls, paused reviews,
and dead letters remain; the installed target timer's timer-to-service path was
accepted on 2026-09-20.

## 10. Quality Requirements

| Scenario | Expected response |
| --- | --- |
| Telegram redelivers an update after a crash | Inbox deduplication and command idempotency produce exactly one financial write. |
| The workstation restarts within 24 hours | The gateway durably ingests the remaining Telegram backlog and the worker processes it in order. |
| The workstation was offline longer than 24 hours | The owner receives a possible-loss warning; documentation requests manual resend and makes no recovery claim. |
| A model emits malformed structured output | One corrective retry occurs, then the workflow asks for clarification without writing. |
| An expense has mixed categories and payment sources | Both independent allocation sums reconcile exactly before posting. |
| A category does not fit | The workflow interrupts and creates nothing without explicit approval. |
| A receipt total and extracted lines differ (Step 6, after V1) | The receipt remains in review with the unexplained difference visible. |
| Langfuse is stopped | Core ingestion, finance, Telegram, API, and dashboard behavior continues without traces. |
| A user outside the paired owner contacts the bot | The gateway rejects the update without exposing finance state. |
| A browser uses a non-loopback route or disallowed origin | The API refuses access or mutation. |
| A dashboard change lowers contrast or hides a control from the keyboard | The real-browser axe gate reports the WCAG A/AA violation before release. |
| A restore is performed on an empty installation | Database, receipt references, retained media, and required configuration recover consistently. |
| The application database restarts while the worker waits for work | The next update's checkpoint session is replaced on first use and the update is answered without waiting for a lease. |
| The owner sends ordinary text messages to a warm system | Warm p95 from durable acceptance to Bot API send stays at most 30 s; the 2026-09-19 controlled sample measured 5,952 ms over 19 warm samples. |
| Concluded Telegram and workflow content turns 30 days old | The daily retention run deletes it in one transaction, keeps every active control, paused review, and dead letter usable, and reports only counts. |
| Finance records are exported while another transaction commits | The artifact contains one internally consistent repeatable-read snapshot, is a new mode-600 file, and reports a digest for transfer verification. |
| A model or prompt candidate misses a safety gate | It is not promoted; the current accepted configuration remains active. |
| A pull request has an invalid branch, commit, or failing required gate | Local policy reports it early, `pr-metadata` or `ci-gate` turns red on the PR, and the `protect-main` ruleset blocks the merge for every actor including administrators, as the negative-proof PR #7 showed. |
| A real-service CI job loses its service flag or discovers no test | `ci_reporting` fails the job instead of reporting a green, all-skipped suite. |

Quantitative evaluation gates are maintained in
[Evaluation Matrix](../experiments/evaluation-matrix.md), not duplicated here.

## 11. Risks and Technical Debt

### Current scaffold risks

- The complete Step 4 runtime is implemented and accepted: messaging
  persistence, gateway, worker, Italian presentation, pairing and recovery
  commands, real owner workflows, five fault injections, p95 latency, and an
  unattended reboot all passed. Physical Telegram client rendering remains
  outside Bot API observability.
- EVD-004 promotes the Qwen3 14B text configuration, but no automatic-post
  subset qualifies. Ordinary existing-category movements remain drafts until a
  later explicit owner or dashboard action posts them.
- The encrypted authoritative backup, isolated restore drill, and persistent
  daily timer are implemented. The target repository, service, and timer are
  installed and timer-trigger verified, but multi-run retention evidence and a
  clean-machine restore remain release work. A real 2026-09-23 snapshot failed
  the semantic restore gate on a legacy default-source pointer. The migration
  recovered it in an isolated target and was deployed to production on
  2026-09-23 after a fresh pre-upgrade recovery proof. A new snapshot passed
  its ordinary isolated drill. This does not replace the clean-machine gate.
  Off-workstation access to
  the recovery secret passed through the owner's authenticated Android password
  manager. The repository is on a separate internal NVMe, so primary-disk
  failure is separated while theft, fire, and whole-machine loss remain risks.
- Runtime content retention has an installed, enabled daily timer whose
  timer-to-service path passed acceptance on 2026-09-20. A deleted row still
  survives in older Restic snapshots for up to about a year, and a delivered
  summary whose control stays active keeps its text until the control is used. Thread
  selection reads the LangGraph checkpoint schema, so a checkpointer upgrade
  must re-run the retention integration test.
- The workstation has no full-disk encryption: on 2026-09-19 the root ext4
  filesystem, holding the home directory, the application and Langfuse Docker
  volumes, the receipt archive, and every export, and the swap partition sat
  directly on their partitions with no `crypt` device. Anyone who reads the
  disk recovers live and deleted owner content, so logical deletion bounds
  what the application exposes but not what the medium keeps. Encrypting the
  disk is an owner decision outside the application.
- Portable finance exports are deliberately unencrypted and contain owner
  content. A copied artifact is outside application retention controls, so the
  operator must protect it in transit and remove every copy when no longer
  needed.
- The real Telegram evidence uses a private test bot, not a production bot.
- Trace retention has an installed, enabled daily timer whose
  timer-to-service path passed acceptance on 2026-09-20. Langfuse volumes hold
  owner content and remain outside the backup set. A
  private export holds the same content outside any store and is the
  operator's to protect and remove.
- Fresh environments receive a random 48-character `LLM_API_KEY`, above the
  trace redactor's twelve-character floor. The upgrade script deliberately
  never rotates existing credentials, so an older installation that still has
  a short placeholder must replace it explicitly.
- The host units' `ProtectHome`, `ProtectSystem`, and `PrivateTmp` directives
  are not applied by this workstation's user manager, which cannot create a
  mount namespace while AppArmor restricts unprivileged user namespaces. The
  services can write anywhere their user can. Earlier exposure scores measured
  configuration, not effect; Step 7 must qualify the sandbox by observing it.
- Keeping the exit flush bounded relies on one private detail of the Langfuse
  SDK: the adapter unregisters the exit handler of the client's resource
  manager. An SDK upgrade must re-run the degradation tests, which fail if the
  flush or stop time exceeds the bound again.
- A service traced calls made in the first seconds after start-up with a new
  model artifact carry `digest_pending` and an incomplete identity while the
  weights are hashed in the background.
- A traced model call names its artifact from a `/props` report reused for up to
  sixty seconds, so a server restarted with other weights inside that window is
  attributed to the previous artifact. The Langfuse public key is stamped on
  every span by the SDK; it identifies the project and grants nothing alone.
- Step 5 browser acceptance gates only the WCAG 2.0/2.1 A/AA axe rules at a
  desktop viewport. Best-practice landmark rules are not gated and the page
  `main` nests inside the shell `main`; narrow layouts, visual focus
  indicators, and hover states other than the destructive filled button are not
  browser-audited.
- Axe reports text drawn over the page's gradient background as incomplete
  (`bgGradient`), not as a violation. The navigation, page headings, and other
  text placed directly on that background are therefore outside the contrast
  gate; only text on an opaque surface, such as a card, badge, or alert, is
  measured.
- Versioned hooks and Claude Code rules can be bypassed, misconfigured, or
  absent in another clone; they only catch mistakes early. The `protect-main`
  ruleset is the guarantee.
- Some gates depend on the owner's model/GPU, real Telegram bot, local
  Langfuse, or installed systemd services and therefore remain manual PR
  evidence.
- This private repository's ruleset is enforced through GitHub Pro from the
  owner's GitHub Student benefit. If the benefit lapses, GitHub stops enforcing
  the ruleset and the Actions allowance falls from 3,000 to 2,000 minutes.
- The ruleset has no bypass actors: a GitHub Actions outage or a required job
  broken by its environment blocks every merge until it is fixed, and only the
  owner may relax enforcement meanwhile.
- The strict check policy re-runs the whole pipeline whenever a pull request
  that fell behind `main` is rebased, which the minute budget must absorb.
- Every push to a PR and to `main` runs all nine CI jobs, and GitHub bills
  each job rounded up to a whole minute. The matrix must stay within the
  measured allowance recorded in [Development](../development.md#github-actions-minute-budget).
- The Langfuse workflow runs only from the default branch, so a change to it is
  first exercised after its merge. Its first scheduled run (2026-09-19) passed
  in 379 seconds; GitHub may start scheduled runs hours late.
- Dependabot never proposes a major container tag, because CI starts images on
  empty volumes and cannot show that a major opens existing data. The owner
  must watch upstream support windows, such as PostgreSQL 17's, and plan each
  major with the controlled container upgrade.
- Restic 0.18.1 and PostgreSQL 17.11 client tools are installed on the target.
  The real repository and production service create verified snapshots, while
  a clean-machine restore drill remains open.

### Target architecture risks

- Telegram cannot guarantee update recovery beyond 24 hours without an external
  always-on component.
- Local models may not meet Italian routing, schema, tool, or receipt quality
  gates within 16 GB VRAM.
- OCR/VLM receipt extraction may require extensive private annotation and manual
  correction. Deferring it after the personal V1 release moves this risk out of
  the release rather than removing it.
- V1 ships without receipt deletion: an original imported through
  `money-tracker-admin receipt-store` stays in the archive and in every backup
  until Step 6 delivers media deletion.
- The localhost dashboard now enforces browser-origin and DNS-rebinding
  controls. Same-user local processes remain trusted in V1; remote access would
  require authentication, TLS, and a new threat model.
- PostgreSQL, retained media, systemd, Docker, models, and Restic increase local
  operational responsibility.
- The lightweight expense model intentionally postpones full accounting and may
  require a consequential migration later.

Risk mitigation and delivery order are defined in the
[roadmap](../roadmap.md) and [operations guides](../operations/README.md).

## 12. Glossary

The product-wide canonical definitions live in the
[Product Glossary](../product/glossary.md). Architecture-specific terms include:

| Term | Meaning |
| --- | --- |
| Application PostgreSQL | Dedicated authoritative database for finance, messaging, audit, and workflow checkpoints; not the Langfuse database. |
| Category allocation | A portion of an expense assigned to one category or subcategory. |
| Command idempotency key | Stable identifier that makes repeated execution produce at most one domain mutation. |
| Dead letter | Inbox work that exhausted automatic retry and awaits manual replay or resolution. |
| Funding source | Cash, card, meal voucher, welfare, or another resource used to pay an expense. |
| Outbox | Transactionally recorded outbound message awaiting delivery. |
| Payment allocation | A portion of an expense paid by one funding source. |
| Pending action | Durable mapping between a user-facing question and the exact suspended workflow. |
| Posted | Validated financial state included in reports and budgets. |
| Receipt extractor | Replaceable local adapter that transforms a receipt image into a structured draft. |
| Workflow checkpoint | Persisted orchestration state used to resume a graph; never the finance source of truth. |
