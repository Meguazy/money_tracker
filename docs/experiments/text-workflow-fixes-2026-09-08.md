# Step 3 contract and evaluation corrections — 2026-09-08

- Status: implementation and development evaluation in progress
- Candidate: text-qwen3-14b; no production promotion
- Scope: Step 3, AD-006, AD-007, AD-008 and AD-012

## Pre-registered changes

The owner authorized contract, dataset and prompt corrections using Qwen3 14B.
Existing reports are historical development evidence, not an untouched holdout.
Keep thresholds and historical dataset files unchanged. Publish new versions
for corrected labels and retain explicit reasons for every changed label.

1. Six `TOO-QAD-*` cases intentionally force the query route, whose registry
   excludes the budget survey. Their expected outcome must be refusal. Add
   separate advice-route cases demonstrating that the survey is answerable.
   The dataset lint also found the six mirrored `TOO-AAD-*` exclusions with
   the same contradictory answered label; those now expect refusal too.
   A valid explicit refusal must not require selecting an unavailable or
   irrelevant tool. Unknown-category refusals may occur before execution.
2. Tool-selection v5 already distinguishes purpose and funding; category-fit v6
   already requests the most specific node. The failure analyses claiming those
   instructions are missing are inaccurate. Test clearer contracts rather than
   adding duplicate instructions.
3. Funding-class totals cannot stand in for individual funding sources or wallet
   balances. Extend the existing read-only monthly report with exact per-source
   payment totals. Balance requests remain explicitly unanswerable by these
   tools. Describe the existing budget survey's unbudgeted-spend output.
4. Clarify taxonomy labels from the product baseline, including insurance and
   purpose versus purchase occasion, and preserve reviewed-label provenance.
   Treat price lookups as unsupported V1 requests; test a class of such inputs,
   not only the previously failed iPhone example.
5. Test one versioned selection candidate and one category candidate against
   revised development datasets. Re-run extraction, routing and durable review
   for regressions, with real inference and isolated PostgreSQL. Keep the
   current router confidence 0.5 experimental.

## Validation and remaining obligations

Unit tests cover observable contract behavior and dataset consistency; real
PostgreSQL tests cover payment reporting and workflow execution. Capture exact
model, source, prompt/schema and dataset identities in each run. No evaluation
writes to the owner database or publishes to Langfuse.

Auto-post calibration, owner usefulness review and a fresh reviewed holdout are
separate required evidence. Empty denominators remain blocking. A better
development score cannot authorize automatic posting or complete Step 3.

## Results

Pending execution. This document supersedes older diagnostic claims about
missing specificity/funding instructions and a budget survey available in the
query registry; it does not supersede the historical measured scores.
