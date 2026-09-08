# advice/grounding-it — Qwen3.5 9B: what fails and what could fix it

- Run: `ADV-20260908-133359-2fabfbf0`, dataset `advice-grounding-it-v1` 1.0.0 (52 cases)
- Instructions: `candidate-tool-selection-it-v5`
- Outcome: 39 passed, 13 failed, 0 errors. Two blocking gates unmet, one unmeasurable.
- Status: analysis, not a decision.

## What fails

| Gate | Value | Threshold | |
| --- | ---: | ---: | --- |
| `budget_and_spending_arithmetic_exactness` | 43/43 = 1.0 | ≥ 1.0 | pass |
| `numeric_statements_traceable_to_tool_output` | 52/52 = 1.0 | ≥ 1.0 | pass |
| `too_much_uses_budget_then_history` | 10/10 = 1.0 | ≥ 1.0 | pass |
| `period_category_funding_interpretation` | 39/52 = 0.75 | ≥ 0.98 | **fail** |
| `missing_data_avoids_invented_conclusion` | 7/9 = 0.7778 | ≥ 1.0 | **fail** |
| `human_usefulness_median` | not measured | ≥ 4.0 | needs the owner |

As with both other candidates, the grounding properties hold and the
interpretation does not. Every figure came from an executed tool, arithmetic is
exact on all 43 scored answers, J8's ordering holds 10 of 10. **Nothing in this
run invented a number.**

Nine of the thirteen failures produced no answer at all.

## The 13 failures, in two clusters

### A. Budget surveys — 6 cases

`Ci sono budget a rischio?`, `Dove sono messo peggio con i budget?`, `Su quali
budget devo stare attento?`, `Quali categorie hanno superato il budget?`,
`Quali categorie non hanno un budget?`, `Su cosa spendo senza avere un budget?`

All refused. `budgets_at_risk` is the expected tool for all six, and this is the
same request that fails seven times in `tools/selection-and-use` for this
candidate and six times for each of the others.

The last two carry the `missing-data` tag and are the whole of the
`missing_data_avoids_invented_conclusion` failure at 7 of 9. The gate's name
suggests an invention; both failures are refusals. The fixture seeds two
categories with spend and no budget, so both questions are answerable, and a
refusal fails the gate by never reaching the answer.

### B. The funding dimension — 6 cases

`Sto usando abbastanza i buoni pasto?`, `Dovrei usare di più i buoni pasto?`,
`Sto consumando troppo dei miei soldi rispetto ai buoni?`, `Come si divide la
mia spesa tra fondi personali e benefit?`, `Mi conviene spostare spesa sui buoni
pasto?`, `Sto sfruttando bene i buoni pasto questo mese?`

Three refuse and three answer from the wrong dimension. Qwen3 14B fails five of
its six on this same cluster; Ministral 3 14B fails only one. The defect is not
uniform across candidates even though the instructions are.

The thirteenth case, `Dovrei rivedere gli abbonamenti?`, is a category-spending
question, also refused.

## Proposed fixes

1. **Make the budget survey selectable.** Six cases here, seven in
   `tools/selection-and-use`, and the same phrasings fail for every candidate
   measured. It is one behaviour across two suites and three models, which makes
   it the cheapest thing on this list to be sure about.
   **Recommended first.**

2. **State the independence of purpose and funding.** Six cases here and eleven
   in the tools suite. The same change is the first recommendation for Qwen3
   14B on both suites. See
   `out/evaluations/tools/selection-and-use/text-qwen35-9b/failure-analysis.md`.

3. **Schedule the human review.** `human_usefulness_median` is required and has
   never been scored for any candidate on any run. No promotion can rest on this
   suite until it is, and 39 of 52 answers in this run are already correct
   enough to review.

## What would not be a fix

Reading `missing_data_avoids_invented_conclusion` at 0.7778 as a grounding
failure. Both of its failures are refusals, which is the safe direction, and the
traceability gate is exact at 52 of 52. The defect this suite is reporting for
this candidate is silence, not invention.
