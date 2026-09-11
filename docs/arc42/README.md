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
primarily send Italian natural-language messages and receipt images through a
private Telegram bot. The system will extract, categorize, persist, and analyze
expenses and refunds. A local web dashboard will provide review, corrections,
budgets, wallet balances, charts, and operational controls.

The approved V1 scope is defined in
[Vision and Requirements](../product/vision-and-requirements.md). Steps 0–4 are
complete. EVD-004 promotes Qwen3 14B, router v6, extraction v6, category-fit
v6, tool-selection v5, routing confidence 0.5, and
`explicit-finance-scope-v1`. The durable Telegram runtime passed owner-driven
workflow, authorization, latency, fault, and reboot acceptance on 2026-09-11.

Step 5 is active. Its first vertical slice implements the loopback FastAPI host,
monthly overview read model and endpoint, compiled React shell, browser security
policy, and host unit. The remaining control-plane views, mutations, wider
analytics, advice exposure, and browser E2E acceptance are still planned within
Step 5. Receipt extraction remains assigned to Step 6.

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
| Owner to administration CLI | Process arguments/stdout plus private input file | Profile-scoped receipt import with safe JSON result | Implemented Step 2 receipt-store command |
| Application to inference | OpenAI-compatible HTTP | Messages, schemas, tool calls, model settings | Implemented basic chat |
| Application to Langfuse | SDK over local HTTP/OTLP | Redacted graph/model observations and aggregate scores | Implemented, optional and healthy |
| Telegram gateway to Bot API | HTTPS long polling and send methods | Updates, callback queries, messages, and bounded file metadata; no Step 4 file download | Implemented and owner-accepted against the real private test bot |
| Browser to application API | Same-origin HTTP JSON | Monthly overview and health implemented; remaining finance, review, analytics, and operations resources planned | First Step 5 slice implemented |
| Application to PostgreSQL | PostgreSQL protocol | Checkpoints, authoritative finance aggregates, normalized messaging offsets/inbox/outbox, pairing, actions, retries, and dead letters | Implemented foundation, finance repositories, and Step 4 messaging persistence |
| Application to receipt archive | Private host filesystem | Fully decoded original JPEG/PNG/WebP keyed and verified by content hash; no derivatives | Local adapter/admin CLI implemented; API/worker wiring, deletion, and backup planned |
| Backup process to repository | Restic repository protocol | Database dump, receipts and recovery configuration | Implemented and restore-tested; scheduling planned |

### Out of scope for V1

- Income tracking, complete asset/liability accounts, transfers, bank sync, and
  statement reconciliation.
- Historical import from another application.
- Household or multiuser access.
- LAN or remote dashboard access.
- Cloud LLM, OCR, vision, trace, or finance storage.
- A cloud relay for Telegram updates older than 24 hours.
- Automatic posting of extracted receipts.

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
  require owner preview for every V1 receipt.
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
│   ├── FastAPI application and compiled React UI        [monthly slice implemented]
│   ├── Telegram gateway                                [implemented; unit template checked in]
│   ├── Sequential workflow worker                      [implemented; unit template checked in]
│   │   ├── typed dispatcher, router, and text graphs  [implemented library]
│   │   └── Italian response renderer                  [implemented library]
│   └── llama.cpp/OpenAI-compatible inference           [scaffolded]
├── Private host data
│   ├── Receipt archive adapter                         [implemented, admin CLI wired]
│   ├── Evaluation data and artifacts                   [implemented foundation]
│   └── Restic staging/configuration                    [implemented, unscheduled]
└── Docker Compose
    ├── Application PostgreSQL                          [implemented finance and messaging core]
    └── Langfuse, PostgreSQL, ClickHouse, Redis, MinIO  [implemented optional]
```

### Current Python scaffold

| Component | Responsibility | Interface/dependencies | Data ownership | Deployment/security |
| --- | --- | --- | --- | --- |
| `cli` | Parse one prompt, invoke the graph, print output, flush traces | Python entry point; graph and observability | No persistent data | Host process; user terminal |
| `config` | Convert environment variables into immutable model settings | Process environment | Runtime configuration | Secrets are not returned or logged |
| `graph` | Define the initial one-node assistant | LangGraph and `BaseChatModel` | In-memory messages for one invocation | Stateless host process |
| `llm` | Construct an OpenAI-compatible chat adapter | LangChain `ChatOpenAI` | No persistent data | Loopback endpoint by default |
| `observability` | Enable and flush optional Langfuse callbacks | Langfuse SDK and environment keys | Buffered telemetry until flush | Local Langfuse only |
| `persistence` | Apply versioned migrations, bootstrap persistent LangGraph checkpoints, and implement finance/messaging repositories | Alembic, psycopg, PostgreSQL checkpointer | Foundation schemas, workflow checkpoints, finance aggregates, and durable messaging state | Host administration CLI and shared host library; least-privilege loopback database role |
| `domain` | Enforce exact money, expense/refund, dual-allocation, taxonomy, wallet, budget, receipt-metadata, and audit contracts | Pure Python value objects and policies; no framework or infrastructure import | No infrastructure state | Shared host-process library; deterministic validation of untrusted inputs |
| `application` | Coordinate idempotent expense lifecycle and receipt registration/review, audited wallet/budget mutations, pending proposal submission, owner category decisions, and read-only reporting through narrow ports | Finance, administration, proposal, review, reporting, and receipt-metadata repository ports plus clock and ID contracts | Command, concurrency, review-boundary, and audit semantics, not stored state | Shared by CLI, API, and worker; profile scope comes from trusted caller context; node creation is reachable only through an owner decision, and receipt deletion is not exposed |
| `workflows` | Own the bounded intent taxonomy, normalized channel work unit, deterministic pre-routing, strict versioned model boundaries, policy vetoes, and post-model read-selection policies, deterministic allocation resolution, draft and correction preparation, stable creation/correction/undo command identities, and the strict-schema retry boundary behind a narrow model port | Pydantic, `zoneinfo`, and pure domain values; no LangGraph, model client, repository, or transport import | Routing destinations, authority classes, stable routing and resolution reason codes, and the product calendar; no persistent state | Shared by the implemented worker; a model can select no deterministic route and supply no profile/timezone/authorization field, no resolved instant, no amount sign, and no funding-source identifier; reason codes carry no owner text |
| `runtime` | Assemble the compiled root-router, expense/refund, correction/undo, category-review, and read graphs; dispatch one normalized work unit to exactly one specialist; adapt the local endpoint to the structured-output port | LangGraph, LangChain, application services, the persistent checkpointer, and `workflows` contracts | No domain state of its own; checkpoint contents remain owned by each graph state contract | Host administration CLI and worker; dispatcher trusts only injected profile/correlation context and emits no presentation text |
| `worker` | Claim the oldest eligible durable work unit, translate it into the normalized channel message, dispatch it once, and commit result, response, correlation, and completion together; commit failure notice with retry or dead-letter state | Messaging repository port, finance read ports, a narrow dispatch port, and the response renderer | No authoritative state; every identifier it creates is derived from the inbox row so a recovered attempt reproduces the same logical response | systemd host service, separate failure domain from the gateway; holds no Bot API credential and imports no transport type |
| `presentation` | Render typed dispatch outcomes, processing failures, committed movement facts, and paused review questions as Italian owner text and bounded controls | Dispatcher outcome contracts, messaging control values, and pure domain values; no model, transport, or repository import | No state; owns the callback code vocabulary and the response-kind names embedded in outbox logical keys | Worker library; content is a function of committed state only, so a replayed render reproduces the same logical response |
| `api.contracts` | Define strict, versioned finance request/response, mutation-header, and problem wire shapes | Pydantic and domain response values; no web framework | No state; stable JSON field semantics only | Shared by the FastAPI adapter; no credentials or private media fields |
| `web` | Compose versioned HTTP reads, static UI serving, health, and browser policy | FastAPI, narrow application read ports, typed settings, and strict wire contracts | No authoritative data; injects the sole trusted profile and owns only HTTP semantics | Host user service on IPv4 loopback; exact Host/Origin, closed CORS, CSRF, CSP, and no secret endpoint |
| `backup` | Create verified encrypted authoritative snapshots, report their freshness, and restore only into explicitly empty isolated targets | Restic and PostgreSQL client executables, psycopg identity/receipt queries, private archive and configuration paths | Snapshot contents and the versioned integrity manifest in the owner repository; no finance state of its own | Host CLI under an exclusive lock; absolute-path and owner-only input checks, recovery secret passed only by file path, failures name no argument or environment |
| `administration` | Expose bounded local profile bootstrap, funding-source configuration, receipt storage/registration, and one-shot dispatch of every Step 3 text route | Database URL, absolute archive setting, application services, the workflow dispatcher, and local model adapter | No independent state; emits safe identifiers, grounded facts, and reason codes only | Host CLI; active-profile preflight for specialists, explicit trusted correlations, no private paths or message content in JSON |
| `persistence.finance` | Bootstrap profile/taxonomy, persist complete expense and budget aggregates, append balance-safe wallet movements, validate/store pending proposals, and register/transition receipt metadata transactionally | psycopg and narrow application repository contracts | Finance tables, allocations, budgets/overrides, wallet ledger, proposals, versioned receipt metadata, audit, and idempotency records | Dedicated application PostgreSQL; no receipt bytes or paths; optimistic versions protect mutable aggregates |
| `media.local` | Decode, validate, atomically retain, and integrity-check private receipt originals | Receipt-media port, Pillow, private filesystem | Original bytes under sharded SHA-256 keys; no database state | Host library outside web roots; `0700` directories, `0600` files, no-follow verified reads; root wired explicitly by admin CLI, API/worker planned |
| `evaluations` | Probe real model protocols, capture exact runtime metadata, score deterministically, collect owner usefulness labels, optionally publish aggregates | OpenAI-compatible HTTP, local files, optional Langfuse SDK, and a temporary standard-library loopback HTTP interface | Ignored local run artifacts and browser-local draft scores, plus published aggregate scores keyed by suite and by the model that produced them; no domain authority | Host CLI; the review page binds only to `127.0.0.1`, uses a per-run submission token and no external assets, and excludes secrets, image bytes and paths |
| `evaluations.catalog` | Read the declared local model candidates, name the model behind a score, and refuse a run that is not measuring the candidate it declared | Committed candidate YAML, local weights paths, and the inference server's own report of what it loaded | No state; the committed catalog is the source of truth for what a candidate is | Host CLI; never downloads, installs, or selects weights, and a candidate it cannot pin by hash is refused at load time |
| `workflows.wire` | Hold the union-shaped schemas a local model is asked to fill and convert them purely to the domain contracts | Pydantic and the `workflows` contracts; no model client or transport | No state; conversion only | Shared by every model boundary; an unstated field has an explicit variant, so a fabricated value has no shape and a split that does not reconcile is refused where it can still be repaired |
| `workflows.two_stage` | Compose the heading and level boundaries into one category judgment, choosing the subtree before the level | The two instruction catalogs, the bound wire schemas, and the narrow model port | No state; a pure composition over two calls | Evaluation only. AD-015 is rejected on its measurement, and this component is kept so that result stays reproducible; no runtime path uses it |
| `workflows.prompts` | Load, validate, and version the instruction catalogs each boundary is given | JSON catalogs under `prompt_data/`, the intent taxonomy, and the tool registry | No state; catalogs are read-only data | Vocabularies are derived from the taxonomy and registry, so a stale catalog is refused at load time; nothing is promoted |
| `telegram.config` | Select one test/production bot and securely read its distinct owner-only token file | Process environment, private filesystem, messaging environment enum | No authoritative state; credential exists only in process memory | Host gateway; absolute path, owner UID, regular no-follow file, and mode 0600 required |
| `telegram.transport` | Call official Bot API methods and reduce responses to typed values | `httpx`, bounded timeouts, messaging outbox contracts | No durable state | Host gateway only; every exception is replaced by a content-free reason code |
| `telegram.ingestion` | Authorize one persisted private owner and normalize a complete observed batch | Telegram transport values and `MessagingRepository` | Acceptance policy only | Runs before persistence; groups/foreign users retain counters only and never trigger file lookup |
| `telegram.gateway` | Run independent long-poll and outbox-delivery loops | Transport, ingestion service, repository, clock, retry policy | No in-memory correctness state | Production `money-tracker-telegram` entry point; no LangGraph, model, or finance repository dependency; user service unit checked in |
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
| Application services (expense/wallet/budget, proposal submission and settlement, and receipt registration/review implemented) | Execute validated commands and queries transactionally | Narrow repository ports, clock, ID and future media ports | Command/content idempotency, versioning, review boundary, and audit semantics | Future FastAPI and implemented worker host processes; profile scope injected; product receipt review remains workflow-owned |
| Finance repositories (expense/wallet/budget/proposal/versioned receipt metadata implemented) | Persist authoritative domain records and analytics projections | PostgreSQL protocol through explicit repository contracts | Finance schema implemented; receipt deletion open | Dedicated application database; least-privilege credentials; optimistic versions and source locks protect concurrent mutations; no media payloads |
| Messaging persistence (implemented) | Receive, order, lease, retry, dead-letter, replay, correlate, and complete transport work | `MessagingRepository` port, psycopg, and PostgreSQL | Minimum normalized inbox data, offsets, typed results, logical outbox, pairing, pending actions, and attempt metadata | Shared gateway/worker/admin adapter; no raw update payload, token, or token-bearing URL in rows |
| Workflow runtime (dispatcher, router, and Step 3 text specialists implemented) | Route one normalized work unit and execute exactly one bounded specialist | Typed dispatcher outcome, LangGraph, model gateway, application services, checkpointer | Small per-graph versioned workflow state only; authoritative domain data stays in repositories | Administration CLI and sequential worker; no media, credentials, or service objects in checkpoints |
| Receipt service | Validate media, invoke extractor, reconcile lines, prepare review | Implemented media-store port, planned extractor, finance service | Receipt metadata, extraction versions, review state | Private filesystem; untrusted image boundary; orchestration planned |
| Analytics service | Compute time, category, budget and funding summaries | Read repositories and deterministic calendar policies | No separate authoritative data | FastAPI and read-only agent tools |
| Backup service (implemented, unscheduled) | Create consistent encrypted backups and verify restores | `pg_dump`, Restic, receipt/configuration paths | Backup snapshots in owner repository | Owner-invoked today; systemd timer and clean-machine qualification planned; recovery secret stored off-device |

### Current and planned adapters and presentation blocks

| Component | Responsibility | Interfaces/dependencies | Data ownership | Deployment/security |
| --- | --- | --- | --- | --- |
| Workflow dispatcher, root router, and text specialists (implemented) | Route one intent and coordinate expense/refund, correction/undo, category review, query, or advice | Typed normalized work unit/outcome, versioned schemas, scoped tool registries, trusted correlations | Namespaced checkpoint references and workflow IDs; no domain ownership | Administration CLI and worker; mutation paths call replay-safe application services |
| Telegram Bot API transport (implemented) | Validate the bot, long poll, obtain file metadata, acknowledge callbacks, and deliver outbox messages | Bounded HTTPS client and messaging services | No authoritative finance data or correctness state | Gateway host service; separate production/test token selected from a private environment file |
| FastAPI adapter (partial) | Expose monthly overview, health, CSRF bootstrap, compiled UI, and later REST resources | Narrow application services | No independent data | Implemented on loopback with Host/Origin and CSRF protection |
| React dashboard (partial) | Display the monthly overview now and later edit finance, review, budget, wallet and operational state | Same-origin REST API | Browser session/UI state only | Compiled static assets; no secrets; accessible chart table alternative |
| Local model gateway | Provide typed chat, schema and tool capabilities | OpenAI-compatible local endpoint | No persistent domain data | Replaceable host inference process |
| Receipt extractor adapter | Produce a versioned structured receipt draft | Selected local OCR/VLM pipeline | No authoritative data until validated | Local-only; selection remains evaluation-gated |
| Langfuse adapter | Record redacted traces and experiment scores | Local Langfuse SDK | Observability copies, not source of truth | Optional development/evaluation profile |

### Step 4 messaging boundaries

AD-018 divides the Step 4 runtime into seven replaceable blocks. The contracts,
retry policy, PostgreSQL repository, ingestion service, Bot API transport,
gateway process, response renderer, sequential worker, pairing, and the
administration and health commands are all implemented:

| Component | Responsibility | Interface/dependencies | Data ownership | Deployment/security |
| --- | --- | --- | --- | --- |
| `messaging.contracts` | Define normalized inbox, outbox, pairing, pending-action, retry, dead-letter, and health values | Pure Python contracts and time/identifier values | No infrastructure state | Shared host library; contains no Bot API token or raw update |
| `messaging.service` (ingestion, pairing, and worker implemented) | Authorize/normalize ingestion, consume one-use pairing, coordinate claim/dispatch/retry/replay/delivery, and summarize content-free text latency | Messaging repository port, response renderer, dispatch port, and durable acceptance/send timestamps | Application policy only | Gateway/worker/admin host processes; owner scope comes only from binding records, pairing codes are never retained in clear, and latency output contains no messages or identities |
| `persistence.messaging` | Implement the AD-018 transactions and uniqueness/lease rules | psycopg and the dedicated application PostgreSQL | Entire `messaging` schema | Least-privilege application role; content is private and included in authoritative backup |
| `telegram.transport` | Implement the official HTTPS Bot API methods with bounded timeouts and redacted failures | `httpx` and configured Bot API base URL/token | No durable state | Gateway only; production/test configuration is mutually exclusive |
| `telegram.gateway` | Run independent ingress and outbox loops | Bot API transport and messaging service | No in-memory correctness state | systemd host service; no model or finance repository dependency |
| `worker` (implemented) | Translate one claimed row to `ChannelMessage`, construct `DispatchContext`, call the Step 3 dispatcher, and finalize messaging state | Messaging/finance read ports and a narrow dispatch port | No authoritative state; derives outbox and pending-action identity from the inbox row so a recovery reproduces them; a modification clarification creates a durable successor reply mapping for the selected movement | systemd host service; no token or Telegram client |
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

1. The CLI reads a prompt and creates optional callbacks.
2. The graph receives a human message and prepends its system contract.
3. The model adapter calls the configured local inference API.
4. The graph returns one assistant message; the CLI prints it and flushes traces.

No state survives the process and no financial write occurs.

### Current finance repository mutations

1. A trusted caller supplies profile-scoped domain objects and non-sensitive
   actor/reason audit metadata to an application service.
2. Expense commands validate the complete aggregate, then create idempotently
   or replace only the caller-observed version.
3. A wallet command locks its ledger funding source, reconstructs the booked
   balance, and rejects a debit that would overdraw before appending anything.
4. A budget command creates version one or replaces the complete recurring
   target and override set only at the expected version.
5. The repository commits the domain mutation and immutable audit event in one
   PostgreSQL transaction; any validation or concurrency failure rolls both
   back.

These services currently have no public API or CLI entry point. Integration
tests exercise them directly through the real PostgreSQL protocol.

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
6. Each review transition increments an optimistic version and commits its
   immutable audit atomically; concurrent writers have exactly one winner.

The media adapter is wired to the local administration CLI but not to an
API/worker entry point. Extraction and the product review workflow remain planned.

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

1. A future transport adapter validates the canonical `Idempotency-Key` header
   separately from a strict expense request body; unknown fields and a
   caller-supplied profile scope are rejected.
2. Exact signed cents, EUR, aware occurrence time, stable UUID references, and
   non-zero movement semantics are validated before application dispatch.
3. A real domain expense maps to a response with stable IDs, ISO timestamp,
   lifecycle status, allocations, and optimistic version, but no command key,
   repository detail, media path, or secret.
4. Failures use a bounded problem-details schema and the
   `application/problem+json` media type.

### Current local monthly dashboard request

1. The browser requests the compiled shell and then an exact `YYYY-MM` overview
   from the same origin.
2. Middleware rejects an unapproved Host or present Origin before routing. An
   unsafe method additionally needs an allowed Origin and matching CSRF cookie
   and header; the current overview itself is read-only.
3. Runtime composition selects the sole profile and supplies the narrow
   `MonthlyOverviewReader`; no HTTP value can change owner scope.
4. `ReportingService` reads posted expenses, funding sources, taxonomy, and
   budgets from PostgreSQL. Funding and category dimensions reconcile
   independently and the response carries exact EUR minor units.
5. TanStack Query renders Italian loading, error, empty, and result states.
   ECharts displays the budget comparison, and the adjacent table exposes the
   same exact values without requiring the chart.

The adapter owns no state, mutation, model call, or receipt access. Other Step
5 routes remain planned rather than implicit in this first slice.

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

Credentials reach subprocesses only through libpq environment variables and the
Restic password-file path. Failures name the failing executable alone. No timer
schedules this yet, and business reconciliation of restored aggregates remains a
manual acceptance check.

### Current text expense turn

1. A normalized channel message receives a source and command idempotency key.
2. The root router produces a typed intent decision.
3. The expense workflow extracts amount, time, description, merchant and payment
   allocations, using the profile's default funding source when omitted.
4. Deterministic validation rejects malformed money, missing invariants, and
   ambiguous data before any write.
5. Category resolution selects an adequate existing node or persists a proposal
   and interrupts the workflow.
6. A high-confidence existing assignment posts atomically with audit and an
   outbound acknowledgement. Lower-confidence input remains a draft and asks a
   targeted question.
7. The owner receives a summary with modify and undo actions.

### Planned category review

1. The workflow persists a proposal and emits a serializable review payload.
2. The owner approves it, requests the best existing category, names an existing
   category, requests another proposal, or cancels.
3. The inbound reply maps to the exact pending workflow and resumes its
   checkpoint.
4. Creation and expense posting occur only after the interrupt in an idempotent
   database transaction.
5. A re-proposal excludes candidates rejected in the current review; after
   three rejections the interface requires existing-list or manual resolution.

### Planned receipt review

1. Telegram media metadata is durably ingested before processing.
2. The worker downloads and validates the image, hashes it, and stores it outside
   the web root.
3. The selected local extractor emits a versioned draft with source evidence and
   confidence.
4. Deterministic reconciliation checks total, receipt lines, category
   allocations, payment allocations, discounts, and rounding.
5. The owner reviews the complete draft through Telegram or the dashboard.
6. Only an approved, reconciled draft is posted; otherwise it remains in review.

### Telegram recovery and processing (implemented, not yet accepted)

The migration/repository, production gateway, response renderer, sequential
worker, and pairing implement every step below. Bot API validation has passed against the real test bot without sending owner
content. No complete owner workflow has been accepted, and no systemd unit has
started the processes yet.

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

### Planned insight request

1. The router selects a spending, budget, wallet, or advice workflow.
2. A bounded agent receives only the read tools needed for that route.
3. Tools compute amounts, periods, budget status, and comparisons
   deterministically from posted allocations.
4. The final answer may narrate only facts returned by tools and states its
   period and interpretation of `too much`.

## 7. Deployment View

### Current deployment

```text
Ubuntu workstation
├── systemd user services: accepted Telegram, worker, llama.cpp units
├── money-tracker-api -> 127.0.0.1:8000  [implemented; install acceptance open]
├── compiled React assets served by money-tracker-api
├── UV-managed Python CLIs
├── optional llama-server on 127.0.0.1:8080  [not installed/configured here]
├── private receipt archive and Restic staging  [owner-configured absolute paths]
├── external Restic repository                  [owner-selected, unscheduled]
└── Docker Compose
    ├── application-postgres on 127.0.0.1:5433  [default, healthy]
    └── observability profile                    [optional, healthy]
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
worker, and llama.cpp, rendered for a checkout by
`scripts/install_systemd_units.sh`. They are user services because the
application owns no system-wide resource, and they are not ordered against
Docker because a user unit cannot be: a database that starts late is handled by
process exit codes instead. Exit code 1 means an operator must change something
and the unit stops;
this includes `database_schema_invalid` when required migrations are absent.
Exit code 2 means a dependency was unreachable and the unit restarts. The
Python entry points replace psycopg diagnostics with stable reason codes.
The three Step 4 units passed enabled-service and reboot acceptance; the new API
unit is checked and tested but has not completed installed browser acceptance.

Systemd user services use lingering, or an equivalent host setup, to recover
without a manual application start. Each service tolerates delayed database,
network, and inference readiness through health checks and retry policies.

Step 4 installed and accepted the Telegram gateway, workflow worker, and
llama.cpp units. The first Step 5 slice adds the API unit and compiled-asset
preflight without coupling Telegram recovery to dashboard availability.

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
- Callback and free-text follow-up map to explicit pending actions and workflow
  IDs instead of assuming one global chat thread.

### Local web browser security contract (first slice implemented)

- Typed configuration accepts only an IPv4 loopback listener, exact loopback
  Host names, exact HTTP origins, and an absolute compiled-asset directory.
- Host validation occurs before routing to block DNS rebinding. A present Origin
  must match exactly, and the service emits no CORS permission headers.
- Unsafe methods require an allowed Origin plus equal random values in an
  HttpOnly, same-site cookie and the `X-CSRF-Token` header. The first monthly
  endpoint is read-only; the middleware contract already covers later writes.
- API responses are `no-store`. CSP restricts content and connections to the
  same origin; framing, referrers, and MIME sniffing are disabled. Interactive API schema endpoints are absent.
- Static serving exposes only the compiled index and asset directory. The API
  returns no credential, profile identity, receipt path, owner text, or raw
  database diagnostic.
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
- Implemented expense, wallet, and budget mutations share a transaction with
  their audit event. Future channel mutations also include their outbox record.
- Ledger wallet debits lock the funding source and validate the committed
  balance before append; budget and expense replacements compare versions.
- Categories and financial history use stable IDs. Archive and void replace
  destructive deletion for referenced records.

### Observability and evaluation

- Daily operation works when Langfuse is stopped.
- Traces use pseudonymous profile/session identifiers and component versions.
- Deterministic evaluators guard monetary, schema, routing, tool, reconciliation,
  and idempotency behavior. Human or model review supplements subjective quality.
- Private receipt and finance datasets never enter Git.

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
  implemented and verified; daily scheduling is not.
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
- Langfuse backup is optional and separate because it is not authoritative.

### Testing

- Unit tests exercise production domain behavior and replace only genuine
  external boundaries when isolation is the goal.
- Integration tests use real PostgreSQL, inference, Langfuse, Telegram, and
  media protocols for the components in scope.
- End-to-end tests traverse real user entry points and clean isolated persistent
  records.
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

### AD-003: Headless local initialization

- Status: accepted
- Implementation: implemented

Generate credentials locally and use the same project keys for Langfuse
initialization and SDK configuration. Never overwrite or commit the sensitive
environment file.

### Partially implemented accepted decisions

- [AD-005: Dedicated application PostgreSQL](decisions/AD-005-application-postgresql.md):
  service, migration boundary, checkpoints, finance schema, expense, wallet,
  budget, pending-proposal, versioned receipt-review, and messaging persistence
  implemented; remaining adapters planned.
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
  isolated restore drill, accepted Step 4 host units, and initial Step 5 API unit
  implemented; scheduling and the clean-machine restore remain planned.
- [AD-011: Evaluation-gated receipt extraction](decisions/AD-011-receipt-extraction.md):
  private original-media boundary implemented; extractor evaluation and review
  workflow planned.
- [AD-009: Local FastAPI and React web application](decisions/AD-009-local-web-application.md):
  strict version-one finance wire contracts plus the first FastAPI/React monthly
  slice, HTTP security, and host deployment implemented; remaining Step 5
  resources and acceptance planned.
- [AD-014: Private content-addressed receipt archive](decisions/AD-014-private-receipt-archive.md):
  safe local adapter, versioned review state, and real filesystem/PostgreSQL
  integration implemented; administration CLI wiring and encrypted archive
  backup/restore implemented; API/worker wiring and deletion planned.
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

## 10. Quality Requirements

| Scenario | Expected response |
| --- | --- |
| Telegram redelivers an update after a crash | Inbox deduplication and command idempotency produce exactly one financial write. |
| The workstation restarts within 24 hours | The gateway durably ingests the remaining Telegram backlog and the worker processes it in order. |
| The workstation was offline longer than 24 hours | The owner receives a possible-loss warning; documentation requests manual resend and makes no recovery claim. |
| A model emits malformed structured output | One corrective retry occurs, then the workflow asks for clarification without writing. |
| An expense has mixed categories and payment sources | Both independent allocation sums reconcile exactly before posting. |
| A category does not fit | The workflow interrupts and creates nothing without explicit approval. |
| A receipt total and extracted lines differ | The receipt remains in review with the unexplained difference visible. |
| Langfuse is stopped | Core ingestion, finance, Telegram, API, and dashboard behavior continues without traces. |
| A user outside the paired owner contacts the bot | The gateway rejects the update without exposing finance state. |
| A browser uses a non-loopback route or disallowed origin | The API refuses access or mutation. |
| A restore is performed on an empty installation | Database, receipt references, retained media, and required configuration recover consistently. |
| A model or prompt candidate misses a safety gate | It is not promoted; the current accepted configuration remains active. |

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
- The encrypted authoritative backup and isolated restore drill are implemented,
  but automatic scheduling, off-device secret verification, and a clean-machine
  restore remain later release work.
- The real Telegram evidence uses a private test bot, not a production bot. The
  Step 5 monthly dashboard has component and real PostgreSQL HTTP coverage but
  still lacks complete browser E2E and installed-service acceptance.

### Target architecture risks

- Telegram cannot guarantee update recovery beyond 24 hours without an external
  always-on component.
- Local models may not meet Italian routing, schema, tool, or receipt quality
  gates within 16 GB VRAM.
- OCR/VLM receipt extraction may require extensive private annotation and manual
  correction.
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
