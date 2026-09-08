# categorization/purpose-it — Qwen3 14B: where this suite stands

- Best measured configuration: `candidate-category-fit-it-v6` on
  `categorization-purpose-it-v1.1`, run `CAT-20260908-153538-8281a737`.
- Outcome: 133 of 153 = 0.8693 on `purpose_label_agreement`, which **clears its
  0.85 gate**. The suite still reports an overall fail, for a reason no
  candidate can affect; see the last section.
- Status: closed by an owner decision to accept this level. Three attempts to
  improve it were measured and all three were worse.

## The gates

| Gate | Value | Threshold | |
| --- | ---: | ---: | --- |
| `purpose_label_agreement` | 133/153 = 0.8693 | ≥ 0.85 | **pass** |
| `false_new_category_proposal` | 1/142 = 0.007 | ≤ 0.05 | pass |
| `merchant_or_payment_method_proposed_as_category` | 0/18 = 0.0 | ≤ 0 | pass |
| `duplicate_or_alias_equivalent_proposal` | 0/3 = 0.0 | ≤ 0.02 | pass |
| `auto_post_subset_category_precision` | not measured | ≥ 0.98 | blocks |
| `auto_post_subset_invariant_validity` | not measured | = 1.0 | blocks |

The accuracy gate crossed by relabelling rather than by a change in the
boundary. Four cases moved between dataset 1.0.0 and 1.1.0 — both insurance
cases, the running shoes and the workplace parking — and the boundary answered
all four the same way before and after. It was scored wrong for them and is now
scored right. The labels moved to meet an answer it was already giving, which
the failure analyses had argued for, and which is a relabelling result rather
than an improvement.

## What still disagrees, and why it is left alone

Twenty cases. Six answer the parent of the correct node, nine reuse a category
where the label expects a proposal, and five are disagreements the taxonomy does
not settle. The
[label review](../../../../evals/datasets/categorization-purpose-it-label-review-2026-09-08.md)
adjudicates them against the taxonomy's own descriptions: eight are settled in
the label's favour, two are settled against it — a cleaner is covered by
"Pulizia" and cigarettes by "beni personali non classificati altrove" — and nine
need an owner decision the product baseline already records as open.

## Three attempts, all measured worse

| Configuration | Agreement | What it tried |
| --- | ---: | --- |
| `candidate-category-fit-it-v6` | **0.8693** | states the specificity rule and illustrates it in prose |
| `candidate-category-fit-it-v7` | 0.2026 | rendered the hierarchy as explicit ancestor paths |
| `candidate-category-fit-it-v8` | 0.7908 | added five worked answers |
| two-stage judgment (AD-015) | 0.7712 | split the heading and the level into two calls |

All three moved answers toward the heading, from three different causes: v7 by
repeating each parent name once per child in the rendering, v8 because one
demonstration choosing a heading outweighed four choosing entries, and the split
because hiding the children removed the evidence that disambiguated the heading.
[AD-015](../../../../docs/arc42/decisions/AD-015-two-stage-category-fit.md)
records the last of these and the flaw in the case for it.

The lesson worth carrying: the heading-versus-entry error is not evidence that
the two decisions should be separated. Separating them makes the first one
harder, because a child's name and guidance are what tell a model which heading
it is looking at.

## Why the suite still reports a fail

`auto_post_subset_category_precision` and `auto_post_subset_invariant_validity`
are emitted at zero denominator, and an unmeasured blocking gate cannot pass.
That is true for every candidate and every run, and it is not a property of this
one. The
[auto-post determination](../auto-post-calibration-2026-09-08.md) shows the
policy cannot be calibrated on a dataset this size: the broadest possible rule
is 0.8733 against a 0.98 gate, and certifying 0.98 needs about 150 clean
observations where the most represented category has fifteen.

Resolving that is a product decision about whether V1 posts anything
automatically, not a categorization result.

## Note on the published latest run

The most recent published run for this pair is the rejected two-stage
experiment, because it ran last. `latest.json` shows 0.7712; the configuration
this document describes is `CAT-20260908-153538-8281a737` in the history beside
it, and the next run of the default candidate will restore the row.
