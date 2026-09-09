# ingestion/expense-it — Ministral 3 14B: what fails and what could fix it

- Run: `ING-20260908-125933-7cfb43b5`, dataset `ingestion-expense-it-v1` 1.0.0 (170 cases)
- Instructions: `candidate-extraction-it-v6`
- Outcome: 167 passed, 3 failed, 0 errors. One blocking gate unmet.
- Profile: text, no vision projector. An earlier run of this suite loaded the
  projector and failed four gates; see the note at the end.
- Status: analysis, not a decision.

## What fails

| Gate | Value | Threshold | |
| --- | ---: | ---: | --- |
| `schema_validity_after_one_repair` | 170/170 = 1.0 | ≥ 1.0 | pass |
| `amount_exact_match` | 161/162 = 0.9938 | ≥ 0.99 | pass |
| `occurrence_semantic_exactness` | 150/152 = 0.9868 | ≥ 0.97 | pass |
| `funding_source_and_class_match` | 152/152 = 1.0 | ≥ 0.97 | pass |
| `split_parts_exact_and_reconciled` | 12/12 = 1.0 | ≥ 0.99 | pass |
| `expense_refund_polarity` | 169/170 = 0.9941 | ≥ 0.99 | pass |
| `fabricated_merchant_date_or_payment_field` | 1/170 = 0.0059 | ≤ 0.01 | pass |
| `required_ambiguity_reported` | 27/28 = 0.9643 | ≥ 0.98 | **fail** |

Seven of eight gates pass. The suite fails on one adversarial case, and it is the
one whose consequence is worst.

## The 3 failures

### The gate: a funding source that does not exist was resolved anyway

`ING-UNF-008` — `Ho speso 25 euro con il buono Amazon`. Tagged `adversarial`,
`unresolvable-funding`, `safety`. The expected outcome is
`funding_source_unresolved`: the profile configures `Carta`, `Contanti`,
`Buoni pasto`, `Carta aziendale` and an archived `Vecchia carta`, and an Amazon
voucher is none of them. The boundary resolved it instead of reporting the
ambiguity it owes.

This is the failure mode the decision log already names for a different
candidate: an invented funding word that happens to reach a real active source
resolves silently and charges a resource the message never mentioned. Here the
message *does* state a funding word, and the word is simply not a source this
owner has. Reporting the ambiguity is the only correct outcome, because the
deterministic resolver cannot tell an unknown source from a mistyped known one.

### Two occurrence failures, below the gate that measures them

| Case | Message | Outcome |
| --- | --- | --- |
| ING-AMT-018 | `Ho speso 15 euro tondi in farmacia` | `occurrence_unreadable` |
| ING-REL-023 | `Ho appena speso 17 euro di spesa` | `occurrence_unreadable` |

Neither message states a date. `Ho appena speso` is tagged `relative-date` and
means now; `15 euro tondi` states no time at all. Both should resolve to the
received instant, and the boundary emitted an occurrence the resolver refused.
`occurrence_semantic_exactness` still passes at 150/152, so these two cost no
gate — but they are the same defect as the gate failure in a milder form: the
boundary produces a value where it should either resolve the obvious one or
report that it cannot.

`ING-AMT-018` also carries a `fabrication` facet, which is what keeps
`fabricated_merchant_date_or_payment_field` at 1 of 170 rather than 0.

## Proposed fixes

1. **Make an unrecognised funding word report ambiguity rather than resolve.**
   One case, and it is the one that matters: the suite's other 27 ambiguity
   cases pass, so the boundary knows how to report an ambiguity and does not
   apply it when the stated source is unknown rather than absent. Worth checking
   whether the instructions distinguish "no funding stated" from "a funding
   word I cannot match", because the resolver treats those very differently.
   **Recommended**, and it is the only change this suite needs to pass.

2. **Resolve a stated-now or unstated occurrence to the received instant.** Two
   cases, no gate. `Ho appena speso` is as explicit as an Italian message gets
   about meaning now. Cheap, and it removes the last fabrication from the run.

3. **Nothing else.** Amounts, funding matching, splits, polarity, and schema
   validity are exact or near-exact over 170 cases. This suite is close, and
   changes aimed at the other suites should be measured here before they are
   kept, because it is the one this candidate nearly passes.

## What would not be a fix

Relaxing `required_ambiguity_reported` from 0.98. The gate exists for exactly
the case that fails it: a payment the owner cannot have made, accepted without a
question. A candidate that reports 27 of 28 ambiguities and silently invents the
28th is not 96% safe on this axis; it is unsafe on the one shape the gate was
written for.

## Note on the earlier run

`ING-20260908-123525-3c9108a6`, published earlier the same day, ran the same
weights with the vision projector loaded, because the catalog attached the
projector to the artifact rather than to the profile. It failed four gates:
`amount_exact_match` 0.9815, `expense_refund_polarity` 0.9824,
`required_ambiguity_reported` 0.9643, and `fabricated_merchant_date_or_payment_
field` 0.0118. Only the ambiguity gate is common to both runs. The direction is
consistent across five metrics, which is more than run-to-run drift would
explain, but one re-run cannot separate the two, and this candidate has not been
shown to reproduce a run the way Qwen3 14B has.

## Final Step 3 measurement — 2026-09-09

Run `ING-20260909-103320-7ba9d870` has 165/170 passing cases and five residual
failures: `ING-AMT-018`, `ING-REL-023`, `ING-REF-015`, `ING-UNF-007`, and
`ING-UNF-008`. Four blocking gates fail: amount exactness 160/162 = 0.9877
against 0.99, polarity 168/170 = 0.9882 against 0.99, required ambiguity 26/28 =
0.9286 against 0.98, and fabricated optional fields 2/170 = 0.0118 against the
0.01 maximum. Schema validity and split reconciliation remain perfect. Warm
per-case suite p95 is 3,427 ms over 169 measured cases.

## Owner-stopped comparison — 2026-09-09

Run `ING-20260909-151929-3b66bd06` fails three blocking gates on the immutable
170-case dataset: amount exactness is 159/162, expense/refund polarity is
167/170, and required ambiguity is 27/28. Funding and splits remain exact, but
three owner-visible movement semantics are wrong. The owner stopped the
candidate sweep on this evidence and selected Qwen3 14B for promotion; no gate
or label changed.
