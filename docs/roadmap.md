# Delivery Roadmap

- Status: accepted
- Sequence authority: this file and the step documents it indexes
- Product baseline: [docs/product](product/README.md)
- Architecture baseline: [docs/arc42](arc42/README.md)

This roadmap delivers Money Tracker through independently verifiable steps. It
does not estimate calendar time. Future agents must implement only the active
step unless the owner explicitly changes scope.

Each step owns one document under [`docs/roadmap/`](roadmap/). This page is the
index: it records which step is active and where to read about it. Intent,
prerequisites, deliverables, exit criteria, and implementation evidence live in
the step document, which is the only place any of them is written down.

## Status summary

| Step | Outcome | Status |
| --- | --- | --- |
| [0](roadmap/step-0-documentation-baseline.md) | Decision-complete documentation baseline | Complete (2026-09-01) |
| [1](roadmap/step-1-platform-and-evaluation.md) | Healthy platform and evaluation foundation | Complete (2026-09-02) |
| [2](roadmap/step-2-finance-domain-and-persistence.md) | Deterministic finance domain and persistence | Complete (2026-09-02) |
| [3](roadmap/step-3-evaluated-text-workflows.md) | Evaluated text workflows | Complete (2026-09-09) |
| [4](roadmap/step-4-durable-telegram-mvp.md) | Durable Telegram MVP | Complete (2026-09-11) |
| [5](roadmap/step-5-dashboard-and-advice.md) | Local dashboard, budgets, analytics, and advice | In progress (2026-09-15) |
| [5.5](roadmap/step-5-5-llm-traceability.md) | Full LLM call traceability in Langfuse | Planned |
| [6](roadmap/step-6-evaluated-receipt-workflow.md) | Evaluated receipt workflow | Planned |
| [7](roadmap/step-7-hardening-and-release.md) | Operational hardening and personal V1 release | Planned |

Each row mirrors the status line of the step document it links to, and the two
are updated in the same change. Status changes require evidence links in the
step document, which is where the evidence itself lives. A step is complete only
when all exit criteria pass; merged code alone is insufficient.

Step 5.5 was inserted between existing steps rather than renumbering the ones
after it, because dated evaluation records name Steps 6 and 7 and those records
are evidence rather than plans. Read the numbers as an order, not as a count.

## Rules that apply to every step

[Sequencing rules and definition of done](roadmap/sequencing-and-done.md) holds
the ordering constraints between steps and the checklist every implementation
step must satisfy before it may be called complete.
