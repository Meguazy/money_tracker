# Local Model Capability Results - 2026-09-03

- Status: concluded screening extension
- Promotion effect: none
- Supersedes: nothing. It extends
  [the 2026-09-02 screening](local-model-capability-results-2026-09-02.md),
  whose results stand for suite version 1.1.0.
- Hardware: NVIDIA GeForce RTX 5070 Ti, 16,303 MiB VRAM
- Runtime: llama.cpp commit `3466812d1f06728effe7c0f3c0671117f461672d`

## Why the screen was extended

Step 3 measured the same failure at three independent boundaries. Categorization
filled `proposed_name` beside `category_name`, extraction filled `payment_parts`,
`merchant`, and `amount` where the message was silent, and tool selection filled
`period` where the owner stated no month. In every case the field is optional,
the instructions say to omit it, and the model fills it anyway. Two capabilities
were named in the [Decision Log](decision-log.md) as things to screen before a
candidate is considered for a product role rather than things to tune with a
prompt: reliable omission of an optional field, and union output.

This run screens both. Step 1 remains complete: its deliverables and exit
criteria were about the protocol probes and the evidence they record, all of
which still hold. This is an addition to the screen that step built, made
because a later step measured the need for it.

## Scope and reproducibility

Suite `capabilities/local-model` version 1.2.0 adds two probes to the four
protocol probes, which are unchanged and still pass. All canonical runs use:

- [capability dataset 1.2.0](../../evals/datasets/capabilities-local-model-v1.2.jsonl),
  SHA-256 `419f6596b04d0e49aa695007b5e3c25ff48b744a86781e4516fc81b446f83300`,
  46 cases: the four 1.1.0 protocol cases carried forward unchanged apart from
  their version, 26 optional-field cases, and 16 union cases;
- Qwen3 14B `Q5_K_M`, GGUF SHA-256
  `e7c9aba1129ca2936be9eca01419d9f86af40e08caa01230d5574b34d08e3e31`;
- llama.cpp binary SHA-256
  `19eaa4a708bb35194ed730b2f7d61fe2058f620d9565ec30a4773f1726695cb2`,
  chat template SHA-256
  `57f1fd00f0013a2be96aa79b857391f27e23df5b5f847072b524c897e24d0361`;
- 8,192-token context, one slot, reasoning disabled, temperature 0;
- local-only raw manifests under `artifacts/evals/`, no Langfuse publication.

Canonical run `CAP-20260903-183125-ea4695f3`, on the same dataset three times:

- `CAP-20260903-182858-589e2aab` ran first and reached the same verdicts. Its
  diagnostics are coarser, because it counted empty text and placeholder words
  in one tally before that counter was split; the split was made because the two
  have different downstream consequences, and it cannot move a verdict, since
  both groupings sit far below the threshold either way. No gate was touched.
- `CAP-20260903-183852-bf33e885` reproduces the canonical run's every figure
  after the gate expressions were refactored, so the reported numbers are the
  numbers the committed code produces.

Only Qwen3 14B was screened, because it is the only candidate the text
boundaries use. The two vision candidates remain screened at 1.1.0 for Step 6
and are unaffected.

Both gates were pre-registered in
[the suite file](../../evals/suites/capabilities/local-model.yaml) and are
recorded there with the run's own thresholds. Neither was moved after a score
was seen.

## Results

| Probe | 1.1.0 | 1.2.0 |
| --- | --- | --- |
| Strict structured output | Pass | Pass |
| Tool call | Pass | Pass |
| Tool result continuation | Pass | Pass |
| Optional field omission | not screened | **Fail** |
| Union output | not screened | **Pass** |

`blocking_gates_passed` is now false for this profile. It means that a screened
capability failed, not that the candidate is rejected for every interface: the
per-probe result is the answer to whether one boundary shape may be used.

### Optional field omission: fails in both shapes

The probe scores slots rather than cases. Each of the 26 messages offers four
optional slots covering the three encodings a contract can use for optionality,
a nullable scalar, a nullable string, and an emptiable list, which gives 68
slots the message leaves silent and 36 it states. The stated half is not
decoration: without it a model that emptied every slot would screen as perfectly
reliable.

Two boundary shapes were measured on the same cases and the same instructions,
differing only in how silence is expressed.

| Shape | Silent slots left empty | Fabricated | Empty text | Placeholder word | Stated slots captured |
| --- | ---: | ---: | ---: | ---: | ---: |
| `required_nullable` | 0.7647 | 14 | 2 | 0 | 1.0 |
| `optional_absent` | 0.4265 | 7 | 32 | 0 | 0.9722 |

`required_nullable` is what the local adapter sends today: every property is
required and an optional one admits null. `optional_absent` leaves an unstated
property out of the document entirely. Neither reaches the pre-registered 0.95,
so the gate fails; the best shape leaves roughly one silent slot in four filled.

The shape comparison is the actionable half, and it does not say what it might
appear to. Making a field genuinely optional made the model *worse* at leaving
it alone, not better. What changed is the kind of failure. Under
`optional_absent` almost every failure is an empty string, and `NonEmptyText`
strips whitespace and requires one character, so the contract refuses it: the
answer costs a retry and then a typed failure, loudly. Under `required_nullable`
the failures are plausible invented values that validate and are stored. The
absent shape roughly halves silent corruption, 14 to 7, by converting it into 32
refusals. That is a trade between a wrong record and a failed turn, not a fix,
and it is the first evidence that any of this is reachable by changing the
boundary rather than the model.

The two runs agree on every structural feature and differ only in
`required_nullable`, 0.7353 against 0.7647, with 16 fabricated slots against 14.
`optional_absent` was identical in both.

One limit belongs with the numbers. A probe is not the boundary it screens, and
an easier probe would clear a capability the boundary then fails: a single
optional field in a short schema is answered correctly by this model. The draft
schema here was therefore given the width of a real contract, four optional
slots inside instructions of the length the extraction boundary actually sends.
It is still not that boundary.

### Union output: passes mechanically in both shapes

The blocking half of this probe is mechanical, and every expectation held in
both shapes across both runs: llama.cpp accepted the `oneOf` schema at the
document root and under a property, all 16 answers matched exactly one variant,
none carried a field belonging to the variant it did not choose, both variants
were reached, and every existing-category answer named a category from the
supplied list.

| Shape | Schema accepted | Invalid | Foreign fields | Both variants reached | Variant selected correctly |
| --- | --- | ---: | ---: | --- | ---: |
| `root_union` | yes | 0 | 0 | yes | 0.8125 |
| `nested_union` | yes | 0 | 0 | yes | 0.6875 |

Zero foreign fields is a property of the mechanism rather than an achievement of
the model: the grammar cannot emit them. That is exactly the point. The
`CategoryFit` contract records that it is a flat schema with conditional
validation because "a local model emits one fixed schema far more reliably than
a `oneOf`, and no model is capability-screened for union output". The second
half of that reason no longer holds for this profile, and the failure the flat
schema then measured, filling `proposed_name` while also filling
`category_name`, is unexpressible in the shape now screened as working.

Variant-selection accuracy is recorded as a diagnostic and gates nothing. It is
not comparable with `categorization/purpose-it`, which scores the real taxonomy
and reads 0.124 agreement; this probe offers six synthetic categories and asks
only whether both branches are reachable and exclusive. Reading 0.8125 as
evidence that a union fixes categorization would be reading a mechanism check as
a domain score.

## Decision

- Record `optional_field_omission` as failed for the Qwen3 14B profile. No
  boundary may rely on this model leaving an optional field alone, in either
  shape, which is what the three Step 3 suites already measured downstream.
- Record `union_output` as passed for the Qwen3 14B profile, mechanically and in
  both shapes. A boundary may now be shaped as a discriminated union without
  contradicting the capability screen.
- Promote no model, prompt, schema, or threshold. A screen establishes what an
  interface may rely on; it does not establish domain accuracy, and neither
  probe here measures any.
- The 2026-09-02 decisions stand. Qwen3 14B remains a text candidate and the two
  vision candidates are untouched; what changed is that one more thing is known
  about the text candidate, and it is a failure.
