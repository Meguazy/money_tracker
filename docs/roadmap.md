# Delivery Roadmap

- Status: accepted
- Sequence authority: this file
- Product baseline: [docs/product](product/README.md)
- Architecture baseline: [docs/arc42](arc42/README.md)

This roadmap delivers Money Tracker through independently verifiable steps. It
does not estimate calendar time. Future agents must implement only the active
step unless the owner explicitly changes scope.

## Status summary

| Step | Outcome | Status |
| --- | --- | --- |
| 0 | Decision-complete documentation baseline | Complete (2026-09-01) |
| 1 | Healthy platform and evaluation foundation | Complete (2026-09-02) |
| 2 | Deterministic finance domain and persistence | Complete (2026-09-02) |
| 3 | Evaluated text workflows | In progress |
| 4 | Durable Telegram MVP | Planned |
| 5 | Local dashboard, budgets, analytics, and advice | Planned |
| 6 | Evaluated receipt workflow | Planned |
| 7 | Operational hardening and personal V1 release | Planned |

Status changes require evidence links in the relevant step. A step is complete
only when all exit criteria pass; merged code alone is insufficient.

## Step 0: Documentation baseline

Status: **Complete (2026-09-01).** Steps 1 and 2 are complete; Step 3 is in
progress.

### Intent

Give future sessions a stable product vocabulary, accepted architecture,
delivery sequence, evaluation policy, and operational boundary before building
new components.

### Deliverables

- Product vision, requirements, journeys, domain/taxonomy, and glossary.
- Updated arc42 current/target views and accepted ADRs.
- This staged roadmap with exit criteria and dependencies.
- Experiment process, evaluation matrix, templates, and decision log.
- Preliminary Telegram, runtime, backup/restore, and security runbooks that
  clearly distinguish current from planned behavior.
- Documentation index and `AGENTS.md` reading/change protocol.

### Exit criteria

- Every agreed product and architecture decision is represented once in a
  canonical source and cross-linked from indexes.
- Planned components and commands are never presented as implemented.
- arc42 covers responsibility, interfaces, dependencies, ownership, runtime,
  deployment, quality, security, and risk for every planned component.
- ADR index distinguishes implemented and planned accepted decisions.
- Requirements, roadmap gates, tests/evaluations, and operations are traceable.
- Markdown links, formatting, duplicate requirement IDs, and Git whitespace
  checks pass.

### Non-goals

No application code, dependency, Compose, systemd, migration, bot, model,
runtime configuration, or test behavior changes in Step 0.

### Completion evidence

- The product baseline, journeys, domain model, taxonomy, glossary, arc42 views,
  ADRs, roadmap, experiment framework, and operational runbooks are documented.
- All relative Markdown links resolve to repository files.
- Requirement identifiers are unique, Markdown code fences are balanced, and the
  experiment run-manifest template parses as YAML.
- `git diff --check` reports no whitespace errors.
- The existing unit suite passes: 3 tests.

## Step 1: Platform health and evaluation foundation

Status: **Complete (2026-09-02).** Platform, persistence, tooling, and
evaluation foundations pass their exit criteria. Product-role model promotion
remains gated by later domain evaluations.

### Prerequisites

Step 0 complete. Existing volumes and sensitive configuration identified through
read-only checks before any migration or repair.

### Deliverables

- Repair the current Langfuse non-cluster ClickHouse configuration with
  `CLICKHOUSE_CLUSTER_ENABLED=false`; verify web/worker health without deleting
  volumes.
- Pin tested container versions and digests, beginning with the installed
  Langfuse 4.26.0 images, and document controlled upgrades.
- Add dedicated application PostgreSQL with health checks, least-privilege
  credentials, persistent volume, and a separate optional Langfuse profile.
- Add migrations and the persistent LangGraph PostgreSQL checkpointer bootstrap.
- Introduce UV development dependencies and repeatable commands for Ruff, type
  checking, pytest, coverage, and documentation validation.
- Add the executable evaluation layout, synthetic/anonymized datasets, private
  artifact ignore rules, runner skeleton, deterministic score reporting, and
  optional Langfuse result publication.
- Implement model capability probes for strict structured output, tool calling,
  tool/result sequencing, and image input where applicable.

### Exit criteria

- Core PostgreSQL starts independently; Langfuse starts only when selected.
- Langfuse services become healthy with preserved data or a documented,
  explicitly approved recovery path.
- Forward migration, rollback policy, and fresh bootstrap are tested against a
  real application PostgreSQL server.
- Capability results include exact model/GGUF/build/chat-template metadata.
- Unit tests and static checks run without optional services; integration checks
  fail or skip transparently when their real service is absent.

### Explicit exclusions

No financial table or product workflow beyond the minimum migration/checkpoint
foundation. No model is promoted merely because the capability probe responds.

### Implementation evidence

- The dedicated `application-postgres` service starts in the default Compose
  project with a persistent volume, health check, and non-superuser application
  role. The separate Langfuse stack is selected with the `observability`
  profile.
- Langfuse 4.26.0 web and worker, PostgreSQL, ClickHouse, Redis, and MinIO are
  pinned by digest. The non-cluster ClickHouse repair was applied and the
  existing named volumes were preserved; web and worker health checks pass.
- Alembic forward migration, guarded foundation rollback, fresh bootstrap, role
  privilege checks, and PostgreSQL checkpoint persistence pass against the real
  application PostgreSQL service.
- UV locks the development toolchain. Ruff, mypy, pytest, coverage, Compose
  validation, documentation validation, and Git whitespace checks have
  repeatable commands in [Development](development.md).
- The executable `evals/` layout contains public synthetic cases, a capability
  suite, ignored private/generated paths, deterministic local reports, exact
  model/build metadata collection, and optional aggregate Langfuse publication.
- A capability run with no inference server fails explicitly and still writes a
  machine-readable local result. Five pinned local candidates were screened on
  the real CUDA llama.cpp runtime; three passed every applicable technical gate
  with exact metadata. See the
  [capability results](experiments/local-model-capability-results-2026-09-02.md).

## Step 2: Finance domain and persistence

Status: **Complete (2026-09-02).** All four exit criteria have evidence below.
The framework-independent domain, initial taxonomy, finance schema,
expense/wallet/budget repositories, pending category-proposal submission,
audit, idempotency, and concurrency foundation are implemented, together with
the private content-addressed receipt-media adapter, versioned receipt
review-state transitions, strict V1 finance wire contracts, a minimal local
administration command, and verified encrypted backup with an isolated restore
drill.

Work this step deliberately does not contain, with its owning step: the FastAPI
listener and HTTP security belong to Step 5, proposal decisions and the owner
review workflow to Step 3, worker media wiring to Steps 3 and 4, receipt
extraction and media deletion to Step 6, and backup scheduling plus the
clean-machine restore drill to Step 7. Step 2 fixes their contracts without
shipping their behavior.

### Deliverables

- Framework-independent money, profile, expense/refund, dual allocation,
  category, proposal, funding source/wallet, monthly budget, receipt metadata,
  and audit contracts.
- Transactional application services and PostgreSQL repositories with
  idempotency and optimistic concurrency.
- Seed the accepted 10-root/14-child taxonomy with stable identifiers and
  inclusion descriptions.
- Implement posted/draft/needs-review/voided lifecycle, correction, undo, parent
  roll-ups, funding-class split, and benefit-wallet entries.
- Versioned `/api/v1` finance contracts and a minimal administrative CLI for
  development/bootstrap; no claim of final UI.
- Content-addressed receipt-media port and safe local implementation, without
  extraction.
- Restic backup implementation for the new authoritative data and a preliminary
  restore test before real personal data is encouraged.

### Exit criteria

- Domain invariant tests cover minor units, both allocation sums, refunds,
  wallet behavior, budget calendar rules, taxonomy constraints, audit, and
  idempotency.
- Real PostgreSQL integration tests cover transactions, constraints,
  migrations, concurrent version conflicts, and restart persistence.
- API contract tests demonstrate stable IDs, ISO timestamps, currency fields,
  problem responses, and required mutation idempotency keys.
- Backup and restore reconstruct a non-production isolated finance fixture and
  its media references.

### Completion evidence

- Pure production contracts cover EUR minor units, profile scope,
  expense/refund polarity, dual allocations, lifecycle, correction/void,
  category proposals, funding sources and wallet entries, Rome-calendar
  budgets, receipt metadata, audit, roll-ups, and funding-class totals.
- The framework-independent `api.contracts` component fixes the `/api/v1`
  expense request/response, exact EUR money, stable UUID, aware ISO timestamp,
  problem-detail, and canonical `Idempotency-Key` shapes. It exposes no HTTP
  listener and accepts no caller-controlled profile scope.
- The accepted taxonomy is code-owned and versioned with deterministic IDs,
  ten roots, fourteen children, inclusion descriptions, two-level validation,
  normalized aliases, and soft complexity warnings.
- Alembic creates authoritative finance tables, deferred reconciliation and
  profile/reference checks, append-only audit/wallet history, idempotency
  records, and guarded non-destructive rollback behavior.
- The PostgreSQL repository bootstraps the profile/taxonomy, reconstructs and
  mutates complete expense and budget aggregates, and appends balance-safe
  wallet entries. Expense and budget mutations use optimistic version checks;
  ledger debits serialize on the funding source so concurrent writers cannot
  overdraw it. Every mutation stores audit in the same transaction.
- Pending category proposals validate their parent, alternatives, and normalized
  name against the active profile taxonomy, then persist with audit in one
  transaction. Owner resolution and category creation remain Step 3 workflow
  work and cannot be pre-resolved by the submission service.
- Receipt metadata registration accepts only initial `stored` records, uses the
  profile/content SHA-256 pair as natural idempotency, rejects conflicting
  size/type claims, and audits only the first insert. It stores no media bytes or
  filesystem paths. The versioned lifecycle permits only `stored` to
  `needs_review` to `approved`, requires the mandatory review step, and
  commits each transition with audit under optimistic concurrency. Deletion
  remains unavailable pending reference and backup-retention semantics.
- The local receipt-media port fully decodes single-frame JPEG/PNG/WebP under
  20 MiB, 40-megapixel, and 10,000-pixel-side limits. It stores originals under
  private sharded SHA-256 paths with atomic no-overwrite writes, verified reads,
  symlink refusal, and tamper detection. Runtime entry-point wiring, deletion,
  and encrypted backup remain open outside the local administration CLI.
- `money-tracker-admin receipt-store` requires an active profile and absolute
  configured archive root, refuses symlink/non-file/oversized inputs, then
  stores and registers metadata idempotently. Its JSON output omits private
  paths.
- `money-tracker-backup` implements the runbook sequence: exclusive host lock,
  database-identity and receipt-hash preflight, a mode-600 `pg_dump` and
  versioned integrity manifest in private mode-700 staging, one tagged Restic
  snapshot of dump, receipt archive, and runtime configuration, repository and
  exact-snapshot verification before staging removal, then seven daily/five
  weekly/twelve monthly retention grouped by host and tag so per-run staging
  paths cannot defeat the policy. `status` reports the newest snapshot and its
  age. Credentials reach subprocesses only through libpq environment variables
  and the Restic password-file path; failures name the failing executable alone.
- `restore-drill` reconstructs a snapshot only into an explicitly named empty
  isolated database and an absent or empty real receipt directory. It requires
  exactly one compatible manifest, verifies dump, configuration, and every
  referenced receipt hash in staging, refuses symlinked restored paths, imports
  with `pg_restore --exit-on-error`, and re-verifies each receipt hash at the
  destination. Backup and restore are unavailable, not silently degraded, when
  Restic or matching PostgreSQL client tools are absent.
- Forty-three domain tests, twenty-nine wallet/budget/proposal/receipt
  application tests, twenty-five media/configuration/administration tests, and
  twenty-three backup safety/CLI tests pass.
  Sixteen strict API contract tests cover the version-one wire boundary at
  100% branch coverage.
  Thirty-seven real PostgreSQL/media integration tests cover migration, full
  storage/metadata restart, receipt review lifecycle, concurrent registration
  and version conflicts, receipt audit rollback, proposal scoping and atomic
  rollback, constraints, restart persistence, immutable history, exact taxonomy
  seeding, retry idempotency, concurrent expense writers, concurrent wallet
  debits, and concurrent/stale budget versions. Two real Restic drills
  reconstruct the isolated finance fixture and its receipt media through the
  service and through the operator CLI, and assert one retention group across
  runs, snapshot freshness, empty-target refusal, and redacted output. The
  complete service-independent suite passes with 151 tests and 42 explicit
  real-service skips; focused domain/application/media/api branch coverage is
  91% and backup branch coverage is 87% with the real drills.

Exit-criteria evidence:

| Exit criterion | Evidence |
| --- | --- |
| Domain invariant tests | [`tests/unit/money_tracker/domain`](../tests/unit/money_tracker/domain) and [`tests/unit/money_tracker/application`](../tests/unit/money_tracker/application) |
| Real PostgreSQL integration tests | [`tests/integration/money_tracker/persistence`](../tests/integration/money_tracker/persistence) and [`tests/integration/money_tracker/media`](../tests/integration/money_tracker/media) |
| API contract tests | [`tests/unit/money_tracker/api`](../tests/unit/money_tracker/api) |
| Backup and restore reconstruct an isolated fixture | [`tests/integration/money_tracker/backup`](../tests/integration/money_tracker/backup) and [Backup and Restore](operations/backup-and-restore.md) |

The real Restic and PostgreSQL 17 client executables are host prerequisites, not
vendored dependencies. The drill was verified with Restic 0.18.1 and
`pg_dump`/`pg_restore` 17.11 against the pinned PostgreSQL 17 service; the
backup runbook records how to install them.

## Step 3: Evaluated text workflows

Status: **In progress.** The framework-independent routing, extraction, and
resolution foundation is implemented: the bounded intent taxonomy with
partitioned mutation authority, the normalized channel work unit, deterministic
pre-routing, the strict versioned router and expense/refund extraction model
boundaries with their deterministic policy vetoes, the versioned category-fit
boundary, pure resolution of occurrence time, amount sign, and both reconciled
allocation dimensions, and the strict-schema retry boundary behind a narrow
model port. The typed root router is assembled as a LangGraph state graph over
the persistent PostgreSQL checkpointer, with a local structured-output adapter
and a local command for exercising it by hand. The expense and refund
specialist workflow runs end to end, storing a draft through the existing
application services or reporting the question it owes. No persistent owner
interrupt, read-only tool registry, channel wiring, or evaluation dataset
exists yet, and no confidence bar, prompt, or model is promoted.

### Prerequisites

Step 2 complete. A capability-screened local candidate exists from Step 1, but
no candidate is promoted to a product role; promotion happens through this
step's gates and the experiment decision log.

### Remaining increments

This order is a handoff aid, not new scope. Each increment must land with its
own tests and documentation, and none may ship a later step's behavior.

1. The read-only query and advice routes with scoped tool registries. The
   movement workflow is implemented.
2. Persistent human-in-the-loop category review over the existing pending
   proposal port, covering approve, best existing, named existing, re-propose,
   cancel, unrelated input, and the three-rejection transition, with side
   effects placed after the interrupt and made idempotent for replay.
3. The pinned `routing/intent-it`, `ingestion/expense-it`,
   `categorization/purpose-it`, and `workflow/category-review-it` datasets and
   suites, then calibration of the routing confidence bar and the auto-post
   eligibility policy on held-out labels. This increment now blocks usable
   end-to-end behaviour, not just promotion: see the extraction finding in the
   evidence below. An ad-hoc check against the screened
   Qwen3 14B profile routed the expense, refund, split-funding, query, budget,
   advice, and non-finance journeys as the product baseline expects, but routed
   `Vorrei caricare 100 euro di buoni pasto` to the expense route instead of
   the unsupported route that journey J5 requires. Misreading a benefit credit
   as spending is a safety-tagged failure, so the intent dataset must cover
   wallet-credit phrasing. Self-reported confidence was coarse and nearly
   constant across every case, which is why the bar has to be calibrated on
   held-out labels rather than on model self-report.
4. Promotion of the model, prompts, schemas, and thresholds through
   [Decision Log](experiments/decision-log.md), which currently records that no
   routing confidence bar and no auto-post threshold are promoted.

### Deliverables

- Typed root router with deterministic handling for channel commands/callbacks
  and specialist routes for expense, refund, queries, advice, category follow-up,
  correction/undo, and clarification.
- Versioned structured extraction and category-fit contracts with evidence and
  explicit unknowns.
- Deterministic expense/refund mutation paths that call application services;
  query/advice agents remain read-only.
- Persistent human-in-the-loop category review covering approve, best existing,
  named existing, re-propose, cancel, and unrelated input.
- Auto-post eligibility policy calibrated on the evaluation set; all other
  inputs remain drafts.
- A minimal local administration command for exercising implemented workflow
  components by hand before a channel exists; no channel adapter, durable
  worker, or owner-facing response.
- Production prompt/model configuration promoted through the experiment
  decision log.

### Exit criteria

- Routing, extraction, categorization, HITL, tool, advice-grounding, injection,
  and latency gates pass on pinned datasets.
- Restart/resume and repeated-command tests produce zero duplicate writes.
- Every Italian acceptance example in the product baseline passes through the
  real local model in integration/evaluation runs.
- Graph state contains only small serializable values and stable references;
  domain data remains in repositories.

### Current implementation evidence

- The `workflows` component is framework-independent: it imports Pydantic,
  `zoneinfo`, and pure domain values, and no LangGraph, model client,
  repository, or transport module. It owns no persistent state.
- Every intent belongs to exactly one authority class, and the classes are
  disjoint and exhaustive over the taxonomy. Query, advice, non-finance,
  unsupported, clarification, and receipt routes are outside the mutating set,
  so a non-write request has no routing path to a financial write.
- Deterministic pre-routing applies a total precedence order over explicit
  interface decisions, image media, supported commands, and blank input. Image
  media outranks its own caption, and a callback naming no pending action is
  clarified instead of applied to an arbitrary pending action. Commands are
  matched exactly and never inferred from prose; a routing decision carries no
  command argument, keeping a one-use pairing code out of routing telemetry.
- The `RouteClassification` schema is strict, versioned, parsed from the real
  JSON transport shape, and rejects unknown fields, coerced numbers,
  out-of-range confidence, repeated unknowns, blank or oversized evidence, and
  the two deterministic-only routes. It exposes no profile, timezone,
  funding-source, authorization, or idempotency field.
- Deterministic acceptance vetoes a category follow-up without exactly one
  correlated pending review, a stale or foreign reply reference, a correction
  or undo without a runtime-resolved movement, and any decision below the
  explicitly supplied confidence bar. Correlation targets come from trusted
  context, never from the classification.
- The `ExpenseExtraction` boundary is strict, versioned independently of the
  router boundary, and parsed from the real JSON transport shape. Cross-field
  validation keeps every declared unknown mutually exclusive with its own
  value, requires an absent amount to be declared unknown, keeps currency and
  amount inseparable, requires a split payment to quantify every portion and
  name distinct sources, and rejects a non-operational currency. Absence of
  temporal words, merchant, or funding portion is the documented default rather
  than a question.
- Occurrence time and amount sign are resolved by pure deterministic code, not
  by the model. The resolver anchors same-day, previous-day, counted day and
  week offsets, named past weekdays, and explicit Italian dates to the message
  timestamp in `Europe/Rome`; it refuses a bare weekday, an unreadable phrase,
  and a day after the message. It preserves the message wall-clock time so a
  named day stays inside its intended Rome day, and it normalizes a wall time
  inside the spring-forward gap onto the equivalent instant after the
  transition. A refund's negative sign comes from the stated polarity alone.
- Payment allocation resolves owner funding words against the profile's
  configured active sources, fills an omitted method from the configured
  default, and refuses an archived source, unmatched words, two portions naming
  one source, a missing default, and a split that does not sum to the total.
  Matching is exact on the folded configured name with no synonym guess. Refund
  allocations carry the negative total sign, and the resolved dimension is
  verified by building a real domain aggregate, which independently enforces
  non-zero rows, unique sources, matching polarity, and exact reconciliation.
- Every resolution failure reports a stable machine-readable reason code, so a
  workflow can ask the one question matching the failing step instead of
  retrying blindly or parsing prose. Like routing justification codes, these
  carry no owner text.
- Category fit is one versioned judgment with two outcomes, validated as a flat
  schema with conditional fields rather than a discriminated union, because no
  local candidate is capability-screened for union output. Categories are named
  and never identified: an existing outcome resolves through the active
  taxonomy's names and aliases, and assembling a proposal checks the candidate
  name, optional parent, and every examined alternative against the taxonomy.
  It refuses a name duplicating an active node or alias, a name already
  rejected in this review, a parent that is not an active root, and a
  fabricated alternative. A duplicate is refused rather than quietly converted
  into reuse, so a wrong judgment stays visible and measurable. No
  deterministic path creates a taxonomy node.
- The purpose dimension is one allocation carrying the whole signed total,
  recording the boundary version that classified it and whether the owner
  forced the category. An archived category cannot receive an allocation.
- The strict-schema boundary implements the AD-006 retry budget as
  straight-line code behind a narrow `StructuredOutputModel` port, so it cannot
  retry twice however it is called. A repair restates the same instructions and
  message and adds only rejected field paths and reasons, never the rejected
  values, and truncates the quoted completion so a runaway output cannot
  outgrow the context. A persistent failure raises a typed error carrying only
  the attempt count, with neither owner nor model text in its message.
  Non-JSON output is treated exactly like an invalid document.
- Candidate router instructions derive their intent vocabulary from the code, so
  the offered routes cannot drift from the taxonomy and no deterministic-only
  route is ever offered. They are labelled `CANDIDATE_` and versioned
  `candidate-`, and the decision log records that no prompt is promoted.
- The compiled root router is a LangGraph state graph whose deterministic node
  ends the graph before any inference call whenever the channel already stated
  the decision, verified by asserting the port received zero requests for
  image, command, callback, and uncorrelated-callback messages. Its
  classification node re-imposes the confidence bar and every routing veto on
  the model answer, and converts an exhausted retry budget into a clarification
  decision carrying the `invalid_model_output` reason instead of failing the
  invocation. Instruction text and confidence bar are required arguments with
  no defaults, so the assembly cannot imply a production configuration.
- Deterministic draft assembly turns one extraction and one purpose judgment
  into a complete movement, or reports the stable reason selecting the question
  to ask. Every movement is created as a draft, because no auto-post policy is
  promoted. A movement whose purpose needs a new category is still assembled
  with its funding dimension resolved and its purpose dimension empty alongside
  a pending proposal, which the domain admits as a draft and refuses to post.
  The result type rejects a combination that both resolved a purpose and
  proposed one.
- The creation key is derived from the durable inbound record and the request
  digest from the request's own semantics, excluding lifecycle status, version,
  and generated identifiers, and normalizing the occurrence instant to UTC so
  the same moment expressed with a different offset matches. Real PostgreSQL
  tests store a draft against a seeded profile's taxonomy and funding sources,
  replay one inbound record into exactly one movement, refuse a changed request
  on that record as a conflict, persist the split-funding journey, and keep a
  pending proposal alongside its draft without extending the taxonomy. A
  redelivery must replay the stored record unchanged, its channel timestamp
  included; a test that re-read the clock instead surfaced that precondition,
  which is now documented.
- The expense and refund workflow is a LangGraph graph over the same
  checkpointer, wiring the two model boundaries and deterministic assembly to
  the existing application services. Routing and extraction judge the direction
  of money independently and a disagreement clarifies rather than picking one.
  The purpose boundary receives the owner's own active category names rendered
  from their taxonomy at call time. Every node either advances or records a
  clarification, so a run stores a complete draft or stores nothing.
- Real PostgreSQL tests store a reconciled draft and the split-funding journey
  against a seeded profile, submit a pending candidate without extending the
  taxonomy, leave the database untouched on every clarification path, store
  exactly one movement for a redelivered record, and resume a finished thread
  from a checkpoint across separate connections.
- Three boundary-design defects were found by running the workflow against the
  real screened model, and all three were mine rather than the model's. The
  extraction schema required the model to restate the V1 currency, which is
  operational configuration it has no basis to supply; the schema handed to the
  endpoint left optional properties out of `required`, which constrained
  decoding does not enforce, so a model could omit a field and fail the whole
  answer; and cross-field mutual-exclusion rules were incompatible with
  requiring every property, because a model then fills a field and flags it in
  the same answer. Boundaries now validate shape only, every property is
  required with optionality expressed as an admitted null, and a declared
  unknown wins over a value supplied beside it.
- With those fixed, both boundaries return schema-valid answers for real
  Italian input and the mechanism completes: one split-funding message stored a
  reconciled draft end to end. The candidate extraction instructions then
  over-declare unknowns, treating an unstated time or payment method as
  unresolved rather than as the documented default, so most clear messages
  reach a clarification instead of a draft. Two prompt revisions did not fix it
  and further ad-hoc tuning is prohibited by AD-012: this is a prompt and model
  quality gap for the `ingestion/expense-it` dataset and its 0.98 ambiguity
  gate to measure, not a mechanism gap.
- `money-tracker-admin route` sends one message through the real compiled
  router and prints only the decision it reached. It takes the trusted routing
  context from flags rather than the database, so it needs no active profile
  and the policy vetoes are reachable by hand. It requires an explicit
  confidence bar, completes a deterministic route with no model involved, names
  the missing service when the local endpoint is unreachable rather than
  surfacing a transport traceback, and echoes no message content. It reports
  routing only: extraction, category fit, and posting exist as contracts and
  resolvers but are not yet wired to a workflow.
- Workflow state is a versioned contract of JSON primitives and stable string
  references. Round-trip tests reconstruct the message, the trusted context,
  and every deterministic and model decision, and a real checkpoint written to
  and read back from PostgreSQL across separate connections is asserted to
  contain no reconstructed rich object. A round-trip test caught and fixed a
  real defect: the decision's own callback action differs from the message's
  whenever an uncorrelated callback is clarified, so the two are stored
  separately.
- The local adapter requests the exact strict `json_schema` mechanism the Step 1
  capability screen validated against the real llama.cpp build, returns raw
  text so retry policy stays deterministic, and refuses non-text content rather
  than coercing it. A recording chat model verifies the request and repair-turn
  shape without an inference server. The opt-in real-endpoint test has been
  executed against a running local server and passes; it asserts schema-valid
  boundary behaviour only, against the screened Qwen3 14B profile. No routing
  accuracy gate is met by it: a gate needs the pinned dataset, and one ad-hoc
  pass over eight journeys is not a measurement.
- Two hundred eighteen workflow-contract unit tests cover the taxonomy
  authority invariants, the
  message contract, command parsing, every pre-routing precedence branch, every
  category-review callback branch, every acceptance veto, every extraction and
  category-fit cross-field invariant, the occurrence resolver including both
  daylight-saving edges, every allocation shape and refusal in both dimensions,
  and every branch of the retry boundary through a scripted port
  implementation. With the state conversions and the runtime assembly, the
  `workflows` and `runtime` components pass at 100% branch coverage. Both resolved
  dimensions are verified by building a real domain aggregate rather than
  restating its rules, and routing decisions are asserted to contain no owner
  text and only small serializable values and stable references.
- No exit criterion is met yet. Every gate in the
  [Evaluation Matrix](experiments/evaluation-matrix.md) requires the model
  half, the specialist workflows, and pinned datasets that remain open.

## Step 4: Durable Telegram MVP

### Deliverables

- Official Bot API adapter using long polling, private-owner pairing, allowed
  update filtering, secure file metadata handling, and redacted diagnostics.
- PostgreSQL inbox/outbox, persisted offset discipline, sequential worker,
  retry/backoff, dead letter, replay, and pending-action mapping.
- Italian message summaries and inline/reply interactions for text expenses,
  category review, modify, undo, help, and status.
- Separate production and test-bot configuration and real Telegram integration
  fixtures with owned-data cleanup.
- Host systemd units for Telegram gateway, workflow worker, API when present,
  and llama.cpp; boot recovery and dependency retry behavior.
- Final executable BotFather, pairing, credential-rotation, and downtime guide.

### Exit criteria

- A real private test bot completes text expense, category-review, correction,
  undo, query, and failure-recovery flows through production entry points.
- Fault injection around receive/commit/ack/process/send proves zero duplicate
  finance writes and visible dead letters.
- A real workstation/service restart recovers the eligible backlog in order.
- Unauthorized users/groups receive no finance data and cannot create work.
- The 24-hour limitation and manual-resend recovery are visible in setup and
  operations documentation.

### Milestone

This is the first Telegram-usable alpha. It does not yet include the complete
dashboard or receipt extraction.

## Step 5: Dashboard, budgets, analytics, and advice

### Deliverables

- FastAPI same-origin host with React/TypeScript/Vite, TanStack Query, React
  Router, Mantine, and Apache ECharts.
- Overview, transactions, receipt metadata, categories/proposals, monthly
  budgets, funding sources/wallets, inbox/dead letters, health, and settings
  views.
- Manual creation/correction using the same application services as workflows.
- Daily, ISO-week, monthly, annual, and custom-range analytics with purpose and
  personal-fund/benefit breakdowns.
- Read-only spending, budget, wallet, comparison, and advice tools with grounded
  Italian responses.
- Local browser protections: loopback bind, Host/Origin allowlists, closed CORS,
  CSRF control, and no secret-returning endpoint.

### Exit criteria

- Browser E2E tests cover the complete control-plane workflows against real
  application services and PostgreSQL.
- Analytics and budget numbers match deterministic fixtures and never
  double-count receipt parents/lines.
- Advice gates demonstrate 100% numeric grounding and zero mutation access.
- Accessibility checks cover keyboard navigation, form labels, contrast,
  loading/empty/error states, and chart table alternatives.
- Non-loopback or disallowed-origin requests cannot read or mutate data.

## Step 6: Evaluated receipt workflow

### Prerequisites

A private annotated receipt dataset exists, and Step 1 capability probes have
identified viable local OCR/VLM candidates.

### Deliverables

- Benchmark OCR plus text LLM, local VLM, and hybrid candidates on identical
  private/synthetic fixtures.
- Record the selected pipeline, exact artifacts, trade-offs, and gates in the
  experiment decision log; keep the extractor behind a replaceable interface.
- Secure Telegram/dashboard image ingestion, private original retention,
  derivative generation, extraction evidence, line editing, purpose allocation,
  payment allocation, discount/rounding handling, and reconciliation.
- Mandatory receipt preview/approval through Telegram and detailed dashboard.
- Media deletion, audit, backup, restore, and reprocessing behavior.

### Exit criteria

- Receipt evaluation gates pass; otherwise the feature remains explicitly
  experimental/manual and cannot be declared complete.
- Real integration/E2E tests cover clear, skewed, blurred, long, discounted,
  mixed-category, malicious-text, and mismatched receipts.
- No image auto-posts; every posted receipt has approval evidence and exact
  category/payment reconciliation.
- Images, download URLs, and embedded tokens do not appear in traces or logs.

## Step 7: Hardening and personal V1 release

### Deliverables

- End-to-end fault matrix across Docker, PostgreSQL, host services, Telegram,
  inference, media, Langfuse-off operation, backup, and restore.
- Daily Restic timer over the Step 2 backup command, observed retention
  enforcement across scheduled runs, off-device recovery-secret check, and a
  clean-machine restore drill.
- Export of authoritative finance records and documented portability contract.
- Data/checkpoint/log/trace retention and secure deletion procedures.
- Performance profiling on the target workstation and remediation of missed
  warm p95 targets or an explicit revised quality decision.
- Final threat model, upgrade/rollback runbook, operations checklist, and current
  versus planned documentation audit.

### Exit criteria

- Product V1 success criteria and every applicable evaluation gate pass.
- Restore on an empty installation reconstructs database, receipt references,
  retained media, configuration, and integrity evidence.
- Core startup and Telegram recovery succeed without Langfuse.
- No critical/high unresolved correctness, data-loss, authorization, or secret
  exposure finding remains.
- Roadmap, product docs, arc42, ADR implementation status, experiment decision
  log, and operations guides all describe the released system accurately.

## Dependency and sequencing rules

- A later step may add a test fixture or narrow interface early only when the
  active step requires it; it must not ship later-step behavior opportunistically.
- Evaluation datasets and regression cases grow continuously, but promotion
  gates are enforced only when their target capability is implemented.
- Backup begins with the first authoritative finance data in Step 2, not as a
  final afterthought.
- Telegram follows validated text workflows; receipts follow the media/domain,
  dashboard review, and extractor experiment foundations.
- A requested scope change that invalidates ordering requires roadmap and ADR
  updates before implementation.

## Definition of done for every implementation step

- Production behavior and observable error paths are implemented.
- Unit, real-service integration, and applicable end-to-end tests pass.
- Relevant evaluation suites meet gates and record a reproducible run.
- Migrations, rollback/recovery, configuration, and security consequences are
  addressed.
- arc42, ADR status, product requirements/journeys, operations, and this roadmap
  are updated in the same change.
- `README.md` remains a short entry point, and all new documentation is linked
  from an index.
- Git commits use Conventional Commits with exactly one author.
