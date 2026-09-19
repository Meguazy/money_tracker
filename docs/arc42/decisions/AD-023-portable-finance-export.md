# AD-023: Versioned portable finance export

- Status: accepted
- Implementation: implemented in the second Step 7 slice
- Date: 2026-09-19

## Context

FR-OPS-004 requires a portable export of authoritative finance records before
V1 can claim data portability. The encrypted Restic snapshot is a recovery
artifact tied to PostgreSQL and the application version; it is not a format an
owner can inspect or transform with ordinary tools. A raw schema dump would
also expose persistence details, mix recovery state with domain records, and
change silently whenever a migration changes a table.

The export contains owner descriptions, merchants, notes, filenames, audit
reasons, and stable identifiers. It therefore needs the same local handling as
the ledger even though it is deliberately not encrypted by the application.

## Decision

Provide the operator-only `money-tracker-export` command. It requires an
explicit profile UUID and an absolute destination outside the checkout. The
destination must not exist, its parent must be owned by the current user with
mode 0700-equivalent privacy, and the new file is created exclusively with mode
0600 without following a final symlink. A partial file is removed if streaming
fails. The path and record content never enter the journal.

Format version one is UTF-8 JSON Lines:

1. a manifest names `money-tracker.finance`, format version, supported
   Alembic revision, export instant, profile, and per-type record counts;
2. each following line carries one stable `type` and one `data` object;
3. UUIDs are strings, dates and aware instants use ISO 8601, money remains
   integer minor units plus ISO currency, and arrays preserve order;
4. record types and rows have a fixed dependency-friendly order.

The PostgreSQL adapter uses explicit column projections and an exact supported
schema revision. It reads all projections in one repeatable-read, read-only
transaction, so the file describes one consistent database snapshot rather
than rows observed across concurrent commits. A schema change must deliberately
update the projections and either preserve format version one or introduce a
new format version.

The export includes the selected profile, categories and aliases, funding
sources, receipt metadata and content hashes, expenses and both allocation
dimensions, wallet entries, budgets and overrides, category proposals, and
immutable audit events. It excludes:

- receipt bytes, which remain in the private archive and authoritative backup;
- messaging inbox/outbox and owner binding, which are channel recovery state;
- workflow checkpoints, which are orchestration state;
- command idempotency records, which protect replay but are not portable
  finance facts;
- credentials, infrastructure configuration, logs, and traces.

The command prints only counts, format version, profile ID, byte size, and the
file SHA-256. The digest lets the owner verify a later copy but is not embedded
in the file it covers.

Historical import remains outside V1. This decision stabilizes an export
contract without claiming that another Money Tracker installation can ingest
it yet.

## Alternatives considered

- **PostgreSQL dump.** Already used for recovery, but not portable, inspectable,
  or independent of schema and extension details.
- **One CSV per table in an archive.** Broadly readable, but requires archive
  packaging and a second manifest convention, and represents arrays and nulls
  inconsistently across tools.
- **Raw table JSON from `SELECT *`.** Simple, but couples the public contract
  to column order, normalized lookup fields, and every migration.
- **Browser download.** Convenient, but widens the local web response surface
  to the complete ledger and introduces browser download-history and temporary
  file exposure. V1 keeps export operator-only.
- **Encrypt the export.** Safer at rest but creates a new key and recovery
  contract. The operator instead chooses a protected destination and may
  encrypt or move the mode-600 file with their storage tooling.

## Consequences

- The JSONL file is intentionally readable and contains owner data; it must
  never be committed, attached to support material, or left in a shared
  directory.
- Explicit projections make schema evolution deliberate and reviewable at the
  cost of updating the adapter for relevant migrations.
- Repeatable read gives a coherent snapshot without blocking ordinary finance
  writes.
- Receipt originals still require backup or a future media portability slice.
- A future import feature must validate the manifest, versions, identities,
  relationships, reconciliation, and audit semantics through a separate
  decision; it cannot replay this file directly into tables.
