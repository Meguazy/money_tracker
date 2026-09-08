# Experiment Decision Log

This file records promotion decisions, not every exploratory run. Append entries;
do not rewrite historical outcomes when a later experiment supersedes them.

## Current production baseline

- Text model: no evaluated production model selected.
- Structured-output method: no capability-gated method selected.
- Routing/category/tool prompts: no V1 production prompt. Unpromoted candidate
  router, extraction, and category-fit instructions exist in code under
  `CANDIDATE_` names and `candidate-` version identifiers so a runtime cannot
  mistake them for a promoted configuration. Two candidate-quality gaps are
  known against the screened Qwen3 14B profile and remain open for the pinned
  suites to measure rather than to tune by hand:
  - `candidate-extraction-it-v2` is now measured by `ingestion-expense-it-v1`
    (170 cases) and fails three blocking gates. It is accurate on everything a
    message states: amounts, occurrence dates, split parts, polarity, and
    schema validity all scored exactly. Every failure is the model filling a
    field the message leaves empty, on 116 of 170 cases against a 1% gate.
    The earlier note that this fabrication "fails safe" is **corrected**: it
    does not. `fabricated_funding_silently_resolved` is 0.845, so 98 of 116
    invented funding words name a real active source, usually the profile
    default, and resolution accepts them. On a profile whose default is not the
    card, that silently charges a resource the owner never mentioned. Only the
    remaining 18 are refused, and those are refused because the invented word
    was a purpose noun such as `affitto` or `pane`.
  - `candidate-category-fit-it-v1` is now measured by
    `categorization-purpose-it-v1` (153 cases) and fails four blocking gates.
    It proposes a new category on 131 of 141 cases where an adequate node
    exists, duplicates an existing name on 10% of proposals, and twice proposes
    a category named for a payment method against a zero-tolerance gate. A
    diagnostic shows that in half of all proposals the model also named the
    correct existing category, so the dominant failure is discriminator
    selection on the flat schema rather than missing knowledge. Union output is
    consequently a capability worth screening, not only a prompt worth
    revising. It has since been screened and passes; see the capability
    baseline below.
  The earlier over-declaration gap in `candidate-extraction-it-v1` is closed.
  It was a boundary-design defect rather than a prompt-tuning problem: the
  schema let the model judge its own uncertainty, which the deterministic side
  was better placed to decide. Removing that channel resolved it. No row is
  added below, because no experiment concluded and nothing was promoted; the
  change is recorded in the evidence of
  [Step 3](../roadmap/step-3-evaluated-text-workflows.md).
- Owner category review: measured by `category-review-it-v1` (37 multi-turn
  scenarios) against real PostgreSQL. The workflow now passes all eight blocking
  gates and every scenario, including zero duplicate writes across each replayed
  branch. The suite found two defects on its first run and both are fixed, with
  the same suite as the evidence:
  - `run_settle` mapped every `TaxonomyError` to `unknown_category`, but a
    re-proposal refused by taxonomy governance is `invalid_candidate` under the
    documented reason vocabulary. The re-proposal branch now handles its own
    refusals instead of leaving them to a handler that cannot tell the causes
    apart.
  - The checkpoint serializer relied on the library's own
    `LANGGRAPH_STRICT_MSGPACK` lookup, which happens at import time and defaults
    to false, while the local guard treated an absent variable as true. An unset
    variable would therefore have allowed arbitrary types to be reconstructed
    from checkpoint data while the guard reported the invariant as satisfied.
    The allowlist is now passed explicitly, which is additive over the library's
    safe types, and names the three contracts that cross a pause.
  Nothing is promoted by this run. The suite's `promotion_effect` is `none` and
  its model port is scripted, so it establishes that the workflow is correct and
  says nothing about any model.
- Read-tool selection: `candidate-tool-selection-it-v1` is measured by
  `tools-selection-and-use-it-v1` (110 cases) and `advice-grounding-it-v1` (52
  cases), both against a seeded PostgreSQL fixture. The design properties hold:
  zero forbidden or unregistered tool requests across 110 cases including twelve
  written to tempt one, no profile or timezone field for a model to fill, every
  figure traceable to an executed tool, arithmetic exact on every answer, and
  J8's budget-before-history rule at 10 of 10. The accuracy gates fail for a
  single reason. The model chooses the correct tool 95 times in 98 and then
  supplies a month on 79% of the messages that state none, against instructions
  that say `questo mese` states no month and must be omitted. The system then
  reads that month, finds no records, and answers zero: grounded, auditable, and
  wrong, with no deterministic veto available because any valid `YYYY-MM` names a
  real month. Cases stating a full month score 13 of 14; cases stating none score
  15 of 67.
- A boundary gap is recorded with it: the selection contract asks for `YYYY-MM`
  but never tells the model which month is current, so a relative period such as
  `il mese scorso` cannot be expressed at all. Nothing is promoted or changed
  here; both are evidence for the experiment that would address them.
- One failure mode now spans three boundaries and is no longer an inference.
  Categorization fills `proposed_name` beside `category_name`, extraction fills
  `payment_parts`, `merchant` and `amount` where the message is silent, and tool
  selection fills `period` where no month is stated. Each field is optional, each
  set of instructions says to omit it, and the model fills it anyway. Reliable
  omission of an optional field joined union output as a capability to screen
  before a candidate is considered for a product role. Both have now been
  screened directly, and they answer differently: see the capability baseline.
- Auto-post threshold: disabled until categorization precision gates pass.
- Routing confidence bar: none promoted, and calibration has now established
  that none should be. The implemented router requires the value as an explicit
  argument and has no default. `routing-intent-it-v1.1` (201 cases) reaches the
  minimum and fails three blocking gates at a 0.5 bar; its decisive failure is a
  safety one, with 10 of 70 read-only inputs reaching a financial write, all of
  them incoming money the router has no concept of. No question, advice request,
  or greeting was ever escalated.
  A held-out calibration over all 21 candidate bars now explains why no
  threshold fixes that. Bars from 0.00 to 0.80 score identically, only 8
  distinct confidence values occur across the dataset, and the first bar meeting
  the zero-tolerance safety gate is 1.00, which clarifies 95% of messages and
  drops macro-F1 to 0.226. The holdout agrees: no escalation, a 0.9294
  clarification rate, macro-F1 0.227. The escalations are produced by a
  confident model rather than an uncertain one, so the bar cannot reach them and
  the routing gap needs the missing concept, not a calibrated number.
  The calibration report's one blocking metric asks only whether an admissible
  bar exists, so it reports its gates as passed while the selected bar is
  unusable. Admissibility is not usefulness, and the rule was left as
  pre-registered rather than widened after the outcome was known.
- Receipt pipeline: no pipeline selected; receipt extraction remains planned.
- Langfuse: optional local integration; the Step 1 capability runner emits
  deterministic local reports and can publish only redacted aggregate scores.
- Capability baseline: Qwen3 14B, Qwen3.5 9B, and Gemma 4 12B pass the four
  protocol probes of screen version 1.1.0. They remain experimental candidates;
  no model has been promoted to a product role.
  Screen version 1.2.0 adds the two capabilities Step 3 measured a need for and
  has been run against Qwen3 14B, the only candidate the text boundaries use.
  See [2026-09-03 results](local-model-capability-results-2026-09-03.md).
  - `optional_field_omission` **fails**, in both boundary shapes. Of 68 slots a
    message leaves silent, the shape the adapter sends today leaves 0.7647
    empty and the genuinely-optional shape 0.4265, against a pre-registered
    0.95. Stated slots are still captured at 1.0 and 0.9722, so the model is
    not emptying slots indiscriminately; it cannot leave one alone.
  - The shape comparison changes which failure occurs, not whether it occurs.
    Under the optional-absent shape almost every failure is an empty string,
    which `NonEmptyText` refuses, so a wrong record becomes a failed turn:
    silent fabrication drops from 14 slots to 7 while refusals rise from 2 to
    32. This is the first evidence that any part of the failure is reachable by
    reshaping a boundary rather than by replacing the model.
  - `union_output` **passes**, mechanically, in both the root and the nested
    shape and in both runs. llama.cpp accepts a `oneOf` schema, every answer
    matched exactly one variant, none carried a field of the variant it did not
    choose, and both variants were reachable. The `CategoryFit` contract's
    stated reason for staying flat, that no model is capability-screened for
    union output, no longer holds for this profile.
  - Variant-selection accuracy inside that probe is a diagnostic and gates
    nothing. `categorization/purpose-it` owns categorization accuracy, and the
    probe's six synthetic categories are not its taxonomy.

## Decisions

| Decision ID | Date | Experiment | Outcome | Selected configuration | Supersedes | Evidence |
| --- | --- | --- | --- | --- | --- | --- |
| EVD-000 | 2026-09-01 | Step 0 documentation baseline | Establish gates before model/pipeline selection | Evaluation process and matrix in this directory | None | Product baseline and AD-012 |
| EVD-001 | 2026-09-02 | Local capability shortlist 1.1.0 | Retain three downstream candidates; reject the current Ministral and GPT-OSS profiles; promote none | Qwen3 14B text; Qwen3.5 9B and Gemma 4 12B vision | None | [Capability results](local-model-capability-results-2026-09-02.md) |
| EVD-002 | 2026-09-03 | Local capability screen 1.2.0 | Record optional-field omission as failed and union output as passed for the text candidate; promote none; retain EVD-001 | Qwen3 14B `Q5_K_M`, llama.cpp `3466812d`, dataset 1.2.0, canonical run `CAP-20260903-183125-ea4695f3` reproduced twice | None; extends EVD-001 | [Capability results](local-model-capability-results-2026-09-03.md) |

## Entry requirements

Every future row must identify:

- the concluded experiment report and complete run manifest;
- pass/fail for every blocking gate;
- exact pinned model/prompt/schema/tool/taxonomy/pipeline versions;
- reason for promotion, rejection, rollback, or experimental status;
- effective application commit and prior rollback target;
- private artifact references by safe local hash/path only.

## EVD-003 — Correct development labels and read-result semantics (2026-09-08)

- Decision: retain experimental status; no promotion or gate relaxation.
- Authorization: owner requested fixes and Qwen3 14B as the test candidate.
- Versions: tools dataset 1.1.0, routing dataset 1.2.0, purpose dataset 1.1.0;
  selection v6, router v6, category v7; read-result contract v2.
- Reasons: forced-route tool exclusions, permitted explicit refusals, baseline
  insurance/footwear/parking precedence, and external-price scope. Changed cases
  carry a revision reason and contract-audit provenance, not claimed owner labels.
- Alternatives rejected: allowing forbidden tools to satisfy contradictory
  labels; treating benefit totals as source balances; adjusting historical files
  or lowering accuracy thresholds. Existing taxonomy IDs and stored data stay
  stable. Remaining disputed labels are unreviewed.
- Evidence: [corrective experiment](text-workflow-fixes-2026-09-08.md).
- Promotion prerequisites remain: complete measured gates, human usefulness,
  auto-post calibration, untouched reviewed holdout and runtime validation.
