# AD-017: Versioned deterministic read-selection policy

- Status: Accepted
- Date: 2026-09-09
- Extends: [AD-006](AD-006-bounded-agent-workflows.md), [AD-012](AD-012-evaluation-driven-promotion.md), and [AD-016](AD-016-explicit-workflow-dispatcher.md)

## Context

The Qwen3 14B Step 3 sweep selected adjacent but wrong read tools for a coherent
cluster of explicit funding questions and a smaller advice cluster. Five prompt
and topology experiments moved the boundary between neighbouring cases rather
than removing the failures. No tools case failed across all four measured model
candidates, so the evidence does not support changing the registry or adding a
more capable tool.

These mistakes are observable before execution. The normalized owner message
explicitly names a configured funding source or funding class, an active category,
or an all-budget question, while the model request selects another registered
scope or abstains. The trusted runtime already owns the routed intent and the
active display names needed to recognize those narrow cases.

Any repair changes which stored facts are read. It therefore needs an explicit
version, evaluation evidence, and a direct rollback path. It must not manufacture
a profile, identifier, amount, figure, or unresolved historical month.

## Decision

Introduce a pure, framework-independent read-selection policy after model output
is parsed and before registry acceptance. The runtime order is:

1. parse the versioned model boundary;
2. apply one named read-selection policy;
3. admit the resulting request against the trusted routed-intent registry;
4. apply the existing unavailable-budget veto;
5. execute the admitted read-only tool.

`model-only-v0` returns the parsed request unchanged and is the direct rollback.
EVD-004 promotes `explicit-finance-scope-v1` after its pinned tool and advice
gates passed without a threshold change.

V1 may use only normalized message text plus active category and funding-source
display names supplied by trusted configuration. It repairs:

- explicit funding-source or funding-class spending language to the aggregate
  funding-class split, unless the message asks for a balance;
- advice that states an active category name to category spending;
- advice asking which categories lack budgets to the all-budget survey;
- a model abstention on the exact broad advice phrase to the all-budget survey.

Text that explicitly asks to ignore instructions or registries, skip
confirmation, reveal prompts, or avoid tools is never repaired; the original
request reaches the existing refusal checks. A repaired model request preserves
its parsed period. If the model abstains and
the message names another month, the policy also abstains instead of defaulting
to the current month. The resulting request still passes the same closed
registry and strict schema checks as any model request. The policy never emits a
figure and cannot reach a mutating service.

The dispatcher and administration CLI carry the policy version to the read
graph. Tools and advice reports record it independently of the prompt and schema
versions. The sweep script accepts the same version. A prior human-usefulness
review may supply scores only for current cases whose message, outcome, and
structured answer are exactly equal under the same rubric; changed results are
shown to the owner again.

## Alternatives considered

- **Revise the selection instructions again.** Rejected for this increment
  because two rewrites, few-shot examples, vocabulary changes, and a two-stage
  topology each attracted a neighbouring correct case without clearing the
  gate.
- **Add or merge tools.** Rejected because the existing tools answer the cases
  correctly when selected, and broadening a registry would weaken route-scoped
  authorization.
- **Use a second model classifier.** Rejected because it adds latency and another
  uncertain boundary to cases whose discriminating terms are already explicit
  in trusted input.
- **Apply broad keyword routing.** Rejected because balance questions and
  unstated historical periods need refusal guards; only enumerated semantic
  shapes are eligible for repair.

## Consequences

- Rollback is one configuration change to `model-only-v0`; prompts, schemas,
  datasets, and repositories do not change.
- The policy is independently attributable in every relevant run.
- Active display-name changes can change candidate matching, so callers must
  supply names from the same profile configuration used for execution.
- EVD-004 records the complete passing measurements and promotes V1. No
  threshold or dataset label changed.
- Adding another repair requires a new policy version and evidence; silently
  widening V1 is not permitted.
