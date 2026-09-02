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
[Vision and Requirements](../product/vision-and-requirements.md). The current
repository contains the completed Step 1 platform foundation and the current
Step 2 finance increment: pure domain contracts, the accepted seeded taxonomy,
authoritative finance tables, transactional expense, wallet, and budget
repository operations, and audited pending category-proposal submission. It
also registers content-addressed receipt metadata idempotently, but has no
Telegram adapter, web application, receipt extraction pipeline, media-store
runtime wiring, backup, or product workflow. A safe local receipt-media adapter
exists as a tested library component.

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
- React and TypeScript will implement the planned dashboard; FastAPI will expose
  and serve it locally.
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
| Application to inference | OpenAI-compatible HTTP | Messages, schemas, tool calls, model settings | Implemented basic chat |
| Application to Langfuse | SDK over local HTTP/OTLP | Redacted graph/model observations and aggregate scores | Implemented, optional and healthy |
| Telegram gateway to Bot API | HTTPS long polling and send methods | Updates, callback queries, messages, file metadata and downloads | Planned |
| Browser to application API | Same-origin HTTP JSON | Finance resources, settings, reviews, analytics and operations | Planned |
| Application to PostgreSQL | PostgreSQL protocol | Checkpoints and finance schema plus expense, wallet, budget, pending-proposal, and receipt-metadata persistence implemented; messaging state planned | Implemented Step 1 foundation and partial Step 2 finance repositories |
| Application to receipt archive | Private host filesystem | Fully decoded original JPEG/PNG/WebP keyed and verified by content hash; no derivatives | Local adapter implemented; runtime wiring/deletion/backup planned |
| Backup process to repository | Restic repository protocol | Database dump, receipts and recovery configuration | Planned |

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
│   ├── FastAPI application and compiled React UI        [planned]
│   ├── Telegram gateway                                [planned]
│   ├── Sequential workflow worker                      [planned]
│   └── llama.cpp/OpenAI-compatible inference           [scaffolded]
├── Private host data
│   ├── Receipt archive adapter                         [implemented, unwired]
│   ├── Evaluation data and artifacts                   [implemented foundation]
│   └── Restic staging/configuration                    [planned]
└── Docker Compose
    ├── Application PostgreSQL                          [implemented finance core]
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
| `persistence` | Apply versioned migrations and bootstrap persistent LangGraph checkpoints | Alembic, psycopg, PostgreSQL checkpointer | Foundation schemas and workflow checkpoints | Host administration CLI; least-privilege loopback database role |
| `domain` | Enforce exact money, expense/refund, dual-allocation, taxonomy, wallet, budget, receipt-metadata, and audit contracts | Pure Python value objects and policies; no framework or infrastructure import | No infrastructure state | Shared host-process library; deterministic validation of untrusted inputs |
| `application` | Coordinate idempotent expense lifecycle and receipt registration, audited wallet/budget mutations, and pending proposal submission through narrow ports | Finance, administration, proposal, and receipt-metadata repository ports plus clock and ID contracts | Command, concurrency, review-boundary, and audit semantics, not stored state | Shared by future CLI/API/worker; profile scope comes from trusted caller context; proposal/receipt transitions are not exposed yet |
| `persistence.finance` | Bootstrap profile/taxonomy, persist complete expense and budget aggregates, append balance-safe wallet movements, validate/store pending proposals, and register receipt metadata transactionally | psycopg and narrow application repository contracts | Finance tables, allocations, budgets/overrides, wallet ledger, proposals, receipt metadata, audit, and idempotency records | Dedicated application PostgreSQL; no receipt bytes or paths; source-row locks serialize ledger debits |
| `media.local` | Decode, validate, atomically retain, and integrity-check private receipt originals | Receipt-media port, Pillow, private filesystem | Original bytes under sharded SHA-256 keys; no database state | Host library component outside web roots; `0700` directories, `0600` files, no-follow verified reads; runtime root not wired yet |
| `evaluations` | Probe real model protocols, capture exact runtime metadata, score deterministically, optionally publish aggregates | OpenAI-compatible HTTP, local files, optional Langfuse SDK | Ignored local run artifacts; no domain authority | Host CLI; secrets, image bytes and paths are excluded |
| `development` | Validate canonical documentation and privacy-ignore invariants | Repository Markdown/YAML/filesystem | No persistent data | Service-independent development command |

### Planned domain and application blocks

| Component | Responsibility | Narrow interfaces/dependencies | Data ownership | Deployment/security |
| --- | --- | --- | --- | --- |
| Finance domain (core implemented) | Enforce money, expense/refund, allocation, budget, category and wallet invariants | Pure value objects and domain policies | No infrastructure state | Python application; no framework imports |
| Application services (expense/wallet/budget, proposal submission, and receipt registration implemented) | Execute validated commands and queries transactionally | Narrow repository ports, clock, ID and future media ports | Command/content idempotency, versioning, review boundary, and audit semantics | Future FastAPI/worker host processes; profile scope injected; proposal and receipt decisions remain workflow-owned |
| Finance repositories (expense/wallet/budget/proposal/receipt metadata implemented) | Persist authoritative domain records and analytics projections | PostgreSQL protocol through explicit repository contracts | Finance schema implemented; proposal and receipt lifecycle transitions open | Dedicated application database; least-privilege credentials; wallet debits serialized per source; no media payloads |
| Messaging inbox/outbox | Receive, order, retry, dead-letter, replay and dispatch transport work | PostgreSQL and Telegram adapter | Raw updates, offsets, delivery attempts, result references | Gateway and worker; bot token never stored in rows |
| Workflow runtime | Route intents and execute specialist graphs | LangGraph, typed model gateway, application services, checkpointer | Small versioned workflow state only | Sequential worker; no images or service objects in checkpoints |
| Receipt service | Validate media, invoke extractor, reconcile lines, prepare review | Implemented media-store port, planned extractor, finance service | Receipt metadata, extraction versions, review state | Private filesystem; untrusted image boundary; orchestration planned |
| Analytics service | Compute time, category, budget and funding summaries | Read repositories and deterministic calendar policies | No separate authoritative data | FastAPI and read-only agent tools |
| Backup service | Create consistent encrypted backups and verify restores | `pg_dump`, Restic, receipt/configuration paths | Backup snapshots in owner repository | Scheduled locally; recovery secret stored off-device |

### Planned adapters and presentation blocks

| Component | Responsibility | Interfaces/dependencies | Data ownership | Deployment/security |
| --- | --- | --- | --- | --- |
| Root router and subgraphs | Route one intent and coordinate extraction, review, query or advice | Versioned Pydantic schemas, scoped tool registries | Checkpoint references and workflow IDs | Worker; mutation paths are deterministic |
| Telegram adapter | Pair owner, long poll, download media and send/edit responses | Telegram Bot API and messaging services | No authoritative finance data | Separate host service; private chats only |
| FastAPI adapter | Expose versioned REST resources and health/operations endpoints | Application services | No independent data | Loopback, allowed Host/Origin, CSRF protection |
| React dashboard | Display and edit finance, review, budget, wallet and operational state | Same-origin REST API | Browser session/UI state only | Compiled static assets; no secrets |
| Local model gateway | Provide typed chat, schema and tool capabilities | OpenAI-compatible local endpoint | No persistent domain data | Replaceable host inference process |
| Receipt extractor adapter | Produce a versioned structured receipt draft | Selected local OCR/VLM pipeline | No authoritative data until validated | Local-only; selection remains evaluation-gated |
| Langfuse adapter | Record redacted traces and experiment scores | Local Langfuse SDK | Observability copies, not source of truth | Optional development/evaluation profile |

Detailed finance concepts and the initial taxonomy live in
[Domain and Taxonomy](../product/domain-and-taxonomy.md).

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
   Those transitions remain part of the planned bounded owner-review workflow.

### Current receipt metadata registration

1. A caller supplies domain-validated metadata only after the local media-store
   port has accepted and identified decoded content.
2. The service accepts only the initial `stored` state and never receives bytes
   or filesystem paths.
3. PostgreSQL creates the first row for `(profile_id, content_sha256)` with its
   audit. An identical retry returns that row and writes no second audit.
4. A retry claiming a different byte size or media type fails; a failed audit
   rolls back the new metadata row.

The media adapter is implemented but not wired to an API/worker entry point.
Receipt review transitions remain planned.

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

### Planned text expense turn

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

### Planned Telegram recovery and processing

1. The gateway requests the earliest unconfirmed batch through long polling.
2. It inserts raw updates under unique `update_id` values and commits.
3. Only then does it request the next offset, confirming prior Telegram updates.
4. The worker locks the oldest eligible inbox record and processes one at a
   time. A waiting human workflow does not block unrelated later messages.
5. Transient failures retry five times with backoff. Exhausted messages become
   dead-letter records visible for manual replay.
6. Domain changes and response outbox records commit atomically. The Telegram
   dispatcher sends outstanding responses independently.

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
├── UV-managed Python CLIs
├── optional llama-server on 127.0.0.1:8080  [not installed/configured here]
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
│   ├── money-tracker-api       -> 127.0.0.1, same-origin API and UI
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

Systemd user services use lingering, or an equivalent host setup, to recover
without a manual application start. Each service tolerates delayed database,
network, and inference readiness through health checks and retry policies.

## 8. Cross-Cutting Concepts

### Money and time

- Monetary values use integer minor units plus ISO currency; binary floats are
  prohibited at storage and public interfaces.
- Both category and payment allocations reconcile exactly with the parent total.
- The occurrence time, not processing time, controls reports and budgets.
- Relative Italian dates resolve against the source message timestamp in
  `Europe/Rome`.

### Identity, authorization, and idempotency

- One paired profile owns all V1 data. Profile identifiers come from trusted
  runtime context, never model-generated tool arguments.
- Every external update and financial command has a stable idempotency key.
- Callback and free-text follow-up map to explicit pending actions and workflow
  IDs instead of assuming one global chat thread.

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
- Structured-output parsing gets one corrective retry, then clarification.
- Receipt or payment mismatches remain visible drafts.

### Backup and retention

- Daily encrypted Restic backups include a consistent application database dump,
  retained receipt files, and required recovery configuration.
- Default snapshot retention is seven daily, five weekly, and twelve monthly.
- The recovery secret has an off-workstation copy. A tested restore is a release
  gate.
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
  service, migration boundary, checkpoints, finance schema, and expense,
  wallet, budget, pending-proposal, and receipt-metadata repository operations
  implemented; remaining finance adapters and messaging planned.
- [AD-007: Expense and dual-allocation model](decisions/AD-007-expense-allocation-model.md):
  domain, schema, expense/wallet/budget persistence, reconciliation, and
  reports implemented; remaining adapters planned.
- [AD-008: Controlled two-level taxonomy](decisions/AD-008-controlled-taxonomy.md):
  policy, accepted stable seed, schema, bootstrap, and taxonomy-scoped pending
  proposal submission implemented; owner decisions and category mutation
  planned.
- [AD-006: Bounded typed agent workflows](decisions/AD-006-bounded-agent-workflows.md):
  persistent checkpoint adapter implemented; typed workflows planned.
- [AD-012: Evaluation-driven model and prompt promotion](decisions/AD-012-evaluation-driven-promotion.md):
  capability runner, artifact contract, and passing technical candidates
  implemented; product-role promotion remains experiment-gated.
- [AD-013: Host runtime and encrypted backup](decisions/AD-013-local-runtime-and-backup.md):
  Compose topology implemented; systemd and Restic planned.
- [AD-011: Evaluation-gated receipt extraction](decisions/AD-011-receipt-extraction.md):
  private original-media boundary implemented; extractor evaluation and review
  workflow planned.
- [AD-014: Private content-addressed receipt archive](decisions/AD-014-private-receipt-archive.md):
  safe local adapter and real filesystem/PostgreSQL integration implemented;
  runtime wiring, deletion, and backup planned.

### Planned accepted decisions

- [AD-004: Local-first privacy with Telegram boundary](decisions/AD-004-local-first-telegram-boundary.md)
- [AD-009: Local FastAPI and React web application](decisions/AD-009-local-web-application.md)
- [AD-010: Telegram long polling and durable messaging](decisions/AD-010-telegram-durable-messaging.md)

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

- The finance domain, schema, taxonomy seed, expense/wallet/budget repository
  operations, pending proposal submission, and receipt metadata registration
  plus the private receipt archive adapter are only a partial Step 2
  implementation. Proposal/category and receipt lifecycle transitions, API,
  administrative bootstrap CLI, media-store runtime wiring, backup, and restore
  remain open.
- The product graph is stateless and has one unconstrained assistant node,
  although the replaceable PostgreSQL checkpoint adapter is available.
- Three pinned llama.cpp/GGUF profiles have passed every applicable Step 1
  technical capability gate with exact runtime metadata. None is yet promoted
  to a product role because the Step 3 behavioral evaluation gates remain open.
- Application data is not yet covered by the accepted Restic backup/restore
  process; persistent personal finance data must not be encouraged before its
  roadmap gate.
- Existing inference integration and E2E tests remain opt-in smoke checks with
  weak behavioral assertions.

### Target architecture risks

- Telegram cannot guarantee update recovery beyond 24 hours without an external
  always-on component.
- Local models may not meet Italian routing, schema, tool, or receipt quality
  gates within 16 GB VRAM.
- OCR/VLM receipt extraction may require extensive private annotation and manual
  correction.
- A localhost-only dashboard still needs browser-origin and local-process threat
  controls.
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
