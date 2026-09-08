# Auto-post eligibility: why it cannot be calibrated on this dataset

- Question: which subset of purpose judgments may be posted without owner review?
- Gates it would have to satisfy, from the
  [evaluation matrix](../../../docs/experiments/evaluation-matrix.md):
  `auto_post_subset_category_precision` ≥ 0.98 and
  `auto_post_subset_invariant_validity` = 1.0, with the matrix requiring that
  "threshold calibration must use held-out labels rather than model
  self-confidence alone".
- Evidence: `CAT-20260908-153538-8281a737`, `candidate-category-fit-it-v6` on
  `categorization-purpose-it-v1.1`, 153 cases, Qwen3 14B.
- Outcome: no policy is calibrated, and none can be on evidence this size. This
  is a conclusion, not an open task.

## Why the question became askable

The policy was previously blocked for a different reason: agreement was 0.124,
so no eligible subset could be defined at all. Agreement is now 133 of 153 =
0.8693 and clears its own gate, so the eligibility question is worth asking. The
answer is still no, for reasons that are arithmetic rather than judgment.

## The boundary offers no confidence to threshold

`CategoryFit` exposes `outcome`, `category_name`, `proposed_name`,
`parent_name`, `description`, `rationale`, `considered_category_names` and
`evidence`. There is no confidence field, deliberately: the boundary gives the
model no channel for declaring its own certainty. So an auto-post policy cannot
be a bar swept over a continuous score the way the routing confidence bar is. It
has to be a deterministic rule over observable properties, and the only property
with enough structure to define a subset is which category was answered.

## The broadest possible policy already fails

Auto-post every `existing` answer, refer every proposal:

> 131 correct of 150 = **0.8733**, against a 0.98 gate.

No narrowing can be justified from a larger number, so every candidate policy is
a subset of one that already misses the gate by eleven points.

## The narrower policy is circular, and the data cannot fix that

Thirteen of the twenty-three answered categories show a precision of 1.000, over
79 cases. Restricting eligibility to those thirteen gives 79 of 79 — but the
thirteen were selected by looking at the same cases the number then scores. That
is precisely what the matrix forbids, and it is not a discipline problem that a
held-out split would solve here:

| Observations, zero errors | One-sided 95% lower bound on precision |
| ---: | ---: |
| 20 | 0.8609 |
| 50 | 0.9418 |
| 100 | 0.9705 |
| **150** | **0.9802** |
| 200 | 0.9851 |

Certifying 0.98 needs about 150 clean observations. The most represented
category in this dataset has **15**, and the dataset has 153 cases in total. A
held-out split would leave two or three cases per category, which supports no
statement about a 98% threshold.

Several of the perfect categories are also small enough to be noise:
`Abitazione e condominio` is 2 of 2 and `Animali` is 1 of 1.

## What the zero-precision categories are

`Casa e utenze` 0/4, `Alimentazione` 0/2, `Tempo libero` 0/1 and `Mobilità` 3/5
are the roots. Every one of those errors is the boundary answering the parent of
the correct node. Any eligibility rule would exclude them, which is right, but it
means the policy would be encoding a known boundary defect as a permanent
property of a category rather than measuring a stable one.

## What would unblock it

Three routes, and they are not equivalent.

1. **Raise the boundary.** If precision over all `existing` answers reaches 0.98,
   a single-subset policy needs no per-category evidence and no calibration at
   all. From 0.8733 that is a boundary improvement rather than a threshold
   exercise, and the parent-answer defect is where the nineteen errors are.
2. **Grow the dataset by roughly twenty times**, to around 150 held-out cases per
   category the policy would admit. That is a data-collection programme, not a
   run.
3. **Change what the gate asks for.** Measuring precision as a point estimate on
   a subset the same data selected is unsound at any dataset size; a gate stated
   as a lower confidence bound would be measurable, at a cost in the number it
   certifies. That is an evaluation-matrix decision.

Until one of these happens, `auto_post_subset_category_precision` and
`auto_post_subset_invariant_validity` stay emitted at zero denominator, and the
categorization suite cannot report an overall pass for any candidate — not
because the candidate failed, but because two of its gates have nothing to
measure.
