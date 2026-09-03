# Step 2: Finance domain and persistence

Part of the [delivery roadmap](../roadmap.md).

Status: **Complete (2026-09-02).** All four exit criteria have evidence below.
The framework-independent domain, initial taxonomy, finance schema,
expense/wallet/budget repositories, pending category-proposal submission,
audit, idempotency, and concurrency foundation are implemented, together with
the private content-addressed receipt-media adapter, versioned receipt
review-state transitions, strict V1 finance wire contracts, a minimal local
administration command, and verified encrypted backup with an isolated restore
drill.

Work this step deliberately does not contain, with its owning step: the FastAPI
listener and HTTP security belong to Step 5, proposal decisions and the owner
review workflow to Step 3, worker media wiring to Steps 3 and 4, receipt
extraction and media deletion to Step 6, and backup scheduling plus the
clean-machine restore drill to Step 7. Step 2 fixes their contracts without
shipping their behavior.

## Deliverables

- Framework-independent money, profile, expense/refund, dual allocation,
  category, proposal, funding source/wallet, monthly budget, receipt metadata,
  and audit contracts.
- Transactional application services and PostgreSQL repositories with
  idempotency and optimistic concurrency.
- Seed the accepted 10-root/14-child taxonomy with stable identifiers and
  inclusion descriptions.
- Implement posted/draft/needs-review/voided lifecycle, correction, undo, parent
  roll-ups, funding-class split, and benefit-wallet entries.
- Versioned `/api/v1` finance contracts and a minimal administrative CLI for
  development/bootstrap; no claim of final UI.
- Content-addressed receipt-media port and safe local implementation, without
  extraction.
- Restic backup implementation for the new authoritative data and a preliminary
  restore test before real personal data is encouraged.

## Exit criteria

- Domain invariant tests cover minor units, both allocation sums, refunds,
  wallet behavior, budget calendar rules, taxonomy constraints, audit, and
  idempotency.
- Real PostgreSQL integration tests cover transactions, constraints,
  migrations, concurrent version conflicts, and restart persistence.
- API contract tests demonstrate stable IDs, ISO timestamps, currency fields,
  problem responses, and required mutation idempotency keys.
- Backup and restore reconstruct a non-production isolated finance fixture and
  its media references.

## Completion evidence

- Pure production contracts cover EUR minor units, profile scope,
  expense/refund polarity, dual allocations, lifecycle, correction/void,
  category proposals, funding sources and wallet entries, Rome-calendar
  budgets, receipt metadata, audit, roll-ups, and funding-class totals.
- The framework-independent `api.contracts` component fixes the `/api/v1`
  expense request/response, exact EUR money, stable UUID, aware ISO timestamp,
  problem-detail, and canonical `Idempotency-Key` shapes. It exposes no HTTP
  listener and accepts no caller-controlled profile scope.
- The accepted taxonomy is code-owned and versioned with deterministic IDs,
  ten roots, fourteen children, inclusion descriptions, two-level validation,
  normalized aliases, and soft complexity warnings.
- Alembic creates authoritative finance tables, deferred reconciliation and
  profile/reference checks, append-only audit/wallet history, idempotency
  records, and guarded non-destructive rollback behavior.
- The PostgreSQL repository bootstraps the profile/taxonomy, reconstructs and
  mutates complete expense and budget aggregates, and appends balance-safe
  wallet entries. Expense and budget mutations use optimistic version checks;
  ledger debits serialize on the funding source so concurrent writers cannot
  overdraw it. Every mutation stores audit in the same transaction.
- Pending category proposals validate their parent, alternatives, and normalized
  name against the active profile taxonomy, then persist with audit in one
  transaction. Owner resolution and category creation remain Step 3 workflow
  work and cannot be pre-resolved by the submission service.
- Receipt metadata registration accepts only initial `stored` records, uses the
  profile/content SHA-256 pair as natural idempotency, rejects conflicting
  size/type claims, and audits only the first insert. It stores no media bytes or
  filesystem paths. The versioned lifecycle permits only `stored` to
  `needs_review` to `approved`, requires the mandatory review step, and
  commits each transition with audit under optimistic concurrency. Deletion
  remains unavailable pending reference and backup-retention semantics.
- The local receipt-media port fully decodes single-frame JPEG/PNG/WebP under
  20 MiB, 40-megapixel, and 10,000-pixel-side limits. It stores originals under
  private sharded SHA-256 paths with atomic no-overwrite writes, verified reads,
  symlink refusal, and tamper detection. Runtime entry-point wiring, deletion,
  and encrypted backup remain open outside the local administration CLI.
- `money-tracker-admin receipt-store` requires an active profile and absolute
  configured archive root, refuses symlink/non-file/oversized inputs, then
  stores and registers metadata idempotently. Its JSON output omits private
  paths.
- `money-tracker-backup` implements the runbook sequence: exclusive host lock,
  database-identity and receipt-hash preflight, a mode-600 `pg_dump` and
  versioned integrity manifest in private mode-700 staging, one tagged Restic
  snapshot of dump, receipt archive, and runtime configuration, repository and
  exact-snapshot verification before staging removal, then seven daily/five
  weekly/twelve monthly retention grouped by host and tag so per-run staging
  paths cannot defeat the policy. `status` reports the newest snapshot and its
  age. Credentials reach subprocesses only through libpq environment variables
  and the Restic password-file path; failures name the failing executable alone.
- `restore-drill` reconstructs a snapshot only into an explicitly named empty
  isolated database and an absent or empty real receipt directory. It requires
  exactly one compatible manifest, verifies dump, configuration, and every
  referenced receipt hash in staging, refuses symlinked restored paths, imports
  with `pg_restore --exit-on-error`, and re-verifies each receipt hash at the
  destination. Backup and restore are unavailable, not silently degraded, when
  Restic or matching PostgreSQL client tools are absent.
- Forty-three domain tests, twenty-nine wallet/budget/proposal/receipt
  application tests, twenty-five media/configuration/administration tests, and
  twenty-three backup safety/CLI tests pass.
  Sixteen strict API contract tests cover the version-one wire boundary at
  100% branch coverage.
  Thirty-seven real PostgreSQL/media integration tests cover migration, full
  storage/metadata restart, receipt review lifecycle, concurrent registration
  and version conflicts, receipt audit rollback, proposal scoping and atomic
  rollback, constraints, restart persistence, immutable history, exact taxonomy
  seeding, retry idempotency, concurrent expense writers, concurrent wallet
  debits, and concurrent/stale budget versions. Two real Restic drills
  reconstruct the isolated finance fixture and its receipt media through the
  service and through the operator CLI, and assert one retention group across
  runs, snapshot freshness, empty-target refusal, and redacted output. The
  complete service-independent suite passes with 151 tests and 42 explicit
  real-service skips; focused domain/application/media/api branch coverage is
  91% and backup branch coverage is 87% with the real drills.

Exit-criteria evidence:

| Exit criterion | Evidence |
| --- | --- |
| Domain invariant tests | [`tests/unit/money_tracker/domain`](../../tests/unit/money_tracker/domain) and [`tests/unit/money_tracker/application`](../../tests/unit/money_tracker/application) |
| Real PostgreSQL integration tests | [`tests/integration/money_tracker/persistence`](../../tests/integration/money_tracker/persistence) and [`tests/integration/money_tracker/media`](../../tests/integration/money_tracker/media) |
| API contract tests | [`tests/unit/money_tracker/api`](../../tests/unit/money_tracker/api) |
| Backup and restore reconstruct an isolated fixture | [`tests/integration/money_tracker/backup`](../../tests/integration/money_tracker/backup) and [Backup and Restore](../operations/backup-and-restore.md) |

The real Restic and PostgreSQL 17 client executables are host prerequisites, not
vendored dependencies. The drill was verified with Restic 0.18.1 and
`pg_dump`/`pg_restore` 17.11 against the pinned PostgreSQL 17 service; the
backup runbook records how to install them.
