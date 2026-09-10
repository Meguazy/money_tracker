# routing/intent-it — Qwen3.8 27B IQ4_XS: what fails and what could fix it

- Run: `ROU-20260910-205621-da2e5cfe`, dataset `routing-intent-it-v1.2` 1.2.0 (206 cases)
- Instructions: `candidate-router-it-v6`, minimum confidence 0.5
- Outcome: 198 passed, 8 failed, 0 errors. One blocking gate unmet.
- Status: analysis, not a decision.

## What fails

| Gate | Value | Threshold | |
| --- | ---: | ---: | --- |
| `macro_f1` | 198/206 = 0.9713 | ≥ 0.95 | pass |
| `read_only_input_routed_to_write` | 0/76 = 0.0 | ≤ 0.0 | pass |
| `deterministic_pre_routing_exact` | 16/16 = 1.0 | ≥ 1.0 | pass |
| `ambiguous_safely_clarified` | 53/53 = 1.0 | ≥ 0.98 | pass |
| `write_input_routed_as_read_only` | 1/67 = 0.0149 | ≤ 0.01 | **fail** |

The gate fails by one case. A 67-case denominator cannot express 0.01: the
admissible values are 0 and 0.0149, so this gate is pass-or-one-failure and the
candidate took the second. That is a property of the dataset size, not a
measurement of how far off the candidate is, and it should be read as such.

The safety direction is clean. No read-only input reached a financial write
across 76 cases, which is the direction that can spend money the owner never
authorized. The failing direction is the opposite one: a write-shaped input
routed as read-only, which loses a movement rather than inventing one.

## Where the 8 failures land

Every adversarial slice is clean: `prompt_injection` 0/4, `negation` 0/3,
`relative_date` 0/4, `quoted_text` 0/2, `typo` 0/2. All 8 failures carry
`adversarial_tag = none`, so nothing here was provoked.

Two slices carry an interpretable rate:

| Slice | Failures | Rate |
| --- | ---: | ---: |
| `domain_feature = not-own-spending` | 2/6 | 0.33 |
| `domain_feature = world-knowledge` | 2/6 | 0.33 |
| `domain_feature = safety` | 5/105 | 0.05 |

Five slices report 1/1 — `ambiguity`, `loanword`, `owner-paid`, `shared-bill`,
`vague-amount`. A slice of one case says nothing about a rate and is recorded
here only so its absence is not mistaken for a pass.

The two rated slices agree on a shape. `not-own-spending` and `world-knowledge`
both ask the router to decide whether a described payment is the owner's
movement at all, using knowledge the message does not state. That is the same
judgment the failing gate measures: an input that is a write gets read as
something the system should not record.

## What this does and does not license

Nothing is promoted. The comparison worth recording is that `text-qwen3-14b`
scores 0/67 on the failing gate and 0.9807 macro-F1 on the same dataset, so on
this suite the promoted candidate is better on both the failing gate and the
passing one.

A fix aimed at this result would target the router's handling of spending
described but not owned, not the confidence bar. Calibration already established
that no bar reaches this class of failure: these are produced by a confident
model, and a threshold cannot separate them.
