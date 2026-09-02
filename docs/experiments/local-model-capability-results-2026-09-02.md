# Local Model Capability Results - 2026-09-02

- Status: concluded technical screening
- Promotion effect: none
- Hardware: NVIDIA GeForce RTX 5070 Ti, 16,303 MiB VRAM
- Runtime: llama.cpp commit `3466812d1f06728effe7c0f3c0671117f461672d`

## Scope and reproducibility

This screening checks only the Step 1 protocol capabilities: exact nested
structured output, forced tool selection, tool-result continuation, and image
input where applicable. It does not measure domain accuracy or promote a model.

All canonical runs use:

- [capability dataset 1.1.0](../../evals/datasets/capabilities-local-model-v1.1.jsonl),
  SHA-256 `b2b9d61dbd98844bd5da5c593380b0c9a8d36855f29a9577cab163d16760a97f`;
- the exact artifacts in the [candidate catalog](../../evals/models/local-candidates.yaml);
- llama.cpp binary SHA-256
  `19eaa4a708bb35194ed730b2f7d61fe2058f620d9565ec30a4773f1726695cb2`;
- 8,192-token context, one slot, reasoning disabled, temperature 0, and the
  embedded model chat template;
- a small non-private system PNG for the image-transport probe;
- local-only raw manifests under `artifacts/evals/`, with no Langfuse
  publication.

Dataset 1.1.0 makes the expected null mapping and exact preservation of Italian
source strings explicit. Dataset 1.0.0 remains unchanged as historical input.
Exploratory artifacts created before the runner derived the version from every
case are excluded rather than edited in place.

## Results

Latency values are milliseconds for each individual probe on this workstation.
They are diagnostic only and are not a throughput benchmark.

| Profile | Structured | Tool call | Tool result | Image | Gate | Canonical run |
| --- | ---: | ---: | ---: | ---: | --- | --- |
| Qwen3.5 9B vision | 1,413 | 438 | 504 | 241 | Pass | `CAP-20260902-084311-cb222924` |
| Gemma 4 12B vision | 1,469 | 305 | 530 | 331 | Pass | `CAP-20260902-084405-d5da6314` |
| Ministral 3 14B vision | 1,542 | 258 | 570 (failed) | 531 | Fail | `CAP-20260902-084504-44d60fee` |
| Qwen3 14B text | 1,450 | 389 | 897 | N/A | Pass | `CAP-20260902-084601-bb29032b` |
| GPT-OSS 20B text | 2,827 (failed) | 270 | 1,587 (failed) | N/A | Fail | `CAP-20260902-084658-91a01a68` |

Every run recorded complete GGUF, projector where applicable, llama.cpp,
chat-template, context, GPU, and driver metadata. Ministral did not consume the
provided tool result. GPT-OSS did not produce schema-valid final text and did
not produce final text after the tool result under this bounded profile.

## Decision

- Retain Qwen3 14B as a text candidate for the Step 3 domain evaluations.
- Retain Qwen3.5 9B and Gemma 4 12B as vision candidates for the Step 6 receipt
  pipeline comparison.
- Do not advance the current Ministral 3 or GPT-OSS profiles. Their installed
  artifacts may be retested only as a new recorded configuration.
- Promote no model. Capability success is necessary but does not establish
  Italian routing, extraction, categorization, advice, or receipt accuracy.

The image check proves OpenAI-compatible image transport and projector
execution only. It is not receipt recognition evidence; that requires the
private annotated receipt dataset and Step 6 gates.
