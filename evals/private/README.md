# Private Evaluation Data

Everything placed beside this README is ignored by Git. Store private messages,
receipts, annotations, and run manifests only on the local workstation with
owner-only permissions.

Committed reports may reference a private dataset only by a safe content hash,
version, aggregate case count, and non-identifying tags. They must not copy raw
inputs, merchant identifiers, Telegram identifiers, local absolute paths,
tokens, signed URLs, or image bytes.

Use a separate immutable holdout and follow the experiment lifecycle in the
[evaluation documentation](../../docs/experiments/README.md).
