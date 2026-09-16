# User Journeys

- Status: accepted V1 behavior
- Implementation: planned

These journeys define observable behavior independent of a particular prompt,
model, database schema, or UI component. Italian phrases are canonical examples
for tests and evaluation datasets, not hard-coded command syntax.

## J1: Initial local and Telegram setup

**Preconditions:** the scaffold is installed; the future setup components are
implemented; the owner has Telegram.

1. The owner creates a bot through the official `@BotFather` `/newbot` flow and
   stores its token in the private local environment file.
2. A local setup command validates the token without printing it and creates a
   short-lived, one-use pairing code.
3. The owner sends `/pair <codice>` in a private bot chat.
4. The application stores the Telegram user/chat IDs as the sole owner and
   invalidates the code.
5. The owner configures at least one funding source, selects the default, and
   optionally establishes meal-voucher or welfare balances.
6. The dashboard and bot show healthy core services without requiring Langfuse.

**Alternatives:** invalid/expired codes reveal no finance state; a second owner
is rejected; a compromised bot token is revoked in BotFather and replaced
locally.

## J2: Clear text expense posts automatically

**Input:** `Ho appena speso 12 euro per i croccantini dei gatti`.

1. The router selects expense ingestion.
2. Extraction obtains EUR 12.00, the source-message occurrence time, and the
   description.
3. The configured default funding source fills the omitted payment method.
4. Category resolution selects `Animali / Cibo e accessori` with sufficient
   evaluated confidence.
5. Both category and payment allocations reconcile to EUR 12.00.
6. The financial record, audit entry, and outbound response are committed once.
7. Telegram shows amount, date, category, source, and, while the movement is
   still a draft, `Registra`/`Modifica`/`Annulla`. Nothing counts toward
   analytics or budgets until the owner records it.

**Postcondition:** one posted expense affects analytics, budget, and the chosen
funding source. Retrying the same update changes nothing.

**Alternative:** missing or ambiguous amount/date/category produces a durable
draft and one targeted question instead of a write.

## J3: Category proposal and all owner decisions

**Precondition:** an extracted expense does not adequately fit the active
taxonomy.

1. The system searches stable IDs, names, aliases, inclusion criteria, and
   nearby existing nodes.
2. It persists a proposed name, parent, rationale, alternatives, and source
   workflow, then pauses without creating a category or posting the expense.
3. Telegram displays the proposal and supported actions.

Branches:

- `Approva`: create the node and post the expense atomically.
- `Usa una delle categorie esistenti`: use the best adequate existing candidate
  and record that the owner forced reuse.
- `Usa la categoria X`: resolve X to a stable active ID; ask for disambiguation
  if necessary.
- `Proponine un'altra`: reject/exclude the current candidate and generate a
  distinct proposal.
- `Annulla`: cancel the draft with no category or expense creation.
- Unrelated message: process it independently and keep the proposal pending.

After three rejected proposals, the bot displays existing candidates and manual
category entry instead of continuing an unbounded proposal loop.

## J4: Split funding and benefit balance

**Input:** `Ho speso 50 euro al supermercato, 30 con i buoni pasto e 20 con la carta`.

1. The system creates one purchase draft for EUR 50.00.
2. It creates payment allocations of EUR 30.00 to the meal-voucher wallet and
   EUR 20.00 to the card funding source.
3. If the benefit wallet tracks balance, the planned debit is validated against
   its available, non-expired balance.
4. Purpose/category allocations are resolved independently from payment.
5. Posting applies both reconciled dimensions atomically.
6. The response and dashboard distinguish total consumption, EUR 20.00 personal
   funds, and EUR 30.00 benefits.

**Alternatives:** payment portions that do not sum to the total or exceed a
tracked benefit balance require correction; they never post partially.

## J5: Benefit credit

**Action:** the owner records a meal-voucher or welfare credit through the
dashboard in V1.

1. The owner selects the wallet, amount, credit date, and optional expiry.
2. The application validates EUR minor units and expiry chronology.
3. It records an immutable wallet entry and audit event.
4. Available and expiring balances update without being classified as income.

Natural-language wallet credits are deferred until a later explicit scope
decision; the V1 router may direct the owner to the dashboard.

## J6: Receipt ingestion and review

**Input:** a Telegram image containing a mixed supermarket receipt, optionally
captioned with its funding source.

1. The Telegram update is durably stored before its offset is acknowledged.
2. The image is downloaded, validated, hashed, and stored privately.
3. The promoted local extractor creates merchant/date/total/line candidates and
   evidence without treating receipt text as instructions.
4. Product lines are allocated by purpose, for example peaches to
   `Alimentazione / Spesa alimentare`, toilet paper to
   `Casa e utenze / Prodotti casa, arredo e manutenzione`, and cat food to
   `Animali / Cibo e accessori`.
5. Discounts, rounding, category totals, and payment allocations are reconciled.
6. Telegram sends a compact preview; the dashboard exposes full line editing.
7. The owner approves, corrects, or cancels.

**Postcondition:** approval creates one posted expense and its line/allocation
detail. The original image remains until manual deletion.

**Alternatives:** unexplained totals, low-confidence lines, or missing funding
stay in review. No receipt auto-posts in V1.

## J7: Ask for spending or remaining budget

**Inputs:** `Quanto ho speso per gli abbonamenti questo mese?` or
`Quanto budget mi rimane per gli abbonamenti?`.

1. The router selects a read-only spending or budget route.
2. The agent resolves the category to a stable ID and calls a narrow read tool.
3. The service computes posted allocations in the requested calendar period.
4. The answer reports period, amount, relevant budget, remaining/overspent
   value, and personal-fund versus benefit split when useful.

**Alternatives:** a missing category or budget is stated plainly; no figure is
guessed and no write tool is available.

## J8: Ask where spending is excessive

**Input:** `Per cosa sto spendendo troppo questa settimana?`.

1. The advice workflow obtains applicable budgets and comparable personal
   history through read-only tools.
2. It reports exceeded or at-risk budgets first.
3. It then highlights material deviations from equivalent prior periods, with
   the comparison window and data completeness stated.
4. It offers practical observations without inventing generic thresholds or
   changing data.

If data is insufficient, the answer identifies what is missing instead of
producing a confident recommendation.

The currently promoted V1 advice contract implements the budget-first portion
through `budgets_at_risk` and can report exact category or funding consumption.
It has no historical-comparison advice tool, so the browser exposes no inferred
deviation or percentage. Deterministic period comparisons remain available in
`/analisi`; extending advice to the later J8 comparison step requires a new
evaluated model-visible contract and is not an implemented claim.

## J9: Modify or undo a write

1. The owner selects `Modifica` or `Annulla` from the most recent summary, replies
   to a specific bot message, or opens the transaction in the dashboard.
2. The request resolves the exact stable transaction ID and current version.
3. A modification validates and reconciles the full replacement state.
4. Undo marks the transaction voided; it does not hard-delete the record.
5. Analytics, budgets, wallet effects, and audit history update atomically.

Concurrent edits return a conflict and refreshed state rather than overwriting
an unseen change.

## J10: Workstation downtime and backlog recovery

1. Telegram retains eligible unconsumed updates while the PC is off.
2. At boot, PostgreSQL and host services recover automatically.
3. The gateway persists all available updates in Telegram order before
   acknowledgement.
4. The worker processes one inbox record at a time. A crash or model outage
   causes retry without duplicate finance writes.
5. Failed records become visible dead letters after five attempts and can be
   replayed from the dashboard.

If measured local downtime exceeds 24 hours, the system warns that Telegram may
have expired updates. It cannot identify or reconstruct missing messages; the
owner manually forwards or resends them.

## J11: Dashboard review and management

1. The owner opens the localhost dashboard.
2. Overview shows current-month spend, budget progress, top purposes, and
   personal-fund/benefit split.
3. The owner filters daily, weekly, monthly, annual, or custom ranges.
4. Transactions and receipts support review and correction; category, budget,
   and funding-source pages support their approved V1 management operations.
5. Inbox and health views expose actionable failures without exposing secrets.

The dashboard remains unavailable over LAN or Internet in V1.

## J12: Backup and restore

1. A scheduled process creates a consistent application database dump.
2. Restic encrypts the dump, retained receipts, and required recovery
   configuration into the configured external repository.
3. Retention keeps seven daily, five weekly, and twelve monthly snapshots.
4. A restore drill initializes an empty installation, restores files and data,
   verifies receipt references, and runs integrity checks.

The recovery secret is stored outside the workstation. Langfuse data is not
required to restore authoritative finance behavior.
