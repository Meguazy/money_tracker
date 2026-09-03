# Step 1: Platform health and evaluation foundation

Part of the [delivery roadmap](../roadmap.md).

Status: **Complete (2026-09-02).** Platform, persistence, tooling, and
evaluation foundations pass their exit criteria. Product-role model promotion
remains gated by later domain evaluations.

## Prerequisites

Step 0 complete. Existing volumes and sensitive configuration identified through
read-only checks before any migration or repair.

## Deliverables

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

## Exit criteria

- Core PostgreSQL starts independently; Langfuse starts only when selected.
- Langfuse services become healthy with preserved data or a documented,
  explicitly approved recovery path.
- Forward migration, rollback policy, and fresh bootstrap are tested against a
  real application PostgreSQL server.
- Capability results include exact model/GGUF/build/chat-template metadata.
- Unit tests and static checks run without optional services; integration checks
  fail or skip transparently when their real service is absent.

## Explicit exclusions

No financial table or product workflow beyond the minimum migration/checkpoint
foundation. No model is promoted merely because the capability probe responds.

## Implementation evidence

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
  repeatable commands in [Development](../development.md).
- The executable `evals/` layout contains public synthetic cases, a capability
  suite, ignored private/generated paths, deterministic local reports, exact
  model/build metadata collection, and optional aggregate Langfuse publication.
- A capability run with no inference server fails explicitly and still writes a
  machine-readable local result. Five pinned local candidates were screened on
  the real CUDA llama.cpp runtime; three passed every applicable technical gate
  with exact metadata. See the
  [capability results](../experiments/local-model-capability-results-2026-09-02.md).
- The screen was extended on 2026-09-03 with two probes, after Step 3 measured
  the same optional-field failure at three independent boundaries and named the
  cause as a capability rather than a prompt. Suite 1.2.0 screens leaving an
  optional field alone and answering with a discriminated union, each under two
  boundary shapes. Against the text candidate omission fails in both shapes and
  union output passes in both; see the
  [2026-09-03 results](../experiments/local-model-capability-results-2026-09-03.md).
  This step's status is unchanged. Its deliverable was the probes for strict
  output, tool calling, sequencing, and image input, and its exit criteria
  concern the platform and the metadata a run records; both still hold. A later
  step measuring a need for one more probe extends the screen this step built
  rather than reopening it.
- Adding those probes made `ProbeStatus.FAILED` reachable for the first time. A
  probe that cannot run reports `error` and one that ran and missed its
  pre-registered threshold reports `failed`, because a screen whose report
  cannot separate an unreachable server from a measured refusal answers the
  wrong question.
