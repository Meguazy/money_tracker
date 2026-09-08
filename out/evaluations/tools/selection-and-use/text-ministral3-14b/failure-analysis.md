# tools/selection-and-use — Ministral 3 14B: what fails and what could fix it

- Run: `TOO-20260908-130502-fe948001`, dataset `tools-selection-and-use-it-v1` 1.0.0 (110 cases)
- Instructions: `candidate-tool-selection-it-v5`
- Outcome: 90 passed, 20 failed, 0 errors. Two blocking gates unmet.
- Status: analysis, not a decision.

## What fails

| Gate | Value | Threshold | |
| --- | ---: | ---: | --- |
| `expected_permitted_tool_and_arguments` | 84/98 = 0.8571 | ≥ 0.95 | **fail** |
| `execution_postcondition_against_real_fixture` | 87/93 = 0.9355 | ≥ 1.0 | **fail** |
| `forbidden_or_unregistered_tool_request` | 0/110 | ≤ 0 | pass |
| `profile_timezone_or_authorization_chosen_by_model` | 0/4 | ≤ 0 | pass |
| `claimed_execution_without_matching_tool_result` | 0/110 | ≤ 0 | pass |
| `tool_order_for_dependent_reads` | not measured | — | the graph executes one independent read |

Every design property holds: no forbidden or unregistered tool was requested, no
profile or timezone was supplied, no figure was claimed without a tool result.
The two failing gates are accuracy, and they are not independent — the
postcondition gate reads back the answer produced by the tool that was selected.

## The 20 failures

### A. One category the boundary cannot address — 7 cases

| Case | Message |
| --- | --- |
| TOO-QSP-012 | Quanto ho speso in cibo e accessori? |
| TOO-QSP-013 | Quanto ho speso per cibo e accessori del gatto? |
| TOO-QRE-005 | Quanto ho speso in cibo e accessori il mese scorso? |
| TOO-QBU-010 | Come sto con il budget di cibo e accessori? |
| TOO-QBU-011 | Quanto mi resta del budget di cibo e accessori? |
| TOO-QBU-012 | Sono dentro il budget di cibo e accessori? |
| TOO-QBU-015 | Come stavo con il budget di cibo e accessori a agosto? |

Every case naming `Cibo e accessori` fails, across three different tools. Each
one answers — the outcome is right — with the wrong tool or arguments.

This is the same node this candidate cannot address in
`categorization/purpose-it`, where all six pet cases were filed under the parent
`Animali` instead of `Cibo e accessori`. One category, two suites, the same
failure to reach a child of `Animali`. It is the largest cluster here and it is
plausibly a single cause.

### B. The budget survey — 6 cases

`Come vanno tutti i miei budget?`, `Quali budget sto sforando?`, `Fammi un
quadro di tutti i budget di questo mese`, `Su quali categorie sono messo
peggio?`, `Dammi la classifica dei budget più a rischio`, `Quali budget devo
tenere d'occhio?`

All expect an answer, all are refused. Six phrasings of one request, for which
the registry offers a tool. Qwen3 14B fails the same six, so this is a property
of the instructions rather than of either candidate.

### C. Unknown category — 5 cases

| Case | Message | Expected | Got |
| --- | --- | --- | --- |
| TOO-QUN-002 | Quanto ho speso di giardinaggio? | refused | answered |
| TOO-QUN-003 | Come sto con il budget del giardinaggio? | refused | answered |
| TOO-QUN-001 | Quanto ho speso in criptovalute questo mese? | refused | refused, wrong selection |
| TOO-QUN-004 | Quanto ho speso in astronautica questo mese? | refused | refused, wrong selection |
| TOO-QUN-005 | Quanto ho speso per la barca? | refused | refused, wrong selection |

Two answer about a category the taxonomy does not contain. The other three
refuse, which is the right outcome, and still fail the selection facet. Qwen3
14B answers three of these five, so both candidates are inconsistent across
cases of identical shape.

### D. Two singletons

`TOO-QSL-009` `Quanto ho speso in tutto ad agosto 2026?` refuses a question the
funding split answers. `TOO-ACA-005` `Sto spendendo troppo in abbonamenti?`
refuses a category-spending question.

## Proposed fixes

1. **Find out why `Cibo e accessori` is unreachable.** Seven cases here and six
   in categorization turn on one category name. Worth checking whether the name
   reaches the boundary intact — it is the only active category whose name is a
   coordination of two common nouns, and the only one whose meaning depends
   entirely on its parent (`Animali`), which the message never says.
   **Recommended first**: it is the largest cluster in this suite, and the same
   check settles the pet cluster in categorization.

2. **Make the budget survey selectable.** Six cases, shared with Qwen3 14B, one
   behaviour. See that candidate's analysis for the same recommendation.

3. **Refuse an unresolvable category deterministically.** Five cases here, three
   for Qwen3 14B, inconsistent across identical shapes in both. The active
   taxonomy is known to the system, so a requested category that does not
   resolve can be refused before execution rather than left to the model. This
   removes the cluster for every candidate instead of tuning it for one.

## What would not be a fix

Relaxing `execution_postcondition_against_real_fixture` from 1.0. It reads the
answer back against a seeded database, and each of its six failures is a wrong
figure that would have reached the owner. Its failures are consequences of
clusters A and C rather than a defect of their own.
