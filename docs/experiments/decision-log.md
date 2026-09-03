# Experiment Decision Log

This file records promotion decisions, not every exploratory run. Append entries;
do not rewrite historical outcomes when a later experiment supersedes them.

## Current production baseline

- Text model: no evaluated production model selected.
- Structured-output method: no capability-gated method selected.
- Routing/category/tool prompts: no V1 production prompt. Unpromoted candidate
  router, extraction, and category-fit instructions exist in code under
  `CANDIDATE_` names and `candidate-` version identifiers so a runtime cannot
  mistake them for a promoted configuration. Two candidate-quality gaps are
  known against the screened Qwen3 14B profile and remain open for the pinned
  suites to measure rather than to tune by hand:
  - `candidate-extraction-it-v2` fabricates a payment method when the message
    names none, including the literal placeholder the text forbids. It fails
    safe, because deterministic resolution refuses words matching no configured
    source, but it costs a question the documented default should have
    answered. Gap for `ingestion/expense-it`.
  - `candidate-category-fit-it-v1` is now measured by
    `categorization-purpose-it-v1` (153 cases) and fails four blocking gates.
    It proposes a new category on 131 of 141 cases where an adequate node
    exists, duplicates an existing name on 10% of proposals, and twice proposes
    a category named for a payment method against a zero-tolerance gate. A
    diagnostic shows that in half of all proposals the model also named the
    correct existing category, so the dominant failure is discriminator
    selection on the flat schema rather than missing knowledge. Union output is
    consequently a capability worth screening, not only a prompt worth
    revising.
  The earlier over-declaration gap in `candidate-extraction-it-v1` is closed.
  It was a boundary-design defect rather than a prompt-tuning problem: the
  schema let the model judge its own uncertainty, which the deterministic side
  was better placed to decide. Removing that channel resolved it. No row is
  added below, because no experiment concluded and nothing was promoted; the
  change is recorded in the evidence of
  [Step 3](../roadmap/step-3-evaluated-text-workflows.md).
- Auto-post threshold: disabled until categorization precision gates pass.
- Routing confidence bar: none promoted. The implemented router requires the
  value as an explicit argument and has no default, so no runtime can adopt
  one before calibration on the pinned `routing/intent-it` dataset is
  recorded here. `routing-intent-it-v1.1` (201 cases) reaches the minimum and
  has been measured at a 0.5 bar, but fails three blocking gates, so it
  supports no promotion and no calibration conclusion. Its decisive failure is
  a safety one: 10 of 70 read-only inputs reached a financial write, all of
  them incoming money the router has no concept of. No question, advice
  request, or greeting was ever escalated to a write.
- Receipt pipeline: no pipeline selected; receipt extraction remains planned.
- Langfuse: optional local integration; the Step 1 capability runner emits
  deterministic local reports and can publish only redacted aggregate scores.
- Capability baseline: Qwen3 14B, Qwen3.5 9B, and Gemma 4 12B pass the Step 1
  technical screen. They remain experimental candidates; no model has been
  promoted to a product role.

## Decisions

| Decision ID | Date | Experiment | Outcome | Selected configuration | Supersedes | Evidence |
| --- | --- | --- | --- | --- | --- | --- |
| EVD-000 | 2026-09-01 | Step 0 documentation baseline | Establish gates before model/pipeline selection | Evaluation process and matrix in this directory | None | Product baseline and AD-012 |
| EVD-001 | 2026-09-02 | Local capability shortlist 1.1.0 | Retain three downstream candidates; reject the current Ministral and GPT-OSS profiles; promote none | Qwen3 14B text; Qwen3.5 9B and Gemma 4 12B vision | None | [Capability results](local-model-capability-results-2026-09-02.md) |

## Entry requirements

Every future row must identify:

- the concluded experiment report and complete run manifest;
- pass/fail for every blocking gate;
- exact pinned model/prompt/schema/tool/taxonomy/pipeline versions;
- reason for promotion, rejection, rollback, or experimental status;
- effective application commit and prior rollback target;
- private artifact references by safe local hash/path only.
