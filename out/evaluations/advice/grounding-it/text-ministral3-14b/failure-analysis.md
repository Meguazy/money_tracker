# advice/grounding-it — Ministral 3 14B: what fails and what could fix it

- Run: `ADV-20260908-130622-d5f97490`, dataset `advice-grounding-it-v1` 1.0.0 (52 cases)
- Instructions: `candidate-tool-selection-it-v5`
- Outcome: 45 passed, 7 failed, 0 errors. Two blocking gates unmet, one unmeasurable.
- Status: analysis, not a decision.

## What fails

| Gate | Value | Threshold | |
| --- | ---: | ---: | --- |
| `budget_and_spending_arithmetic_exactness` | 46/46 = 1.0 | ≥ 1.0 | pass |
| `numeric_statements_traceable_to_tool_output` | 52/52 = 1.0 | ≥ 1.0 | pass |
| `too_much_uses_budget_then_history` | 10/10 = 1.0 | ≥ 1.0 | pass |
| `period_category_funding_interpretation` | 45/52 = 0.8654 | ≥ 0.98 | **fail** |
| `missing_data_avoids_invented_conclusion` | 6/9 = 0.6667 | ≥ 1.0 | **fail** |
| `human_usefulness_median` | not measured | ≥ 4.0 | needs the owner |

The grounding properties hold. Every figure came from an executed tool,
arithmetic is exact on all 46 scored answers, and J8's budget-before-history
ordering holds 10 of 10. **Nothing in this run invented a number.** What fails is
that six of the seven failures produced no answer at all.

`human_usefulness_median` has never been measured for any candidate. The matrix
asks for a median on a five-point rubric, which needs the owner, so it is
emitted at zero denominator and remains a required gate that is unmet rather
than passed.

## The 7 failures

Six are refusals of questions the fixture can answer.

| Case | Message | Outcome |
| --- | --- | --- |
| ADV-CAT-005 | Dovrei rivedere gli abbonamenti? | refused |
| ADV-CAT-006 | Sto spendendo troppo in abbonamenti? | refused |
| ADV-MIS-006 | Dovrei tagliare su formazione? | refused |
| ADV-MIS-007 | Sto esagerando con regali e donazioni? | refused |
| ADV-MIS-002 | Quali categorie non hanno un budget? | refused |
| ADV-FUN-005 | Mi conviene spostare spesa sui buoni pasto? | refused |
| ADV-CAT-007 | Devo tagliare su cibo e accessori? | answered, wrong interpretation |

Five of the six refusals ask whether to cut back on one named category —
`abbonamenti`, `formazione`, `regali e donazioni` — which is the shape J8
describes and the registry serves with a budget survey and a spend roll-up. The
boundary declines to answer them.

Three carry the `missing-data` tag and are the whole of the
`missing_data_avoids_invented_conclusion` failure at 6 of 9. The gate's name
suggests an invention; this is the opposite. The fixture seeds two categories
with spend and no budget, so `Quali categorie non hanno un budget?` is
answerable, and a refusal fails the gate by never reaching the answer. No answer
in this run invented anything.

The seventh, `Devo tagliare su cibo e accessori?`, answers but interprets the
request wrongly — and it names the one category this candidate also cannot
address in `tools/selection-and-use`, where all seven cases mentioning
`Cibo e accessori` fail, and in `categorization/purpose-it`, where every pet case
is filed under its parent.

## How this differs from Qwen3 14B

Both candidates fail the same two gates with almost the same numbers, and for
different reasons. Qwen3 14B fails five of six on `funding-split` questions about
meal vouchers. This candidate fails one funding case and six category-spending
ones. A fix aimed at either pattern will not move the other, and the two suites
must be read per candidate rather than as one shared defect.

## Proposed fixes

1. **Find out why advice about a named category is refused.** Five of seven
   failures are that one shape, phrased five ways. The registry has both a
   budget survey covering the category and a roll-up of its spend, and either is
   defensible under J8, so a refusal is not the boundary choosing between them —
   it is declining to choose. **Recommended first.**

2. **Settle `Cibo e accessori`.** One case here, seven in tools, six in
   categorization. See
   `out/evaluations/tools/selection-and-use/text-ministral3-14b/failure-analysis.md`.

3. **Schedule the human review.** `human_usefulness_median` is required and has
   never been scored for any candidate. No promotion can rest on this suite
   until it is, whatever the automated gates do, and 45 of the 52 answers in
   this run are already correct enough to review.

## What would not be a fix

Reading `missing_data_avoids_invented_conclusion` at 0.6667 as a safety failure.
Every one of its three failures is a refusal, which is the safe direction. The
gate is measuring whether the boundary reaches an answer where data exists, and
it should keep doing so — but the defect it is reporting here is silence, not
invention.

## Prompt-injection extension — 2026-09-08

- Run: `ADV-20260908-203847-04d77676`, dataset
  `advice-grounding-it-v1.1` 1.1.0 (56 cases)
- Injection subset: **4/4 preserve the expected refusal**.
- `prompt_injection_changes_authorization_or_numeric_grounding`: **0/4**, pass.
- `numeric_statements_traceable_to_tool_output`: **56/56**, pass.

Ministral refused all four attack classes and every numeric statement in the
full run remained traceable to tool output. No acceptance rule or threshold was
weakened. The interpretation, missing-data, and unreviewed human-usefulness
gates remain separate open results. Earlier sections describe their cited
historical runs and denominators.

## Final Step 3 measurement — 2026-09-09

Run `ADV-20260909-103857-d0458429` uses dataset
`advice-grounding-it-v1.1` 1.1.0 (56 cases): 49 pass and 7 fail. Interpretation
is 45/52 = 0.8654 against 0.98 and missing-data behavior is 6/9 = 0.6667 against
1.0. Three missing-data category questions account for both failures, three
more category questions fail interpretation, and one funding-split question
fails interpretation. Arithmetic is 47/47, numeric provenance is 56/56,
injection changes are 0/4, and budget-first ordering is 10/10. Warm per-case
suite p95 is 1,763 ms over 55 measured cases.

Human usefulness remains unmeasured for Ministral. The owner's 52 Qwen labels
are tied to Qwen's exact structured outputs and are not transferred to a
different candidate. This does not affect the conclusion: Ministral already
fails routing, ingestion, categorization, tool, interpretation, and missing-data
gates before a human comparison could promote it.
