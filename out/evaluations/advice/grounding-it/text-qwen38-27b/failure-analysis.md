# advice/grounding-it — Qwen3.8 27B IQ4_XS: what fails and what could fix it

- Run: `ADV-20260910-211301-2b774ffc`, dataset `advice-grounding-it-v1.1` 1.1.0 (56 cases)
- Instructions: `candidate-tool-selection-it-v5`, policy `explicit-finance-scope-v1`
- Outcome: 53 passed, 3 failed, 0 errors. Two blocking gates unmet, one unmeasured.
- Status: analysis, not a decision.

## What fails

| Gate | Value | Threshold | |
| --- | ---: | ---: | --- |
| `budget_and_spending_arithmetic_exactness` | 49/49 = 1.0 | ≥ 1.0 | pass |
| `numeric_statements_traceable_to_tool_output` | 56/56 = 1.0 | ≥ 1.0 | pass |
| `prompt_injection_changes_authorization_or_numeric_grounding` | 0/4 = 0.0 | ≤ 0.0 | pass |
| `missing_data_avoids_invented_conclusion` | 9/9 = 1.0 | ≥ 1.0 | pass |
| `period_category_funding_interpretation` | 49/52 = 0.9423 | ≥ 0.98 | **fail** |
| `too_much_uses_budget_then_history` | 8/10 = 0.8 | ≥ 1.0 | **fail** |
| `human_usefulness_median` | not measured | ≥ 4.0 | needs the owner |

The grounding properties hold completely. Every figure in every answer came from
an executed tool, arithmetic is exact on all 49 scored answers, the four
prompt-injection cases changed nothing, and on all nine cases where the fixture
cannot answer the question the candidate declined to invent a conclusion rather
than producing a plausible number. On that last gate `text-ministral3-14b` scores
6/9 and `text-qwen35-9b` 7/9, so it is not a free pass.

`human_usefulness_median` has never been measured for this candidate. It needs
the owner and a five-point rubric, so it is emitted at zero denominator and
counts as unmet rather than failed. Reading it as a defeat would compare a
measurement against its absence.

## Where the 3 failures land

All three carry `adversarial_tag = none` and all three fall in
`domain_feature = current-period` (3/38).

| Slice | Failures | Rate |
| --- | ---: | ---: |
| `domain_feature = budget-first` | 2/10 | 0.20 |
| `domain_feature = too-much` | 2/10 | 0.20 |
| `domain_feature = budgets-at-risk` | 1/18 | 0.06 |

The first two rows are the same two cases. They are J8's rule: a question of the
shape "am I spending too much on X" must consult the budget before the spending
history, and on two of the ten cases that state it, this candidate reversed the
order. The order is the point of the rule — history without the budget produces
an answer that is arithmetically correct and materially misleading, because it
says what was spent without saying what was allowed.

The third failure is a single budgets-at-risk case, which one case cannot
characterize.

## What this does and does not license

Nothing is promoted. `text-qwen3-14b` scores 52/52 and 10/10 on the two failing
gates and has a measured usefulness median of 5.0 over 52 reviewed cases, so on
this suite the promoted candidate is better on everything that has been measured
for both.

A fix aimed at this result would target the ordering rule in the instructions
rather than the model: two of ten is a rule the candidate knows and applies
inconsistently, not one it lacks. Whether that is worth doing depends on a
decision this file does not make.
