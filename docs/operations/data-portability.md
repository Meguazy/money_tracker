# Data Portability

- Status: finance export implemented in the second Step 7 slice
- Decision:
  [AD-023](../arc42/decisions/AD-023-portable-finance-export.md)
- Entry point: `money-tracker-export`

The finance export is a portable, readable copy of one profile's authoritative
finance records. It complements the encrypted backup; it does not replace
backup, restore, or receipt-media retention, and V1 does not import it.

## Privacy boundary

The file contains owner descriptions, merchants, wallet notes, receipt
filenames, proposals, audit reasons, and monetary amounts. It is not encrypted.
Create it only in an existing directory owned by the current user and
inaccessible to group and others:

    install -d -m 700 ~/private/money-tracker-exports

The command refuses a relative path, an existing path, a path inside the
checkout, a missing/non-directory parent, a symlink at the final path, or a
parent with any group/other permission. It creates the file mode 0600 and
removes a partial file if writing fails. Never commit, email, attach, or paste
the export into support material or an AI conversation.

## Creating an export

Use the stable profile UUID shown by the trusted administration setup output:

    uv run --env-file .env money-tracker-export \
      --profile-id PROFILE_UUID \
      --output ~/private/money-tracker-exports/finance-$(date +%F).jsonl

The output path must be new. A successful command prints safe JSON containing
the profile ID, format version, record counts, byte count, and SHA-256. It
prints neither the destination nor record content. The journal likewise
contains only the profile ID, version, counts, size, and stable failure codes.

Verify a copied file against the reported digest:

    sha256sum ~/private/money-tracker-exports/finance-YYYY-MM-DD.jsonl

Moving or encrypting the file after creation is an owner storage decision.
Deleting the export does not delete application records.

## Version-one contract

The file is UTF-8 JSON Lines. The first line is a manifest:

    {"database_revision":"REVISION","exported_at":"INSTANT","format":"money-tracker.finance","format_version":1,"profile_id":"UUID","record_counts":{"profile":1},"type":"manifest"}

Every other line has a stable record type and data object:

    {"data":{"amount_minor":1299,"currency":"EUR","id":"UUID"},"type":"expense"}

The complete type order is:

1. `profile`;
2. `category`;
3. `category_alias`;
4. `funding_source`;
5. `receipt`;
6. `expense`;
7. `category_allocation`;
8. `payment_allocation`;
9. `wallet_entry`;
10. `monthly_budget`;
11. `monthly_budget_override`;
12. `category_proposal`;
13. `audit_event`.

Within each type, rows use deterministic identifiers or occurrence order.
UUIDs are lowercase canonical strings. Dates and aware instants use ISO 8601.
Amounts remain signed integer minor units and always carry their ISO currency.
Null stays JSON `null`; arrays stay JSON arrays. Text remains exact owner
content and is not normalized for presentation.

The manifest's record counts cover data lines, not the manifest itself. The
command reads all types in one PostgreSQL repeatable-read, read-only
transaction and refuses an unknown schema revision. A migration therefore
cannot silently change the meaning of format version one.

## Included and excluded state

Included:

- profile preferences and default funding-source reference;
- taxonomy nodes and aliases;
- funding sources and immutable wallet entries;
- expenses/refunds with category and payment allocations;
- budgets and month overrides;
- category proposals and audit history;
- receipt metadata and content hashes.

Excluded:

- original receipt bytes;
- Telegram inbox, outbox, pairing, and owner binding;
- workflow checkpoints;
- command idempotency records;
- environment files, credentials, logs, traces, and evaluation artifacts.

Use the encrypted authoritative backup to recover operational continuity and
retained media. The export exists for inspection and transfer into independent
tools. Historical import remains outside V1.

## Failure handling

Exit code 1 means the request, destination, profile, or schema was refused.
| `export_write_failed` | Check destination filesystem health and free space, then retry with a new filename. |
| `export_cleanup_failed` | Protect and remove the partial mode-600 file manually before investigating the filesystem. |
Exit code 2 means PostgreSQL or output writing failed transiently. The journal event
`finance_export.refused` carries the safe reason code; an unexpected database
failure also records `finance_export.database.failed` with a redacted
traceback. No failure overwrites an existing file.

Common reasons:

| Reason | Action |
| --- | --- |
| `export_parent_not_private` | Set the destination directory to mode 0700 and confirm ownership. |
| `export_inside_repository` | Choose a private directory outside the checkout. |
| `export_path_exists` | Choose a new filename; the command never overwrites. |
| `profile_not_found` | Recheck the trusted profile UUID. |
| `database_schema_unsupported` | Use the application version matching the database, or complete the documented upgrade. |
| `database_failed` | Check application PostgreSQL health without printing the URL. |

## Verification

Unit coverage validates path refusal, type conversion, and CLI arguments. The
real PostgreSQL integration test migrates an isolated database, writes finance
state through the production repository, exports it through both the adapter
and CLI, and verifies permissions, digest, manifest, type counts, owner content,
and content-free diagnostics:

    RUN_POSTGRES_INTEGRATION_TESTS=1 uv run --env-file .env pytest -q \
      tests/integration/money_tracker/persistence/test_export.py
