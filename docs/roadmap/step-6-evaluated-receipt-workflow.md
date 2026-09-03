# Step 6: Evaluated receipt workflow

Part of the [delivery roadmap](../roadmap.md).

## Prerequisites

A private annotated receipt dataset exists, and Step 1 capability probes have
identified viable local OCR/VLM candidates.

## Deliverables

- Benchmark OCR plus text LLM, local VLM, and hybrid candidates on identical
  private/synthetic fixtures.
- Record the selected pipeline, exact artifacts, trade-offs, and gates in the
  experiment decision log; keep the extractor behind a replaceable interface.
- Secure Telegram/dashboard image ingestion, private original retention,
  derivative generation, extraction evidence, line editing, purpose allocation,
  payment allocation, discount/rounding handling, and reconciliation.
- Mandatory receipt preview/approval through Telegram and detailed dashboard.
- Media deletion, audit, backup, restore, and reprocessing behavior.

## Exit criteria

- Receipt evaluation gates pass; otherwise the feature remains explicitly
  experimental/manual and cannot be declared complete.
- Real integration/E2E tests cover clear, skewed, blurred, long, discounted,
  mixed-category, malicious-text, and mismatched receipts.
- No image auto-posts; every posted receipt has approval evidence and exact
  category/payment reconciliation.
- Images, download URLs, and embedded tokens do not appear in traces or logs.
