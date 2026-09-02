# AD-011: Evaluation-Gated Receipt Extraction

- Status: accepted
- Implementation: private content-addressed original archive implemented;
  extraction evaluation and review workflow planned
- Date: 2026-09-01

## Context

Receipt quality varies, llama.cpp multimodal support and local model capability
are model-dependent, and the workstation has 16 GB of VRAM. Selecting a vision
pipeline without measurements would risk an unreliable architecture.

## Decision

Define a replaceable receipt-extractor interface and compare three local
pipelines: OCR plus text LLM, a local VLM, and a hybrid. Promote a pipeline only
after it meets the committed receipt evaluation gates. Prefer correctness and
manual-correction rate, then latency and resource use. If none passes, keep the
feature experimental rather than lowering the safety rules.

Store original receipt images in a content-addressed local archive outside the
web root until manual deletion. Store only references in workflow state and
traces. Every receipt requires owner preview and approval in V1, even when the
extractor has high confidence.

## Alternatives considered

- A cloud OCR/VLM could improve quality but violates the privacy decision.
- Selecting one model in advance ignores hardware and Italian receipt evidence.
- Immediate deletion prevents later correction and reprocessing.

## Consequences

- A private, locally stored, annotated receipt dataset is required.
- Reconciliation failures remain drafts and are never silently posted.
- Media validation, retention, backup, and secure deletion become application
  responsibilities.

## Implementation evidence

[AD-014](AD-014-private-receipt-archive.md) implements the extraction-independent
original-media boundary with decoded format/size limits, private hash paths,
atomic idempotent writes, verified reads, and real filesystem/PostgreSQL tests.
It does not select or promote an extractor and does not weaken mandatory owner
preview.
