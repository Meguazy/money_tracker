# Experiment Decision Log

This file records promotion decisions, not every exploratory run. Append entries;
do not rewrite historical outcomes when a later experiment supersedes them.

## Current production baseline

- Text model: no evaluated production model selected.
- Structured-output method: no capability-gated method selected.
- Routing/category/tool prompts: scaffold prompt only; no V1 production prompts.
- Auto-post threshold: disabled until categorization precision gates pass.
- Receipt pipeline: no pipeline selected; receipt extraction remains planned.
- Langfuse: optional scaffold integration; evaluation runner not implemented.

## Decisions

| Decision ID | Date | Experiment | Outcome | Selected configuration | Supersedes | Evidence |
| --- | --- | --- | --- | --- | --- | --- |
| EVD-000 | 2026-09-01 | Step 0 documentation baseline | Establish gates before model/pipeline selection | Evaluation process and matrix in this directory | None | Product baseline and AD-012 |

## Entry requirements

Every future row must identify:

- the concluded experiment report and complete run manifest;
- pass/fail for every blocking gate;
- exact pinned model/prompt/schema/tool/taxonomy/pipeline versions;
- reason for promotion, rejection, rollback, or experimental status;
- effective application commit and prior rollback target;
- private artifact references by safe local hash/path only.
