# Documentation Map

This directory contains the canonical product, architecture, delivery,
evaluation, development, and operations documentation for Money Tracker.

The repository currently contains an executable local-inference scaffold plus
the Step 1 PostgreSQL, migration/checkpoint, observability, development-tooling,
and evaluation foundations. Finance, Telegram, receipt, and web product
components remain accepted target state and are marked `planned` until their
roadmap step is completed.

## Required reading order

1. [Product baseline](product/README.md) for V1 goals, scope, journeys, domain
   rules, taxonomy, and shared language.
2. [Delivery roadmap](roadmap.md) for the active step, prerequisites, and exit
   criteria.
3. [arc42 architecture](arc42/README.md) for current and target components,
   interfaces, runtime, deployment, quality, and risks.
4. [Architecture decisions](arc42/decisions/README.md) for binding choices and
   their implementation status.
5. [Experiments and evaluations](experiments/README.md) for model, prompt,
   routing, tool, categorization, advice, and receipt promotion gates.
6. [Operations](operations/README.md) for Telegram, local processes, security,
   backup, restore, and recovery.
7. [Development](development.md) for commands that work in the current scaffold.

## Status vocabulary

- `implemented`: the behavior exists in repository code or deployed
  configuration and has verification evidence.
- `planned`: the behavior is approved target state but is not available yet.
- `accepted`: the decision is binding even if its implementation remains
  planned.
- `experiment-gated`: implementation or promotion depends on a recorded
  experiment meeting its acceptance gates.
- `deferred`: intentionally outside the active scope or V1.

Future agents must verify status in the repository before describing a planned
command or component as usable. Maintenance rules are defined in
[AGENTS.md](../AGENTS.md).
