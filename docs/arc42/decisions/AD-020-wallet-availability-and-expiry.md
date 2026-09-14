# AD-020: Wallet Availability and Expiry Projection

- Status: accepted
- Implementation: domain projection, source administration, PostgreSQL locking,
  local API, and React control-plane surface implemented
- Date: 2026-09-14
- Extends: [AD-005](AD-005-application-postgresql.md),
  [AD-007](AD-007-expense-allocation-model.md), and
  [AD-009](AD-009-local-web-application.md)

## Context

AD-007 permits lightweight balance-tracked wallets and expiring benefit credit,
but it did not decide how a debit is assigned when several credit lots have
different expiry dates. A sum of immutable entries is a useful booked balance,
yet it overstates what can still be spent after a credit expires. Silently
writing expiry rows from a read would make a projection mutate authoritative
history, while choosing a lot differently on different paths would make the
same ledger produce conflicting balances.

The V1 control plane also needs to archive sources and accept manual credits or
adjustments without allowing a stale browser snapshot or concurrent append to
produce an invalid tracked balance.

## Decision

Keep wallet entries immutable and derive two distinct values at an explicit
observation instant:

- `booked_balance` is the exact signed sum of every entry effective by that
  instant;
- `available_balance` is the remaining non-expired credit after deterministic
  consumption.

Opening and credit entries create positive lots and may carry `expires_on`.
Refunds and positive adjustments create undated value. Debits consume only
currently usable value in first-expiry-first order, with undated value last.
Expiry entries and negative adjustments write down booked lots in the same
order, including already expired value, but can never make the booked balance
negative. Credit is usable through its `expires_on` date in the
`Europe/Rome` calendar and becomes unavailable on the next Rome day. Reads do
not synthesize an expiry entry or rewrite booked history.

A debit that consumes more than was available at its effective time is
invalid and refused. A write-down that exceeds the remaining booked value is
likewise refused; reducing expired booked value never restores availability.
Manual browser entries cannot occur in the future, and opening is allowed only as the first entry. An untracked funding
source has no authoritative booked or available balance.

Serialize every wallet append on its funding-source row. Serialize owner source
lifecycle/default changes on the profile row and the affected source rows.
Archiving a ledger source requires both booked and available balances to be
zero and rechecks them inside the same transaction and lock boundary as the
state replacement, command claim, and audit. The current default can never be
archived; changing it advances both source versions and the profile pointer in
one transaction.

## Alternatives considered

- **Use booked balance as availability.** Rejected because expired benefit
  credit would remain spendable and a negative adjustment could create an
  economically impossible state.
- **Consume oldest-created credit first.** Deterministic, but it can leave an
  earlier-expiring lot unused while consuming later value. First-expiry-first
  minimizes avoidable expiry and matches the owner's actionable balance.
- **Consume the latest expiry first.** Rejected because it systematically
  maximizes value at risk of expiry.
- **Materialize automatic expiry entries during reads or with a timer.**
  Rejected for V1 because reads must not write, a scheduler is not yet an
  accepted core dependency, and the immutable ledger already contains enough
  facts for a deterministic projection.
- **Introduce general accounts, transfers, or bank synchronization.** Rejected
  as outside the V1 lightweight-wallet scope in AD-007.

## Consequences

- Booked and available balances may differ legitimately and every UI or advice
  surface must label which one it presents.
- The projection must receive complete ordered history and an aware observation
  instant; tests must include Rome day boundaries and expired-lot overdrafts.
- A source with expired booked value cannot be archived until an explicit
  immutable expiry or adjustment brings booked value to zero. No destructive
  cleanup is permitted.
- Any future automatic expense-to-wallet posting must reuse this projection and
  lock boundary rather than implement a second balance policy.
- No model-visible tool contract changes in this decision. Exposing additional
  wallet facts to a model remains evaluation-gated by AD-012.

## Implementation evidence

`money_tracker.domain.funding.wallet_availability` implements the pure
first-expiry-first projection and returns booked, available, and dated remaining
lots. `FundingSourceAdministrationService` separates versioned source lifecycle
from immutable wallet append commands. `PostgresFinanceRepository` claims
idempotency keys and commits source changes or wallet entries with audit rows;
it locks the profile/source rows, validates availability, and rechecks a zero
balance during archive. Strict FastAPI resources bind the trusted profile and
the React `/fonti` page exposes labelled state and bounded manual operations.
Unit, HTTP, component, and real-PostgreSQL integration tests are recorded in
[Step 5](../../roadmap/step-5-dashboard-and-advice.md).
