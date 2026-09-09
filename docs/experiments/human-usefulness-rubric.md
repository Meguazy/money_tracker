# Human usefulness rubric for advice

- Rubric ID: `human-usefulness-v1`
- Applies to: `advice/grounding-it` non-injection cases
- Reviewer: the owner only
- Scale: one integer from 1 to 5 per case
- Gate: median at least 4, after all 52 applicable cases are scored

## What to review

Score the structured result produced by the exact run, with the synthetic Italian
request beside it. Judge whether that result would help you make the financial
decision or understand the requested state. Do not consult the expected label or
the automated pass/fail result while scoring. Prompt-injection cases are excluded:
they test safe refusal, not advice usefulness.

| Score | Meaning |
| ---: | --- |
| 1 | Misleading, harmful, or unusable for the request. |
| 2 | Mostly unusable; substantial information or direction is wrong or absent. |
| 3 | Partly useful, but it needs a major correction or important missing context. |
| 4 | Useful and actionable; only a minor clarification or presentation improvement is needed. |
| 5 | Clear, complete, and immediately useful for the request. |

Use usefulness rather than style preference. A concise correct result may score 5.
A fluent result that answers a different period, category, or funding question
should score at most 2. A refusal can score above 1 only when declining is useful
for the actual request and says what information is missing.

## Recording procedure

Run the suite interactively against the pinned candidate:

    python -m money_tracker.evaluations.cli advice \
      --model-profile <profile-id> --review-human-usefulness

After producing all 52 applicable results, the runner prints a loopback URL.
Open it in a browser to review one readable case card at a time. The page formats
money amounts, summarizes budget results, keeps the raw JSON in a collapsible
section, accepts the keys 1–5, and saves the current draft in browser local storage.
It has no external assets and is available only from the host at `127.0.0.1`.

`Submit all ratings` is enabled only when every case has a score. On
submission, the runner validates the complete score set and automatically writes
`human-usefulness-review.json` in the raw run directory. The file contains the
rubric ID, run ID, dataset identity and hash, each reviewed case, the observed
structured result, and the owner score; no manual editing is required. The
manifest reports the median computed from those entries. The committed score
contains only the aggregate metric. Use `--human-review-port <port>` if the
default port `8765` is occupied.

For a new run after a versioned workflow change, reuse an earlier completed
review and show only changed results with:

    python -m money_tracker.evaluations.cli advice \
      --model-profile <profile-id> --review-human-usefulness \
      --reuse-human-usefulness-review <prior-run>/human-usefulness-review.json

A score is reused only when the rubric, owner disposition, case identifier,
message, outcome, and structured answer all match. The new run receives a full
sidecar containing reused and newly entered scores; any changed result must be
scored again.

If the owner explicitly defers review to Step 5, use
`--defer-human-usefulness`. That records an inapplicable gate with the deferral
reason and writes a pending-free sidecar; it does not invent a score or treat an
automated judge as a human reviewer. Review and deferral options are mutually
exclusive. Numeric grounding and authorization gates remain independently
blocking and cannot be overridden by this score.
