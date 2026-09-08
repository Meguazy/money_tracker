# categorization/purpose-it — Qwen3.5 9B: what fails and what could fix it

- Run: `CAT-20260908-133038-3ce99b34`, dataset `categorization-purpose-it-v1` 1.0.0 (153 cases)
- Instructions: `candidate-category-fit-it-v6`
- Outcome: 103 passed, 50 failed, 0 errors. One blocking gate unmet.
- Status: analysis, not a decision.

## What fails

| Gate | Value | Threshold | |
| --- | ---: | ---: | --- |
| `purpose_label_agreement` | 103/153 = 0.6732 | ≥ 0.85 | **fail** |
| `false_new_category_proposal` | 0/141 = 0.0 | ≤ 0.05 | pass |
| `merchant_or_payment_method_proposed_as_category` | 0/18 = 0.0 | ≤ 0 | pass |
| `duplicate_or_alias_equivalent_proposal` | not measured | ≤ 0.02 | no proposal was offered |

`proposals_offered` is 0 over 153 cases. As with Ministral 3 14B, the proposal
safety gates are vacuous rather than clean: a gate on proposed names cannot fail
when nothing is proposed.

## The 50 disagreements

### A. Answered the parent of the expected node — 29 cases

Fifty-eight per cent of the failures are one rule, and this is the largest
instance of it across the three candidates measured.

| Root answered instead of one of its children | Cases |
| --- | ---: |
| `Tempo libero` | 13 |
| `Casa e utenze` | 6 |
| `Animali` | 5 |
| `Mobilità` | 3 |
| `Salute e cura personale` | 2 |

`Affitto di settembre` reaches `Casa e utenze`, `Pedaggio autostradale` reaches
`Mobilità`, `Tessera annuale dei mezzi` reaches `Mobilità`. The subtree is right
every time and the answer stops one level short.

**Recovering these 29 would move the gate to a projected 132/153 = 0.8627, over
the 0.85 threshold.** This is the only candidate for which the specificity rule
alone would clear the gate. It is a projection from one run's arithmetic, not a
measurement: it assumes the other twenty-one disagreements are unchanged.

The dataset expects a parent in exactly five of 141 `existing` cases, each one a
case where no child fits (`Taxi`, `Car sharing`, `Monopattino in sharing`,
`Pensione per il gatto`, `Assicurazione casa`). The rule it encodes is
specificity, and no candidate is being told it.

### B. Should have proposed, reused an existing node — 12 cases

Every `CAT-PRO-*` case fails, as it does for Ministral 3 14B. Nursery under
`Regali e donazioni`, school canteen under `Bar, ristoranti e asporto`,
baby-sitter under `Tempo libero`, notary and lawyer under `Finanza e obblighi`.
Three of the twelve are tagged `debatable` by the dataset's authors.

With zero proposals in the whole run, this is not a threshold slightly too
strict: the boundary does not propose at all under v6 with this candidate.

### C. Unrelated node — 8 cases

| Case | Message | Expected | Answered |
| --- | --- | --- | --- |
| CAT-EXI-019 | Rata del mutuo | Abitazione e condominio | Finanza e obblighi |
| CAT-EXI-040 | Assicurazione auto | Auto e moto | Finanza e obblighi |
| CAT-EXI-110 | Assicurazione casa | Casa e utenze | Finanza e obblighi |
| CAT-EXI-094 | Tassa di iscrizione all'esame | Formazione | Finanza e obblighi |
| CAT-EXI-073 | Ho comprato un libro | Sport, hobby e cultura | Formazione |
| CAT-SAF-004 | Ho comprato su Amazon un libro | Sport, hobby e cultura | Formazione |
| CAT-EXI-078 | Scarpe da corsa | Sport, hobby e cultura | Acquisti personali |
| CAT-SAF-012 | Croccantini presi da Arcaplanet | Cibo e accessori | Spesa alimentare → `Bar, ristoranti e asporto` |

The first four are the same reading all three candidates converge on: a
financial obligation belongs to `Finanza e obblighi` rather than to the thing it
pays for. The dataset says otherwise and is inconsistent with itself about it,
filing car insurance under the vehicle and home insurance under the home.

`Croccantini presi da Arcaplanet` under `Bar, ristoranti e asporto` is a plain
error, and a safety-tagged one: pet food filed as restaurant spending.

### D. Sibling under the same parent — 1 case

`Occhiali da vista` under `Cura personale` rather than
`Farmaci e prestazioni sanitarie`. Both are children of
`Salute e cura personale`, and prescription glasses are neither a medicine nor a
service, so the expected label is itself a stretch.

## Proposed fixes

1. **State the specificity rule.** "Answer the most specific category that fits;
   use a parent only when no child fits." Twenty-nine cases here, sixteen on
   Ministral 3 14B, four on Qwen3 14B: one sentence, three candidates, and for
   this one it is projected to clear the gate on its own. **Recommended first,
   by a wide margin.**

   Do not implement it as a leaves-only schema: five expected labels are
   parents.

2. **Establish whether this candidate can propose at all.** Zero proposals in
   153 cases, the same as Ministral. Two of the three candidates measured never
   propose under v6, which points at the instructions rather than at either
   model.

3. **Settle the taxonomy where cluster C splits.** Mortgage, insurances and an
   exam fee against `Finanza e obblighi`: all three candidates read it the same
   way and the labels do not. That is a taxonomy decision, and every case in
   this dataset is `reviewer_status: unreviewed`.

## What would not be a fix

Rejecting this candidate on 0.6732 without applying item 1 first. Fifty-eight
per cent of its disagreements are a rule it was never given, and the projection
that follows from giving it crosses the gate.
