# tools/selection-and-use — Qwen3.5 9B: what fails and what could fix it

- Run: `TOO-20260908-133253-56fc6298`, dataset `tools-selection-and-use-it-v1` 1.0.0 (110 cases)
- Instructions: `candidate-tool-selection-it-v5`
- Outcome: 80 passed, 30 failed, 0 errors. Two blocking gates unmet.
- Status: analysis, not a decision.

## What fails

| Gate | Value | Threshold | |
| --- | ---: | ---: | --- |
| `expected_permitted_tool_and_arguments` | 76/98 = 0.7755 | ≥ 0.95 | **fail** |
| `execution_postcondition_against_real_fixture` | 77/93 = 0.828 | ≥ 1.0 | **fail** |
| `forbidden_or_unregistered_tool_request` | 0/110 | ≤ 0 | pass |
| `profile_timezone_or_authorization_chosen_by_model` | 0/4 | ≤ 0 | pass |
| `claimed_execution_without_matching_tool_result` | 0/110 | ≤ 0 | pass |
| `tool_order_for_dependent_reads` | not measured | — | the graph executes one independent read |

This is the weakest of the three candidates on both failing gates: Qwen3 14B and
Ministral 3 14B both score 0.8571 on selection, against 0.7755 here.

Every design property holds. No forbidden or unregistered tool was requested, no
profile or timezone supplied, no figure claimed without a tool result.

## The 30 failures

Fourteen answer where a refusal was expected or refuse where an answer was; the
rest answer with the wrong tool or arguments.

### A. The funding dimension — 11 cases, the largest cluster

`funding_class_split` is the expected tool in eleven of the thirty failures,
more than any other. `Quanto ho speso con i buoni pasto questo mese?`,
`Quanto ho usato di buoni pasto?`, `Quanto ho speso questo mese tra fondi
personali e benefit?`, `Come si divideva la spesa nel mese 2026-08?`,
`Qual è il totale speso questo mese?`

Some answer from the purpose dimension, some refuse outright. This is the same
cluster that dominates Qwen3 14B's failures here (7 cases) and its advice suite,
and AD-007 keeps purpose and funding independent while the instructions do not
say so.

### B. The budget survey — 7 cases

`Come vanno tutti i miei budget?`, `Quali budget sto sforando?`, `Fammi un
quadro di tutti i budget di questo mese`, `Su quali categorie sono messo
peggio?`, `Dammi la classifica dei budget più a rischio`, `Quali budget devo
tenere d'occhio?`, `Ci sono budget a rischio?`

All expect an answer, all are refused. **All three candidates fail the same six
core cases**, which makes this a property of the instructions rather than of any
model.

### C. Unknown category — 5 cases

`giardinaggio` and `astronautica` are answered where a refusal is required;
`criptovalute`, `giardinaggio`'s budget question, and `barca` refuse but fail the
selection facet. Every candidate measured is inconsistent across cases of
identical shape: Qwen3 14B answers three, Ministral two, this one two.

### D. Category spending — 8 cases

`Quanto mi costano gli abbonamenti questo mese?`, `Quanto ho speso di farmaci e
prestazioni sanitarie?`, `Quanto ho speso di abitazione e condominio?`,
`Quanto ho speso in bollette e comunicazioni?` and four more answer with the
wrong tool or arguments.

Three of the four named above are **child categories whose parent this candidate
answers instead** in `categorization/purpose-it`, where 29 of 50 failures are
exactly that. The same inability to address a child node plausibly explains part
of this cluster.

## Proposed fixes

1. **State the independence of purpose and funding.** Eleven cases here, and the
   same change is the first recommendation for Qwen3 14B in this suite and in
   advice. One instruction change across three candidates and two suites.
   **Recommended first.**

2. **Make the budget survey selectable.** Seven cases here, six for each of the
   other two candidates, the same phrasings every time. Nothing about this
   cluster varies by model.

3. **Refuse an unresolvable category deterministically.** Five cases, and the
   same recommendation as for the other two candidates: the active taxonomy is
   known to the system, so the refusal can be a resolver rule instead of a
   model judgment.

4. **Re-measure cluster D after the categorization specificity fix.** If the
   child-node failure is one cause, part of this cluster moves without any
   change to this suite's instructions. Worth measuring rather than assuming.

## What would not be a fix

Relaxing `execution_postcondition_against_real_fixture` from 1.0. Sixteen of its
ninety-three cases produced a figure that did not match the seeded database,
which is sixteen wrong numbers that would have reached the owner.
