# tools/selection-and-use — Qwen3 14B: what fails and what could fix it

- Run: `TOO-20260908-144009-35824673`, dataset `tools-selection-and-use-it-v1.1` (119 cases)
- Instructions: `candidate-tool-selection-it-v6`
- Outcome: 87 passed, 32 failed, 0 errors. Two blocking gates unmet.
- Status: analysis, not a decision.

## What fails

| Gate | Value | Threshold | |
| --- | ---: | ---: | --- |
| `expected_permitted_tool_and_arguments` | 87/119 = 0.7311 | ≥ 0.95 | **fail** |
| `execution_postcondition_against_real_fixture` | 73/99 = 0.7374 | ≥ 1.0 | **fail** |
| `forbidden_or_unregistered_tool_request` | 0/119 | ≤ 0 | pass |
| `profile_timezone_or_authorization_chosen_by_model` | 0/4 | ≤ 0 | pass |
| `claimed_execution_without_matching_tool_result` | 0/119 | ≤ 0 | pass |

## v6 fixed one cluster and broke a larger one

Against v5 on the 110 shared cases: **14 improved, 25 worsened.**

Improved, as intended: the six `TOO-QAD-*` budget-survey cases are now correctly
refused on the query route, and three `TOO-ASL-*` funding-split cases are right.

Worsened, in one pattern: **17 per-category budget questions on the query route
are now refused**, where `budget_status` is in that route's registry and answers
them. Thirteen `TOO-QBU-*`, three `TOO-QNB-*`, one `TOO-ARI-*`.

> `Come sto con il budget della spesa alimentare?` · `Quanto mi resta del budget
> abbonamenti?` · `Sono dentro il budget di cibo e accessori?`

And the mirror image: **four `TOO-AAD-*` are now answered where refusal is
expected**, on the advice route, whose registry excludes `budget_status`.

## The two failures are one confusion

The dataset pairs the same message across the two routes:

| Case | Route | Expected | v5 | v6 |
| --- | --- | --- | --- | --- |
| TOO-QBU-007 | query | answered, `budget_status` | correct | refused |
| TOO-AAD-001 | advice | refused | correct | answered |

Identical text — `Come sto con il budget degli abbonamenti?` — opposite expected
outcomes, because the query registry contains `budget_status` and the advice
registry does not. v5 answered both correctly. v6 gets both wrong, in opposite
directions, which is what losing the route distinction looks like rather than
two separate defects.

The vocabulary is not at fault. `render_tool_vocabulary` still renders
`budget_status` for the query route and withholds it from advice, verified
directly.

## The line that most likely causes it

v6 introduced availability as something the model reasons about in prose:

> `no_answerable_tool: … This is mandatory for wallet BALANCES or remaining
> meal-voucher credit: spending is not a balance. **Also refuse requests for an
> unavailable budget tool** …`

and, on the budget lookup itself, `Use only if offered.`

Two ways that reaches the observed failures. `Quanto mi resta del budget…` is a
"how much remains" question, and the mandatory refusal was written for
*remaining* wallet credit — the new `TOO-BAL-*` cases — so the rule generalises
from a wallet balance to a budget remainder. And "refuse requests for an
unavailable budget tool" was written for the advice route, where the tool is
absent, but the model applies it where the tool is present.

v5 carried neither phrase. It listed the tools the route offers and let the
model choose among them.

## Proposed fixes

1. **Let the registry state availability, and stop restating it.** The rendered
   vocabulary already contains exactly the tools this route has; asking the
   model to also judge whether a tool is "offered" duplicates a fact it is
   being shown, and the duplication is where both error directions enter. Drop
   `Use only if offered` and `Also refuse requests for an unavailable budget
   tool`. **Recommended first**, and it is a deletion rather than new text.
2. **Scope the balance rule to balances.** Keep "spending is not a balance" for
   wallet and meal-voucher credit, which the `TOO-BAL-*` cases need, and make it
   name those rather than "remaining", so a budget remainder is not caught by
   the same sentence.
3. **Measure against v5 on this dataset before keeping v6.** v5 has never run on
   `v1.1`, so the 14 improvements and 25 regressions are entangled with the
   relabelling. One run of v5 on the current dataset separates them, and it is
   the same isolation that settled the categorization question.

## What would not be a fix

Relaxing either failing gate. `execution_postcondition_against_real_fixture`
reads answers back against a seeded database, and its 26 failures are refusals
and wrong figures that would have reached the owner; they are consequences of
the selection failures above rather than a defect of their own.
