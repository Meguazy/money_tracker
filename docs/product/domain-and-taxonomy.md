# Domain and Taxonomy

- Status: accepted V1 domain baseline
- Implementation: core domain contracts, stable taxonomy seed, finance schema,
  and expense/wallet/budget persistence implemented; remaining Step 2 adapters
  planned

This document defines business semantics. Database tables, ORM classes, API
wire shapes, and graph state may differ internally, but they must preserve these
concepts and invariants.

## Core distinction: purpose versus funding

Every expense answers two independent questions:

1. **What was purchased?** Category allocations classify spending purpose.
2. **Which resources paid for it?** Payment allocations identify funding
   sources such as cash, card, meal vouchers, or welfare.

A supermarket is a merchant/context, not a spending purpose. A EUR 50.00 receipt
containing food, toilet paper, and cat food remains one purchase but may have
three purpose allocations. It may independently have EUR 30.00 of meal vouchers
and EUR 20.00 paid by card.

## Money and time

- Store money as integer minor units plus a three-letter ISO currency code.
- EUR is the only permitted operational V1 currency.
- Never use binary floating point for storage, domain arithmetic, tool
  arguments/results, or REST payloads.
- Store instants with timezone. Resolve relative dates from source-message time
  and report in `Europe/Rome`.
- Weeks start Monday. Monthly budgets follow Rome calendar-month boundaries.

## Aggregate concepts

### Profile

Represents the owner scope and preferences: locale, timezone, currency,
Telegram identity after pairing, and default funding source. V1 has exactly one
active profile; records still carry its stable ID.

### Expense

One economic purchase or refund with stable ID, positive purchase total or
negative refund amount, occurrence time, optional merchant and description,
source metadata, version, and status.

Statuses:

- `draft`: incomplete or not yet validated;
- `needs_review`: structurally valid but awaiting owner resolution;
- `posted`: authoritative and included in analytics/budgets;
- `voided`: retained in history but excluded from current totals.

A refund may reference the original expense. Unlinked refunds still require a
purpose and funding source so reports remain coherent.

### Category allocation

Assigns part of an expense amount to one active category node. A normal text
expense generally has one allocation; a mixed receipt has multiple. An
allocation may reference a receipt line and records classification provenance,
confidence/evidence, and owner overrides.

Required invariant:

```text
sum(category allocation amounts) == expense total
```

Analytics and budgets sum these allocations. They never add receipt lines or
the expense parent again.

### Payment allocation

Assigns part of the same expense to a funding source. It records the resource
used without changing spending purpose.

Required invariant:

```text
sum(payment allocation amounts) == expense total
```

Category and payment dimensions must both reconcile before posting.

### Funding source and wallet entry

A funding source has a stable name, class, status, default flag, and balance
tracking mode.

Funding classes:

- `personal_funds`: cash, bank card, or another personally funded instrument;
- `benefit`: meal vouchers, welfare, or another restricted employer/public
  benefit;
- `other`: future non-personal resources that do not fit the prior classes.

Balance modes:

- `untracked`: the source labels payments but Money Tracker does not claim its
  balance is authoritative;
- `ledger`: balance derives from opening, credit, debit, refund, expiry, and
  manual adjustment entries.

Benefit credits may have expiry dates. Available balance excludes expired
credit. The exact lot-consumption policy must be deterministic and documented
when implemented; until then no implementation may claim expiry-aware balance.

### Receipt and receipt line

A receipt references one private original media object, its content hash,
source, extraction version, OCR/VLM evidence, processing status, and the expense
draft it supports. Receipt lines retain original and normalized descriptions,
quantity, unit price when available, line total, discounts, and review state.

Receipt lines are evidence/detail, not independent transactions. Global
discount and rounding allocation must be deterministic. Any unexplained
difference remains visible and blocks posting.

### Category

A category has stable ID, unique normalized active name, optional parent,
description/inclusion criteria, aliases, provenance, status, and audit history.
Maximum depth is two levels. A parent and child are both assignable; a direct
parent allocation appears in drill-down as `Senza sottocategoria`.

Used categories may be renamed or archived. Hard deletion and silent reparenting
are prohibited because they would erase or rewrite historical meaning.

### Category proposal

Represents an auditable, not-yet-created candidate with proposed name, parent,
rationale, alternatives, excluded prior candidates, source draft/workflow, and
review status. Approval creates the category through a deterministic service;
the model never inserts a category directly.

### Monthly budget

A budget is a recurring monthly EUR target for a category, optionally overridden
for one calendar month. V1 has no rollover.

```text
remaining = effective monthly target - posted category spend
```

Parent spend includes direct and descendant allocations. Parent and child
targets are independent comparisons and must not be summed into a fabricated
combined budget. Draft/needs-review amounts are shown separately.

Every funding class counts toward purpose consumption. Reports additionally
show how much was paid with personal funds and benefits.

### Audit event

Records who/what performed a mutation, source command, prior/current version,
reason, time, and affected stable IDs. It supports correction, owner override,
proposal decisions, voiding, and operational investigation. It is not a prompt
or trace dump.

## Initial category hierarchy

The seed follows household-consumption purposes and deliberately starts small:
10 roots and 14 high-value children, 24 active nodes in total.

Its reference basis is the household-purpose structure in
[Eurostat COICOP 2018](https://ec.europa.eu/eurostat/cache/metadata/en/nama_10_cp18_esms.htm)
and the practical Italian expense groupings in the
[Banca d'Italia budget calculator](https://economiapertutti.bancaditalia.it/strumenti/calcolatori/calcolatore-del-budget/).
Those sources inform the starting vocabulary; the product rules in this
document remain authoritative for Money Tracker.

| Root category | Initial children | Inclusion guidance |
| --- | --- | --- |
| Alimentazione | Spesa alimentare; Bar, ristoranti e asporto | Food and non-alcoholic household consumption; prepared food and dining remain distinguishable. |
| Casa e utenze | Abitazione e condominio; Bollette e comunicazioni; Prodotti casa, arredo e manutenzione | Housing, utilities/connectivity, cleaning consumables, furniture and household upkeep. |
| Mobilità | Trasporto pubblico; Auto e moto | Personal transport, fares, fuel, parking, servicing and vehicle costs. |
| Salute e cura personale | Farmaci e prestazioni sanitarie; Cura personale | Medical treatment/products versus hygiene and personal care. |
| Animali | Cibo e accessori; Veterinario | Pet supplies versus veterinary care. |
| Tempo libero | Abbonamenti; Sport, hobby e cultura; Viaggi e alloggi | Recurring entertainment/digital services, leisure activities, and travel. |
| Acquisti personali | None | Clothing, footwear, electronics and other personal goods not covered elsewhere. |
| Formazione | None | Courses, books and education costs whose primary purpose is learning. |
| Regali e donazioni | None | Gifts, charitable donations and support given to others. |
| Finanza e obblighi | None | Insurance, taxes, fees, interest and unavoidable financial charges. |

Canonical examples:

| Description | Category |
| --- | --- |
| Netflix | Tempo libero / Abbonamenti |
| Cat food | Animali / Cibo e accessori |
| Shoes | Acquisti personali |
| Peaches | Alimentazione / Spesa alimentare |
| Toilet paper | Casa e utenze / Prodotti casa, arredo e manutenzione |

There is no active catch-all `Altro`. An unresolved draft is safer and more
visible than an apparently valid miscellaneous allocation.

## Controlled taxonomy growth

A proposed node must satisfy all of these criteria:

- no existing node or alias adequately represents the purpose;
- the proposal represents a reusable purpose, not a merchant, brand, trip,
  payment method, or one-off description;
- the distinction can change a budget, report, or spending decision;
- a child is preferred when an existing root already covers the broader purpose;
- the name is normalized and non-duplicative;
- the owner explicitly approves it.

Soft guardrails warn above 30 active nodes or more than five children under one
parent and suggest reuse, rename, archive, or merge review. They do not
automatically mutate the taxonomy.

`Proponine un'altra` excludes rejected candidates in the active review. After
three rejections, the system presents active choices and manual entry rather
than continuing an unbounded model loop.

## Posting and correction rules

A transaction can become posted only when:

- money, currency, occurrence time, and profile are valid;
- category and payment allocations each reconcile exactly;
- every referenced category and funding source is active and authorized;
- any tracked wallet constraint is satisfied;
- receipt drafts, when present, have explicit owner approval;
- the command idempotency key has not already produced the mutation.

Clear text expenses using existing categories may post automatically only in an
evaluation-calibrated high-precision subset. Every automatic post exposes modify
and undo. Category creation and receipt posting always require explicit owner
action.

Corrections replace the validated aggregate under optimistic concurrency and
append audit history. Undo changes status to `voided`; no referenced financial
record is physically deleted through normal product behavior.

## Future accounting compatibility

V1 intentionally avoids a general double-entry ledger. Stable profile,
transaction, funding-source, allocation, and audit boundaries reduce migration
risk, but future income, assets, liabilities, transfers, and reconciliation will
require a dedicated product decision and migration rather than reinterpretation
of V1 data.
