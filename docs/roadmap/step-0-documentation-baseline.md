# Step 0: Documentation baseline

Part of the [delivery roadmap](../roadmap.md).

Status: **Complete (2026-09-01).**

## Intent

Give future sessions a stable product vocabulary, accepted architecture,
delivery sequence, evaluation policy, and operational boundary before building
new components.

## Deliverables

- Product vision, requirements, journeys, domain/taxonomy, and glossary.
- Updated arc42 current/target views and accepted ADRs.
- This staged roadmap with exit criteria and dependencies.
- Experiment process, evaluation matrix, templates, and decision log.
- Preliminary Telegram, runtime, backup/restore, and security runbooks that
  clearly distinguish current from planned behavior.
- Documentation index and `AGENTS.md` reading/change protocol.

## Exit criteria

- Every agreed product and architecture decision is represented once in a
  canonical source and cross-linked from indexes.
- Planned components and commands are never presented as implemented.
- arc42 covers responsibility, interfaces, dependencies, ownership, runtime,
  deployment, quality, security, and risk for every planned component.
- ADR index distinguishes implemented and planned accepted decisions.
- Requirements, roadmap gates, tests/evaluations, and operations are traceable.
- Markdown links, formatting, duplicate requirement IDs, and Git whitespace
  checks pass.

## Non-goals

No application code, dependency, Compose, systemd, migration, bot, model,
runtime configuration, or test behavior changes in Step 0.

## Completion evidence

- The product baseline, journeys, domain model, taxonomy, glossary, arc42 views,
  ADRs, roadmap, experiment framework, and operational runbooks are documented.
- All relative Markdown links resolve to repository files.
- Requirement identifiers are unique, Markdown code fences are balanced, and the
  experiment run-manifest template parses as YAML.
- `git diff --check` reports no whitespace errors.
- The existing unit suite passes: 3 tests.
