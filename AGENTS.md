# Project Instructions

These instructions apply to the entire repository.

## 1. Testing

- Organize tests into `tests/unit/`, `tests/integration/`, and `tests/e2e/`.
- Within each test category, mirror the package structure under `src/`. For
  example, tests for `src/money_tracker/config.py` belong in
  `tests/unit/money_tracker/test_config.py`.
- Test real production code and observable behavior. Do not mock the code under
  test or introduce mocks merely to make a test easier to write.
- Unit tests may replace a genuinely external boundary only when isolation is
  the purpose of the test. Prefer lightweight real implementations, in-memory
  implementations, or fakes with the same contract over interaction-heavy
  mocks.
- Integration tests must exercise real services and real protocols. Do not
  replace llama.cpp, Langfuse, PostgreSQL, ClickHouse, Redis, MinIO, or other
  integration boundaries with mocks in integration tests.
- End-to-end tests must cover complete user-visible workflows through the real
  application entry points and all services relevant to that workflow.
- Tests that create persistent service data must use isolated test identifiers
  and clean up the data they own.

## 2. Python Docstrings

- Use Google-style docstrings in Python code.
- Document modules, public classes, public functions, public methods, and
  non-obvious internal functions.
- Use the applicable Google-style sections, including `Args:`, `Returns:`,
  `Raises:`, `Yields:`, and `Attributes:`. Do not add empty sections.
- Docstrings must explain behavior, contracts, and important constraints rather
  than restating names or type annotations.

## 3. Logging

- Log through `money_tracker.structured_logging`. Get a module logger with
  `get_logger(__name__)`. Do not call `logging.getLogger`, `logging.info`, or
  `print` for diagnostics in library or adapter code; `print` remains correct
  only where a CLI's machine-readable result is its output contract.
- A record is a stable event name plus named fields, never an interpolated
  sentence. Name events `noun.verb_in_past_tense` in a dotted namespace that
  matches the component, such as `work_unit.committed` or
  `transport.request.rejected`. The name must not vary with a value.
- Pass every value as a keyword field: `_LOGGER.info("expense.replaced",
  expense_id=expense.id, version=expense.version)`. Fields are what make the
  journal queryable, and a field is added deliberately while a formatted
  message tends to absorb whatever the author had in scope.
- Use the levels as follows, and do not promote a record to get attention:
  - `DEBUG` for an internal step that matters only while diagnosing: a query
    about to run, a graph node entered, an empty poll.
  - `INFO` for a state change or a completed unit of work worth keeping: a
    movement replaced, a work unit committed, a delivery sent, a service
    started.
  - `WARNING` for a degraded or rejected outcome the system handled itself: a
    scheduled retry, an expired lease, a rejected request, a routing veto.
  - `ERROR` for a failure that needs the owner to act: a dead letter, a
    permanent transport rejection, an idempotency conflict, an unreachable
    dependency.
  - `CRITICAL` only for a condition that ends the process or puts authoritative
    data in doubt, such as a restore checksum mismatch.
- Use `_LOGGER.exception(...)` inside an `except` block whenever the cause of
  a failure is not fully described by its fields. A swallowed exception with no
  traceback anywhere is a defect.
- Never log owner content or a credential. Message text, receipt bytes, model
  completions, category and merchant names, monetary amounts, tokens, database
  URLs, and file paths under the receipt archive stay out of every field.
  Record identifiers, stable machine-readable codes, enum values, counts,
  versions, and durations instead. When a size or a shape is the diagnostic
  fact, log the count or the length rather than the value.
- Redaction in `money_tracker.logging_safety` is the last line of defence, not
  a licence: it removes token-shaped runs from messages, fields, and
  tracebacks, and it cannot recognize owner text.
- Correlate an operation with `log_context(...)` rather than threading
  identifiers through call signatures. Bind the work unit at the boundary that
  owns it, and remember that a `contextvars` binding does not cross into a
  thread the operation did not start, so each long-running loop binds its own.
- Use `_LOGGER.timed("operation")` for a bounded operation whose duration or
  outcome matters; it emits the start at `DEBUG`, the completion at `INFO` with
  `duration_ms`, and an escaping failure at `ERROR` with its traceback.
- Every entry point calls `configure_logging()` once, before any work that can
  fail. Library code never configures logging.
- When a component introduces a failure path, add the record in the same
  change. A failure the journal cannot explain is an incomplete implementation,
  and funnels such as a shared error constructor or a single problem-response
  builder are the right place to log, rather than each of their callers.

## 4. Architecture and Documentation

- Design the architecture for long-term maintainability and extensibility.
- Separate the system into cohesive components when responsibilities, lifecycle,
  data ownership, external boundaries, or reasons to change are distinct.
- Give components explicit, narrow interfaces and keep implementation details
  private. Depend on stable contracts so infrastructure, model providers,
  storage, and workflow steps can be replaced or extended independently.
- Keep domain logic separate from framework and infrastructure adapters, and
  keep dependency direction clear.
- Avoid both monolithic components and premature fragmentation: introduce a
  component only when it creates a meaningful boundary, not merely to reduce
  file size.
- Place project documentation under `docs/`. Root-level files required by
  repository tooling, such as `README.md`, `AGENTS.md`, and `LICENSE`, are
  exceptions; keep the README as a short entry point that links to `docs/`.
- Structure architecture documentation according to the arc42 template:
  introduction and goals, constraints, context and scope, solution strategy,
  building-block view, runtime view, deployment view, cross-cutting concepts,
  architecture decisions, quality requirements, risks and technical debt, and
  glossary.
- Every new architectural component must be documented in the same change that
  introduces it. Components include application services, packages with a
  distinct responsibility, LangGraph nodes or subgraphs, adapters, persistence
  components, inference or observability services, and deployment components.
- At minimum, document each component's responsibility, interfaces,
  dependencies, data ownership, runtime interactions, deployment location, and
  relevant quality or security considerations in the appropriate arc42
  sections.
- Record consequential architectural choices and their alternatives as
  architecture decisions under the arc42 architecture-decisions section.
- Update existing diagrams and runtime or deployment views whenever a component
  changes their behavior or relationships.

## 5. Using the Project Documentation

- Treat the documentation as an implementation input, not as optional
  background reading. Before planning or changing a feature, read the following
  sources in order:
  1. `docs/product/README.md` and the linked product baseline for goals, V1
     scope, user journeys, domain language, and taxonomy.
  2. `docs/roadmap.md` to identify the active delivery step, then that step's
     document under `docs/roadmap/` for its prerequisites, deliverables, exit
     criteria, and implementation evidence.
  3. `docs/arc42/README.md` for current and target architecture, component
     boundaries, runtime interactions, deployment, and cross-cutting rules.
  4. `docs/arc42/decisions/README.md` and every ADR relevant to the change.
  5. `docs/experiments/README.md` and the evaluation matrix when the work
     affects prompts, models, routing, tools, categorization, advice, or receipt
     extraction.
  6. `docs/operations/README.md` and the relevant runbook when the work affects
     Telegram, processes, credentials, storage, backup, restore, or recovery.
- Distinguish documented target state from repository truth. Components and
  commands marked `planned` do not exist yet. Inspect source, migrations,
  manifests, and tests before describing a capability as implemented.
- Accepted ADRs are binding implementation constraints. Do not contradict one
  silently. If a requested change supersedes an ADR, call out the conflict and
  update the decision record, arc42 views, product documentation, and roadmap in
  the same change.
- Work within the active roadmap step unless the user explicitly changes scope.
  Do not implement later-step infrastructure opportunistically.
- When a roadmap item is completed, update its status and evidence in that
  step's document under `docs/roadmap/`, and update the matching row of the
  status table in `docs/roadmap.md` in the same change. The table and the step
  documents must never disagree about which step is active. Keep
  `implemented`, `planned`, and experiment-gated decisions accurate across the
  roadmap, arc42, ADR index, and operations guides.
- Do not promote a model, prompt, routing threshold, tool contract, taxonomy
  policy, or receipt pipeline without satisfying the documented evaluation
  gates and recording the result in `docs/experiments/decision-log.md`.
- Add or update documentation in the same commit as code that changes behavior,
  interfaces, ownership, deployment, security, or operations. Link new
  documents from the appropriate index so future agents can discover them.
- If documents disagree, stop and resolve the drift rather than choosing the
  most convenient statement. Repository-level instructions and explicit user
  direction take precedence; record the resulting decision in the canonical
  documentation.

## 6. Git Commits

- Use the Conventional Commits format:
  `<type>(<optional-scope>): <imperative summary>`.
- Use an appropriate type such as `feat`, `fix`, `docs`, `test`, `refactor`,
  `perf`, `build`, `ci`, or `chore`.
- Keep the subject concise, describe the change in the imperative mood, and add
  a body when the motivation or important consequences are not obvious.
- Mark breaking changes with `!` in the header or a `BREAKING CHANGE:` footer.
- Every commit must have exactly one author. Do not add `Co-authored-by`
  trailers or otherwise attribute a commit to additional authors.
