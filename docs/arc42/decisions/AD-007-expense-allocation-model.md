# AD-007: Expense and Dual-Allocation Model

- Status: accepted
- Implementation: planned
- Date: 2026-09-01

## Context

A receipt is one purchase but may contain products with different purposes. A
purchase may also be paid with multiple instruments, such as meal vouchers and
a card. Treating every line as an independent expense or giving the entire
receipt one category would distort totals or hide useful information.

## Decision

Represent one expense total with two independent, reconciling dimensions:

- category allocations describe what the money bought;
- payment allocations describe which resources paid for it.

Both allocation sums must equal the expense total before posting. Ordinary text
expenses contain one category allocation; itemized receipts may contain many.
Analytics and budgets sum category allocations only, never the parent total plus
its lines.

Store EUR as integer minor units and quantities as decimals, never binary
floating-point money. Support draft, needs-review, posted, and voided states.
Model refunds as negative movements, optionally linked to an original expense.

Funding sources may be untracked labels or lightweight balance-tracked wallets.
Benefit credits may carry an expiry. Budgets include all sources but reports
separate personal funds from benefits.

## Alternatives considered

- Independent transactions per receipt line make correction and receipt-level
  payment reconciliation harder.
- A single receipt category cannot describe mixed supermarket purchases.
- A general double-entry accounting engine now would exceed the initial scope.

## Consequences

- Posting and correction services must enforce two reconciliation invariants.
- Full accounting remains possible later but will require an explicit migration
  rather than being prematurely embedded in the V1 domain.
- Corrections and voids require an audit trail instead of destructive deletion.
