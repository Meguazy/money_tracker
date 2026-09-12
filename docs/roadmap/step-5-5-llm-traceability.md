# Step 5.5: Full LLM call traceability

Part of the [delivery roadmap](../roadmap.md).

This step is numbered 5.5 because it was inserted between two already-documented
steps. Renumbering Steps 6 and 7 would have rewritten dated evaluation records
under [`docs/experiments/`](../experiments/README.md) that name them, and those
are evidence rather than plans. Do not renumber them later for tidiness.

## Prerequisites

Step 5 is complete. The Langfuse stack delivered in
[Step 1](step-1-platform-and-evaluation.md) is healthy and digest-pinned, and
the owner has accepted the trace-content decision this step records.

## The gap this step closes

Langfuse is installed but the product does not use it. Only two paths in the
repository emit anything:

- `money_tracker.cli` attaches callbacks to `money_tracker.graph`, which is the
  initial one-node assistant scaffold rather than any promoted workflow.
- `money_tracker.evaluations.publication` copies redacted aggregate capability
  scores under `--publish-langfuse`.

Every LLM call the product actually makes is untraced. The structured-output
boundary in `money_tracker.runtime.model` invokes the chat model with no
callbacks, and `money_tracker.runtime.dispatcher` invokes each compiled graph
with a configuration carrying only a LangGraph thread identifier. The worker,
the Telegram gateway, and the administration CLI never import the observability
module at all. The router, expense/refund, correction/undo, category-review and
read graphs therefore produce no traces in the runtime that Step 4 accepted.

No roadmap step ever owned that wiring: Step 1 delivered the stack and the
aggregate publication, and Step 7 assumes traces already exist when it schedules
their retention and deletion. This step owns it.

## Owner decision recorded by this step

Traces must carry everything, at both data and metadata level: complete prompts,
completions, and decision context, not pseudonymous summaries. Langfuse is a
local, owner-controlled store on the same workstation as the authoritative
database, so full fidelity does not widen the
[AD-004](../arc42/decisions/AD-004-local-first-telegram-boundary.md) privacy
boundary the way an external sink would.

This supersedes the current cross-cutting statement that traces use pseudonymous
identifiers, so the step must record a new ADR and update the affected arc42
sections rather than leaving the two descriptions in conflict. It does not relax
the separate rule that owner content stays out of logs: the journal and the
trace store have different audiences, different retention, and different
exposure in a support report.

## Deliverables

- A tracing boundary that reaches the real inference path: callbacks and run
  metadata flow into the structured-output model and every compiled graph, from
  every entry point that executes them, without those components importing the
  Langfuse SDK directly.
- Complete call payloads: system instructions, owner message, repair turns, raw
  completion, parsed contract, and the validation errors that triggered the
  single schema retry.
- Model artifact identity on every call: model alias, GGUF and chat-template
  digests, server version, temperature, timeout, and context size, reusing the
  identity fields the capability report already collects so a trace names the
  exact artifact that answered.
- Versioned contract and policy metadata: router, extraction, category-fit and
  tool-selection versions, the active read-selection policy, and the routing
  confidence threshold in force.
- Deterministic decision metadata beside the model output: routed intent, stable
  reason code, confidence, policy vetoes, allocation resolution reasons, the
  admitted read tools, and their grounded results.
- End-to-end correlation: one Telegram update, its durable inbox work unit, the
  worker claim, the dispatched specialist, every model call, and the committed
  movement or rendered response belong to one navigable trace, keyed by the
  existing source reference and thread identifiers.
- Cost and performance observations: token counts, per-call and per-node
  latency, retry counts, and endpoint failures.
- Evaluation linkage so a promoted version can be traced back to the dataset run
  that promoted it, extending the existing aggregate publication rather than
  replacing it.
- Lifecycle and degradation: an explicit enable/disable switch, flushing that
  suits long-running services rather than only short-lived commands, and bounded
  behavior when Langfuse is slow, full, or stopped.
- Trace retention, export, and deletion procedures, plus an explicit decision on
  whether the Langfuse volumes enter the backup set or are treated as a
  reconstructible observability copy.
- Corrected current-state documentation: the operations guide currently lists
  Langfuse callbacks among executable operations and arc42 lists a Langfuse
  adapter component, both of which overstate what the scaffold CLI actually
  covers today.

## Exit criteria

- Every LLM call made by the promoted workflows appears in Langfuse with its
  complete input, output, and the model artifact identity that produced it.
- One owner message produces one correlated end-to-end trace spanning ingestion,
  dispatch, inference, and commitment.
- With Langfuse stopped, core finance, Telegram, worker, API, and dashboard
  behavior is unchanged, and the added latency stays inside a stated budget when
  it is running.
- No credential, token-bearing URL, database URL, or receipt-archive path
  appears in any trace; receipt bytes remain outside the trace store.
- Trace retention and deletion are documented and exercised, not deferred.
- Real integration tests verify trace emission against a running local Langfuse
  instead of a mocked SDK, and verify the disabled path emits nothing.
- A new ADR records the full-fidelity trace decision and its consequences, and
  arc42, the operations guides, and the experiment documentation agree with it
  in the same change.

## Evaluation impact

This step changes no model, prompt, routing threshold, tool contract, taxonomy
policy, or receipt pipeline, so it triggers no promotion gate and EVD-004
remains the promoted text baseline. Instrumentation must not alter the prompts
or schemas sent to the endpoint; if capturing a payload would require changing
what is sent, that is a contract change and needs its own evaluation evidence.
