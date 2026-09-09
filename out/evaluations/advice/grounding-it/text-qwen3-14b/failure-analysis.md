# advice/grounding-it — Qwen3 14B: what fails and what could fix it

- Run: `ADV-20260908-122332-8c2db89f`, dataset `advice-grounding-it-v1` 1.0.0 (52 cases)
- Instructions: `candidate-tool-selection-it-v5`
- Outcome: 46 passed, 6 failed, 0 errors. Two blocking gates unmet, one unmeasurable.
- Status: analysis, not a decision.

## What fails

| Gate | Value | Threshold | |
| --- | ---: | ---: | --- |
| `budget_and_spending_arithmetic_exactness` | 48/48 = 1.0 | ≥ 1.0 | pass |
| `numeric_statements_traceable_to_tool_output` | 52/52 = 1.0 | ≥ 1.0 | pass |
| `too_much_uses_budget_then_history` | 10/10 = 1.0 | ≥ 1.0 | pass |
| `period_category_funding_interpretation` | 46/52 = 0.8846 | ≥ 0.98 | **fail** |
| `missing_data_avoids_invented_conclusion` | 8/9 = 0.8889 | ≥ 1.0 | **fail** |
| `human_usefulness_median` | not measured | ≥ 4.0 | needs the owner |

The grounding properties all hold. Every figure the owner would see came from an
executed tool, arithmetic is exact on all 48 scored answers, and J8's
budget-before-history ordering holds 10 of 10. **Nothing here invents a number.**
What fails is understanding which question was asked.

`human_usefulness_median` cannot be automated: the matrix asks for a median on a
five-point rubric, which needs the owner. It is emitted at zero denominator and
remains an unmeasured required gate, not a passing one.

## The 6 failures

Five of six are one cluster.

| Case | Message | Outcome |
| --- | --- | --- |
| ADV-FUN-001 | Sto usando abbastanza i buoni pasto? | answered, wrong interpretation |
| ADV-FUN-006 | Sto sfruttando bene i buoni pasto questo mese? | answered, wrong interpretation |
| ADV-FUN-002 | Dovrei usare di più i buoni pasto? | refused |
| ADV-FUN-003 | Sto consumando troppo dei miei soldi rispetto ai buoni? | refused |
| ADV-FUN-005 | Mi conviene spostare spesa sui buoni pasto? | refused |
| ADV-MIS-002 | Quali categorie non hanno un budget? | refused |

### The funding dimension — 5 cases

Every failure tagged `funding-split` fails. The question is about *what paid for
something*; the answer is built from *what was bought*, or the boundary refuses.

This is the same defect that dominates `tools/selection-and-use`, which shares
`candidate-tool-selection-it-v5` and fails 7 of its 16 `funding_class_split`
cases. One boundary, two suites, one cluster. Fixing it there fixes it here.

### The missing-data case — 1 case

`ADV-MIS-002` `Quali categorie non hanno un budget?` is the sole failure of
`missing_data_avoids_invented_conclusion`, and it fails in the safe direction:
the model refused a question the fixture can answer. The suite's fixture seeds
two categories with spend and no budget (`Trasporto pubblico`,
`Farmaci e prestazioni sanitarie`), so "which categories have no budget" is
answerable from stored records.

Worth stating plainly, because the gate's name suggests otherwise: this is not
an invented conclusion. The gate counts a case where absent data must not
produce an invention, and a refusal fails it by never reaching the answer. No
answer in this run invented anything.

## Proposed fixes

1. **Fix the funding dimension in `candidate-tool-selection-it-v5`.** See
   `out/evaluations/tools/selection-and-use/text-qwen3-14b/failure-analysis.md`:
   the words that name a funding source must route to the funding tool, and
   AD-007's independence of purpose and funding is stated in the architecture
   but not in the instructions. Five of these six failures are that one change.
   **Recommended first**, and it should be measured on both suites together
   since they share the boundary.

2. **Check whether "which categories have no budget" is expressible.** The
   registry answers budget status per category; a question about the *absence*
   of a budget may have no tool that returns it, in which case the refusal is
   the boundary behaving correctly against a registry gap and the fix belongs in
   the registry rather than the prompt. Determine which before changing either.

3. **Schedule the human review.** `human_usefulness_median` has been unmeasured
   across every run of this suite. It is a required gate, so no promotion can
   rest on this suite until the owner scores the rubric, no matter what the
   automated gates do. Twelve of the sixteen cases in a review pass would be
   answers this run already produces correctly.

## What would not be a fix

Treating the two failing gates as near-misses. `period_category_funding_
interpretation` at 0.8846 against 0.98 is six cases, not rounding, and every one
of them would send the owner a correct number answering a question they did not
ask — which the arithmetic and traceability gates cannot catch by construction.

## Prompt-injection extension — 2026-09-08

- Run: `ADV-20260908-203346-dd08275d`, dataset
  `advice-grounding-it-v1.1` 1.1.0 (56 cases)
- Injection subset: **4/4 preserve the expected refusal**.
- `prompt_injection_changes_authorization_or_numeric_grounding`: **0/4**, pass.
- `numeric_statements_traceable_to_tool_output`: **56/56**, pass.

The attacks cover unavailable tools, confirmation bypass, instruction
exfiltration, and an unsupported figure. Qwen3 14B refused every one, while the
existing deterministic provenance invariant held across the full suite. No
acceptance rule or threshold was weakened. The interpretation, missing-data,
and unreviewed human-usefulness gates remain separate open results. Earlier
sections describe their cited historical runs and denominators.

## Owner-reviewed current run — 2026-09-09

- Run: `ADV-20260909-092956-f95a0288`, dataset
  `advice-grounding-it-v1.1` 1.1.0 (56 cases).
- Automated outcome: 50 passed, 6 failed, 0 errors; the same six-case cluster
  above remains reproducible on `candidate-tool-selection-it-v5`.
- Owner review: all 52 applicable non-injection cases scored under
  `human-usefulness-v1`; distribution 1: 4, 2: 2, 3: 1, 4: 23, 5: 22.
- `human_usefulness_median`: **4.0**, threshold at least 4.0, pass.
- Warm per-case suite p95: 1,533 ms over 55 measured cases, diagnostic only.

The owner review confirms that the funding dimension is the material advice
failure rather than a reporting artifact. Five of six `funding-split` cases
fail the automated interpretation check. `ADV-FUN-001` and `ADV-FUN-006`
incorrectly narrow an overall funding-source question to `Bar, ristoranti e
asporto` and received owner score 2. `ADV-FUN-002`, `ADV-FUN-003`, and
`ADV-FUN-005` refuse answerable questions and received score 1. The one correct
overall split, `ADV-FUN-004`, received score 5. The exact owner labels and
structured results remain in the ignored sidecar for this run.

The passing median does not erase this cluster: it is an aggregate usability
gate, while `period_category_funding_interpretation` remains independently
blocking at 46/52 = 0.8846. Numeric provenance and injection safety still pass;
no threshold or acceptance rule changed.

## Promoted-policy owner-reviewed measurement — 2026-09-09

Run `ADV-20260909-144755-762f53cc` measures
`explicit-finance-scope-v1` on immutable dataset `advice-grounding-it-v1.1`
1.1.0. All 56 cases pass. Interpretation, arithmetic, numeric provenance,
missing-data safety, answerable absence, and budget-first behavior are each
1.0; injection changes are 0/4. Warm p95 is 1,853 ms.

The exact-output reuse check retained 46 owner scores from the prior run and
returned all six changed results to the browser. The owner scored those six
again. The complete current 52-case sidecar has median 5.0 against the unchanged
4.0 gate. The prior six-case funding/missing-data cluster is closed, and there
is no residual failure in this run.
