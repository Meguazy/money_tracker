# Experiment: EXP-XXX — Short Title

- Status: proposed | running | concluded | invalidated
- Owner:
- Date opened:
- Decision target:
- Related requirements/ADR/roadmap step:

Copy this file into a dated experiment report. Fill every section before running
the final holdout; use `not applicable` with a reason instead of deleting fields.

## Hypothesis

State one falsifiable claim and why it matters to an approved product risk.

## Baseline and candidates

| Role | Identifier | Exact version/hash | Reason included |
| --- | --- | --- | --- |
| Baseline | | | |
| Candidate A | | | |
| Candidate B | | | |

## Fixed variables

List everything held constant: dataset version, prompt/schema/tool/taxonomy,
temperature/seed/context, server build/chat template, preprocessing, hardware,
concurrency, and clock/timezone fixtures.

## Independent variables

List only the intentional differences between candidates.

## Dataset and sampling

- Dataset IDs and immutable versions:
- Train/development/holdout separation:
- Case counts and required tag coverage:
- Annotation process and agreement:
- Private-data location/hash policy:
- Leakage risks and controls:

## Metrics, graders, and gates

| Metric | Grader | Blocking/diagnostic | Required threshold |
| --- | --- | --- | ---: |
| | | | |

Link the applicable evaluation-matrix section. Define normalization and semantic
matching before execution.

## Safety and privacy review

- Expected external calls:
- Secret/private data controls:
- Prompt-injection cases:
- Mutation isolation and cleanup:
- Stop conditions:

## Execution plan

1. Validate environment and capability prerequisites.
2. Run baseline and capture a complete manifest.
3. Run candidates under identical fixed variables.
4. Compute deterministic scores.
5. Perform pre-registered human/model review.
6. Inspect tagged failures and compare confidence intervals where applicable.

## Tie-break policy

Specify the ordered choice among candidates that pass every blocking gate. Never
use a tie-break to rescue a failed safety gate.

## Results

| Candidate | Blocking gates | Key metrics | Latency/resources | Artifact reference |
| --- | --- | --- | --- | --- |
| | | | | |

Summarize failure clusters without copying private examples into the report.

## Decision

- Outcome: promote | retain baseline | reject | remain experimental | rerun
- Selected pinned configuration:
- Reason:
- Effective commit/configuration:
- Rollback target:

Add the concluded decision to `decision-log.md`.

## Follow-up

- Regression cases added:
- Documentation/ADR changes:
- New risks or debt:
- Next experiment, if any:
