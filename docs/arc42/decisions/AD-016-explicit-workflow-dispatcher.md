# AD-016: Explicit Workflow Dispatcher

- Status: accepted
- Implementation: implemented for every Step 3 text specialist; Telegram,
  receipt processing, commands, and owner-facing presentation remain Step 4 or
  later work.
- Date: 2026-09-08
- Supersedes: nothing. Refines
  [AD-006](AD-006-bounded-agent-workflows.md) and provides the call boundary for
  the future worker in [AD-010](AD-010-telegram-durable-messaging.md).

## Context

The root router and each specialist existed as separately compiled LangGraph
graphs, but no production component composed them. Evaluation suites invoked a
graph directly, and the administration command imperatively handed only
`expense` and `refund` decisions to a specialist. Query, advice, category
follow-up, correction, and undo ended at the routing decision.

Step 4 needs one transport-neutral call after durable ingestion. That call must
preserve the authority split in AD-006, correlate persistent owner decisions,
and avoid one union state containing every graph's model answers and domain
references.

## Decision

Use an explicit `WorkflowDispatcher` outside the LangGraph graphs. Its public
operation accepts one normalized `ChannelMessage` and a trusted
`DispatchContext`, invokes the root router once, selects exactly one Step 3
specialist from the accepted intent, and returns a typed `DispatchOutcome`.
The context carries profile scope, pending review references, the latest
correctable movement, and exact pending-action-to-movement mappings. None of
those values is model-facing.

Keep the router, expense/refund, correction/undo, query/advice, and category
review as independently compiled graphs with their existing narrow state
contracts. Give each invocation a stable namespaced thread identifier. Domain
data remains in repositories; checkpoints contain only the specialists' small
primitive states and stable references.

When expense/refund creates a pending category proposal, the dispatcher opens
the corresponding review checkpoint before returning. A correlated later work
unit resumes that checkpoint through a typed `ReviewDecision`. Explicit
callbacks already carry the branch. The framework-independent text grammar
accepts only the direct Italian J3 branch statements; anything else asks for a
clear decision and cannot be guessed into a write.

A callback action reference is not treated as a movement identifier. Trusted
runtime context maps that action to the exact movement. Stale category actions
and unresolved movement actions return clarification before a specialist write.

Receipt, command, non-finance, and unsupported decisions return a typed routed
outcome. Their transport or product behavior is outside Step 3 and remains for
the roadmap step that owns it.

## Alternatives considered

- **Compose all specialists as LangGraph subgraphs under one root graph.** This
  would require a union state spanning unrelated schemas, couple every
  checkpoint migration to the whole topology, and make the category interrupt
  share lifecycle and replay semantics with one-shot routes. The graphs already
  provide the needed bounded execution; another graph layer would add state
  ownership without adding domain behavior.
- **Keep composition in each adapter.** Rejected because the administration
  command already demonstrated the drift: it implemented only two handoffs.
  Telegram, a future API, and tests would each be able to choose different
  destinations and checkpoint conventions.
- **Let the router invoke tools or services directly.** Rejected because it
  merges classification authority with mutation/read authority and contradicts
  AD-006's specialist boundaries.

## Consequences

- Step 4's worker will construct trusted context from durable repositories and
  call `dispatch`; it will not know specialist graph nodes or state shapes.
- A routing-only administration diagnostic uses the same operation with no
  profile. Profile gating makes every finance specialist unreachable.
- Adding an intent requires an explicit dispatcher branch and typed outcome,
  making an unwired specialist visible in review and tests.
- Separate graph namespaces preserve replay and migration boundaries, while
  application-service idempotency remains the final protection for mutations.
- The dispatcher owns orchestration only. It stores no domain data and emits no
  Telegram text, credentials, media, or transport identifiers.
