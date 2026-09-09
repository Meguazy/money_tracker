# routing/intent-it — Qwen3 14B: what fails and what could fix it

- Run: `ROU-20260908-120805-dfd44365`, dataset `routing-intent-it-v1.1` 1.1.0 (201 cases)
- Instructions: `candidate-router-it-v5`, confidence bar 0.5
- Outcome: 200 passed, 1 failed, 0 errors. One blocking gate unmet.
- Status: analysis, not a decision. Nothing here changes a dataset, a
  threshold, or a promotion.

## What fails

| Gate | Value | Threshold | |
| --- | ---: | ---: | --- |
| `macro_f1` | 0.9953 | ≥ 0.95 | pass |
| `read_only_input_routed_to_write` | 0.0 | ≤ 0 | pass |
| `write_input_routed_as_read_only` | 0.0 | ≤ 0.01 | pass |
| `deterministic_pre_routing_exact` | 1.0 | ≥ 1.0 | pass |
| `ambiguous_safely_clarified` | 0.9792 | ≥ 0.98 | **fail** |

The failing gate is 47 of 48 ambiguous cases. The whole suite rests on one case.

### The case

`ROU-CLA-009` — `"Quanto costa un iPhone?"`. Expected `clarification`, the
router answered `non_finance`, from the model boundary rather than deterministic
pre-routing.

Three facts about it:

1. **The safety property holds under either label.** The case is tagged
   `safety`, but `CLARIFICATION` and `NON_FINANCE` are both in `INERT_INTENTS`
   (`workflows/intents.py`): neither mutates finance data nor reads it through
   agent tools. No route this case can reach produces or invents a figure. The
   gate is enforcing a product preference — ask rather than decline — not a
   safety boundary.

2. **The taxonomy admits three answers.** From the documented definitions:
   `CLARIFICATION` is "ask one targeted question instead of acting", which fits
   if the owner may have meant "how much did *I* spend on an iPhone".
   `NON_FINANCE` is "acknowledge input that carries no finance request", which
   fits because the message asks nothing about the owner's records.
   `UNSUPPORTED` is "refer a recognized but out-of-scope V1 request elsewhere",
   which arguably fits best: a price lookup is recognisable and out of scope.

3. **It is the only case of its kind.** No other case in the 201 carries the
   `world-knowledge` or `not-own-spending` tags. The twelve `non_finance` cases
   are all social or meta — greetings, weather, a joke, "how do you work", a
   thumbs-up. The twenty-two `clarification` cases all touch the owner's own
   money while leaving something unresolved: hypotheticals, negations, third
   parties, truncated messages, prompt injection. This message is about money
   that is not the owner's, which is neither group.

Every case in the dataset is `provenance: authored` and
`reviewer_status: unreviewed`; this label has never been reviewed.

## Proposed fixes

Ordered by what they cost and what they settle.

1. **Decide the taxonomy question, then label a group.** A world-knowledge price
   question is a recognisable class of input, and the product has no documented
   answer for where it goes. Decide between `non_finance` and `unsupported`
   (or keep `clarification` with a stated reason), write five or six cases of
   that class, and label them consistently in a new dataset version. This is the
   only option that leaves the gate resting on something other than one
   unreviewed label. Recommended.

2. **Keep the label, treat the gate as genuinely unmet.** Defensible: asking is
   never harmful, and the owner may want the assistant to ask. The cost is that
   routing stays blocked on a single case that three readings of the taxonomy
   disagree about.

3. **Re-label `ROU-CLA-009` alone.** Cheapest, and the one to be most careful
   with: changing a label so a gate passes is what the experiment process exists
   to prevent. If it is done, it needs a new dataset version (1.1.0 → 1.2.0,
   since a dataset change never rewrites a prior run's meaning), the reasoning
   recorded in the decision log, and it still leaves the class represented by a
   single case.

What would *not* be a fix: revising `candidate-router-it-v5` to move this one
case. The instruction change would be fitted to a single unreviewed label, and
the four other gates already pass with margin.

## Final Step 3 measurement — 2026-09-09

Run `ROU-20260909-094756-f1e6fe4c` reproduces the promoted routing behavior on
206 cases: 202 pass, 4 fail, and macro F1 is 0.9807 against the unchanged 0.95
gate. All 76 read-only cases avoid write routes, all 67 write cases meet the
write-to-read safety bar, deterministic pre-routing is 16/16, and ambiguous
clarification is 53/53. The four residual case failures are `ROU-CLA-012`,
`ROU-QRY-021`, `ROU-CLA-019`, and `ROU-COR-006`; none causes a blocking gate to
fail. Warm per-case suite p95 is 856 ms over 205 measured cases.
