# AD-015: Two-Stage Category Fit

- Status: rejected
- Implementation: implemented, measured, and rejected on the measurement. The
  boundaries, schemas, composition and the `--two-stage` evaluation flag are
  kept so the result stays reproducible; no runtime path uses them.
- Date: 2026-09-08
- Supersedes: nothing. Extends [AD-006](AD-006-bounded-agent-workflows.md) and
  [AD-008](AD-008-controlled-taxonomy.md); promotion still runs through
  [AD-012](AD-012-evaluation-driven-promotion.md).

## Context

The category-fit boundary asks one model call to choose among all active
categories at once. Against the seed taxonomy that is eighteen options drawn
from two levels: ten headings, six of which have children, and fourteen
children.

Three candidates measured on `categorization/purpose-it` fail the same way. The
subtree is chosen correctly and the level is not:

| Candidate | Correct subtree | Correct leaf given the subtree | Agreement in one call |
| --- | ---: | ---: | ---: |
| Qwen3 14B | 0.9574 | 0.9704 | 0.8693 |
| Ministral 3 14B | 0.9291 | 0.8702 | 0.7451 |
| Qwen3.5 9B | 0.9433 | 0.7744 | 0.6732 |

Subtree selection varies by three points across candidates whose overall
agreement varies by twenty. The subtree is the robust half of the judgment and
the level is not, on every model measured.

Two attempts have been made to fix the level inside one call, and both moved
answers toward the heading:

- `candidate-category-fit-it-v7` rendered the hierarchy as explicit ancestor
  paths and instructed the model to return the final name. It measured 31 of 153
  against v6's 133, with 116 of its 122 failures answering a root. The flat
  rendering repeats each parent name once per child, so a heading appears three
  or four times in the list against one appearance of each child.
- `candidate-category-fit-it-v8` added five worked answers to v6, four choosing
  a child and one choosing a heading where no child fits. It measured 121 of
  153. `Mobilità` rose from 5 answers to 13, root answers across the run rose
  from 13 to 25, and proposals fell from 3 to 0 because every demonstration
  showed the existing-category shape.

The instruction text already states the rule and illustrates it. v6 says "Choose
the most specific entry that covers the purpose. Choose a heading only when the
purpose belongs under that heading but matches none of the specific entries
beneath it", and the boundary still answers the heading. Stating it, rendering
it and demonstrating it have each been measured, and the last two made it worse.

## Decision

Split the judgment into two model boundaries, run as two nodes.

**Stage one selects the subtree.** Given the message and the ten headings with
their inclusion descriptions, the answer is one heading, or a variant stating
that no heading covers the purpose. The proposal shape belongs to that variant:
a candidate name, its parent heading, its description and the comparison the
taxonomy requires. A proposal therefore competes against ten headings rather
than against eighteen mixed options, and its parent is chosen while the model is
already reasoning about headings.

**Stage two selects the level.** Given the message and the chosen heading's two
or three children with their descriptions, the answer is one child, or the
heading itself when no child fits. The heading competes only against its own
children, which is the comparison the taxonomy's shape makes meaningful, and the
enum is three or four options instead of eighteen.

Deterministic resolution against the active taxonomy is unchanged, as are the
strict versioned schemas, the single corrective retry, and the runtime-injected
context that AD-006 requires. Neither stage is an agent loop.

A heading with no children skips stage two.

## Why this addresses the measured failure rather than restating the rule

The failure is not that the rule is unstated. It is that a heading and its
children occupy the same enum, so any signal making the heading expressible also
makes it attractive: v7 supplied that signal through repetition in the rendering
and v8 through a demonstration, and both raised heading answers. The five
dataset cases that legitimately expect a heading are why the heading cannot
simply be removed from the enum.

Two stages remove the competition instead of trying to bias it. Stage one never
offers a child, so a heading answer there carries no information about level.
Stage two offers the heading against two or three siblings, where the prior a
model brings to a small closed choice has far less room than across eighteen.

## Alternatives considered

- **Restrict the schema to leaf nodes.** Rejected: five of 141 `existing` cases
  expect a heading, each one a purpose that belongs under a heading and matches
  no child — `Taxi`, `Car sharing`, `Monopattino in sharing`, `Pensione per il
  gatto`, `Assicurazione casa`. A leaves-only enum makes those unanswerable.
- **More instruction text.** Measured twice and rejected, above.
- **Accept 0.8693 and move on.** Defensible today, because that clears the 0.85
  gate. It rests on a three-case margin and on labels of which 149 of 153 are
  unreviewed, and it leaves the weaker candidates at 0.7451 and 0.6732, so a
  candidate change would reopen the gate.
- **A single call returning a path rather than a name.** This is what v7
  attempted through rendering. A schema whose values are paths would express the
  hierarchy exactly, but it enlarges the enum rather than shrinking it and does
  not separate the two decisions.

## Measured outcome, and why the case for this decision was wrong

`CAT-20260908-161944-e9204e87`, Qwen3 14B on `categorization-purpose-it-v1.1`:
118 of 153 = 0.7712, against the single call's 133 of 153 = 0.8693. Twenty cases
worsened and five improved.

Both stages measured worse than the numbers this ADR was built on:

| | Single call | Two stages |
| --- | ---: | ---: |
| Subtree correct | 0.9574 | 0.8905 |
| Level correct given the subtree | 0.9704 | 0.9344 |
| Agreement | 0.8693 | 0.7712 |

The context above quotes 0.9574 as the accuracy of subtree selection. It is not.
It is the accuracy of *the subtree implied by a leaf the model chose while
looking at all eighteen categories*. No subtree decision was ever made, so
nothing measured how well this candidate makes one. Presenting a derived
quantity as a decision accuracy is the error, and the two-stage design is what
exposed it by finally making the decision explicit.

The failure cases show the mechanism. A veterinary visit went to `Salute e cura
personale` rather than `Animali`; a streaming subscription to `Formazione` and
to `Acquisti personali` rather than `Tempo libero`; a utility bill to
`Mobilità`. In the single call the child's name and guidance were the evidence
that disambiguated the heading: choosing `Veterinario` implies `Animali` without
reasoning about `Animali` in the abstract. Hiding the children to make the first
decision cleaner removed the evidence that made it easy.

Proposals rose from 3 to 9 and `false_new_category_proposal` from 0.007 to
0.0352, still inside its 0.05 ceiling. Judging that no heading covers a purpose,
against ten abstract headings, is a lower bar than judging that no category
covers it against eighteen concrete ones.

## Consequences

- Two model calls per categorized movement, roughly five seconds against two and
  a half. The text workflow's p95 target is thirty seconds, so the budget holds,
  but the cost per movement doubles.
- A wrong subtree cannot be recovered by stage two. This costs nothing that is
  not already lost: when the subtree is wrong today the answer is wrong too.
- Two boundaries to version, screen and promote instead of one, and two places
  where a capability screen's union-output result applies.
- The evaluation suite gains the two measurements this decision rests on.
  Subtree accuracy and leaf-given-subtree accuracy become reported metrics
  rather than numbers computed by hand from a manifest.
- The taxonomy is ten headings and fourteen children. The decision buys less at
  this size than it would at a deeper one, which is the strongest argument for
  deferring it and the reason the two cheaper attempts were measured first.

## What stands after the rejection

The two attempts inside one call, v7 and v8, remain measured regressions, so the
heading-versus-entry error is not solved and `candidate-category-fit-it-v6` at
0.8693 remains the best measured configuration. What has changed is the reason
to prefer it: the level error is not evidence that the two decisions should be
separated, because separating them makes the first one harder.

Anything revisiting this should measure a subtree decision directly before
designing around its accuracy. The cheapest form is a first stage that sees the
children as evidence while still answering a heading, which is neither this
design nor the single call.

## Open questions, no longer blocking acceptance

- Whether stage one's no-heading-fits variant should carry the proposal fields
  or hand off to a third boundary. Carrying them keeps the call count at two and
  matches where the parent is already being chosen.
- Whether stage two is worth running at all for a heading whose children are
  clearly disjoint, or whether the gain is concentrated in the crowded subtrees.
- What the two stages cost in the durable review path, where a re-proposal
  already delegates to this boundary.
