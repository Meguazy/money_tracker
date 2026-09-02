# AD-014: Private Content-Addressed Receipt Archive

- Status: accepted
- Implementation: local adapter and filesystem/PostgreSQL integration implemented;
  runtime wiring, deletion, and backup planned
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
by profile and digest. No product deletion API exists until audit, references,
and backup-retention semantics are implemented together.

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
  detection using real filesystem and PostgreSQL protocols.
