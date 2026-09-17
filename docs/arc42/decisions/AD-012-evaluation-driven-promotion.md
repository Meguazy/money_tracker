# AD-012: Evaluation-Driven Model and Prompt Promotion

- Status: accepted
- Implementation: executable gates and the EVD-004 text stack promotion
  implemented; later model-visible capabilities remain gate-controlled
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

## Implementation evidence

Roadmap Step 1 adds versioned synthetic capability cases, ignored private/raw
artifact locations, deterministic machine-readable reports, and collection of
model alias, GGUF/build/template/context/projector metadata. Real
OpenAI-compatible probes cover strict structured output, tool calls,
tool-result continuation, and optional images. Local reports remain available
without Langfuse; publication is explicit and aggregate-only.

EVD-004 records the passing Step 3 promotion of `text-qwen3-14b`, router v6,
extraction v6, category-fit v6, tool-selection v5, routing confidence 0.5, and
`explicit-finance-scope-v1`. The read-only browser advice slice reuses that
stack without changing a prompt, schema, tool registry, threshold, routing
intent, or policy, so it does not constitute a new model-visible promotion.

Roadmap Step 3 makes the candidate catalog executable rather than descriptive.
A run may declare the profile it is measuring and is refused when either the
configured weights or the running inference server disagrees with it, which is
the failure that would otherwise attribute one candidate's answers to another
without any downstream signal. Raw runs and published scores are keyed by
the model that produced them, so measuring a second candidate no longer
overwrites or interleaves with the first and the two can be compared. The model
is named on every surface a score is read on. A sweep script owns the candidate
switch end to end, because the switch is two steps that must agree and only one
of them is visible from the runner.

Step 5.5 makes a promotion traceable from runtime. `money_tracker.workflows.promotion`
records EVD-004 as code: decision, model profile and weights digest, pinned
prompt, threshold, and policy versions, and the run identifier of each of the
six suites. A unit test requires every run to be published, eligible, and on
the promoted weights, the decision log entry to name the same runs and digest,
and the runtime defaults to equal the pinned versions, with the router,
extraction, and tool-selection instruction texts hashing to what their runs
recorded. Runtime traces carry the decision and runs when their versions match,
and `money-tracker-eval publish-promotion` publishes the committed evidence to
the local store. The record adds no promotion: a new one still needs its gates
and a decision-log entry first.

## Consequences

- Exact and rule-based graders precede subjective review.
- Model-as-judge may supplement, never replace, human labels and deterministic
  safety gates.
- Changes to prompts, schemas, tools, or taxonomy require targeted regression
  runs before promotion.
- Comparing two candidates requires them to share a dataset and an instruction
  candidate. Both are published beside every score, because a row measured on
  different instructions reports a different experiment rather than a worse
  model.
