# AD-007: Expense and Dual-Allocation Model

- Status: accepted
- Implementation: domain, schema, expense/wallet/budget persistence, and
  deterministic reports and local source/wallet administration implemented; remaining adapters planned
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

## Step 3 reporting clarification (2026-09-08)

The existing monthly payment report exposes both funding-class totals and
individual source totals, computed from posted payment allocations with signed
refunds. Aggregating meal vouchers and welfare into one benefit total cannot
answer a question about one of those sources. These totals measure consumption,
not wallet balance; unsupported balance questions must be clarified.

## Step 5 wallet clarification (2026-09-14)

The local control plane preserves the distinction between a payment allocation
and a wallet ledger entry. A source names which resource funded an expense; a
ledger movement changes the tracked resource's balance. Source class and
`BalanceMode` are immutable after creation, and untracked labels never acquire
an inferred balance.

AD-020 defines tracked availability independently from booked balance: negative
entries consume earliest-expiring lots first and expired credit is not
spendable even while it remains in immutable booked history. The browser may
create an opening, credit, or explicit adjustment, but exposes no destructive
entry deletion, transfer, income account, bank synchronization, or general
accounting surface. Expense consumption totals in analytics remain payment-
allocation facts and are not presented as wallet balances.
