# AD-006: Bounded Typed Agent Workflows

- Status: accepted
- Implementation: checkpoint foundation, deterministic root routing, the typed
  router and extraction model boundaries, and deterministic occurrence-time and
  sign resolution implemented; specialist subgraphs planned
- Date: 2026-09-01

## Context

Expense recording is a consequential write, whereas questions and advice are
read-only and exploratory. A single unconstrained tool-calling agent would make
routing, validation, authorization, and regression testing unnecessarily hard.

## Decision

Use a typed LangGraph root router and specialist subgraphs. Deterministic graph
nodes own financial mutations. Only query and advice workflows may use a
bounded agent loop, and those workflows receive read-only domain tools.

All LLM boundaries use strict versioned schemas. Runtime context injects the
profile, timezone, services, and idempotency identifiers; the model cannot
choose them. Structured-output parsing receives one corrective retry and then
routes to clarification instead of looping indefinitely.

Use persistent interrupts for category approval and other human decisions.
Place side effects after interrupts and make them idempotent because resumed
nodes can be replayed.

## Alternatives considered

- One general agent is initially compact but expands the mutation and prompt
  injection surface.
- Fully deterministic natural-language parsing cannot handle the required
  variety of Italian input.
- Independent autonomous agents complicate shared state and ordering without a
  demonstrated need.

## Implementation evidence

Roadmap Step 1 supplies a narrow PostgreSQL checkpointer factory with
`autocommit`, dictionary rows, the dedicated `workflow` search path, and strict
message serialization enabled by default. Bootstrap invokes the checkpointer's
real schema setup, and an integration test writes and reads a checkpoint across
separate connections.

Roadmap Step 3 implements the framework-independent `workflows` component. The
bounded intent taxonomy partitions every destination into exactly one authority
class, so a read-only, inert, or non-finance route cannot reach a mutation, and
the two deterministic-only routes cannot be selected by a model. Deterministic
pre-routing resolves explicit interface decisions, image media, supported
commands, and blank input by a total precedence order without consulting a
model, and it clarifies an uncorrelated callback rather than applying it to an
arbitrary pending action.

The strict versioned `RouteClassification` schema forbids unknown fields,
requires a short verbatim evidence span, records explicitly declared unknowns,
and exposes no profile, timezone, funding-source, authorization, or idempotency
field. Deterministic acceptance then vetoes a category follow-up without
exactly one correlated pending review, a correction or undo without a
runtime-resolved movement, and any decision below an explicitly supplied
confidence bar; every veto falls back to clarification.

The confidence bar is a required argument with no default. This decision record
does not authorize a value: calibration on the pinned routing dataset and an
entry in the experiment decision log precede any runtime use.

The versioned `ExpenseExtraction` boundary applies the same rule to expense and
refund candidates. It is versioned independently of the router boundary, keeps
every declared unknown mutually exclusive with its own value, requires a split
payment to quantify every distinct portion, and exposes no resolved instant and
no signed amount. Occurrence time and amount sign are resolved by pure
deterministic code instead: the model reports the owner's temporal words
verbatim and a positive magnitude, so timezone, daylight-saving, and sign
arithmetic stay exactly reproducible across a retry, a resume, and an
evaluation run.

Funding-source resolution, category fit, specialist subgraphs, production
prompts, the model call and its single corrective retry, persistent interrupts,
and workflow state are not implemented yet.

## Consequences

- Tool registries are scoped per workflow; no generic SQL tool is permitted.
- Workflow schemas and node names require versioning and migration care for
  interrupted threads.
- Routing, extraction, tool selection, and execution are evaluated separately.
