# Product Baseline

- Status: accepted
- Implementation: mostly planned
- Baseline date: 2026-09-01
- Product locale: Italian (`it-IT`)
- Technical documentation language: English

This directory defines what Money Tracker V1 must do and what its domain terms
mean. It is a decision baseline for implementation, not a claim that the
features already exist.

## Documents

- [Vision and Requirements](vision-and-requirements.md) defines goals, scope,
  requirements, constraints, and V1 success criteria.
- [User Journeys](user-journeys.md) defines the expected behavior of Telegram,
  receipt, review, correction, insight, and offline-recovery workflows; the
  receipt journey is deferred after V1.
- [Domain and Taxonomy](domain-and-taxonomy.md) defines finance concepts,
  invariants, the initial category hierarchy, budgets, and funding sources.
- [Glossary](glossary.md) is the canonical vocabulary for code, API contracts,
  prompts, tests, UI copy, and architecture documents.

Architecture and delivery detail live in the
[arc42 documentation](../arc42/README.md) and [roadmap](../roadmap.md).

## Product decisions at a glance

- V1 serves one owner but retains a stable profile boundary.
- It records expenses and refunds, not full personal accounting.
- A purchase has one total, category allocations describing purpose, and
  payment allocations describing funding sources.
- Meal vouchers and welfare are distinct from personal funds. Split payments
  are supported from the first usable release.
- Categories have at most two levels and never grow without owner approval.
- Clear text expenses may post automatically and always offer modify/undo.
- Receipt drafts always require preview and explicit approval. The receipt
  workflow itself is deferred after the personal V1 release (2026-09-19); V1
  keeps only the private original archive and its metadata review.
- Monthly category budgets have no rollover. They include benefit-funded
  spending while reporting personal-fund and benefit portions separately.
- Advice is read-only, on demand, and grounded in deterministic tool results.
- Telegram is the primary channel and its 24-hour backlog limit is accepted.
- The dashboard is local to the workstation.
- The personal V1 recovery gate is an isolated restore of a current encrypted
  snapshot; clean-machine recovery and whole-machine-loss protection are not
  claimed for this one-workstation release.

## Change control

A change to scope, financial semantics, taxonomy governance, confirmation
policy, privacy boundary, or success criteria must update this baseline and any
affected ADR, arc42 view, roadmap step, evaluation case, and operations guide in
the same change. Planned behavior must remain labelled as planned until verified
implementation evidence exists.
