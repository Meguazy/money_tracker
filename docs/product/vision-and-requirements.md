# Vision and Requirements

- Status: accepted V1 baseline
- Implementation: planned unless explicitly noted
- Baseline date: 2026-09-01

## Vision

Money Tracker is a private personal-finance assistant that turns short Italian
messages, and after the personal V1 release receipt photos, into reliable,
reviewable expense records. It helps
the owner understand spending by purpose, funding source, time period, and
monthly budget without sending finance data to cloud AI services.

Telegram is the primary conversational channel. A localhost web dashboard is
the control plane for review, manual changes, categories, budgets, wallets,
statistics, failures, and settings.

## Intended outcomes

- Recording an ordinary expense takes one natural-language message.
- After the personal V1 release, mixed supermarket receipts reveal the purpose
  of individual items without double-counting the purchase total.
- Meal vouchers, welfare, cards, and cash remain analytically distinct.
- Category growth improves decisions without making reports unreadable.
- Questions about spending and budgets use stored facts and deterministic
  calculations rather than model invention.
- Messages recover automatically after workstation downtime within Telegram's
  supported backlog window.
- The owner can correct, audit, export, back up, and restore authoritative data.

## Users and operating context

V1 has one owner/operator. The owner uses Italian natural language, EUR, the
`Europe/Rome` timezone, Telegram on one or more personal devices, and a browser
on the workstation. The workstation is not always powered on and has an NVIDIA
RTX 5070 Ti with 16 GB VRAM.

The schema retains a stable profile identifier so a later household version is
possible, but V1 implements neither multiple profiles nor sharing.

## Functional requirements

### Ingestion and financial records

- **FR-ING-001 — Text expense:** Extract amount, occurrence time, description,
  optional merchant, category allocation, and payment allocation from an
  Italian message such as `Ho appena speso 12 euro per i croccantini dei gatti`.
- **FR-ING-002 — Text refund:** Record a refund as a negative movement,
  optionally linked to the original expense, and reverse the relevant category
  and funding effects.
- **FR-ING-003 — Default funding source:** When the owner omits the payment
  method, apply the configured default and show it in the confirmation summary.
- **FR-ING-004 — Split payment:** Support multiple payment sources on one
  purchase, for example `30 euro in buoni pasto e 20 con la carta`.
- **FR-ING-005 — Draft and posting:** Include only validated, reconciled,
  `posted` records in budgets and analytics. Keep ambiguous records as visible
  drafts or needs-review items.
- **FR-ING-006 — Correction and undo:** Allow a recent automatic write to be
  modified or voided without destructive history deletion.

### Categories and subcategories

- **FR-CAT-001 — Existing-category fit:** Search active names, aliases, and
  semantics before considering a new node.
- **FR-CAT-002 — Controlled proposal:** Propose a new category only when no
  existing node is adequate; never create it without explicit approval.
- **FR-CAT-003 — Review actions:** Support approve, use the best existing node,
  use a named existing node, propose another candidate, and cancel.
- **FR-CAT-004 — Re-proposal memory:** Exclude candidates already rejected in
  the same review. After three rejected proposals, require existing-list or
  manual resolution.
- **FR-CAT-005 — Bounded hierarchy:** Enforce at most one category and one
  subcategory level. Warn when the taxonomy grows beyond the documented soft
  complexity thresholds.
- **FR-CAT-006 — Stable history:** Permit rename and archive, but prohibit hard
  deletion or silent reparenting of referenced nodes.

### Funding sources and lightweight wallets

- **FR-FND-001 — Funding classification:** Distinguish personal funds from
  benefits and other sources independently of spending categories.
- **FR-FND-002 — Optional balances:** Allow a funding source to be a simple label
  or a balance-tracked wallet.
- **FR-FND-003 — Benefit ledger:** Support opening balance, manual credit,
  expense, refund, adjustment, and optional credit expiry for meal-voucher and
  welfare wallets.
- **FR-FND-004 — Reconciliation:** Require payment allocations to sum exactly to
  the parent expense total before posting.

### Receipts

These requirements are accepted but, except where noted, deferred after the
personal V1 release; see
[Deferred after the personal V1 release](#deferred-after-the-personal-v1-release).
V1 includes only the retention and backup part of FR-RCP-006 for the originals
the private archive already holds.

- **FR-RCP-001 — Image ingestion:** Accept receipt images from Telegram and the
  local dashboard within validated type and size limits.
- **FR-RCP-002 — Structured extraction:** Extract merchant, purchase date,
  total, line descriptions, quantities, prices, discounts, and source evidence
  through an evaluation-selected local pipeline.
- **FR-RCP-003 — Purpose allocation:** Allocate mixed receipt lines by spending
  purpose rather than putting the full receipt under a supermarket category.
- **FR-RCP-004 — Total reconciliation:** Reconcile line/category/payment sums,
  expose unexplained differences, and keep inconsistent results in review.
- **FR-RCP-005 — Mandatory preview:** Require explicit owner approval before
  posting every receipt.
- **FR-RCP-006 — Retention:** Keep the original image locally until manual
  deletion and include retained media in authoritative backups.

### Budgets, analytics, and advice

- **FR-BUD-001 — Monthly budget:** Configure a recurring monthly EUR target for
  a category and optionally override it for a specific calendar month.
- **FR-BUD-002 — No rollover:** Do not carry unused or overspent amounts into the
  next month in V1.
- **FR-BUD-003 — Hierarchy roll-up:** A parent category's spend includes direct
  allocations and child allocations. Parent and child targets remain separate
  comparisons and are never added together.
- **FR-BUD-004 — Benefit treatment:** Count all posted consumption toward a
  category budget while showing personal-fund and benefit portions separately.
- **FR-ANA-001 — Time views:** Report daily, ISO-week, monthly, annual, and custom
  date-range totals using occurrence time in `Europe/Rome`.
- **FR-ANA-002 — Breakdowns:** Group by category, subcategory, merchant, funding
  class, and funding source without double-counting.
- **FR-AGT-001 — Intent routing:** Distinguish record, refund, receipt, spending
  query, budget query, advice, category follow-up, correction/undo, and
  clarification intents before starting a specialist workflow.
- **FR-AGT-002 — Data tools:** Give query and advice agents only narrow,
  read-only domain tools; never expose arbitrary SQL or mutation tools.
- **FR-AGT-003 — Grounded answers:** Derive every reported figure from tool
  output and state the period and filters used.
- **FR-AGT-004 — Meaning of excessive spending:** Evaluate `too much` against a
  configured budget first, then comparable personal history, and clearly label
  the criterion.
- **FR-AGT-005 — On-demand advice:** Send no proactive coaching or budget alerts
  in V1.

### Telegram and web experience

- **FR-TEL-001 — Private owner pairing:** Pair one Telegram account and private
  chat through a locally generated, expiring code; reject all other users and
  groups.
- **FR-TEL-002 — Durable ingestion:** Commit every incoming update locally before
  advancing the Telegram offset.
- **FR-TEL-003 — Ordered processing:** Process the durable inbox one record at a
  time with deduplication, retry, dead-letter visibility, and manual replay.
- **FR-TEL-004 — Offline recovery:** Recover available Telegram updates after PC
  startup within the accepted 24-hour limit and warn after a longer downtime
  without claiming expired-message recovery.
- **FR-TEL-005 — Review controls:** Use concise Italian summaries, inline
  actions, and reply correlation for approval, modification, undo, and category
  review.
- **FR-WEB-001 — Local control plane:** Provide localhost-only views for
  overview, transactions, receipts, categories, budgets, funding sources,
  failures, and settings.
- **FR-WEB-002 — Manual control:** Allow manual creation and correction of
  finance records through the same application services as agent workflows.
- **FR-WEB-003 — Accessible analytics:** Present readable EUR charts and tables,
  keyboard-accessible controls, empty/error states, and responsive layouts.

### Operations and portability

- **FR-OPS-001 — Automatic startup:** Start the core database and host services
  automatically after workstation boot without requiring the observability
  stack.
- **FR-OPS-002 — Encrypted backup:** Create daily encrypted backups of a
  consistent database dump, retained receipts, and recovery configuration.
- **FR-OPS-003 — Restore:** Document and test restoration onto an empty local
  installation before the personal release is considered ready.
- **FR-OPS-004 — Export:** Provide portable finance export before claiming data
  portability complete; historical import remains outside V1.
- **FR-OPS-005 — Observability independence:** Continue core operation while
  Langfuse is stopped or unconfigured.

## Non-functional requirements

- **NFR-COR-001:** Duplicate delivery, retry, graph resume, and process restart
  must produce at most one financial mutation per idempotency key.
- **NFR-COR-002:** Money uses integer minor units and ISO currency; no binary
  floating-point representation crosses a domain or public interface.
- **NFR-PRI-001:** LLM, OCR/VLM, database, retained images, traces, and evaluation
  artifacts remain local. Telegram is the only accepted cloud transport.
- **NFR-SEC-001:** Secrets and Telegram download URLs never appear in source,
  frontend responses, logs, traces, or committed fixtures.
- **NFR-SEC-002:** Treat prompts, captions, OCR text, and images as untrusted data;
  they cannot alter system instructions or tool authorization.
- **NFR-REC-001:** The authoritative restore set is encrypted and verified by a
  repeatable restore drill.
- **NFR-REP-001:** Model, receipt extractor, persistence, messaging, and UI
  adapters depend on stable, narrow contracts.
- **NFR-OBS-001:** Every agent run records code, prompt, graph, schema, taxonomy,
  tool, and model versions when local tracing is enabled.
- **NFR-EVL-001:** No model, prompt, threshold, tool contract, or receipt pipeline
  is promoted without the applicable documented evaluation gates.
- **NFR-PER-001:** Initial warm targets are p95 at most 30 seconds for a text
  response and, once the receipt workflow ships, 90 seconds for a receipt
  preview on the target workstation.
- **NFR-OPS-001:** Planned core services tolerate delayed network, database, and
  inference readiness and expose actionable failure state.
- **NFR-MNT-001:** A behavior or architecture change updates documentation,
  tests/evaluations, and migration or operations guidance in the same change.

## V1 acceptance examples

| Input or action | Required observable result |
| --- | --- |
| `Ho speso 5,99 € per Netflix` | One EUR expense, `Tempo libero / Abbonamenti`, default funding source shown, modify/undo offered. |
| `Ho speso 12 euro per i croccantini dei gatti` | One EUR expense allocated to `Animali / Cibo e accessori`. |
| `50 euro di spesa, 30 con buoni pasto e 20 con carta` | One expense; category and payment dimensions each reconcile to EUR 50.00; benefit and personal portions remain visible. |
| `Usa una delle categorie esistenti` during review | No category creation; the best adequate existing category is used and audited. |
| `Usa la categoria X` | Stable category/alias resolution or a disambiguation question; never a model-invented ID. |
| `Proponine un'altra` | Current candidate is excluded and a different valid candidate is proposed. |
| A photo sent to the bot | No download and no movement; an Italian reply says photographed receipts are not handled yet and asks for the expense in words. |
| `Quanto budget mi rimane per X?` | Deterministic monthly limit, posted spend, remaining amount, period, and personal/benefit split. |
| `Per cosa sto spendendo troppo questa settimana?` | Budget risks first, then comparable history; all figures trace to read tools. |

## V1 success criteria

- The Telegram text-expense, correction, category-review, budget-query, and
  advice journeys pass real end-to-end tests.
- The evaluation matrix passes for the promoted local text model.
- Retry and restart tests demonstrate zero duplicate financial writes.
- The dashboard exposes all planned V1 control-plane views on localhost.
- A fresh-install restore drill reconstructs authoritative data and media.
- arc42, ADR status, roadmap evidence, experiment decision log, and operations
  guides match the released behavior.

## Deferred after the personal V1 release

On 2026-09-19 the owner deferred the receipt workflow past the personal V1
release in favour of a working text-first V1. It remains accepted product
scope and is planned as roadmap
[Step 6](../roadmap/step-6-evaluated-receipt-workflow.md), listed under future
developments. Until it ships, these parts of this baseline do not apply to V1:

- FR-RCP-001 to FR-RCP-005, and the manual deletion in FR-RCP-006;
- the receipt-preview target in NFR-PER-001;
- journey [J6](user-journeys.md#j6-receipt-ingestion-and-review);
- the mixed-receipt acceptance example, which V1 does not have to meet:

  | Input or action | Required observable result |
  | --- | --- |
  | Mixed supermarket receipt | One purchase total with purpose-based line allocations and mandatory preview. |

- the success criteria that the receipt-review journey passes a real
  end-to-end test and that the evaluation matrix passes for a promoted receipt
  pipeline.

What V1 already provides stays: the private original archive, its backup and
restore, and the dashboard's mandatory metadata review. The rule that every
receipt draft requires preview and explicit approval is unchanged and binds
the workflow whenever it ships. Bringing receipts into a release updates this
section, the roadmap, and the affected documents in the same change.

## Deferred evolution

Full personal accounting remains an architectural horizon: income, real account
balances, liabilities, transfers, bank import/sync, statement reconciliation,
multiple currencies with exchange rates, household profiles, remote access, and
proactive coaching require future product and architecture decisions.
