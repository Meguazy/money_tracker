# Step 4: Durable Telegram MVP

Part of the [delivery roadmap](../roadmap.md).

Status: **In progress — one named flow is still unproven and the evidence
entry remains.** Step 3 and its EVD-004 baseline remain unchanged.

Accepted against the real test bot on 2026-09-10 and 2026-09-11, on the
promoted `text-qwen3-14b` profile with all three services under the user
manager:

- the token was rotated after its exposure, and `validate` passed against the
  replacement;
- one-use owner pairing bound the private owner. The clear code never reached a
  durable row, only its SHA-256 was stored, and the confirmation arrived
  through the outbox;
- text expense, `Registra` and its idempotency, query, `Modifica` with a direct
  reply, correction, `Annulla` on both a draft and a posted movement, advice,
  `/help`, and `/status` all completed through the production entry points;
- a category review opened, survived a worker restart, and settled its finance
  transaction correctly. The owner was never told, because the settled outcome
  dropped the movement it had posted; that is fixed and awaits a second live
  approval to confirm, so the flow the exit criterion names is not yet proven
  end to end;
- unauthorized observations produced four rejections, zero inbox rows, zero
  responses, zero finance rows, and no stored pairing code;
- warm text latency measured p95 5,770 ms over ten samples against the 30,000 ms
  requirement of NFR-PER-001;
- the journal carried no token-shaped run and no Bot API URL;
- a workstation reboot with lingering enabled brought all three services up
  unaided, llama.cpp acquired the GPU, and the two queued messages were
  processed in the order they arrived, one attempt each and no manual
  recovery. Inference measured one to two and a half seconds per model call;
- four fault injections passed: the database stopped and returned, the
  model stopped until a dead letter formed, the worker killed mid-work, and the
  gateway killed after committing a batch. Across all of them the finance
  ledger gained exactly one write per message, 40 inbox rows held 40 distinct
  update identifiers, and 48 outbox rows held 48 distinct logical keys;
- the fifth injection isolated the Bot API alone, by resolving
  `api.telegram.org` to the loopback while the database and the model stayed
  reachable. It found that a total Bot API outage cannot produce an outbox dead
  letter at all: `prepare()` calls `getMe` before either loop starts, so the
  gateway exits 2 and restarts instead of ever attempting delivery. The queued
  response therefore waited through the outage having consumed none of its five
  attempts, and went out unaided on recovery with `attempt_count` still 1. That
  is the better outcome, and the document previously did not distinguish it: an
  outbox dead letter requires per-request failures against an otherwise healthy
  gateway, not an unreachable Bot API, and that path stays covered by its real
  subprocess test.

Still missing: one owner-visible category-review settlement, and the recorded
evidence entry.

Thirteen defects were found by running the product rather than by reading it,
and each is described in the sections below with the commit that fixed it. Two
— stale controls on a superseded summary, and a movement that ends leaving its
old buttons live — were found by the owner looking at the screen. Four existed
only at boot: units that could not start at all, a binary a login shell
resolved and a boot did not, a GPU the model silently gave up on, and a model
still loading that killed the worker outright. One had never been reachable
until an owner pressed the button: every category-review settlement lost its
response. No test would have caught any of them.

An earlier session's run against `money_tracker_acceptance` on 2026-09-09 is
not evidence: it executed while implementation commits were landing, predates
every fix listed here, and is the session in which the credential was exposed.

## Repository-truth inventory

- `WorkflowDispatcher.dispatch` is the single implemented transport-neutral
  entry point. It covers every Step 3 text specialist and uses independently
  namespaced, PostgreSQL-backed LangGraph checkpoints.
- Application services and `PostgresFinanceRepository` own finance writes,
  audit, optimistic concurrency, command idempotency, category review, and
  reporting. Telegram code must not mutate their tables directly.
- Migration `20260909_0005` creates environment-specific offsets, the
  minimum normalized inbox, bounded leases/retries/dead letters, unique logical
  outbox state, hashed pairing codes, owner bindings, and scoped pending
  actions. Forward-only migrations `20260910_0006` and `20260910_0007` add
  manual outbox replay accounting and the second outbound operation that
  revokes superseded controls. `PostgresMessagingRepository` implements their
  AD-018 transactions.
- `telegram.config`, `BotApiClient`, `TelegramUpdateIngestor`, and
  `TelegramGateway` now provide separate credential-file selection, official
  HTTPS methods, private-owner filtering, durable long polling, callback
  acknowledgement, and an independent
  outbox loop. The separate sequential worker and Italian response renderer
  consume only durable normalized work.
- `money-tracker-telegram` is the production gateway entry point with
  `validate`, `pair`, `poll-once`, and `run`. `money-tracker-worker` is the
  separate processing entry point with `process-once` and `run`. `health`,
  `dead-letters`, `replay`, `resend`, and `latency` cover operator
  inspection, recovery for both failure queues, and the content-free
  NFR-PER-001 measurement.
- `deploy/systemd/` holds user-service templates for the gateway, the worker,
  and llama.cpp, rendered for a checkout by `scripts/install_systemd_units.sh`.
  They use direct locked-virtualenv entry points, checkout-correct documentation
  links, dependency-aware restart codes, and hardened user-service sandboxes.
  All three are enabled with lingering and have started unaided across a real
  reboot.
- Compose provides application PostgreSQL and optional Langfuse. The three
  systemd unit templates above are checked in; FastAPI is neither installed nor
  required by the Step 4 runtime.
- Thirteen gateway unit tests cover token-file security, Bot API parsing and
  redaction, authorization, commit-before-ack, and outbox send-before-complete.
  One additional real-PostgreSQL test proves that foreign users and groups
  create no inbox rows. The opt-in real test-bot integration test has passed
  `getMe` and backlog-preserving webhook removal; it sends no owner message, so
  it is transport evidence rather than product acceptance.
- Category-review decisions now claim one proposal-wide finance idempotency
  key in the settlement transaction. Identical approval, reuse, cancellation,
  and re-proposal commands return the first stored result; competing branches
  conflict without another finance write.
- `presentation.telegram` renders every dispatch status in Italian and owns the
  bounded callback vocabulary.
- `SequentialWorker` claims the oldest eligible row, resolves its correlation,
  dispatches once, and commits result, response, mapping, and completion in one
  messaging transaction. It holds no Bot API credential.
- `MessagingAdministration` and gateway ingestion implement one-use owner
  pairing. The clear code is shown once to the operator and never stored; the
  message carrying it is never retained.

## Reconciled design

[AD-018](../arc42/decisions/AD-018-telegram-processing-transactions.md)
defines the Step 4 transaction and ordering contract. The component interfaces
are deliberately narrow:

| Component | Interface | Owns |
| --- | --- | --- |
| Bot API transport | Typed validate/webhook/poll/file/send/callback operations | HTTP lifecycle and redacted Bot API errors only |
| Update ingestion | Normalize, authorize, pair, and commit one observed batch | Acceptance policy, not queues or finance data |
| Messaging repository | Ingest batch, claim/complete/fail/replay, claim/send/fail/resend outbox, resolve actions, report health | Offset, inbox/outbox, retries, dead letters, manual replay counts, binding, pairing, pending actions |
| Sequential worker | Process one claimed work unit through `WorkflowDispatcher.dispatch` | Lease lifecycle and typed outcome handoff only |
| Response renderer | Render typed outcomes plus repository-read display data | Italian text and bounded controls, no persistence or transport |
| Gateway runtime | Run independent polling and outbox loops | Process lifecycle; no in-memory correctness state |
| Administration/health | Generate pairing, report health and text latency, list dead letters, replay one inbox item, or resend one outbox item | Operator policy and safe output, no private message rendering |

The gateway atomically inserts authorized normalized rows and advances its
environment-specific next offset. The worker claims only the first non-terminal
update, so a future retry time creates intentional head-of-line waiting until
success or dead letter. Dispatch uses
`telegram:<environment>:<update-id>` as its stable source reference. Typed
result, unique logical outbox response, pending-action mapping, and inbox
completion then commit together. Finance writes remain in their existing
application-service transactions and must return the same result under replay.

The gateway process also delivers outbox rows through an independent loop. A
Bot API timeout can make physical delivery ambiguous, so Step 4 guarantees one
logical response and zero duplicate finance writes, not an impossible
exactly-once claim about Telegram. Health output distinguishes database,
transport, worker, outbox, model, downtime risk, and dead letters without
printing private content or identifiers.

## Persistence implementation evidence

- `messaging.contracts` and `messaging.retry` keep queue state transport-neutral,
  serializable, bounded, and free of Bot API credentials or raw updates.
- Migrations `20260909_0005`, `20260910_0006`, and `20260910_0007` own six
  messaging tables, manual outbox replay accounting, and the outbox operation
  that revokes superseded controls; all three refuse destructive downgrade.
  Stable uniqueness covers bot/update, source reference, logical response,
  pairing hash, and the per-bot owner binding.
- `PostgresMessagingRepository` commits ingest-plus-offset and
  result-plus-outbox-plus-action transitions atomically, serializes claims per
  bot, recovers expired leases, and exposes content-free health counts.
- Twenty-nine messaging unit tests and fourteen
  `@pytest.mark.postgres` repository tests passed. The
  PostgreSQL suite uses the real service and protocol; a two-connection race
  proved that only one worker receives the oldest item.

## Workflow replay prerequisite evidence

- Category-review settlement and re-proposal now commit their command claim,
  proposal transition, movement/category changes, and audits atomically.
- The settling handler turns a conflicting command key and a review that is no
  longer pending into the same already-settled question, so a late second
  button ends the thread instead of failing the work unit.
- Forty-seven focused unit tests passed across the review service and graph.
  Nine real-PostgreSQL application tests passed, including identical replay,
  competing-decision conflict, and stable re-proposal reconstruction.

## Response rendering evidence

- Every dispatch status is rendered: movement summaries carrying amount, date,
  purpose, and funding source with `Modifica`/`Annulla`; the paused review with
  only the branches AD-008 still allows; all four read tools; both channel
  commands; and one owner question for every stable clarification code.
- A replaced summary no longer keeps live controls. The owner keeps every
  message in their chat, so recording a draft left the original summary
  offering `Registra`, `Modifica`, and `Annulla` for a movement that had since
  changed; a real owner pressed the older one and got a clarification. The
  step document already claimed the control disappears once the movement is
  recorded, which was true only of the new message. Forward-only migration
  `20260910_0007` gives the outbox a second operation, `revoke_controls`, and
  the gateway clears the keyboard with `editMessageReplyMarkup`. The worker
  cannot make that call itself, because it holds no Bot API credential, so the
  revocation is durable work committed with the response that supersedes the
  controls. Revocation keys off the lifecycle outcome rather than off the new
  response carrying controls of its own: ending a movement answers with plain
  confirmation and no controls, which is exactly the case a response-shaped
  rule skips and the worst one to skip, since the earlier summary went on
  offering `Registra` for a movement that no longer existed to record. Owner
  testing found that gap after the first implementation. A clarification
  deliberately revokes nothing, since it asks the owner to use the controls
  they already have.
- A correction no longer rewrites the movement's description, as an interim
  measure against silent falsification. The correction path reuses the
  extraction contract and prompt written for creating a movement, where
  `description` is required, so a message that is an instruction rather than a
  purchase leaves the model nothing else to return: correcting an amount with
  "correggi l'importo a 35 euro" replaced a real description with that
  sentence. The owner then sees a plausible summary and has no reason to look.
  No suite would have caught it, because the ingestion dataset scores amount,
  time, merchant, polarity, and funding and has never scored the description.
  Suspending the rewrite costs the case where the owner does restate the
  purpose. Restoring it needs a correction contract whose absent description
  means unchanged, which is a new model-visible boundary that AD-012 puts
  behind an evaluation run, a dataset that does not yet exist, and a promotion
  decision.
- The rule the replacement contract must encode is settled and recorded under
  [evaluation work](#evaluation-work-this-acceptance-identified), together with
  the two other model-boundary questions this acceptance raised.
- A settled category review now tells the owner what happened. The dispatcher
  built the approved and reused outcomes without the movement the review had
  just posted, and the renderer requires it, so every settlement committed its
  finance transaction and then failed while rendering: the category was
  created, the movement was posted, and the work unit became a dead letter with
  no response. The owner saw only silence, and an operator reading
  `dead_letters` would reasonably conclude the approval had not happened and
  repeat it. It was unconditional rather than a race, and no test caught it
  because the dispatcher suite asserted only the status while the renderer
  suite was handed a movement directly. Owner acceptance produced the first
  real approval and the first failure with it.
- `Annulla` now has a transition on the state every movement starts in. It
  sent `undo`, which voids, and voiding reverses something that already
  counted; a draft never did, so the domain refused it and the owner got a
  clarification while the movement stayed a draft permanently. `discard` was
  written for exactly this case, but its only caller was the category-review
  cancellation, leaving the `discarded` state unreachable from Telegram.
  `FinanceService.cancel_expense` resolves the transition from the stored
  status under one command key, so a redelivered callback returns the first
  result instead of re-resolving a status the first run already changed. The
  rendered confirmation is unchanged, because one owner intent should not
  expose a domain distinction; the audit records which transition ran.
- Response content depends only on the outcome and committed state. Live queue
  figures are excluded by design, because a replayed render must reproduce the
  logical response its outbox key already names. Operational status stays with
  the pending administration and health path.
- Twenty-four unit tests passed, including a contract test that drives the real
  ingestion callback grammar with the rendered buttons, and one that fails if a
  new reason code reaches the owner as generic prose.

## Owner posting evidence

- EVD-004 qualified no automatic-post subset, so every movement is created as a
  draft. Until now nothing in the product could record one: `post_expense` had
  no production caller, and the channel offered only `Modifica` and `Annulla`.
  Every Telegram movement was therefore permanently invisible to analytics,
  budgets, and every read tool, which made the Step 4 query flow answer zero by
  construction.
- A draft summary now leads with `Registra`. The control disappears once the
  movement is recorded, because there is nothing left to confirm.
- Posting is an explicit normalized callback action, not a model-visible intent.
  The deterministic pre-router sends `post_expense` through the existing
  movement-lifecycle specialist while retaining the callback action in small,
  serializable graph state. AD-012 therefore continues to reserve the judgement
  that money counts for the owner; the promoted router schema cannot request it.
- `post_expense` became replay-safe like correction and undo, claiming a
  namespaced command key so a redelivered `Registra` returns the first result.
  Real-PostgreSQL tests prove one post audit across two invocations, and that a
  stale control on an already recorded movement writes nothing.
- The active routing suite still names the promoted
  `routing-intent-it-v1.2` dataset. Unit coverage exercises the deterministic
  posting callback and pins every model-visible enum value, so Step 4 adds no
  unevaluated route to the EVD-004 boundary.

## Operations implementation evidence

- `health`, `dead-letters`, `replay`, and `resend` print JSON carrying
  counts, ages, safe identifiers, and reason codes, and no message content.
  Real-PostgreSQL tests prove both failure queues are listable without owner
  text, both manual actions stay inside the selected bot scope, inbox replay
  preserves its finance source identity, and outbox resend keeps the original
  logical row while starting a fresh bounded delivery cycle.
- A real subprocess test drives the installed `money-tracker-telegram`
  entry point against PostgreSQL, proves `dead-letters` omits the stored
  response text, invokes `resend --outbox-id`, and observes the same row
  pending with its replay counter incremented.
- The same production entry point implements
  `latency --since <aware-ISO-8601>`. It selects successful non-media text
  inbox/outbox pairs, measures accepted input to committed Bot API send,
  excludes the earliest success as warm-up, and reports nearest-rank p95
  without content or identifiers. Unit, real-PostgreSQL, and subprocess tests
  verify the method. The real workstation sample and result remain missing.
- Recoverable and terminal worker failures now create distinct concise Italian
  responses. Their stable outbox identity and retry or dead-letter transition
  commit atomically; repeated attempts reuse one recoverable notice, and no
  internal reason or owner content enters it. Unit tests cover both messages and
  real-PostgreSQL tests prove the terminal notice is claimable with the dead
  letter while the original text is absent.
- Both production entry points catch psycopg schema errors and print only
  `database_schema_invalid`, with no traceback, SQL, URL, or relation metadata.
  Three real-process cases prove the behavior against an isolated empty
  PostgreSQL database and a database stopped at messaging revision 0005; the
  same `health` command was exercised against the current down-level local
  database.
- Both long-running processes classify why they stopped: exit code 1 for
  something an operator must change, 2 for an unreachable dependency. The units
  set `RestartPreventExitStatus=1`, so a missing credential stops visibly
  instead of restarting forever, while a database that starts late converges on
  its own. Eleven unit tests cover the classification and the safety of the
  printed reason. llama.cpp does not share that convention and needed
  `RestartPreventExitStatus=1 2`: it exits 1 when it cannot bind its port,
  which no restart ever frees. A first real run against an occupied 8080
  produced 54 restarts in a row before this was corrected. Abnormal
  termination by signal is not an exit status, so the case that genuinely
  deserves a restart still gets one, and a parametrized test now pins each
  unit's policy.
- The first real reboot with lingering enabled found that llama.cpp could not
  start at boot. `LLAMA_SERVER_BIN` defaults to the bare name `llama-server`,
  which a login shell resolves through `~/.local/bin`; a lingering user manager
  starts before any login session exists and has none of that on its PATH, so
  the launcher could not find the binary and the unit stopped with its
  operator-must-fix code. Starting the service by hand always worked, because
  it inherited the session PATH, which is why no earlier check caught it. The
  unit now sets an explicit PATH that includes `%h/.local/bin`. The gateway and
  worker were unaffected: they name absolute entry points inside the locked
  virtualenv. The exit-code contract behaved correctly throughout, stopping
  visibly rather than looping.
- The same reboot also showed llama.cpp starting without its GPU. CUDA
  initialization ran 400 milliseconds before `/dev/nvidiactl` existed
  (10:22:28.040 against 10:22:28.432), and llama.cpp treats that as a warning
  it keeps running past. The unit stayed active, the health endpoint answered,
  no queue grew, and every inference took roughly seventy seconds instead of
  two, which NFR-PER-001 cannot absorb. Nothing in the readiness matrix would
  have reported it, because liveness and the health endpoint were both honest.
  A user unit cannot order itself against the driver, so the launcher gates on
  it and refuses to start rather than degrade where nothing would notice;
  `LLAMA_REQUIRE_GPU=0` accepts CPU speed deliberately. The first gate waited
  for `nvidia-smi` to answer and a second reboot failed anyway: what CUDA needs
  is `/dev/nvidia-uvm`, which `nvidia-smi` never touches and which arrived 7.4
  seconds after the devices it does use. The launcher now creates that node
  through the setuid `nvidia-modprobe`, which is what a CUDA runtime would have
  done, instead of waiting on a correlated signal.
- A model that is merely still starting no longer kills the worker. llama.cpp
  accepts connections while it reads the model off disk and answers 503 until
  ready. A refused connection already arrived as `ModelConnectionError`, but
  this one did not: it escaped the work-unit handling entirely, ended the
  process with a traceback and exit code 1, and the unit reads 1 as something
  an operator must fix and refuses to restart. A thirty-second startup
  therefore took the worker down indefinitely with its claimed item stranded in
  `processing`, which is the opposite of the module's own rule that a failing
  work unit is not a failing process. The provider boundary now translates any
  server-side status into `ModelConnectionError`, which the worker already
  retries and which exits 2 so the unit restarts; a 4xx still propagates,
  because that is the caller's own request and no retry fixes it.
- A requested stop is now the third way these processes end, and the first real
  restart showed it was missing: neither entry point installed a signal handler,
  so `SIGINT` — the units' own `KillSignal` — raised `KeyboardInterrupt`
  wherever execution happened to be. A planned stop therefore abandoned the
  claimed work unit, left its lease held until expiry, took the crash-recovery
  path, and gave `TimeoutStopSec` nothing to wait for. `shutdown_event`
  installs `SIGINT`/`SIGTERM` handlers that set an event both loops check
  between iterations, so the worker finishes the unit it claimed and the
  gateway finishes its current poll or delivery, then each returns and exits
  zero as the operations table promises. Two unit tests cover both signals, and
  real restarts of both services now log a clean stop with no traceback.
- Rendered unit files pass `systemd-analyze verify`, and all three now start
  under the real user manager. The first real start exposed what static
  analysis cannot see: `CapabilityBoundingSet=`, `PrivateDevices=`,
  `ProtectClock=`, `ProtectKernelLogs=`, and `ProtectKernelModules=` all
  require `CAP_SETPCAP`, which an unprivileged user manager does not hold, so
  every unit failed to execute with `218/CAPABILITIES`. Both `verify` and
  `security` score a unit's configuration rather than its ability to run, and
  the earlier 3.5/3.5/3.8 exposure figures described templates that could not
  start at all. The five directives are removed and named in the units so they
  are not reintroduced. Measured exposure for what actually runs is 7.0 MEDIUM
  for each of the three; the read-only system and home views, private tmp,
  `NoNewPrivileges`, address-family and namespace restrictions, and native
  syscall architecture are enforced. Closing the remaining gap belongs to
  [Step 7](step-7-hardening-and-release.md), which owns hardening, because
  every further directive has to be qualified by starting the unit rather than
  by scoring it. The installer has been exercised into an isolated XDG
  directory, where every `Documentation=`, environment, and executable path
  resolved. llama.cpp has been started through its unit and answered its health
  endpoint. Reboot recovery remains acceptance work.
- The gateway warns the owner when it resumes after longer than the Bot API
  keeps undelivered updates. The warning names when it last listened rather
  than an elapsed duration, and is keyed by that instant, so restarting inside
  one outage produces one message. Five unit tests cover the threshold, the
  unpaired case, the never-polled case, and the repeated restart.
- Token rotation stays a manual BotFather procedure by decision: revoking a
  token has no Bot API method behind it, so a helper could automate only the
  revocation half. The tested `store_telegram_token.sh` helper safely performs
  only the local hidden-input, atomic mode-0600 file swap and says so;
  `validate` verifies the replacement before the gateway starts with it.

## Pairing implementation evidence

- `money-tracker-telegram pair` resolves the sole active profile, revokes any
  earlier live code, and prints one short-lived code with the exact message the
  owner must send. Persistence keeps only its SHA-256.
- Ingestion recognizes a private `/pair` before authorization and drops it
  whether the code was right, wrong, or absent, so a typed secret never reaches
  a durable row. A successful pairing counts as an authorized observation that
  produced no work; a failed one counts as rejected.
- Consuming the code, creating the binding, and queueing the owner's
  confirmation commit together, so a paired owner is told that it worked
  without an operator reading the database.
- Two real-PostgreSQL tests take a generated code through the production
  ingestor: one proves the binding, the consumed code, and the deliverable
  confirmation appear while the inbox holds only the owner's ordinary message;
  the other proves a second holder of the same code binds nothing. Thirteen
  unit tests cover code generation, expiry, and every pairing disposition.

## Worker implementation evidence

- The worker translates one claimed row into `ChannelMessage`, derives the
  trusted `DispatchContext` from finance and messaging reads, and calls only
  the dispatch port. An unresolved callback keeps its branch but loses its
  correlation, so routing clarifies instead of applying an old decision to a
  new target.
- Outbox and pending-action identifiers are derived from the inbox row. A real
  PostgreSQL test uses the production dispatcher composition to post a draft,
  kills the process after the finance commit but before the messaging
  transaction, releases the row, and proves the re-dispatched item retains one
  `post` audit mutation and one logical response.
- A `Modifica` callback that lacks replacement details commits its
  clarification with a successor reply mapping for the selected movement. A
  real-PostgreSQL test delivers that question, recreates the repository, and
  proves the Telegram reply message ID still resolves the same movement.
- Six real-PostgreSQL tests cover the complete terminal transaction, immediate
  deliverability of the committed response, the crash window, head-of-line
  ordering behind a retrying row, consumption of an answered correlation, and
  modification follow-up across restart. Twenty unit tests cover translation,
  correlation, failure classification, the bounded attempt budget, and lease
  loss.
- The dispatch assembly is rebuilt per work unit, because the read-selection
  policy receives the active category names at build time and an approved
  review changes them between two messages.

## Gateway implementation evidence

- Production and test select distinct absolute token files; only the selected
  mode-0600 owner file is read, and neither token nor its URL appears in errors.
- `BotApiClient` calls `getMe`, backlog-preserving `deleteWebhook`, long-poll
  `getUpdates`, `getFile`, `sendMessage`, and `answerCallbackQuery` with bounded
  timeouts and typed, redacted failures.
- Ingestion authorizes the persisted private owner before retaining content or
  file metadata, commits all observed dispositions with the next offset, and
  acknowledges callbacks only after that transaction.
- Gateway ingress and outbox loops run independently and have no model,
  LangGraph, or finance-repository dependency. Delivery uses existing durable
  leases and five-attempt/dead-letter transitions.
- `uv run pytest -q tests/unit/money_tracker/telegram` passed 26 tests. The real
  PostgreSQL authorization test passed. The real Bot API test passed `getMe`
  and backlog-preserving webhook removal without owner content; this is
  transport evidence, not Telegram workflow acceptance.

## Evaluation work this acceptance identified

Running the product against a real owner surfaced three model-boundary
questions that Step 4 cannot settle, because each one changes an evaluated,
promoted, model-visible boundary and AD-012 puts that behind a run and a
promotion decision. All three are recorded here with what is already decided,
so the next evaluation cycle starts from evidence rather than memory.

- **Correction extraction.** Corrections reuse the contract and prompt written
  for creating a movement, where `description` is required, so an instruction
  leaves the model nothing else to return. The description is preserved
  unconditionally for now, which also costs the case where the owner does
  restate the purpose. The owner settled the rule the dataset must encode: only
  an explicit correction changes a description, contrastive ("era benzina, non
  spesa") or by direct request. Naming a purchase in passing changes nothing,
  because an owner correcting the amount of "benzina per il generatore" should
  not silently lose the detail.
- **Routing an expense that names a benefit source.** `90 euro per spesa con
  buoni pasto` was classified as needing clarification, while `ho speso 90 euro
  con i buoni pasto per la spesa` was recorded normally. The dataset carries
  three meal-voucher cases whose expected intent is `unsupported`, about
  loading or receiving vouchers, against one that is an expense. A model that
  hesitates on the shape in between is the predictable result of that balance,
  and the fix is positive cases rather than a code change.
- **When the category boundary should propose.** There is no threshold to tune
  here: `CategoryFit` carries no confidence field and nothing in the path
  applies a bar, unlike routing which uses the promoted 0.5. The decision is
  entirely the model's, and the promoted prompt deliberately leans away from
  proposing, calling it expensive because it interrupts the owner, and setting
  the bar at "where the owner would look for this spending later". Eleven
  adversarial purposes were probed read-only against the live 25-category
  taxonomy and every one was fitted to an existing category, including a
  funeral placed under `Regali e donazioni` and tobacco under `Alimentazione`,
  which fail the prompt's own criterion. Those eleven were chosen to be hard
  and are not a measurement; the categorization suite is, and it passes 0.8693
  against its 0.85 gate. What is missing from that suite is a group of cases
  with no plausible neighbour whose expected outcome is a proposal, which would
  say whether the prompt or the model is responsible. The concern is not
  cosmetic: a catch-all such as `Acquisti personali` absorbing anything unusual
  defeats what AD-008 exists for, which is the owner owning their taxonomy.

## Deliverables

- Official Bot API adapter using long polling, private-owner pairing, allowed
  update filtering, secure file metadata handling, and redacted diagnostics.
- PostgreSQL inbox/outbox, persisted offset discipline, sequential worker,
  retry/backoff, dead letter, replay, and pending-action mapping.
- Italian message summaries and inline/reply interactions for text expenses,
  category review, record, modify, undo, help, and status.
- Separate production and test-bot configuration and real Telegram integration
  fixtures with owned-data cleanup.
- Host systemd units for Telegram gateway, workflow worker, API when present,
  and llama.cpp; boot recovery and dependency retry behavior.
- Final executable BotFather, pairing, credential-rotation, and downtime guide.

## Exit criteria

- A real private test bot completes text expense, category-review, correction,
  undo, query, and failure-recovery flows through production entry points.
- Fault injection around receive/commit/ack/process/send proves zero duplicate
  finance writes and visible dead letters.
- A real workstation/service restart recovers the eligible backlog in order.
- Unauthorized users/groups receive no finance data and cannot create work.
- The 24-hour limitation and manual-resend recovery are visible in setup and
  operations documentation.

## Milestone

This is the first Telegram-usable alpha. It does not yet include the complete
dashboard or receipt extraction.
