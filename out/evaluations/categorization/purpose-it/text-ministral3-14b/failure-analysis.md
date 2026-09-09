# categorization/purpose-it — Ministral 3 14B: what fails and what could fix it

- Run: `CAT-20260908-130212-e930bef4`, dataset `categorization-purpose-it-v1` 1.0.0 (153 cases)
- Instructions: `candidate-category-fit-it-v6`
- Outcome: 114 passed, 39 failed, 0 errors. One blocking gate unmet.
- Status: analysis, not a decision.

## What fails

| Gate | Value | Threshold | |
| --- | ---: | ---: | --- |
| `purpose_label_agreement` | 114/153 = 0.7451 | ≥ 0.85 | **fail** |
| `false_new_category_proposal` | 0/141 = 0.0 | ≤ 0.05 | pass |
| `merchant_or_payment_method_proposed_as_category` | 0/18 = 0.0 | ≤ 0 | pass |
| `duplicate_or_alias_equivalent_proposal` | not measured | ≤ 0.02 | no proposal was offered |

The boundary proposed a new category **zero times** in 153 cases. Every safety
gate is clean by construction, and the whole failure is in which existing node
it picks.

## The 39 disagreements

### A. Answered the parent of the expected node — 16 cases

This is the dominant defect and it is close to a rule.

| Expected subtree | Answered | Cases |
| --- | --- | ---: |
| `Cibo e accessori` (pet food, litter, leash, bed, shampoo) | `Animali` | 6 |
| `Sport, hobby e cultura` (cinema, gym, concert, museum) | `Tempo libero` | 4 |
| `Abbonamenti` (streaming) | `Tempo libero` | 1 |
| `Farmaci e prestazioni sanitarie` (dentist) | `Salute e cura personale` | 1 |
| `Auto e moto` (motorway toll) | `Mobilità` | 1 |
| `Abitazione e condominio` (roof levy) | `Casa e utenze` | 1 |
| others | root of the same subtree | 2 |

The boundary reaches the correct subtree on every one of these and then answers
its root. It is not a knowledge failure: `Croccantini per i gatti` reached
`Animali`, which is right and one level too high.

The taxonomy is 10 roots and 14 children, and the dataset expects a parent in
exactly five of its 141 `existing` cases — each one a case where no child fits
(`Taxi`, `Car sharing`, `Monopattino in sharing`, `Pensione per il gatto`,
`Assicurazione casa`). The rule it encodes is specificity: the most specific
node that fits, and a parent only when no child does.

Recovering these 16 would move the gate to a projected 130/153 = 0.8497, which
is still under 0.85 by half a case. It is by far the largest single lever, and
on its own it is not enough.

### B. Should have proposed, reused an existing node — 12 cases

Every `CAT-PRO-*` case fails. Baby-sitter under `Casa e utenze`, nursery under
`Formazione`, school canteen under `Alimentazione`, cigarettes under
`Cura personale`, removals and notary under `Casa e utenze`, dog grooming under
`Animali`, gardening tools under `Prodotti casa, arredo e manutenzione`.

With zero proposals in 153 cases, this is not a threshold that is slightly too
strict — the boundary never proposes at all under these instructions. Three of
the twelve are tagged `debatable` by the dataset's own authors, so at most nine
are clean.

### C. Unrelated node — 10 cases

| Case | Message | Expected | Answered |
| --- | --- | --- | --- |
| CAT-EXI-019 | Rata del mutuo | Abitazione e condominio | Finanza e obblighi |
| CAT-EXI-040 | Assicurazione auto | Auto e moto | Finanza e obblighi |
| CAT-EXI-110 | Assicurazione casa | Casa e utenze | Finanza e obblighi |
| CAT-EXI-094 | Tassa di iscrizione all'esame | Formazione | Finanza e obblighi |
| CAT-EXI-053 | Occhiali da vista | Farmaci e prestazioni sanitarie | Acquisti personali |
| CAT-EXI-078 | Scarpe da corsa | Sport, hobby e cultura | Acquisti personali |
| CAT-EXI-079 | Colori e pennelli per dipingere | Sport, hobby e cultura | Acquisti personali |
| CAT-EXI-073 | Ho comprato un libro | Sport, hobby e cultura | Formazione |
| CAT-SAF-004 | Ho comprato su Amazon un libro | Sport, hobby e cultura | Formazione |
| CAT-EXI-046 | Lavaggio auto | Auto e moto | Prodotti casa, arredo e manutenzione |

Four of these route a financial obligation to `Finanza e obblighi` — mortgage,
two insurances, an exam fee — which is a consistent reading the taxonomy does
not rule out, applied more consistently than the labels apply theirs. One is a
clear error: a car wash under household products.

### D. Sibling under the same parent — 1 case

`CAT-EXI-008` `Spesa online consegnata a casa` was filed under
`Bar, ristoranti e asporto` rather than `Spesa alimentare`. Home-delivered
groceries against restaurant delivery is a real ambiguity in the message.

## Proposed fixes

1. **State the specificity rule.** "Answer the most specific category that fits;
   use a parent only when no child fits." Sixteen cases here, and the same
   sentence is worth four on Qwen3 14B, which makes it the one change both
   candidates want. **Recommended first.**

   Do not implement it as a leaves-only schema: five expected labels are
   parents, and that enum makes them unanswerable.

2. **Find out why nothing is ever proposed.** Zero proposals in 153 cases is a
   different condition from proposing too rarely, and it makes every safety gate
   here vacuous — a gate on proposal names cannot fail when there are no
   proposals. Establish whether the boundary can propose at all under v6 with
   this candidate before treating cluster B as a threshold to tune.

3. **Settle the taxonomy where cluster C splits.** Whether an insurance premium,
   a mortgage instalment, and an exam fee belong to the thing they pay for or to
   `Finanza e obblighi` is a taxonomy decision, and the dataset currently answers
   it inconsistently: car insurance under the vehicle, home insurance under the
   home, both under `Finanza e obblighi` by this boundary. Fix the category
   descriptions, not the prompt. Every case in this dataset is
   `reviewer_status: unreviewed`.

4. **Nothing about the safety gates.** Zero merchant or payment-method
   proposals, zero false proposals. They are clean and must stay clean through
   any change made for 2.

## What would not be a fix

Lowering `purpose_label_agreement`. At 0.7451 the boundary disagrees with the
labels on one case in four, and the largest cluster is a rule it is not being
told rather than a judgment it is getting wrong.

## Final Step 3 measurement — 2026-09-09

Run `CAT-20260909-103551-23b7246d` scores 121/153 = 0.7908 against the 0.85
agreement gate, with 32 case disagreements. It offers no proposals, so the
proposal-specific duplicate/alias metric has an applicable empty denominator
and remains failed; this is a model behavior, separate from the two automatic-
post metrics that are explicitly inapplicable under the V1 no-auto-post policy.
Warm per-case suite p95 is 1,062 ms over 152 measured cases. Ministral remains
below Qwen on the primary category agreement gate and is not a promotion
candidate.
