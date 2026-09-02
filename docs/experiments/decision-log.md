# Experiment Decision Log

This file records promotion decisions, not every exploratory run. Append entries;
do not rewrite historical outcomes when a later experiment supersedes them.

## Current production baseline

- Text model: no evaluated production model selected.
- Structured-output method: no capability-gated method selected.
- Routing/category/tool prompts: no V1 production prompt. Unpromoted candidate
  router, extraction, and category-fit instructions exist in code under
  `CANDIDATE_` names and `candidate-` version identifiers so a runtime cannot
  mistake them for a promoted configuration. The candidate extraction text is
  known to over-declare unknowns against the screened Qwen3 14B profile, so
  clear messages reach a clarification instead of a draft; that is a gap for
  the `ingestion/expense-it` suite to measure rather than to tune by hand.
- Auto-post threshold: disabled until categorization precision gates pass.
- Routing confidence bar: none promoted. The implemented router requires the
  value as an explicit argument and has no default, so no runtime can adopt
  one before calibration on the pinned `routing/intent-it` dataset is
  recorded here.
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
