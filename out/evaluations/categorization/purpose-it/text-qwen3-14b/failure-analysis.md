# categorization/purpose-it — Qwen3 14B: what fails and what could fix it

- Run: `CAT-20260908-121857-94182b39`, dataset `categorization-purpose-it-v1` 1.0.0 (153 cases)
- Instructions: `candidate-category-fit-it-v6`
- Outcome: 129 passed, 24 failed, 0 errors. One blocking gate unmet.
- Status: analysis, not a decision.

## What fails

| Gate | Value | Threshold | |
| --- | ---: | ---: | --- |
| `purpose_label_agreement` | 129/153 = 0.8431 | ≥ 0.85 | **fail** |
| `false_new_category_proposal` | 1/141 = 0.0071 | ≤ 0.05 | pass |
| `merchant_or_payment_method_proposed_as_category` | 0/18 = 0.0 | ≤ 0 | pass |
| `duplicate_or_alias_equivalent_proposal` | 0/3 = 0.0 | ≤ 0.02 | pass |

The gate misses by two cases: 0.85 × 153 = 130.05, so 131 agreements are needed
and 129 were reached.

**Read this against the September baseline before concluding anything.** The
decision log records `candidate-category-fit-it-v1` at 0.124 agreement, proposing
a new category on 131 of 141 cases where an adequate node existed. Version 6
proposes three times in total and offers one false proposal. The dominant defect
of this boundary is gone; what remains is a different, smaller set.

## The 24 disagreements, by what the model actually did

### A. Should have proposed, reused an existing node — 10 cases

| Case | Message | Answered |
| --- | --- | --- |
| CAT-PRO-001 | Baby-sitter per sabato sera | Tempo libero |
| CAT-PRO-003 | Retta della mensa scolastica | Alimentazione |
| CAT-PRO-004 | Sigarette | Acquisti personali |
| CAT-PRO-005 | Trasloco con i facchini | Mobilità |
| CAT-PRO-006 | Notaio per il rogito | Finanza e obblighi |
| CAT-PRO-008 | Colf per le pulizie | Prodotti casa, arredo e manutenzione |
| CAT-PRO-009 | Toelettatura del cane | Cura personale |
| CAT-PRO-010 | Commercialista | Finanza e obblighi *(dataset: debatable)* |
| CAT-PRO-011 | Attrezzi da giardinaggio | Casa e utenze *(dataset: debatable)* |
| CAT-PRO-012 | Parcheggio del posto di lavoro | Auto e moto *(dataset: debatable)* |

This is v1's failure inverted: the boundary now under-proposes. Three of the ten
are tagged `debatable` by the dataset's own authors, so at most seven are clean.

One is wrong on its own terms regardless of the proposal question:
`Toelettatura del cane` was placed under `Cura personale`, the owner's personal
care, while an `Animali` subtree exists. That is a semantic error, not a
threshold one.

### B. Answered the parent of the expected node — 4 cases

| Case | Message | Expected | Answered |
| --- | --- | --- | --- |
| CAT-EXI-019 | Rata del mutuo | Abitazione e condominio | Casa e utenze |
| CAT-EXI-020 | Quota straordinaria per il tetto | Abitazione e condominio | Casa e utenze |
| CAT-EXI-033 | Elettricista per la presa | Prodotti casa, arredo e manutenzione | Casa e utenze |
| CAT-SAF-001 | Spesa al supermercato con buoni pasto | Spesa alimentare | Alimentazione |

The model reaches the right subtree and stops one level early. This is the most
mechanical cluster and the only one with a deterministic-looking fix.

The taxonomy is 10 roots and 14 children; six roots have children. The dataset
expects a parent in exactly five of its 141 `existing` cases, and every one of
them is a case where **no child fits**: `Monopattino in sharing`, `Taxi`,
`Car sharing` (neither `Trasporto pubblico` nor `Auto e moto`),
`Pensione per il gatto` (neither `Cibo e accessori` nor `Veterinario`),
`Assicurazione casa` (none of the three `Casa e utenze` children). So the rule
the dataset encodes is specificity: answer the most specific node that fits, and
fall back to the parent only when no child does.

### C. Proposed instead of reusing — 1 case

`CAT-EXI-097` `Bomboniere per il matrimonio` proposed a new category where
`Regali e donazioni` was expected.

### D. Unrelated node — 9 cases

| Case | Message | Expected | Answered |
| --- | --- | --- | --- |
| CAT-EXI-040 | Assicurazione auto | Auto e moto | Finanza e obblighi |
| CAT-EXI-110 | Assicurazione casa | Casa e utenze | Finanza e obblighi |
| CAT-EXI-102 | Multa per divieto di sosta | Finanza e obblighi | Mobilità |
| CAT-EXI-053 | Occhiali da vista | Farmaci e prestazioni sanitarie | Acquisti personali |
| CAT-EXI-073 | Ho comprato un libro | Sport, hobby e cultura | Formazione |
| CAT-SAF-004 | Ho comprato su Amazon un libro | Sport, hobby e cultura | Formazione |
| CAT-EXI-078 | Scarpe da corsa | Sport, hobby e cultura | Acquisti personali |
| CAT-EXI-046 | Lavaggio auto | Auto e moto | Prodotti casa, arredo e manutenzione |
| CAT-SAF-012 | Croccantini presi da Arcaplanet | Cibo e accessori | Spesa alimentare |

Two of these are clear model errors: `Lavaggio auto` under household products,
and pet kibble under human groceries.

The rest are genuine disagreements about a taxonomy that does not settle them.
Note the two insurance cases: the dataset files car insurance under the vehicle
and home insurance under the home, while the model filed both under
`Finanza e obblighi`. **The model is more internally consistent than the labels
here.** A book between `Formazione` and `Sport, hobby e cultura`, and running
shoes between sport and personal purchases, are the same kind of case.

## Proposed fixes

1. **State the specificity rule in the instructions.** "Answer the most specific
   category that fits; use a parent only when no child fits." This targets
   cluster B, whose four cases are exactly the rule's violation, and it matches
   what the dataset already encodes. Four recovered agreements would put the
   gate at 133/153 = 0.869. **Recommended first**, because it is the one change
   supported by the dataset's own structure rather than by a preference.

   Do not implement this as a schema restriction to leaf nodes: five expected
   labels are parents, and a leaves-only enum would make those unanswerable.

2. **Review the nine cluster-D labels against the taxonomy before touching the
   prompt.** Six of them are defensible either way, and two show the labels
   disagreeing with themselves (the insurance pair). Tuning instructions against
   labels that are themselves unsettled fits the boundary to noise. The taxonomy
   descriptions, not the prompt, are where insurance, books, and sports goods
   should be decided. Every case in this dataset is `provenance: authored` and
   `reviewer_status: unreviewed`.

3. **Re-balance the proposal threshold only after 1 and 2.** Cluster A is the
   v6 instruction over-correcting v1's over-proposal. It is real, but it is
   seven clean cases against a gate that two other changes may already clear,
   and pushing the boundary back toward proposing risks reopening the failure
   that made v1 unusable. If it is done, `false_new_category_proposal` (now
   0.0071 against a 0.05 ceiling) is the metric that must not move.

4. **Fix `Toelettatura del cane` separately.** Placing a dog's grooming under
   the owner's personal care while an `Animali` subtree exists is a semantic
   error independent of the proposal question, and it belongs with cluster D's
   pet-food error rather than with the threshold discussion.

## What would not be a fix

Lowering `purpose_label_agreement` from 0.85, or re-labelling cluster D to match
the model. Both would make the gate pass without making the boundary better, and
a dataset change needs a new version and a recorded reason.
