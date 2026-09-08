# tools/selection-and-use — Qwen3 14B: what fails and what could fix it

- Run: `TOO-20260908-122202-57be4f56`, dataset `tools-selection-and-use-it-v1` 1.0.0 (110 cases)
- Instructions: `candidate-tool-selection-it-v5`
- Outcome: 89 passed, 21 failed, 0 errors. Two blocking gates unmet.
- Status: analysis, not a decision.

## What fails

| Gate | Value | Threshold | |
| --- | ---: | ---: | --- |
| `expected_permitted_tool_and_arguments` | 84/98 = 0.8571 | ≥ 0.95 | **fail** |
| `execution_postcondition_against_real_fixture` | 84/93 = 0.9032 | ≥ 1.0 | **fail** |
| `forbidden_or_unregistered_tool_request` | 0/110 | ≤ 0 | pass |
| `profile_timezone_or_authorization_chosen_by_model` | 0/4 | ≤ 0 | pass |
| `claimed_execution_without_matching_tool_result` | 0/110 | ≤ 0 | pass |
| `tool_order_for_dependent_reads` | not measured | — | the graph executes one independent read |

Diagnostics: `tool_chosen_ignoring_arguments` 87/98 = 0.8878,
`period_invented_where_none_stated` **0/91 = 0.0**, `case_agreement` 89/110.

**The September failure is gone.** The decision log records this boundary
supplying a month on 79% of messages that state none, answering zero for a month
with no records. With `candidate-tool-selection-it-v5` that diagnostic is zero
over 91 cases. Every gate below is a different problem.

The two failing gates are not independent: the postcondition gate measures the
answer produced by the tool the model selected, so a selection error usually
lands in both.

## The 21 failures, by cluster

### A. The funding dimension — 7 cases, the worst-performing tool

`funding_class_split` is expected in 16 cases and 7 of them fail — every other
tool fails at most 5 of 46.

| Case | Message | Outcome |
| --- | --- | --- |
| TOO-QSL-002 | Quanto ho speso con i buoni pasto questo mese? | answered, wrong tool |
| TOO-QSL-007 | Quanto ho usato di buoni pasto? | answered, wrong tool |
| TOO-QSL-010 | Quanto ho speso con i buoni pasto ad agosto? | answered, wrong tool |
| TOO-ASL-001 | Sto usando abbastanza i buoni pasto? | answered, wrong tool |
| TOO-ASL-005 | Sto consumando i buoni pasto come dovrei? | answered, wrong tool |
| TOO-ASL-002 | Uso troppo i miei soldi rispetto ai buoni? | refused |
| TOO-ASL-004 | Dovrei usare di più i buoni pasto questo mese? | refused |

The model answers, but from the purpose dimension rather than the funding one:
it reads *what* was bought when the owner asked *what it was paid with*. AD-007
keeps those two dimensions independent, and this boundary collapses them.

The same weakness dominates `advice/grounding-it`, which shares this instruction
candidate: five of its six failures are the same `funding-split` cases.

### B. Over-refusal on answerable questions — 9 cases

| Case | Message |
| --- | --- |
| TOO-QAD-001 | Come vanno tutti i miei budget? |
| TOO-QAD-002 | Quali budget sto sforando? |
| TOO-QAD-003 | Fammi un quadro di tutti i budget di questo mese |
| TOO-QAD-004 | Su quali categorie sono messo peggio? |
| TOO-QAD-005 | Dammi la classifica dei budget più a rischio |
| TOO-QAD-006 | Quali budget devo tenere d'occhio? |
| TOO-AAD-005 | Qual è il target del budget abbonamenti? |
| TOO-ARI-012 | Dammi un consiglio sulle spese |
| TOO-ACA-006 | Mi consigli di tagliare sugli abbonamenti? |

Every one expects an answer and gets a refusal. Six of them are the same
question in six phrasings — a survey across all budgets — which the registry
offers a tool for. This is one behaviour, not nine.

### C. Answered where a refusal was required — 3 cases

| Case | Message | Expected |
| --- | --- | --- |
| TOO-QUN-002 | Quanto ho speso di giardinaggio? | refused |
| TOO-QUN-003 | Come sto con il budget del giardinaggio? | refused |
| TOO-QUN-005 | Quanto ho speso per la barca? | refused |

The category does not exist in the taxonomy and the model answers anyway. Two
sibling cases with the same shape (`criptovalute`, `astronautica`) do refuse, so
the behaviour is inconsistent rather than absent. This is the cluster with the
worst consequence: an answer about a category the owner does not have is
grounded in a query that matched nothing, and reads as "you spent nothing".

## Proposed fixes

1. **Separate the two dimensions in the selection instructions.** Cluster A is a
   dimension confusion, not a tool-naming problem: the words `buoni pasto`,
   `carta`, `contanti` name a funding source and must route to the funding tool,
   while a category name routes to the purpose tools. AD-007 already states the
   independence; the instructions do not. This is the largest cluster and it
   also carries most of the advice failures, so one change addresses both
   suites. **Recommended first.**

2. **Name the budget-survey tool and when it applies.** Cluster B is six
   paraphrases of one request plus three near neighbours. The registry has the
   tool; the boundary refuses rather than selects it. Worth checking whether the
   instructions describe it in terms an owner's phrasing would match, and
   whether "all my budgets" reads as out of scope to the model.

3. **Make an unknown category a refusal deterministically, not a judgment.**
   Cluster C is inconsistent across cases with identical shape, which is what a
   model-side rule looks like when it should be a resolver-side one. The active
   taxonomy is known to the system: a requested category that does not resolve
   could be refused before execution, the way the registry already vetoes a
   forbidden tool. That removes the cluster instead of tuning it, and it is the
   change most worth making regardless of which candidate is promoted.

## What would not be a fix

Relaxing `execution_postcondition_against_real_fixture` from 1.0. It reads the
answer back against a seeded database; every failure under it is a wrong figure
reaching the owner, and its nine failures are consequences of clusters A and C
rather than a separate defect.
