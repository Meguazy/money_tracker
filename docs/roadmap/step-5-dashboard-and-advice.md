# Step 5: Dashboard, budgets, analytics, and advice

Part of the [delivery roadmap](../roadmap.md).

Status: **In progress (2026-09-12).** The implemented vertical slices provide
the loopback same-origin host, deterministic monthly overview and transaction
register, accessible React shell, audited manual create, correct, post, and
cancel workflows, the multi-allocation editor for both reconciling dimensions,
the monthly budget planner, owner review of pending category proposals,
consultation and mandatory review of retained receipt metadata, and browser
inspection and requeue of the durable messaging queues. Daily, ISO-week,
monthly, annual, and bounded custom-range analytics now expose separate purpose
and funding views. The remaining control-plane views, merchant/comparison
analytics, advice surface, and complete browser E2E acceptance are still open.
No model, prompt, routing threshold, tool contract, or taxonomy policy changed,
so EVD-004 remains the promoted text baseline.

## Deliverables

- FastAPI same-origin host with React/TypeScript/Vite, TanStack Query, React
  Router, Mantine, and Apache ECharts.
- Overview, transactions, receipt metadata, categories/proposals, monthly
  budgets, funding sources/wallets, inbox/dead letters, health, and settings
  views.
- Manual creation/correction using the same application services as workflows.
- Daily, ISO-week, monthly, annual, and custom-range analytics with purpose and
  personal-fund/benefit breakdowns.
- Read-only spending, budget, wallet, comparison, and advice tools with grounded
  Italian responses.
- Local browser protections: loopback bind, Host/Origin allowlists, closed CORS,
  CSRF control, and no secret-returning endpoint.

## Exit criteria

- Browser E2E tests cover the complete control-plane workflows against real
  application services and PostgreSQL.
- Analytics and budget numbers match deterministic fixtures and never
  double-count receipt parents/lines.
- Advice gates demonstrate 100% numeric grounding and zero mutation access.
- Accessibility checks cover keyboard navigation, form labels, contrast,
  loading/empty/error states, and chart table alternatives.
- Non-loopback or disallowed-origin requests cannot read or mutate data.

## Implementation evidence

### First vertical slice (2026-09-11)

- `money_tracker.web` adds a narrow FastAPI adapter, strict monthly response
  contracts, content-free liveness/readiness probes, and a production CLI. The
  single owner profile is selected at composition time and never accepted from
  HTTP input.
- `ReportingService.monthly_overview` composes the existing posted-payment and
  category-budget reads. Exact EUR minor units are preserved; category and
  funding dimensions reconcile independently, so the response does not add a
  movement, receipt parent, or line twice.
- `frontend/` uses the accepted React/TypeScript/Vite, TanStack Query, React
  Router, Mantine, and ECharts stack. The implemented `/` view shows a selected
  Rome month, personal/benefit funding split, budget positions, explicit
  loading/empty/error states, a keyboard-focusable chart, and an equivalent
  table.
- The HTTP boundary accepts only an IPv4 loopback listener, exact loopback Host
  and Origin allowlists, emits no CORS permission, requires a matching
  HttpOnly-cookie/header token plus an allowed Origin for unsafe methods, and
  sets a restrictive CSP and browser hardening headers. It exposes no
  credential or private receipt path.
- `money-tracker-api.service`, the environment generator/upgrader, and the
  checkout-aware installer provide the host deployment path. Installation
  refuses a missing locked entry point or missing compiled frontend.
- A production-command smoke test on 2026-09-11 started the service at
  `127.0.0.1:8000` against the real application database: liveness and
  readiness returned healthy, the compiled Italian shell loaded, and a
  disallowed Host returned 403. The process then shut down cleanly.
- Focused Python tests pass for deterministic totals, configuration, Host,
  Origin, CSRF, health, static serving, and deployment templates. A real
  PostgreSQL integration test posts an expense through `FinanceService` and
  reads its exact value through the HTTP endpoint. Frontend typecheck, component
  tests, production build, and a runtime dependency audit pass.

### Transaction register and manual lifecycle slice (2026-09-12)

- `ReportingService.transaction_register` reads every lifecycle state for one
  month plus taxonomy and funding-source references. The response retains
  exact dual allocations and optimistic versions while omitting profile,
  idempotency, source-reference, and private-media data.
- The same-origin API adds `GET /api/v1/transactions` and strict create,
  complete-replacement, post, and cancel routes. A narrow web adapter injects
  trusted profile, actor, source reference, aggregate ID, and transport command
  key before calling the existing audited, replay-safe `FinanceService`.
- Unsafe requests exercise the already documented Origin and double-submit
  CSRF policy. Every mutation also requires `Idempotency-Key`; optimistic or
  idempotency conflicts return bounded 409 problems, invalid domain transitions
  return bounded 422 problems, and cancellation preserves history by discarding
  draft/needs-review movements or voiding posted movements.
- The domain now revalidates active owner-scoped category and funding-source
  references when an already posted movement is corrected.
- The React `/movimenti` view provides labelled manual entry, correction,
  registration and cancellation controls; lifecycle, allocation and reference
  facts; loading, empty, failure and retry states; and exact string-to-cent
  conversion without binary floating-point arithmetic. The first editor
  intentionally supported one category and one funding source, while the API and
  register preserved arbitrary reconciled allocation sets. The slice below
  removed that limit.
- Unit/component tests cover the register, strict browser boundary, deep link,
  accessible manual form, exact cents, lifecycle actions, and failure recovery.
  A real PostgreSQL HTTP integration test creates, replays, corrects, posts,
  replays, reads, voids, and verifies the resulting monthly total.

### Multi-allocation editor slice (2026-09-12)

- The `/movimenti` editor now holds both reconciling dimensions as independent
  row lists. The owner adds and removes category and funding-source rows, and
  each dimension reports its own exact sum, its own difference from the total,
  and its own errors, because AD-007 reconciles the two dimensions separately
  rather than against each other.
- Sums use `BigInt` over the already exact parsed cents, so no split is decided
  by binary floating-point arithmetic, and a restored amount is rendered from
  integer digits instead of a division.
- A correction loads every stored allocation. A row whose category the owner
  does not change keeps its `provenance`, `evidence`, and `owner_override`,
  so a workflow classification is not silently relabelled as an owner
  decision. A recategorized row takes owner provenance but keeps its
  `receipt_line_id`: that field records which purchased line the money came
  from, which recategorizing does not change.
- `TransactionCategoryAllocationContract` therefore exposes `receipt_line_id`.
  It is a stable allocation-scoped identifier, not a private media path, and
  the register still returns no profile, source reference, or receipt bytes.
- Editing rows offer only active references. An archived category or funding
  source appears solely in the historical row that already uses it, as a
  disabled option with a named warning, so existing history stays readable
  while a new allocation cannot select a reference the domain refuses when the
  movement is posted.
- The form refuses a duplicate reference, an allocation whose sign differs from
  the total, an unparseable amount, and a dimension left without rows, which a
  `needs_review` draft carrying no resolved category legitimately reaches.
  Native validation reports an empty control, and the alert list reports what
  it cannot.
- Component tests cover the exact two-by-two create, complete correction
  preservation, metadata replacement on a recategorized row, separate
  reconciliation errors, duplicates, wrong signs, invalid amounts, the
  category-less review draft, archived references, keyboard row removal, and
  API failure recovery. A web unit test pins the exact split contract including
  `receipt_line_id`.
- A real PostgreSQL HTTP test creates a two-category and two-source movement,
  reads back both exact dimensions, keeps an unreconciled draft editable while
  refusing to post it, refuses an unreconciled correction of a posted movement,
  rewrites the split into three categories and two sources, and verifies through
  the real `FinanceService` and `PostgresFinanceRepository` that the stored
  allocations and the monthly personal/benefit split match exactly.

### Monthly budget planner slice (2026-09-12)

- The overview has rendered budget positions since the first slice, but nothing
  outside the tests could configure one: no CLI command and no API route reached
  `FinanceAdministrationService`. `/budget` closes that gap, so the budget
  comparison now measures targets the owner actually set.
- `ReportingService.budget_plan` composes the existing posted-spend and budget
  reads. Unlike the overview it also returns each aggregate's stable category,
  explicit overrides, and optimistic version, because the control plane has to
  correct the stored plan rather than only display it. Entries keep the
  overview's ordering, worst position first.
- `create_budget` and `replace_budget` are now replay-safe. They claim the same
  general `finance.idempotency_records` table the movement commands use, keyed
  by a canonical digest of scope, category, recurring target, and overrides.
  Override order does not change the digest, so two orderings of one plan are a
  single decision. This keeps the AD-009 rule that reusing a mutation key
  retries the identical command true for every implemented resource rather than
  only for movements.
- `replace_budget` now raises `AggregateNotFoundError` instead of a bare
  `LookupError`, so the web boundary maps one typed application failure.
- The API adds `GET /api/v1/budgets`, `POST /api/v1/budgets`, and
  `PUT /api/v1/budgets/{category_id}`. A budget is addressed by its stable
  category, so the browser never receives a budget row identifier it has no
  route for, and the responses carry no profile identity. Bounded problems
  distinguish a missing budget, a conflict, and a refused domain rule.
- The React `/budget` view lists each exact position with its remaining amount,
  configures a target for any category that has none, and corrects a stored plan
  under the version it displayed. Month overrides are added and removed as rows,
  amounts convert through exact cents, and the form refuses a non-positive
  target, a month without an amount, and two exceptions for one month.
- Unit tests cover plan ordering and the version a correction must quote, the
  exact wire shape, trusted scope injection, a refused profile field in a
  request body, replayed creation, a reused key with changed content, and
  override-order equivalence. Component tests cover listing, creation,
  correction with stored overrides, validation, keyboard removal, API failure,
  retry, and the empty state.
- Real PostgreSQL tests configure a budget over HTTP, replay it, read the exact
  position for its month and for an overridden month, refuse a key reused for
  other content, refuse a stale version, and verify the corrected plan in the
  database and in the monthly overview. A repository test proves one replayed
  command writes a single audit row.

### Category proposal review slice (2026-09-12)

- `/categorie` makes every pending category proposal reachable from the local
  control plane. Each card names the candidate, optional parent, inclusion
  guidance, rationale, rejected candidates, shortlist, and the exact movement
  waiting for a purpose decision.
- The read model pairs proposal and movement server-side through the trusted
  workflow `source_reference`. The browser receives neither that reference nor
  the profile ID and never supplies an expense ID; missing or ambiguous
  correlation is logged as a stable structured failure and refused.
- `GET /api/v1/category-proposals` returns the pending queue and active taxonomy.
  Strict approve, use-existing, and cancel resources reuse the existing
  `CategoryReviewService`, so approval creates a node and posts atomically,
  reuse posts without taxonomy growth, and cancellation discards the
  never-posted draft without deletion.
- Every HTTP branch requires the proposal-wide `Idempotency-Key` that the
  existing settlement transaction claims. An identical request replays its
  first result, while another branch or a key for another proposal conflicts.
- The page offers the evaluated shortlist and a labelled selector over all
  active categories. Loading, empty, failure/retry and pending-decision states
  are explicit; controls are keyboard accessible and the four-item navigation
  wraps on narrow screens.
- Generating another model candidate remains in the Telegram review workflow.
  The browser can always resolve the review deterministically by choosing an
  active category, but it does not gain an inference dependency or a second
  path that can invent taxonomy.
- Unit tests cover source correlation, cross-workflow refusal, strict wire
  shape, trusted scope, canonical command identity, bounded problems and deep
  linking. Component tests cover proposal facts, approval, shortlist/manual
  reuse, cancellation, keyboard operation, and empty/error recovery.
- A real PostgreSQL HTTP test lists three correlated proposals, approves and
  replays one, reuses an existing category for another, cancels the third, and
  verifies terminal proposal and movement state through the production
  repository. No migration, model-visible contract, prompt, threshold, tool,
  or taxonomy policy changed; EVD-004 remains the promoted baseline.

### Receipt metadata and review slice (2026-09-12)

- `/scontrini` makes the private receipt archive's metadata reachable from the
  control plane for the first time. Until now the `money-tracker-admin
  receipt-store` command could retain an original and register its safe facts,
  but the mandatory `stored` → `needs_review` → `approved` lifecycle AD-014
  requires had no owner-facing surface at all: only tests advanced it.
- The page lists every retained receipt newest first with its display
  filename, decoded media type, exact byte size, storage instant, review
  state, and the optimistic version a decision must quote. A receipt with no
  filename is named as such rather than left blank.
- The response deliberately omits the content digest. It is the archive
  storage key under AD-014, no browser route consumes it, and the two review
  decisions are addressed by the stable receipt identifier, so the key stays
  out of the browser entirely. Profile identity and source reference are
  omitted exactly as the movement register omits them.
- Nothing in this slice reads, serves, links to, or deletes an original. The
  `ReceiptReviewWriter` port exposes only the archive listing and the two
  transitions, so the browser object graph contains no media store, no
  extractor, and no deletion path. Extraction, line items, and media deletion
  remain Step 6 and AD-011 work.
- `ReceiptMetadataService` transitions became replay-safe in the same change.
  They now claim the general `finance.idempotency_records` table in the same
  transaction as the state change and its audit, keyed by the receipt and the
  version the caller observed. A receipt is reviewed in successive steps, so
  the command is the transition rather than the receipt: `receipt-review:
  {receipt_id}:{expected_version}` replays one decision, while a different
  decision taken from the same displayed state conflicts instead of applying a
  transition the owner never chose. This keeps the AD-009 rule that reusing a
  mutation key retries the identical command true for every implemented
  resource.
- A repeated transition is resolved before the current row is read, so a retry
  never depends on state the first attempt already advanced. Approving a
  receipt that never entered review is refused by the domain as a bounded 422,
  and a decision quoting a version another transition already left is refused
  the same way. No migration was needed: the command table and the receipt
  version column already existed.
- The web boundary's database failures are now recorded in the one function
  that builds their response, with the traceback the response deliberately
  withholds. The monthly overview previously produced that problem without the
  shared funnel and so without a traceback; every read and every mutation now
  goes through the same record.
- Every client route is now served for direct navigation. `/budget` was
  reachable by in-app navigation but not by reload or bookmark since the budget
  slice, which the local-web runbook already described as working; `/budget`
  and `/scontrini` are both mounted and covered by a test that asserts each
  route returns the same shell.
- Unit tests cover the safe wire shape and the absent digest, the canonical
  command identity, the transition-wide key the boundary requires, the version
  a decision quotes, a refused unknown body, replay of one transition, a
  conflicting decision on one observed version, a stale version, and
  newest-first ordering. Component tests cover the listed metadata, the absence
  of any image or link, keyboard operation, the approval offered only after
  review, a refused decision, retry, and the empty state.
- A real PostgreSQL HTTP test registers two originals through the production
  service, lists them newest first without the digest or source reference,
  opens and replays one review, refuses approval of a receipt that never
  entered review, approves the reviewed one, refuses a stale version, refuses
  an unknown receipt and a non-canonical key, and verifies both terminal states
  through the production repository. Repository tests prove one replayed
  command writes a single audit row and a single claim, and that a failed audit
  leaves no claim behind. No migration, model-visible contract, prompt,
  threshold, tool, or taxonomy policy changed; EVD-004 remains the promoted
  baseline.

### Messaging operations slice (2026-09-12)

- `/messaggistica` makes the durable Telegram queues reachable from the control
  plane. `money_tracker.messaging.administration` has exposed `health`,
  `dead_letters`, `outbox_dead_letters`, `replay`, and `resend` on
  `PostgresMessagingRepository` since Step 4, with recovery and latency
  acceptance evidence, but only `money-tracker-telegram health|dead-letters|
  replay|resend` could reach them. The owner had to open a terminal to learn
  that a message had failed.
- The page shows the selected bot environment, whether an owner is paired, the
  persisted offset, the last poll, active correlations, both queue depths with
  their oldest waiting item, and both dead-letter queues with attempt counts,
  requeue counts, and bounded reason codes. It warns when the bot has no owner
  binding and when the gap since the last poll exceeds the 24 hours Telegram
  keeps undelivered updates.
- This is the first control-plane domain that is not scoped by profile.
  Durable messaging state belongs to one bot environment, so a new
  `MessagingScope` reads only `MONEY_TRACKER_TELEGRAM_ENVIRONMENT` and derives
  the stable bot key. `TelegramSettings` now derives its key from that same
  value instead of repeating it. The scope is bound into
  `MessagingOperationsAdapter` at composition time and no adapter method takes
  a scope, so the HTTP layer has no argument through which a request could name
  another bot. The web process reads no Bot API credential: it inspects and
  requeues rows and never speaks to Telegram.
- Nothing the owner wrote reaches this surface. The Step 4 `DeadLetter` and
  `OutboxDeadLetter` projections are content-free by construction, so the
  browser/journal distinction the brief anticipated does not arise: the same
  values are safe in both. The response additionally omits the bot key and the
  inbox `source_reference`, which is the finance command identity a replay
  preserves and which the movement register and receipt response omit for the
  same reason.
- Replay and resend are requeue commands, not new financial writes, and their
  identity was decided before the routes were written. They claim no entry in
  `finance.idempotency_records`: that table is claimed in the same transaction
  as the finance state it protects, while a requeue commits in the messaging
  schema through a different repository and connection, so a claim there would
  promise an atomicity that does not exist. The durable pin is instead the
  row's own `replay_count`, checked and advanced inside the single messaging
  transaction that requeues it. The HTTP key names that transition —
  `messaging-replay:{inbox_id}:{observed_replay_count}` and
  `messaging-resend:{outbox_id}:{observed_replay_count}` — and the adapter
  refuses any other value. No migration was needed.
- The repository therefore locks the row, applies the requeue only from the
  pinned state, and reports a repeat of that exact transition as `replayed`
  rather than requeuing twice. Without the pin a stale tab could requeue an
  item a second time after the first requeue had already failed again. Every
  other outcome is one bounded 409, because AD-018 requires an inbox identifier
  from another bot environment to be indistinguishable from a missing or
  non-dead-letter item. The unattended `money-tracker-telegram replay|resend`
  commands keep their unpinned form, since a terminal names an item rather than
  a view, and both forms now share one implementation.
- The refusal of a requeue is recorded once, in the repository funnel that
  decides it, with the queue, the observed status and count, and the pinned
  count. The outbox refusal previously produced no record at all. Both service
  commands now record the outcome they achieved instead of the intervention
  they attempted.
- `MessagingConflictError` and `MessagingLeaseError` moved from
  `persistence.messaging` to `messaging.errors` and are re-exported unchanged.
  They are part of what the `MessagingRepository` port promises, so a caller
  that depends only on the port can now name them without importing a storage
  adapter.
- Nothing in this slice sends a message, changes the pairing, moves the Bot API
  offset, or deletes a row. The adapter's port exposes the snapshot and the two
  requeues and nothing else, so the browser object graph contains no Bot API
  transport, owner binding, or deletion path.
- Unit tests cover the pinned transition identity and the typed requeue result,
  the composed snapshot including an unpaired bot, the content-free wire shape
  with no bot key or source reference, the trusted scope the read uses, both
  command families, a crossed key, one bounded conflict for every refusal, a
  refused body naming a bot, and the mounted deep link. Component tests cover
  the rendered state, both requeue controls under the counts they displayed,
  keyboard operation, the unpaired and expired-backlog warnings, a refused
  requeue, retry, and the healthy empty state.
- Real PostgreSQL tests drive one update to an inbox dead letter and one notice
  to a delivery dead letter through the production repository, then over HTTP
  read both without the owner's text, the failure notice text, or the bot key,
  replay the work item, repeat that request and receive `replayed`, refuse a
  stale count, an unknown item, and a crossed key with the identical problem,
  and resend the response. The repository is then read back to prove one
  requeue per queue and a recovered work item that kept its source reference.
  Repository tests additionally prove the pinned requeue applies once for both
  queues and that a stale or unknown pin changes nothing. No migration,
  model-visible contract, prompt, threshold, tool, or taxonomy policy changed;
  EVD-004 remains the promoted baseline.

### Calendar analytics slice (2026-09-13)

- `money_tracker.domain.periods` owns day, Monday-to-Sunday ISO week, calendar
  month, calendar year, and custom interval resolution in `Europe/Rome`.
  Periods are closed local-date ranges converted to half-open aware instants at
  the repository boundary, so the 23-hour and 25-hour daylight-saving days
  remain one calendar day. Custom input is ordered and capped at 366 days.
- `ReportingService.range_overview` reads posted movements through a new bounded
  repository method without replacing the monthly method the promoted tools
  call. It returns one payment-derived total, its funding-class and
  funding-source views, and a direct-category partition ordered by absolute
  spend. Parent names are display context only: no parent roll-up is added to a
  child figure, so purpose entries sum to the movement total exactly once.
- Monthly budget positions appear only when the resolved dates cover one whole
  calendar month, including a custom selection with those exact dates. A day,
  week, partial month, or year receives no prorated target and the browser says
  why.
- `GET /api/v1/overview/range` accepts exactly the parameter set belonging to
  the named granularity, resolves it through the domain calendar policy, and
  returns the resolved dates and ISO numbering. Unknown granularities,
  malformed dates, reversed or oversized ranges, and unrepresentable upper
  bounds are bounded 422 problems. Profile scope remains composition-owned.
- The React `/analisi` page selects all five granularities, submits one stable
  selection at a time, and shows exact summary cards plus purpose, source, and
  applicable monthly-budget tables. The tables explain that the two
  reconciling dimensions are views of the same money and that source
  consumption is not a wallet balance. Loading, empty, dependency-failure, and
  retry states are explicit; controls and tables are labelled and keyboard
  operable, and the route supports reload and bookmarking.
  The shell lazy-loads every page behind an announced fallback; the monthly
  chart loads only when budget data needs it, with ECharts and `zrender`
  emitted separately so every production JavaScript chunk remains below
  Vite's warning threshold.
- Domain and application unit tests cover ISO year changes, daylight-saving
  bounds, leap years, whole-month recognition, the custom-range limit, posted
  filtering, signed exact splits, source zeros, category partitioning, and
  budget applicability. Web unit tests pin every query shape and bounded
  refusal. Component tests cover the exact tables, ISO-week and custom
  selection, monthly-only budgets, empty data, and keyboard retry.
- A real PostgreSQL HTTP test posts a split movement and two boundary
  movements through `FinanceService`, then proves that an ISO week crossing
  New Year includes only its own rows and preserves both exact dimensions. A
  whole-month custom read includes its additional row and compares the parent
  budget against child allocations without adding the unrelated purpose.
- No migration, model-visible tool contract, prompt, routing threshold, or
  taxonomy policy changed. The monthly tool read remains untouched, so no
  evaluation gate is triggered and EVD-004 remains the promoted baseline.

### Work still required before completion

- Add category rename/archive management, funding sources/wallets, and settings
  views.
- Add merchant breakdowns and explicit period-over-period comparison without
  changing the promoted model-visible tool contract accidentally.
- Expose the already promoted read-only advice capabilities without widening
  their mutation authority; rerun and record any evaluation required by a
  changed model-visible contract.
- Add complete browser E2E coverage against real application services and
  PostgreSQL, accessibility/contrast automation, and installed-service browser
  acceptance. Step 5 remains in progress until every exit criterion above has
  that evidence.
