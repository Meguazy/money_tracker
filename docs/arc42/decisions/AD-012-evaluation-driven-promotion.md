# AD-012: Evaluation-Driven Model and Prompt Promotion

- Status: accepted
- Implementation: planned
- Date: 2026-09-01

## Context

Local models vary in Italian comprehension, structured output, routing, tool
calling, and vision quality. Self-reported model confidence is not a sufficient
control for financial writes.

## Decision

Version evaluation datasets and deterministic graders in the repository. Keep
private financial examples and receipt media outside Git. Record reproducible
run manifests containing code, prompt, graph, schema, taxonomy, tool, model,
GGUF, llama.cpp, and hardware versions.

Production prompts and schemas remain code-owned. Langfuse is an optional local
trace, dataset, and score viewer rather than a runtime source of truth. Promote
a model, prompt, routing threshold, or receipt extractor only after the relevant
gates in the evaluation matrix pass. Record the outcome in the experiment
decision log.

## Alternatives considered

- Manual spot checks are fast but do not prevent regressions.
- Model-as-judge alone is nondeterministic and cannot prove monetary or tool
  correctness.
- Runtime prompts controlled only from Langfuse would make daily operation
  depend on the observability stack.

## Consequences

- Exact and rule-based graders precede subjective review.
- Model-as-judge may supplement, never replace, human labels and deterministic
  safety gates.
- Changes to prompts, schemas, tools, or taxonomy require targeted regression
  runs before promotion.
