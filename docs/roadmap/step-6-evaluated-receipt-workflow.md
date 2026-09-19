# Step 6: Evaluated receipt workflow

Part of the [delivery roadmap](../roadmap.md).

Status: **Deferred after V1 (2026-09-19).** The owner moved this step out of
the personal V1 release into the roadmap's future developments. It is not
active and must not be started until the owner schedules it again.

## Deferral (2026-09-19)

The owner does not need receipts to start using Money Tracker and preferred a
working text-first V1 over a later V1 with receipts. [Step 7](step-7-hardening-and-release.md)
therefore follows Step 5.5 directly and releases V1 without receipt extraction.
The step keeps its number because dated evaluation records name it.

What already exists and ships in V1 unchanged:

- the private content-addressed original archive
  ([AD-014](../arc42/decisions/AD-014-private-receipt-archive.md)), reachable
  only through `money-tracker-admin receipt-store`, and its inclusion in the
  encrypted backup and restore;
- the versioned `stored` → `needs_review` → `approved` metadata review, from
  the dashboard's `/scontrini` page;
- Telegram acceptance of image metadata after owner authorization, with the
  deterministic `receipt` route answering that photographed receipts are not
  handled yet and asking for the expense in words. No image is downloaded and
  none can post a movement.

What stays deferred with this step: every deliverable below, including
Telegram and dashboard image download, extraction, line editing, receipt
preview, and media deletion. The decision in
[AD-011](../arc42/decisions/AD-011-receipt-extraction.md) is unchanged and
binds this step whenever it resumes.

Resuming the step requires the owner to schedule it, this step to move back
into the roadmap's status summary, and the
[product baseline](../product/vision-and-requirements.md#deferred-after-the-personal-v1-release)
to bring the receipt requirements back into the scope of the release that
ships them, all in the same change.

## Prerequisites

A private annotated receipt dataset exists, and Step 1 capability probes have
identified viable local OCR/VLM candidates. Because the step now follows the
personal V1 release, the released V1 from Step 7 is also a prerequisite: its
fault matrix, backup, restore, and upgrade procedures must be extended rather
than bypassed by receipt work.

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
