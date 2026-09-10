# Local Model Shortlist - 2026-09-01

- Status: installed and SHA-256 verified; capability screening concluded
- Target: NVIDIA GeForce RTX 5070 Ti with 16,303 MiB VRAM
- Runtime: pinned CUDA llama.cpp build, 8,192-token initial context,
  concurrency 1, reasoning disabled
- Promotion effect: none
- Results: [Local Model Capability Results - 2026-09-02](local-model-capability-results-2026-09-02.md)

## Goal and constraints

Select three strong text profiles and three vision profiles that fit a local
16 GB GPU closely enough to run controlled Money Tracker evaluations. "Best" is
treated as a shortlist for this hardware and workload, not a universal ranking.
Tool use, structured output, Italian support, receipt-image potential, current
llama.cpp support, and the memory left for runtime state all matter.

The six profiles use five distinct model weights because Ministral 3 is tested
both with and without its matching vision projector. Exact revisions, byte
sizes, hashes, and relative installation paths are recorded in the
[candidate catalog](../../evals/models/local-candidates.yaml).

## Selected profiles

| Profile | Artifact | Approximate artifact size | Why it is included |
| --- | --- | ---: | --- |
| Text | GPT-OSS 20B MXFP4 | 11.28 GiB | Strong tool/agent baseline with native low-bit weights and explicit structured-output positioning. |
| Text | Qwen3 14B Q5_K_M | 9.79 GiB | Strong multilingual baseline, including Italian, with mature llama.cpp packaging and tool behavior. |
| Text | Ministral 3 14B Q5_K_M | 8.96 GiB | Compact instruction and tool candidate whose same weights can also exercise vision. |
| Vision | Qwen3.5 9B UD-Q5_K_XL + BF16 projector | 7.14 GiB | Recent natively multimodal Qwen candidate with relatively generous runtime headroom. |
| Vision | Gemma 4 12B QAT Q4_0 + projector | 6.66 GiB | Recent local-first multimodal candidate specifically positioned for a 16 GB machine. |
| Vision | Ministral 3 14B Q5_K_M + BF16 projector | 9.78 GiB | Larger multimodal comparison point with official GGUF and projector artifacts. |

Artifact sizes exclude KV cache, CUDA/runtime allocations, and temporary prompt
state. Fit therefore remains an empirical gate at the configured context and
must not be inferred from weight size alone.

## Addendum 2026-09-08: two installed candidates promoted to text profiles

No new weights. Qwen3.5 9B and Gemma 4 12B were installed and screened as vision
candidates, and neither had ever run a product suite, because a profile carries
its modality and the text suites select text profiles. Both passed the
capability screen at the time, both are the lightest artifacts installed
(6.28 and 6.50 GiB against 9.79 for Qwen3 14B), and Qwen3.5 is a later
generation than the text Qwen in this shortlist. Measuring them on text costs a
catalog entry and a sweep, so `text-qwen35-9b` and `text-gemma4-12b` are now
declared beside the vision profiles over the same artifacts.

Their capability passes are not carried over. Both were screened against suite
versions 1.0.0 and 1.1.0, which predate the `optional_field_omission` and
`union_output` probes added in 1.2.0 — the two capabilities that turned out to
be the binding constraint on Step 3. Neither has been screened on them.

The same change corrected what a text profile means. A projector belongs to the
profile that uses it rather than to the weights: this document already states
that Ministral 3 is tested "both with and without its matching vision
projector", but the catalog attached the projector to the artifact, so a text
profile over multimodal weights loaded it too. `text-ministral3-14b` was
therefore a second vision run under another name. Text profiles now carry no
projector, and the Ministral text runs recorded on 2026-09-08 were produced
before the correction, with the projector loaded.

## Addendum 2026-09-10: Qwen3.8 27B at IQ4_XS fits and is measured

The exclusion above rests on an arithmetic premise, not on parameter count: a
"roughly 16 GB quantized weight" plus a dense 27B KV cache does not fit. Both
halves of that premise are false for `Qwen3.8-27B-UD-IQ4_XS.gguf`, and the
candidate is now declared as `text-qwen38-27b`.

The weight is 13.27 GiB rather than roughly 16 GB. More importantly the
architecture is hybrid: `qwen35.full_attention_interval = 4`, so 16 of the 64
blocks hold a KV cache and the other 48 carry a recurrent state whose size does
not grow with the context. At the target 8,192-token context the KV cache is
512 MiB, against roughly 2 GiB for a dense model of the same parameter count.
The recurrent state adds about 145 MiB and is constant. llama.cpp also ignores
block 64, the `nextn` multi-token-prediction layer, which the packaging includes
but the server does not allocate.

Fit was measured rather than inferred, at the pinned target configuration with
no concession: 8,192 context, f16 KV cache, all layers on GPU, flash attention
on, concurrency 1. The server loaded and served the capability suite with a peak
of 15,355 MiB of the 16,303 MiB available, leaving 475 to 545 MiB free while a
desktop session held about 1.4 GiB. It generated at 41 to 47 tokens per second
and evaluated prompts at 300 to 1,080 tokens per second, which is what full GPU
residency looks like; nothing spilled to CPU.

The margin is real but thin, and it is the reason no vision profile is declared
over these weights. Adding the 0.87 GiB projector would not leave enough for the
image tensors, and a catalogued profile that cannot run is a claim the catalog
should not carry. The projector is therefore not installed and not declared.

The candidate passes the 1.2.0 capability screen on every required probe. Two of
those probes are the constraint that decided Step 3, and this is where the
result is worth reading rather than summarising:

| Candidate | `optional_field_omission` | `required_nullable` | `optional_absent` | `union_output` |
| --- | --- | ---: | ---: | --- |
| text-qwen3-14b (promoted) | failed | 0.7500 | 0.4118 | passed |
| text-ministral3-14b | passed | 1.0000 | 0.7059 | failed |
| text-qwen35-9b | passed | 0.9706 | 0.3676 | passed |
| text-qwen38-27b | passed | 1.0000 | 0.9412 | passed |

Under `required_nullable` the candidate left all 68 silent slots empty and
captured all 36 stated ones, with no fabricated value. Under `optional_absent`,
the shape on which every other candidate collapses, it reached 0.9412; that is
below the 0.95 threshold, so the shape does not pass on its own and
`required_nullable` remains the boundary shape to use. Union output passes
mechanically in both shapes with no foreign field and both variants reachable.
Variant-selection accuracy is 0.875, recorded as a diagnostic because
categorization/purpose-it owns that gate.

Nothing is promoted by this run. A capability screen establishes that a
boundary shape may be used, not that a candidate is accurate enough for a role.
The screen does establish one thing worth stating plainly: on suite 1.2.0 the
promoted text model fails `optional_field_omission`, and only `text-qwen35-9b`
and `text-qwen38-27b` pass every blocking gate.

The six product suites were then run in one sweep, `20260910-225231`, and they
do not carry the screen's advantage over. The candidate wins the one suite with
headroom left and loses three by small margins:

| Suite | text-qwen3-14b (promoted) | text-qwen38-27b |
| --- | --- | --- |
| routing/intent-it | pass | **fail** by one case of 67 |
| ingestion/expense-it | pass, every gate at 1.0 | pass, every gate at 1.0 |
| categorization/purpose-it | pass, agreement 0.8693 | **pass, agreement 0.9216** |
| tools/selection-and-use | pass | **fail**, 94/99 postconditions |
| advice/grounding-it | pass | **fail**, two gates, one unmeasured |
| workflow/category-review-it | pass | pass (scripted model port) |

Categorization is the result worth keeping. It was the promoted candidate's
narrowest blocking gate anywhere — 0.8693 against a 0.85 threshold — and this
candidate reaches 0.9216, cutting label errors from 20 of 153 to 12 while
holding false-proposal and duplicate rates at the same values over more
proposals offered. Ingestion is a tie because both are at the ceiling; routing,
tools and advice go to the promoted candidate by one, five and three cases.

Every safety-shaped gate is clean across all six suites: no read-only input
routed to a write, no forbidden or unregistered tool, no authorization or
timezone chosen by the model, no claimed execution without a tool result, no
figure untraceable to an executed tool, exact arithmetic, and no effect from any
prompt-injection case. Where this candidate loses, it loses on accuracy.

The cost is latency, and it is uneven rather than uniform. Warm p95 is 1860 ms
against 1909 ms on tools, 1977 against 1853 on advice, but 3648 against 1171 on
categorization. Summed over the boundaries an expense message crosses, the model
calls come to about 13.7 seconds against 6.9. NFR-PER-001 allows 30 seconds for
a text response, so the budget holds at roughly half rather than a quarter.

Per-suite analyses of the three failures are recorded beside the scores, in
`out/evaluations/{routing/intent-it,tools/selection-and-use,advice/grounding-it}/text-qwen38-27b/failure-analysis.md`.

## Local installation layout

Weights are workstation-owned and are never committed. Installed candidates go
under `${XDG_DATA_HOME:-$HOME/.local/share}/money-tracker/models/` in
`text/`, `vision/`, and `shared/` subtrees, one directory per profile named
after that profile's identifier in the table above, with a vision projector
kept beside the weights it belongs to. See
[Development](../development.md) for the launcher settings that select one.

## Sources and packaging boundary

- [OpenAI GPT-OSS 20B](https://huggingface.co/openai/gpt-oss-20b) and the
  [ggml-org GGUF packaging](https://huggingface.co/ggml-org/gpt-oss-20b-GGUF)
  describe native MXFP4, tool use, and structured outputs.
- [Qwen3 release](https://qwenlm.github.io/blog/qwen3/) and the
  [official Qwen3 14B GGUF](https://huggingface.co/Qwen/Qwen3-14B-GGUF)
  document multilingual and agent capabilities.
- [Official Ministral 3 GGUF](https://huggingface.co/mistralai/Ministral-3-14B-Instruct-2512-GGUF)
  supplies both model and matching projector.
- [Official Qwen3.5 9B source](https://huggingface.co/Qwen/Qwen3.5-9B) is paired
  with the [Unsloth GGUF conversion](https://huggingface.co/unsloth/Qwen3.5-9B-GGUF);
  this third-party quantization boundary is explicit and must be evaluated.
- [Official Qwen3.8 27B source](https://huggingface.co/Qwen/Qwen3.8-27B)
  publishes no first-party GGUF at this size, so it is paired with the
  [Unsloth GGUF conversion](https://huggingface.co/unsloth/Qwen3.8-27B-GGUF);
  this is the same third-party quantization boundary as Qwen3.5 9B and must be
  evaluated as such.
- [Google's Gemma 4 12B guide](https://developers.googleblog.com/gemma-4-12b-the-developer-guide/)
  and [official QAT GGUF repository](https://huggingface.co/google/gemma-4-12B-it-qat-q4_0-gguf)
  position the model as local and multimodal.
- [llama.cpp server documentation](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)
  defines the OpenAI-compatible runtime boundary used by the probes.

## Exclusions

Qwen3.8 27B and similarly sized candidates are not in the first pass. A roughly
16 GB quantized weight leaves insufficient predictable headroom on this GPU for
KV cache and runtime allocations at the target context. Smaller candidates can
be added later when a failure mode justifies them; widening the list before the
first controlled comparison would increase evaluation cost without improving
the initial coverage.

This exclusion has since been revisited for one artifact and one artifact only.
See [Addendum 2026-09-10](#addendum-2026-09-10-qwen38-27b-at-iq4_xs-fits-and-is-measured).
It stands for every other candidate of this size: what the addendum revisits is
the arithmetic, which a hybrid-attention artifact changes and a dense one of the
same size does not.

## Evaluation plan

1. Resume each pinned download and accept it only after byte-size and SHA-256
   verification.
2. Run the text capability probes for all three text profiles.
3. Run the same probes plus image input with each matching projector for all
   three vision profiles.
4. Record exact model, projector, llama.cpp binary, template, context, latency,
   and resource metadata in local artifacts.
5. Keep every candidate experimental. Promotion requires the Step 3 or Step 6
   datasets and all blocking gates in the evaluation matrix.
