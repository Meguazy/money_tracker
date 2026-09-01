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

## 3. Architecture and Documentation

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

## 4. Using the Project Documentation

- Treat the documentation as an implementation input, not as optional
  background reading. Before planning or changing a feature, read the following
  sources in order:
  1. `docs/product/README.md` and the linked product baseline for goals, V1
     scope, user journeys, domain language, and taxonomy.
  2. `docs/roadmap.md` to identify the active delivery step, prerequisites, and
     exit criteria.
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
- When a roadmap item is completed, update its status and evidence. Keep
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

## 5. Git Commits

- Use the Conventional Commits format:
  `<type>(<optional-scope>): <imperative summary>`.
- Use an appropriate type such as `feat`, `fix`, `docs`, `test`, `refactor`,
  `perf`, `build`, `ci`, or `chore`.
- Keep the subject concise, describe the change in the imperative mood, and add
  a body when the motivation or important consequences are not obvious.
- Mark breaking changes with `!` in the header or a `BREAKING CHANGE:` footer.
- Every commit must have exactly one author. Do not add `Co-authored-by`
  trailers or otherwise attribute a commit to additional authors.
