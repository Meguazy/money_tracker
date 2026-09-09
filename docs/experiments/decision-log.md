# Experiment Decision Log

This file records promotion decisions, not every exploratory run. Append entries;
do not rewrite historical outcomes when a later experiment supersedes them.

## Current production baseline

- Text model: EVD-004 promotes profile `text-qwen3-14b` and
  `Qwen3-14B-Q5_K_M.gguf` for Step 3 text workflows.
- Structured-output method: the promoted strict union-shaped boundaries use the
  exact llama.cpp and schema versions pinned by EVD-004.
- Routing/category/tool prompts: router v6, extraction v6, category-fit v6, and
  tool-selection v5 are the V1 production defaults, with routing confidence 0.5
  and `explicit-finance-scope-v1`. `model-only-v0` is the direct
  read-selection rollback. There is no promoted auto-post subset or receipt
  model.

The following paragraphs retain the historical pre-promotion evidence rather
than rewriting it after EVD-004. At that point, candidate instructions existed
under `CANDIDATE_` names and `candidate-` version identifiers, and two
candidate-quality gaps remained open:
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

## Step 3 auto-post applicability — 2026-09-09

- Evidence: `CAT-20260908-153538-8281a737` and
  `out/evaluations/categorization/purpose-it/auto-post-calibration-2026-09-08.md`.
- Measured value: the broadest possible policy, auto-post every `existing`
  answer and refer every proposal, is 131/150 = 0.8733 against the unchanged
  0.98 precision gate. The boundary exposes no confidence field, and selecting
  narrower categories on these same labels would not be held-out calibration.
- Consequence: `auto_post_subset_category_precision` and
  `auto_post_subset_invariant_validity` are inapplicable in V1 because there is
  no automatic-post subset to which either obligation applies. Every movement
  remains a draft for owner confirmation, as the product baseline permits.
- This records no threshold change and promotes no model, prompt, or policy.

## Step 3 human usefulness disposition — 2026-09-09

- Owner decision: complete the advice usefulness review in Step 3 rather than
  defer it to Step 5.
- Evidence: Qwen3 14B run `ADV-20260909-092956-f95a0288`, immutable dataset
  `advice-grounding-it-v1.1` 1.1.0 with content hash
  `8f02096a061864e238f4c034bbfec02be9f6f06d5967d6dd062146a5e85eaa2f`,
  `candidate-tool-selection-it-v5`, and rubric `human-usefulness-v1`.
- Measured value: 52 of 52 applicable cases received an owner score; the median
  is 4.0 against the unchanged threshold of at least 4.0, so the gate passes.
  Score distribution: 1: 4, 2: 2, 3: 1, 4: 23, 5: 22.
- Residual evidence: five of six funding-split cases fail automated
  interpretation and received owner scores 1 or 2; the sole correct overall
  split received 5. Human usefulness cannot override that independently
  blocking 46/52 interpretation result.
- This records no threshold change and promotes no model or prompt.

## Step 3 threshold disposition and missing-data semantics — 2026-09-09

- Owner decision: do not lower the four failing Qwen thresholds; attempt a
  versioned deterministic repair and retain a direct rollback path.
- Fresh evidence: `TOO-20260909-101133-04d24021` and
  `ADV-20260909-092956-f95a0288`. The owner-observed funding failures are
  product-visible semantic errors and remain unacceptable despite the passing
  aggregate usefulness median.
- Metric correction: `advice-grounding-metrics-v2` keeps
  `missing_data_avoids_invented_conclusion` at 100% but counts a refusal with no
  answer as safe abstention. `answerable_missing_data_result` reports whether
  the fixture-backed absence was actually returned, and interpretation remains
  independently blocking. The v1 8/9 result is retained in its published run.
- This changes no dataset, label, threshold, prompt default, or promotion.

## Step 3 restored prompt defaults — 2026-09-09

- This append corrects the selected-configuration line in EVD-003 without
  rewriting that historical entry. After its measurements, commit `abd6d16`
  restored `tool_selection` v5 and commit `7c6c3b7` restored `category_fit` v6.
- The effective pre-promotion defaults are router v6, extraction v6,
  category-fit v6, and tool-selection v5. The rejected category subtree/leaf v1
  boundary from AD-015 has no runtime path.
- This correction changes no result, threshold, dataset, or label.

## EVD-004 — Promote Qwen3 14B for Step 3 text workflows (2026-09-09)

- Decision: promote profile `text-qwen3-14b` with
  `Qwen3-14B-Q5_K_M.gguf`, quantization `Q5_K_M`, GGUF SHA-256
  `e7c9aba1129ca2936be9eca01419d9f86af40e08caa01230d5574b34d08e3e31`.
  The owner explicitly selected Qwen3 14B after stopping the Ministral
  comparison on its failing ingestion result.
- Inference runtime: local llama.cpp `version: 0.3.0-dev` build 1, commit
  `3466812`; server binary SHA-256
  `19eaa4a708bb35194ed730b2f7d61fe2058f620d9565ec30a4773f1726695cb2`;
  context 8192; chat-template SHA-256
  `57f1fd00f0013a2be96aa79b857391f27e23df5b5f847072b524c897e24d0361`.
- Promoted prompts and policy: router v6, extraction v6, category-fit v6,
  tool-selection v5, `explicit-finance-scope-v1`, routing confidence 0.5, and
  no automatic-post subset. No threshold moved.
- Schemas and deterministic semantics: `RouteClassification`/router contract
  v1, `WireExpenseExtraction`/extraction contract v1,
  `BoundWireCategoryFit`/category-fit contract v1, `WireToolRequest`/tool
  contract v2, workflow state v1, read-result contract v2,
  `advice-grounding-metrics-v2`, and `human-usefulness-v1`.
- Taxonomy: `v1-2026-09-01`. Immutable datasets: routing 1.2.0
  (`routing-intent-it-v1.2`), ingestion 1.0.0 (`ingestion-expense-it-v1`),
  categorization 1.1.0 (`categorization-purpose-it-v1.1`), tools 1.2.0
  (`tools-selection-and-use-it-v1.2`), advice 1.1.0
  (`advice-grounding-it-v1.1`), and category review 1.0.0
  (`category-review-it-v1`).
- Complete Qwen evidence on implementation commit
  `c02c2f00e189276c3b7e21d382ff8452a7844ca5`: routing
  `ROU-20260909-145350-b1919d44`, ingestion
  `ING-20260909-150059-3f9abaac`, categorization
  `CAT-20260909-150354-f2f4e790`, tools
  `TOO-20260909-150703-7b2c9501`, owner-reviewed advice
  `ADV-20260909-144755-762f53cc`, and category review
  `REV-20260909-150839-7a1a525f`. Every applicable blocking gate passes.
- Material residuals: routing has four ordinary label errors while both
  read/write crossover gates remain zero; categorization has 20 disagreements
  but passes 0.8693 against 0.85; tools has three unknown-category accuracy
  errors but passes 0.9748 and every real-fixture, authorization, injection, and
  provenance gate. V1 auto-post remains disabled. Per-suite latency is
  diagnostic; NFR-PER-001 remains assigned to Step 4 end-to-end measurement.
- Rollback target: select `model-only-v0` for read selection and return to the
  unpromoted Step 3 configuration at commit `5ef71ab`. There was no previously
  promoted text model. The model-dependent entry point can therefore be
  disabled rather than silently substituting another candidate.
