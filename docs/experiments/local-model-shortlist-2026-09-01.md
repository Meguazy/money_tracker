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
