# Step 3: Evaluated text workflows

Part of the [delivery roadmap](../roadmap.md).

Status: **In progress.** The framework-independent routing, extraction, and
resolution foundation is implemented: the bounded intent taxonomy with
partitioned mutation authority, the normalized channel work unit, deterministic
pre-routing, the strict versioned router and expense/refund extraction model
boundaries with their deterministic policy vetoes, the versioned category-fit
boundary, pure resolution of occurrence time, amount sign, and both reconciled
allocation dimensions, and the strict-schema retry boundary behind a narrow
model port. The typed root router is assembled as a LangGraph state graph over
the persistent PostgreSQL checkpointer, with a local structured-output adapter
and a local command for exercising it by hand. The expense and refund
specialist workflow runs end to end, storing a draft through the existing
application services or reporting the question it owes, and the extraction
boundary no longer blocks that path. The owner category review pauses on a
persistent interrupt and settles every J3 branch, and the read-only routes
answer from scoped registries of domain read tools. All four pinned suites are
executable and measured, and each fails at least one blocking gate. No channel
wiring exists yet, and no confidence bar, prompt, or model is promoted.

## Prerequisites

Step 2 complete. A capability-screened local candidate exists from Step 1, but
no candidate is promoted to a product role; promotion happens through this
step's gates and the experiment decision log.

## Remaining increments

This order is a handoff aid, not new scope. Each increment must land with its
own tests and documentation, and none may ship a later step's behavior.

1. The read-only query and advice routes with scoped tool registries.
   Implemented; see the evidence below.
2. Persistent human-in-the-loop category review over the existing pending
   proposal port, covering approve, best existing, named existing, re-propose,
   cancel, unrelated input, and the three-rejection transition, with side
   effects placed after the interrupt and made idempotent for replay.
   Implemented; see the evidence below.
3. The pinned `routing/intent-it`, `ingestion/expense-it`,
   `categorization/purpose-it`, and `workflow/category-review-it` datasets and
   suites, then calibration of the routing confidence bar and the auto-post
   eligibility policy on held-out labels. All four suites are executable, reach
   their promotion minimums, and have been measured; each fails at least one
   blocking gate, so none supports a promotion or a calibration conclusion. This
   increment now blocks usable end-to-end behaviour, not just promotion: see the
   measured findings in the evidence below. An ad-hoc check against the screened
   Qwen3 14B profile routed the expense, refund, split-funding, query, budget,
   advice, and non-finance journeys as the product baseline expects, but routed
   `Vorrei caricare 100 euro di buoni pasto` to the expense route instead of
   the unsupported route that journey J5 requires. Misreading a benefit credit
   as spending is a safety-tagged failure, so the intent dataset must cover
   wallet-credit phrasing. Self-reported confidence was coarse and nearly
   constant across every case, which is why the bar has to be calibrated on
   held-out labels rather than on model self-report.
4. Promotion of the model, prompts, schemas, and thresholds through
   [Decision Log](../experiments/decision-log.md), which currently records that no
   routing confidence bar and no auto-post threshold are promoted.

## Deliverables

- Typed root router with deterministic handling for channel commands/callbacks
  and specialist routes for expense, refund, queries, advice, category follow-up,
  correction/undo, and clarification.
- Versioned structured extraction and category-fit contracts with evidence.
- Deterministic expense/refund mutation paths that call application services;
  query/advice agents remain read-only. Implemented.
- Persistent human-in-the-loop category review covering approve, best existing,
  named existing, re-propose, cancel, and unrelated input.
- Auto-post eligibility policy calibrated on the evaluation set; all other
  inputs remain drafts.
- A minimal local administration command for bootstrapping the owner profile
  and its funding sources, and for exercising implemented workflow components
  by hand before a channel exists; no channel adapter, durable worker, or
  owner-facing response.
- Production prompt/model configuration promoted through the experiment
  decision log.

## Exit criteria

- Routing, extraction, categorization, HITL, tool, advice-grounding, injection,
  and latency gates pass on pinned datasets.
- Restart/resume and repeated-command tests produce zero duplicate writes.
- Every Italian acceptance example in the product baseline passes through the
  real local model in integration/evaluation runs.
- Graph state contains only small serializable values and stable references;
  domain data remains in repositories.

## Current implementation evidence

- The `workflows` component is framework-independent: it imports Pydantic,
  `zoneinfo`, and pure domain values, and no LangGraph, model client,
  repository, or transport module. It owns no persistent state.
- Every intent belongs to exactly one authority class, and the classes are
  disjoint and exhaustive over the taxonomy. Query, advice, non-finance,
  unsupported, clarification, and receipt routes are outside the mutating set,
  so a non-write request has no routing path to a financial write.
- Deterministic pre-routing applies a total precedence order over explicit
  interface decisions, image media, supported commands, and blank input. Image
  media outranks its own caption, and a callback naming no pending action is
  clarified instead of applied to an arbitrary pending action. Commands are
  matched exactly and never inferred from prose; a routing decision carries no
  command argument, keeping a one-use pairing code out of routing telemetry.
- The `RouteClassification` schema is strict, versioned, parsed from the real
  JSON transport shape, and rejects unknown fields, coerced numbers,
  out-of-range confidence, repeated unknowns, blank or oversized evidence, and
  the two deterministic-only routes. It exposes no profile, timezone,
  funding-source, authorization, or idempotency field.
- Deterministic acceptance vetoes a category follow-up without exactly one
  correlated pending review, a stale or foreign reply reference, a correction
  or undo without a runtime-resolved movement, and any decision below the
  explicitly supplied confidence bar. Correlation targets come from trusted
  context, never from the classification.
- The `ExpenseExtraction` boundary is strict, versioned independently of the
  router boundary, and parsed from the real JSON transport shape. It requires a
  split payment to quantify every portion and name distinct sources. It offers
  the model no channel for declaring its own uncertainty, and no currency:
  both are judgments the deterministic side is better placed to make, and the
  boundary is asserted to expose neither. Absence of temporal words, merchant,
  or funding portion is the documented default rather than a question, and an
  absent amount is simply unresolvable.
- Occurrence time and amount sign are resolved by pure deterministic code, not
  by the model. The resolver anchors same-day, previous-day, counted day and
  week offsets, named past weekdays, and explicit Italian dates to the message
  timestamp in `Europe/Rome`; it refuses a bare weekday, an unreadable phrase,
  and a day after the message. It preserves the message wall-clock time so a
  named day stays inside its intended Rome day, and it normalizes a wall time
  inside the spring-forward gap onto the equivalent instant after the
  transition. A refund's negative sign comes from the stated polarity alone.
- Payment allocation resolves owner funding words against the profile's
  configured active sources, fills an omitted method from the configured
  default, and refuses an archived source, unmatched words, two portions naming
  one source, a missing default, and a split that does not sum to the total.
  Matching is exact on the folded configured name with no synonym guess. Refund
  allocations carry the negative total sign, and the resolved dimension is
  verified by building a real domain aggregate, which independently enforces
  non-zero rows, unique sources, matching polarity, and exact reconciliation.
- Every resolution failure reports a stable machine-readable reason code, so a
  workflow can ask the one question matching the failing step instead of
  retrying blindly or parsing prose. The codes describe what the deterministic
  side could not do with the message, never what the model said about itself. Like routing justification codes, these
  carry no owner text.
- Category fit is one versioned judgment with two outcomes, validated as a flat
  schema with conditional fields rather than a discriminated union, because no
  local candidate is capability-screened for union output. Categories are named
  and never identified: an existing outcome resolves through the active
  taxonomy's names and aliases, and assembling a proposal checks the candidate
  name, optional parent, and every examined alternative against the taxonomy.
  It refuses a name duplicating an active node or alias, a name already
  rejected in this review, a parent that is not an active root, and a
  fabricated alternative. A duplicate is refused rather than quietly converted
  into reuse, so a wrong judgment stays visible and measurable. No
  deterministic path creates a taxonomy node.
- The purpose dimension is one allocation carrying the whole signed total,
  recording the boundary version that classified it and whether the owner
  forced the category. An archived category cannot receive an allocation.
- The strict-schema boundary implements the AD-006 retry budget as
  straight-line code behind a narrow `StructuredOutputModel` port, so it cannot
  retry twice however it is called. A repair restates the same instructions and
  message and adds only rejected field paths and reasons, never the rejected
  values, and truncates the quoted completion so a runaway output cannot
  outgrow the context. A persistent failure raises a typed error carrying only
  the attempt count, with neither owner nor model text in its message.
  Non-JSON output is treated exactly like an invalid document.
- Candidate router instructions derive their intent vocabulary from the code, so
  the offered routes cannot drift from the taxonomy and no deterministic-only
  route is ever offered. They are labelled `CANDIDATE_` and versioned
  `candidate-`, and the decision log records that no prompt is promoted.
- The compiled root router is a LangGraph state graph whose deterministic node
  ends the graph before any inference call whenever the channel already stated
  the decision, verified by asserting the port received zero requests for
  image, command, callback, and uncorrelated-callback messages. Its
  classification node re-imposes the confidence bar and every routing veto on
  the model answer, and converts an exhausted retry budget into a clarification
  decision carrying the `invalid_model_output` reason instead of failing the
  invocation. Instruction text and confidence bar are required arguments with
  no defaults, so the assembly cannot imply a production configuration.
- Deterministic draft assembly turns one extraction and one purpose judgment
  into a complete movement, or reports the stable reason selecting the question
  to ask. Every movement is created as a draft, because no auto-post policy is
  promoted. A movement whose purpose needs a new category is still assembled
  with its funding dimension resolved and its purpose dimension empty alongside
  a pending proposal, which the domain admits as a draft and refuses to post.
  The result type rejects a combination that both resolved a purpose and
  proposed one.
- The creation key is derived from the durable inbound record and the request
  digest from the request's own semantics, excluding lifecycle status, version,
  and generated identifiers, and normalizing the occurrence instant to UTC so
  the same moment expressed with a different offset matches. Real PostgreSQL
  tests store a draft against a seeded profile's taxonomy and funding sources,
  replay one inbound record into exactly one movement, refuse a changed request
  on that record as a conflict, persist the split-funding journey, and keep a
  pending proposal alongside its draft without extending the taxonomy. A
  redelivery must replay the stored record unchanged, its channel timestamp
  included; a test that re-read the clock instead surfaced that precondition,
  which is now documented.
- The expense and refund workflow is a LangGraph graph over the same
  checkpointer, wiring the two model boundaries and deterministic assembly to
  the existing application services. Routing and extraction judge the direction
  of money independently and a disagreement clarifies rather than picking one.
  The purpose boundary receives the owner's own active category names rendered
  from their taxonomy at call time. Every node either advances or records a
  clarification, so a run stores a complete draft or stores nothing.
- Real PostgreSQL tests store a reconciled draft and the split-funding journey
  against a seeded profile, submit a pending candidate without extending the
  taxonomy, leave the database untouched on every clarification path, store
  exactly one movement for a redelivered record, and resume a finished thread
  from a checkpoint across separate connections.
- Four boundary-design defects were found by running the workflow against the
  real screened model, and all four were mine rather than the model's. The
  extraction schema required the model to restate the V1 currency, which is
  operational configuration it has no basis to supply; the schema handed to the
  endpoint left optional properties out of `required`, which constrained
  decoding does not enforce, so a model could omit a field and fail the whole
  answer; cross-field mutual-exclusion rules were incompatible with requiring
  every property, because a model then fills a field and flags it in the same
  answer; and the extraction boundary offered an abstention channel at all.
- That last one was the blocking defect. Two prompt revisions failed to stop
  the model over-declaring unknowns, which looked like a prompt-quality gap for
  `ingestion/expense-it` to measure. It was not. The boundary was asking the
  model to judge whether an aspect was resolvable, a judgment the deterministic
  side is strictly better placed to make: it holds the profile's configured
  funding sources and the Italian date forms the resolver reads, and the model
  is shown neither. No prompt could close that information gap, so the channel
  was removed rather than tuned. Boundaries now validate shape only, every
  property is required with optionality expressed as an admitted null, and
  omission carries the documented default.
- With the channel removed, extraction no longer blocks the mechanism. Every
  V1 acceptance example, both split-funding phrasings, and the relative-date
  and refund journeys return schema-valid answers whose amounts, polarity,
  temporal words, and split portions are correct against the real screened
  Qwen3 14B profile. This is an ad-hoc check over ten messages, not a
  measurement: it establishes that the mechanism completes, and the pinned
  suite still owns the accuracy claim.
- Two candidate-prompt gaps remain, and AD-012 prohibits tuning either by hand.
  Both are recorded in [Decision Log](../experiments/decision-log.md):
  - The extraction text fabricates a payment method when the message names
    none, including the literal placeholder it forbids. It fails safe, because
    deterministic resolution refuses words matching no configured source, but
    it costs the owner a question the documented default should have answered,
    and it defeats that default path whenever the configured source is not
    named exactly as the model guessed. Gap for `ingestion/expense-it`.
  - The category-fit text prefers a new proposal over an adequate existing
    category, sometimes proposing a name the taxonomy already has while its own
    rationale cites that name as the match, and names a non-root parent.
    Deterministic acceptance refuses both, so the run clarifies with
    `category_parent_invalid` instead of drafting. The purpose boundary is now
    the binding constraint on end-to-end behaviour. Gap for
    `categorization/purpose-it`.
  - The worst instance of that gap is the split-funding journey J4, which is a
    V1 acceptance example. On `Ho speso 50 euro al supermercato, 30 con i buoni
    pasto e 20 con la carta` the candidate proposes `Spesa con buoni e carte`,
    a category defined by payment method, and its own rationale states that it
    is classifying by payment method rather than purpose. That contradicts the
    instruction text and collapses the two allocation dimensions AD-007 keeps
    independent. It reproduced identically on three consecutive runs at the
    screened profile's sampling settings, so it is systematic rather than a
    sampling artifact.
  - Deterministic acceptance rejected that proposal, but only incidentally: the
    veto that fired was the non-root parent, not the dimension confusion. Had
    the candidate named an active root as its parent, a payment-method category
    would have reached the owner for approval. The taxonomy authority rules
    therefore do not currently express `AD-007`'s separation of purpose from
    funding, and `categorization/purpose-it` must measure it directly. No rule
    is added here, because inventing one by hand is exactly the ad-hoc policy
    change AD-012 reserves for a gated experiment.
- Owner category decisions are implemented as `CategoryReviewService`, the only
  component that can extend the taxonomy. Approval creates the node, attaches
  the whole signed total, and posts the movement in one transaction, so no
  state exists in which a category was created for a movement that never
  received it. The node is validated by extending the real taxonomy aggregate
  rather than by restating its rules. Both reuse branches resolve through
  active names and aliases and record the owner override; a re-proposal
  inherits every refused candidate and the loop is capped; cancelling discards
  the draft. Settling updates the proposal row only while it is still pending,
  so a replayed decision is refused rather than applied twice, which is what
  makes the branch safe for a resumed thread.
- Cancelling a review exposed a missing domain state. `voided` requires
  reconciliation, because voiding reverses a movement that already counted,
  while a cancelled draft never counted and has no purpose dimension. Migration
  `20260903_0004` adds a `discarded` terminal state instead of weakening that
  invariant; the existing reconciliation trigger already applied only to
  `posted` and `voided`, so it needed no change.
- Real PostgreSQL tests approve a candidate into a stored node and a posted
  reconciled movement, prove the settlement atomic by making a second review
  claiming the same name fail whole after the first won, post through reuse
  without extending the taxonomy, discard a cancelled draft, open a successor
  that carries the refused candidate, refuse a replayed decision, and list the
  open reviews routing correlates against.
- The paused owner category review is a LangGraph two-node loop over the same
  checkpointer. The asking node renders the question from the authoritative
  stored proposal and pauses; the settling node applies the branch and decides
  whether the review is over. Only reads precede the pause and every write
  follows it, because LangGraph replays an interrupted node from its start: one
  node holding both would replay its settlement the second time around the
  loop, when the cached first interrupt returns immediately. Replay safety does
  not rest on the topology alone, since settlement independently refuses a
  review that is no longer pending.
- A review decision that cannot be applied keeps the review open and pauses
  again with a stable reason and no write: an unresolvable name, an empty reuse
  shortlist, a capped loop, or a replacement the taxonomy refused. A
  re-proposal re-invokes the purpose boundary with every refused candidate
  excluded, and classifies the stored movement's own description rather than
  the original message, so a checkpoint that may wait indefinitely holds no raw
  owner text it does not need. Resume payloads are validated rather than
  trusted. Unrelated input needed no branch: deterministic acceptance already
  requires a category follow-up to correlate with exactly one pending review,
  so the paused thread keeps waiting while the message is routed on its own.
- Real PostgreSQL tests pause a review, reopen it on a separate connection with
  its state still JSON round-trippable and its next node still the asking one,
  resume it from that checkpoint into a created node and a posted reconciled
  movement, discard a cancelled draft, and persist a re-proposal's successor
  carrying the refused candidate.
- The read-only query and advice routes are one bounded loop with a single
  model decision. Each route owns a scoped registry of named tools, and the two
  registries genuinely differ: the advice survey is unreachable from a question
  and the single-budget lookup is unreachable from advice. No mutating,
  deterministic, or inert route owns a registry at all. The offered vocabulary
  is rendered from the registry rather than retyped, so a model is never
  offered a tool acceptance would refuse, and the vocabulary contains no
  generic query member.
- Acceptance admits a request against the registry of the routed intent, taken
  from trusted context rather than from the model, so a model cannot widen its
  own scope by claiming a different route. Every figure then comes from the
  domain read models over stored records: the model supplies a tool name, an
  owner-named category, and at most a calendar month, and never an amount. The
  reporting service depends on a port exposing only reads, so a mutation is
  absent from the object graph those nodes reach rather than merely
  disallowed. The tool-grounding and forbidden-tool gates are therefore
  properties of the topology, though the pinned suites still own the selection
  accuracy claim.
- Absence is reported rather than smoothed: an unconfigured budget is stated as
  missing, posted spend with no budget to compare against is named, and an
  unresolvable category becomes a question instead of a zero. The advice survey
  is ordered worst-position-first deterministically rather than by a model's
  sense of severity.
- Real PostgreSQL tests read a month bounded by the real occurrence column,
  exclude an unposted draft, separate benefits from personal funds, read a
  stored budget, order the survey, and name posted spend that has no budget.
- Dataset-backed workflow suites are executable. A suite loads a JSONL dataset,
  identifies it by content hash rather than by filename, refuses a repeated
  case identifier or mixed dataset versions, scores every gate as data rather
  than judgment, and writes a manifest plus a compact score summary. A case
  record carries labels and tags but never prose, so a committed summary cannot
  reconstruct anything private. Promotion eligibility requires both every
  blocking gate and the dataset minimum, and the runner refuses to compute it
  any other way.
- `routing/intent-it` v1.1.0 holds 201 authored synthetic Italian cases and is
  the first dataset in this repository to reach its promotion minimum. It
  covers every routable intent and the risk classes the evaluation matrix
  names: negation, hypothetical and reported speech, compound requests, prompt
  injection, benefit credits, income, relative dates, thousands separators,
  amounts written as words, typos, and every deterministic pre-routing branch.
  Version 1.1.0 carries all 112 v1.0.0 cases forward unchanged and adds 89, so
  the earlier run keeps its meaning; a unit test asserts that carry-forward
  field by field rather than trusting it.
- Measured against the screened Qwen3 14B profile at a 0.5 confidence bar,
  v1.1.0 fails three blocking gates. Deterministic pre-routing scored 16/16 and
  no expense or refund was demoted to a read-only route.
  - `read_only_input_routed_to_write` counted 10 of 70 against a zero-tolerance
    gate. Every one of them is the same blind spot, and none came from a
    question, an advice request, or a greeting: the router has no concept of
    money *arriving* that is not a refund. Six benefit credits and four income
    reports were forced into a movement route, seven as refunds and three as
    expenses. `Ho caricato la carta welfare con 250 euro` and `Registra
    l'entrata dello stipendio, 1800 euro` were both read as spending.
  - `ambiguous_safely_clarified` reached 0.708 against a 0.98 gate. The ten
    credit cases dominate it; the rest are a bare `Spesa` read as an expense
    and two advice questions read as queries.
  - `macro_f1` reached 0.8955 against a 0.95 gate.
  Every remaining miss is safe: an unrecognised banking term, a terse review
  approval, and two out-of-scope requests all failed towards asking or towards
  another read-only route, and a prompt-injection attempt landed on unsupported.
- The finding is sharper than the ad-hoc J5 note recorded earlier in this step.
  That note read as a phrasing problem around one sentence. Measured across
  sixteen credit and income cases, it is a missing concept: V1's taxonomy has a
  destination for incoming money it does not handle, and the candidate router
  text never teaches the model that such a destination exists. No prompt change
  was attempted, because AD-012 reserves that for a gated experiment and this
  run is the evidence such an experiment would start from.
- `ingestion/expense-it` v1.0.0 holds 170 authored synthetic cases against a
  fixture profile with a default card, cash, meal vouchers, a company card, and
  an archived source, reaching its 150-case minimum. It covers the risk classes
  the matrix names for extraction: Italian decimal commas and thousands
  separators, amounts written as words, every temporal form the resolver
  accepts and eight it refuses, explicit calendar dates, twelve splits, twelve
  refunds, negation, quoted expenses, multiple numbers in one message, typos,
  and eight funding words the profile does not configure. Every date label was
  checked against the real resolver before the first run, and one case was
  rewritten rather than relabelled when its outcome turned out to depend on
  which words the model chose to copy.
- Measured against the screened Qwen3 14B profile, the boundary is accurate on
  everything a message actually states, and fails three blocking gates on
  everything it does not.
  - Exact: `amount_exact_match` 162/162, `occurrence_semantic_exactness`
    152/152, `split_parts_exact_and_reconciled` 12/12,
    `expense_refund_polarity` 170/170, and
    `schema_validity_after_one_repair` 170/170. Warm latency was a 2.0 second
    median and a 2.7 second p95 per case.
  - `fabricated_merchant_date_or_payment_field` 0.682 against a 0.01 gate: 116
    of 170 cases supplied a field the message leaves empty.
  - `funding_source_and_class_match` 0.855 against a 0.97 gate, and
    `required_ambiguity_reported` 0.929 against a 0.98 gate.
- The shape of the fabrication is the finding, not its rate. The model does not
  choose the configured default; it fills the funding slot with whichever noun
  is nearest. `Ho speso 250 euro di affitto` returned a payment part named
  `affitto`, `Ho speso 5 euro di pane` returned `pane`, `ho spes 20 eur di
  spesa` returned the currency token `eur`, and `Mi hanno rimborsato 20 euro`
  returned `non specificato`, which is the placeholder the instructions forbid
  by name in three other spellings.
- Purpose nouns appearing as payment methods is the AD-007 dimension collapse
  running in the opposite direction from the one `categorization/purpose-it`
  measured. There, purpose absorbed funding and proposed `Spesa con buoni e
  carte`. Here funding absorbs purpose. One defect, two directions, and neither
  boundary is shown the other's dimension.
- The `fabricated_funding_silently_resolved` diagnostic is 0.845 and corrects a
  claim previously recorded in the decision log and in the development guide.
  The fabrication does not fail safe. 98 of 116 invented words name a real
  active source, usually the default card, so deterministic resolution accepts
  them; only the 18 that are purpose nouns are refused. On a profile whose
  default is cash rather than a card, the accepted guess would charge the wrong
  resource with nothing to catch it.
- The two `required_ambiguity_reported` misses are worse than an unasked
  question. `amount_invented_where_none_stated` is 0.25: `Ho fatto benzina
  stamattina`, a message carrying no figure at all, returned 2500 cents, and
  because the same answer also invented a resolvable funding word the case
  assembled into a storable draft built on a total the owner never wrote.
- Taken with the categorization result, one hypothesis now covers both. This
  model does not reliably leave an optional field empty. Categorization filled
  `proposed_name` while also filling `category_name`; extraction fills
  `payment_parts`, `merchant`, and even `amount` where the message is silent.
  Both boundaries are flat schemas with conditional fields, chosen because no
  candidate is capability-screened for union output. Reliable omission of an
  optional field therefore joins union output as a capability worth screening
  in Step 1, rather than a prompt worth revising in Step 3. No prompt or schema
  was changed, because AD-012 reserves that for a gated experiment and these
  runs are the evidence one would start from.
- `categorization/purpose-it` v1.0.0 holds 153 authored synthetic cases against
  the real seed taxonomy, reaching its 150-case minimum. It covers 21 of the 24
  active nodes, 18 safety cases that foreground a merchant, brand, or payment
  method, 12 purposes the taxonomy genuinely does not cover, and 8 alias traps.
  Every safety case carries the exact tokens its message foregrounds, so the
  zero-tolerance dimension gate is checked deterministically rather than by
  reading. The three uncovered nodes are roots whose children partition their
  space, so no realistic message resolves to them directly.
- Measured against the screened Qwen3 14B profile, the suite fails four
  blocking gates and the result is much worse than routing's:
  - `purpose_label_agreement` 0.124 against a 0.85 gate.
  - `false_new_category_proposal` 0.929 against a 0.05 gate: 131 of 141 cases
    with an adequate existing node produced a proposal instead.
  - `merchant_or_payment_method_proposed_as_category` counted 2 against zero
    tolerance, confirming the ad-hoc `Spesa con buoni e carte` finding as a
    measured safety failure rather than an anecdote.
  - `duplicate_or_alias_equivalent_proposal` 0.105 against a 0.02 gate.
  The two auto-post gates are emitted at zero denominator, because no auto-post
  policy is promoted and the eligible subset is therefore empty by
  construction. They are kept visible rather than omitted so a reader sees that
  they exist and are unmeasured.
- One diagnostic reframes the failure. `existing_category_named_while_proposing`
  is 0.497: in 71 of 143 proposals the model *also* named a real active
  category, having identified the right answer and then selected the wrong
  discriminator. `Ho speso 43 euro di spesa al supermercato` returned outcome
  `proposal` with both `category_name` and `proposed_name` set to
  `Spesa alimentare`. This is therefore not primarily a knowledge gap. The
  flat-schema-with-conditional-fields shape, chosen because no candidate was
  capability-screened for union output, is a live suspect, which makes union
  output a capability worth screening rather than only a prompt worth revising.
  The harness was verified against the real endpoint before this was recorded:
  the instruction text does carry all 24 category names.
- `workflow/category-review-it` v1.0.0 holds 37 authored multi-turn scenarios
  against real PostgreSQL, above its 30-scenario minimum, covering all seven
  branches the matrix gates plus five replayed decisions. It is the first suite
  whose gates are invariants rather than quality trends, so its model port is
  scripted: substituting the real endpoint could only let a model failure mask
  or mimic the wiring failure the suite exists to find. Postconditions are read
  back from storage rather than from returned state, because a graph reporting
  an approval while writing nothing would satisfy a state-only check.
- Seven of the eight blocking gates pass at 100%. Approve, best-existing,
  named-existing including alias and accent resolution, the three-proposal cap
  and its transition to manual resolution, cancel, and independent handling of
  concurrent work all hold their postconditions exactly, and
  `duplicate_writes_under_replay` counted zero across every replayed branch.
- `repropose_excludes_rejected` fails at 0.714 on a real defect. `run_settle`
  catches `TaxonomyError` once and maps every instance to
  `unknown_category`, but that error has two distinct causes. From the reuse
  branch it does mean the owner named a category that does not exist. From the
  re-proposal branch it means the replacement candidate was refused by taxonomy
  governance, either as a duplicate of an active node or as a name already
  rejected in this review, which the reason vocabulary defines as
  `invalid_candidate`. The channel would ask the owner which category they meant
  when the correct response is that the candidate is unusable. The re-proposal
  branch already returns `invalid_candidate` for its other refusals, so the
  intent is clear and the outer handler simply catches too broadly.
- A diagnostic records a second, latent defect. `pause_payload_survives_restart`
  is 0.0 across four scenarios: the strict msgpack allowlist that forbids pickle
  fallback also refuses to reconstruct `ReviewQuestion`, `ReviewDecision`, and
  `CallbackAction`, so a review paused in one process reads its question back as
  a plain mapping. No field is lost, and nothing consumes the question across a
  restart yet, so this costs nothing today. It will cost the channel adapter
  immediately, because a persistent checkpointer exists precisely so a review
  can outlive the process that opened it. `_decision_from` already accepts either
  shape on the resume side; the question side has no equivalent.
- The fixture extends the seed taxonomy with one aliased node and one archived
  node, because the accepted taxonomy declares neither and both branches are
  gated. Writing the aliases surfaced a third observation: `bootstrap_profile`
  inserts seed categories without their aliases. That is invisible today, since
  the seed taxonomy has none, but a future taxonomy carrying an alias would lose
  it silently at bootstrap.
- Five replay expectations were corrected after the first run, and the reason
  matters for how the correction should be read. Replaying a decision on a
  review that already ended is a no-op: the thread has reached its end, so the
  graph has nothing to resume, returns the stored terminal state, and writes
  nothing. The original labels expected an `already_settled` clarification,
  which is defence in depth for a thread still mid-flight rather than the
  behaviour of a completed one. This was a misreading of a deterministic
  contract that could have been established by reading it, not a threshold moved
  to fit an outcome, and the gate the matrix states, zero duplicate writes,
  passed unchanged before and after.
- The purpose boundary is confirmed as Step 3's binding constraint. Routing
  fails three gates with most errors landing safely; categorization fails four
  with a majority of cases producing an unusable judgment. The two suites that
  measure model judgment fail broadly, while the two that measure deterministic
  behaviour, extraction of what a message states and the review workflow's
  postconditions, are close to exact. The remaining work in this step is a model
  and boundary-shape problem, not a workflow problem.
- Four routing dataset labels warrant owner review before the next run, and
  none was changed after seeing the outcome. `Mio fratello ha speso 50 euro` and `Quanto
  costa un iPhone?` were labelled clarification and routed to non-finance and
  unsupported. `Come sto andando con le spese questo mese?` and `Come sono
  messo con i budget?` were labelled advice and routed to query. All four model
  answers are defensible, so these may be annotation errors rather than routing
  errors; every case records `reviewer_status: unreviewed`, which is what that
  field is for.
- `money-tracker-admin profile-init`, `profile-list`, and
  `funding-source-add` make the finance components reachable without
  hand-written Python. Until they existed, every command taking a profile
  identifier, including the Step 2 receipt import, required an operator to
  create a profile through an ad-hoc script and to query the database for its
  identifier. They refuse a second owner, an unknown profile, a blank name, a
  name repeating an existing source, and a second default.
- `money-tracker-admin route` sends one message through the real compiled
  router and prints only the decision it reached. It takes the trusted routing
  context from flags rather than the database, so it needs no active profile
  and the policy vetoes are reachable by hand. It requires an explicit
  confidence bar, completes a deterministic route with no model involved, names
  the missing service when the local endpoint is unreachable rather than
  surfacing a transport traceback, and echoes no message content. Given
  `--profile-id` it continues a movement route into the expense workflow and
  reports that outcome too, which is how the candidate-prompt gaps below are
  reachable by hand.
- Workflow state is a versioned contract of JSON primitives and stable string
  references. Round-trip tests reconstruct the message, the trusted context,
  and every deterministic and model decision, and a real checkpoint written to
  and read back from PostgreSQL across separate connections is asserted to
  contain no reconstructed rich object. A round-trip test caught and fixed a
  real defect: the decision's own callback action differs from the message's
  whenever an uncorrelated callback is clarified, so the two are stored
  separately.
- The local adapter requests the exact strict `json_schema` mechanism the Step 1
  capability screen validated against the real llama.cpp build, returns raw
  text so retry policy stays deterministic, and refuses non-text content rather
  than coercing it. A recording chat model verifies the request and repair-turn
  shape without an inference server. The opt-in real-endpoint test has been
  executed against a running local server and passes; it asserts schema-valid
  boundary behaviour only, against the screened Qwen3 14B profile. No routing
  accuracy gate is met by it: a gate needs the pinned dataset, and one ad-hoc
  pass over eight journeys is not a measurement.
- Two hundred eighteen workflow-contract unit tests cover the taxonomy
  authority invariants, the
  message contract, command parsing, every pre-routing precedence branch, every
  category-review callback branch, every acceptance veto, every extraction and
  category-fit cross-field invariant, the occurrence resolver including both
  daylight-saving edges, every allocation shape and refusal in both dimensions,
  and every branch of the retry boundary through a scripted port
  implementation. With the state conversions and the runtime assembly, the
  `workflows` and `runtime` components pass at 100% branch coverage. Both resolved
  dimensions are verified by building a real domain aggregate rather than
  restating its rules, and routing decisions are asserted to contain no owner
  text and only small serializable values and stable references.
- No exit criterion is met yet. Every gate in the
  [Evaluation Matrix](../experiments/evaluation-matrix.md) requires the model
  half, the specialist workflows, and pinned datasets that remain open.
