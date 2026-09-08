# Step 3 evaluation recovery — 2026-09-08

- Status: running
- Scope: Step 3; AD-006, AD-007, AD-008, AD-012
- Production status: experimental; no prompt, model, threshold, or auto-post
  policy is promoted.

## Baseline and hypothesis

The uncommitted work introduces model-facing union schemas and versioned prompt
catalogs. The published September 3 runs substantially improve routing,
extraction, and categorization, but five suites still fail a blocking metric.
The September 8 tool run `TOO-20260908-091335-296d2481` executed none of its 110
cases and nevertheless reported a pass. That result is invalid evidence. The
pending correction rejects completely errored runs but still admits partially
executed runs and treats unmeasured blocking metrics as passing.

This recovery first makes the reports fail closed, then measures the existing
candidates without changing dataset labels or relaxing thresholds. A completed
run, measured blocking metrics, and complete model identity are prerequisites
for any promotion claim. A documented inapplicable metric is different from an
unmeasured required metric: the single-read graph has no dependent tool order,
whereas human usefulness and auto-post precision remain required and open.

## Fixed configuration and sampling

- Existing candidates: router v5 at confidence 0.5; extraction v6;
  categorization v6; read-tool selection v5.
- Local Qwen3 14B Q5_K_M, existing llama.cpp build and chat template, context
  8192, one slot, reasoning off, existing temperature 0.1.
- Existing pinned datasets and deterministic fixture clocks remain unchanged.
- The old datasets have already informed prompt revisions. Re-running them is
  regression/development evidence, not a fresh untouched final holdout.
- Raw manifests remain under ignored `artifacts/evals/`; only synthetic,
  aggregate results may be published.

## Gates and execution

The [evaluation matrix](evaluation-matrix.md) remains binding. No safety or
quality threshold is changed. Run unit/static checks for report integrity,
then the existing candidates through the real local endpoint. PostgreSQL suites
run sequentially against guarded `money_tracker_test` on local port 5433, with
their owned fixture data cleaned afterwards. No owner finance database is a
test target. No Langfuse publication is requested.

Compare the schemas used by the real runtime with those evaluated: a passing
experimental adapter cannot stand in for an application path using a different
schema. Record exact model, prompt/schema, dataset and source identities in
new reports. Preserve historical manifests rather than rewriting their claims.

## Results and decision

Pending. Missing human review, auto-post calibration, untouched holdout
evidence, and failed metrics must stay visible. The roadmap remains in progress
until all exit criteria are supported by evidence.
