# Product Glossary

- Status: canonical
- Applies to: product docs, architecture, code, API, prompts, tests, evaluations,
  UI copy, and operations

Use these terms consistently. When Italian UI text differs, keep the domain
meaning and stable identifier unchanged.

| Term | Canonical meaning |
| --- | --- |
| Active category | A category currently available for new allocations. Historical allocations can still reference archived categories. |
| Advice | Read-only interpretation of deterministic finance data; it cannot mutate records or invent generic limits. |
| Allocation | A monetary portion of one expense. It must be qualified as category allocation or payment allocation. |
| Archived category | A historical category unavailable for new automatic assignments but still valid for existing records and reports. |
| Audit event | Immutable record of a domain mutation, owner decision, correction, or void, including source and versions. |
| Benefit | A non-personal-fund resource such as meal vouchers or welfare credit. It still counts as consumption for category budgets. |
| Budget | Monthly target attached to a category. V1 has no rollover. Italian UI: `budget`. |
| Category | Stable purpose classification at the root of the two-level taxonomy. Italian UI: `categoria`. |
| Category allocation | Portion of an expense assigned to a category or subcategory; the authoritative basis for spending analytics. |
| Category proposal | Persisted candidate awaiting owner review; it is not yet a category. Italian UI: `proposta di categoria`. |
| Command idempotency key | Stable key that makes retries and redeliveries produce at most one financial mutation. |
| Correction | Validated replacement of an existing aggregate with preserved audit history. Italian UI: `modifica`. |
| Dead letter | Durable inbound work that exhausted automatic retry and requires operator action or replay. |
| Default funding source | Configured source applied when a text expense omits payment method; always shown to the owner. |
| Draft | Incomplete or unvalidated record excluded from budgets and analytics. Italian UI: `bozza`. |
| Expense | One purchase aggregate with a total, purpose allocation, payment allocation, time, source, and status. Italian UI: `spesa`. |
| Funding class | `personal_funds`, `benefit`, or `other`; used to distinguish economic source without changing purpose. |
| Funding source | Instrument or resource used to pay, such as cash, card, meal vouchers, or welfare. Italian UI: `fonte di pagamento` or `strumento`. |
| Grounded answer | Response whose facts and figures are traceable to validated tool results and named periods/filters. |
| Inbox | Durable local record of incoming external work awaiting or recording processing. |
| Merchant | Seller/provider associated with a purchase. It is metadata, not automatically a category. Italian UI: `esercente`. |
| Needs review | Structurally retained record that cannot post until an owner resolves ambiguity or mismatch. Italian UI: `da verificare`. |
| Occurrence time | Time the purchase/refund happened; controls reporting even when processing is delayed. Italian UI: `data della spesa`. |
| Outbox | Transactionally recorded outbound response awaiting transport delivery. |
| Owner | The single paired person authorized to access V1 finance data. |
| Payment allocation | Portion of an expense paid by one funding source. |
| Pending action | Durable link from a user-facing question/callback to the exact suspended workflow. |
| Personal funds | Owner-funded resources such as cash or card, contrasted with benefits. Italian UI: `fondi personali`. |
| Posted | Validated authoritative state included in budgets and analytics. Italian UI: `registrata`. |
| Profile | Stable data and preference scope. V1 contains one owner profile. |
| Receipt | Image-backed evidence and extraction state linked to one expense draft. Italian UI: `scontrino`. |
| Receipt line | Extracted product/service line retained as detail and evidence, not an independent transaction. Italian UI: `riga dello scontrino`. |
| Refund | Negative movement that reduces spending and restores the applicable funding effect, optionally linked to an expense. Italian UI: `rimborso`. |
| Rollover | Carrying budget remainder/overspend into another month; explicitly absent in V1. |
| Root category | Top-level purpose in the taxonomy. |
| Subcategory | Optional second and final taxonomy level under one root. Italian UI: `sotto-categoria`. |
| Taxonomy | Controlled set of active and archived category nodes, aliases, and inclusion meanings. |
| Tool | Narrow typed application query exposed to an agent; never synonymous with arbitrary database access. |
| Transaction | Generic technical term for an expense/refund aggregate or a database atomic unit; qualify it when ambiguity is possible. |
| Undo | Owner action that voids a posted record while retaining audit history. Italian UI: `annulla`. |
| Void | Excluded historical record preserved for audit, not physically deleted. Italian UI: `annullata`. |
| Wallet | Funding source with ledger-based balance tracking. Italian UI: `portafoglio`. |
| Wallet entry | Opening balance, credit, debit, refund, expiry, or adjustment contributing to a tracked wallet balance. |
| Workflow checkpoint | Persisted orchestration state for pause/resume; never the authoritative finance ledger. |

## Phrases that require qualification

- `Spesa` can mean the product's expense aggregate or colloquial supermarket
  shopping. Use `expense` for the aggregate and classify supermarket products
  by purpose.
- `Saldo` is authoritative only for a funding source configured as a tracked
  wallet. Untracked card/cash sources do not claim a balance.
- `Confidenza` is an evaluated decision threshold plus deterministic evidence,
  not an uncalibrated number emitted by a model.
- `Automatico` never bypasses reconciliation, authorization, idempotency, or the
  explicit category/receipt approval rules.
