# AD-006: Bounded Typed Agent Workflows

- Status: accepted
- Implementation: checkpoint foundation implemented; typed workflows planned
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
separate connections. The typed router, specialist nodes, interrupts, and
product workflow state are not implemented yet.

## Consequences

- Tool registries are scoped per workflow; no generic SQL tool is permitted.
- Workflow schemas and node names require versioning and migration care for
  interrupted threads.
- Routing, extraction, tool selection, and execution are evaluated separately.
