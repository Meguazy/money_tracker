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
| [5](roadmap/step-5-dashboard-and-advice.md) | Local dashboard, budgets, analytics, and advice | Complete (2026-09-16) |
| [5.5](roadmap/step-5-5-llm-traceability.md) | Full LLM call traceability in Langfuse | Complete (2026-09-18) |
| [7](roadmap/step-7-hardening-and-release.md) | Operational hardening and personal V1 release | In progress — release-readiness audit slice implemented (2026-09-23) |

## Future developments

Steps in this table are accepted plans outside the personal V1 release. None
of them is active, and no agent may start one until the owner schedules it and
this page moves it back into the status summary.

| Step | Outcome | Status |
| --- | --- | --- |
| [6](roadmap/step-6-evaluated-receipt-workflow.md) | Evaluated receipt workflow | Deferred after V1 (2026-09-19) |

Each row in both tables mirrors the status line of the step document it links
to, and the two are updated in the same change. Status changes require evidence
links in the step document, which is where the evidence itself lives. A step is
complete only when all exit criteria pass; merged code alone is insufficient.

Step numbers are stable identifiers, not a count, and the order of the status
summary is the delivery order. Step 5.5 was inserted between existing steps
rather than renumbering the ones after it, because dated evaluation records name
Steps 6 and 7 and those records are evidence rather than plans. For the same
reason Step 7 keeps its number although it now follows Step 5.5 directly.

On 2026-09-19 the owner deferred the receipt workflow past the personal V1
release in favour of shipping a working text-first V1 sooner. The personal V1
release is therefore Step 7 without receipt extraction; the
[product baseline](product/vision-and-requirements.md#deferred-after-the-personal-v1-release)
records the resulting scope, and the
[Step 6 document](roadmap/step-6-evaluated-receipt-workflow.md#deferral-2026-09-19)
records what already exists and what resuming it requires.

## Rules that apply to every step

[Sequencing rules and definition of done](roadmap/sequencing-and-done.md) holds
the ordering constraints between steps and the checklist every implementation
step must satisfy before it may be called complete.
