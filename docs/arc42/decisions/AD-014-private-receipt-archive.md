# AD-014: Private Content-Addressed Receipt Archive

- Status: accepted
- Implementation: local adapter, administration CLI, filesystem/PostgreSQL
  integration, encrypted archive backup/restore, replay-safe versioned
  receipt-review transitions, and browser metadata review implemented;
  API/worker media wiring and deletion planned for roadmap Step 6, deferred
  after the personal V1 release on 2026-09-19
- Date: 2026-09-02

## Context

Original receipt images are untrusted, private, authoritative inputs. Keeping
them under Telegram filenames, inside a web root, or without decoded-image
limits would allow path confusion, format spoofing, decompression denial of
service, and accidental disclosure. Database metadata must also survive retries
and point to exactly the bytes retained on disk.

## Decision

Expose a narrow receipt-media port that accepts bytes and returns only safe
derived facts: SHA-256, detected MIME type, exact byte size, width, and height.
Use Pillow 12.x as the pinned decoder and accept only fully decodable,
single-frame JPEG, PNG, and WebP originals.

Apply these V1 limits before persistence:

- at most 20 MiB encoded bytes;
- at most 40,000,000 decoded pixels;
- at most 10,000 pixels on either side;
- no animation or multi-frame input.

Store files outside presentation roots as
`<archive>/<sha256[0:2]>/<sha256>`. The archive and shard modes are `0700`; file
mode is `0600`. Writes fsync a temporary file, create the final key with an
atomic no-overwrite hard link, fsync the shard, and remove the temporary name.
Concurrent identical writes converge on one file. Reads accept only a lowercase
SHA-256 key, do not follow final symlinks, enforce the byte limit again, and
verify the digest before returning content.

The media store does not create database rows. After storage succeeds, the
receipt-metadata application service registers the returned facts idempotently
by profile and digest. Metadata then advances only from `stored` to
`needs_review` to `approved`, with mandatory review, optimistic concurrency,
and an atomic audit for every transition. No product deletion API exists until
audit, references, and backup-retention semantics are implemented together.

Each transition is a command in its own right, not a property of the receipt.
It claims the general command record described in
[AD-005](AD-005-application-postgresql.md) under the key
`receipt-review:{receipt_id}:{expected_version}` in the same transaction as the
state change and its audit, so a retried decision replays its first result
while a different decision taken from the same observed version conflicts. The
control plane may read this metadata and complete the review; it may not read,
serve, or delete an original, and the content digest is not exposed to the
browser because it is the archive storage key.

The local administration CLI wires these boundaries for a profile-scoped
manual import. It requires an explicitly configured absolute archive root,
preflights the active profile, refuses final input symlinks and oversized files,
and prints no private paths.

## Alternatives considered

- Trusting a declared content type or magic bytes is smaller but does not prove
  that the complete image decodes safely.
- PostgreSQL binary objects would merge media and transactional lifecycles,
  enlarge dumps, and weaken the explicit filesystem/backup boundary.
- User or Telegram filenames are useful display metadata but unsafe storage
  keys.
- Overwriting a digest path simplifies writes but hides corruption and creates
  unnecessary replacement races.
- Cloud object storage conflicts with the accepted local-first privacy boundary.

## Consequences

- Pillow is a direct locked runtime dependency and requires controlled security
  upgrades.
- The archive is authoritative and must be included in encrypted backup and
  restore integrity checks before personal media is encouraged.
- EXIF remains in the private original. Future derivatives must strip it and
  require a separate contract.
- Tests cover supported formats, full decode failure, byte/dimension limits,
  permissions, concurrent idempotency, restart, symlink refusal, and tamper
  detection plus migration, concurrent review transitions, replayed and
  conflicting review commands, and audit rollback using real filesystem and
  PostgreSQL protocols.

## Implementation evidence

The Step 5 `/scontrini` slice exposes this metadata to the owner for the first
time. `ReceiptMetadataService.stored_receipts` lists the retained archive
newest first, and the strict `GET /api/v1/receipts` response carries display
filename, decoded media type, exact byte size, storage instant, review state,
and optimistic version. It carries no content digest, profile identity, or
source reference, and no route returns or links to an original.
`ReceiptReviewMutationAdapter` reaches only that listing and the two transition
use cases, so the browser object graph contains no media store, extractor, or
deletion path.

The two transitions became replay-safe in the same change. A repeat is resolved
before the current row is read, the domain still decides which transition is
legal, and a failed audit leaves neither a state change nor a command claim.
Real PostgreSQL tests cover the HTTP listing, an opened and replayed review, a
refused approval of a receipt that never entered review, an approval, a stale
version, an unknown receipt, a non-canonical command key, and the terminal
states read back through the production repository.
