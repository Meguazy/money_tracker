# Experiments and Evaluations

- Status: accepted process; Step 1 executable foundation complete
- Executable infrastructure: capability runner, dataset-backed workflow suite
  runner, and deterministic local reports
- Decision authority: [Evaluation Matrix](evaluation-matrix.md) and
  [Decision Log](decision-log.md)
- Current hardware shortlist:
  [Local Model Shortlist - 2026-09-01](local-model-shortlist-2026-09-01.md)
- Active Step 3 recovery:
  [Text workflow evaluation recovery — 2026-09-08](text-workflow-recovery-2026-09-08.md).
  The historical results below precede the uncommitted union-schema candidates.
  No candidate is promoted; missing observations cannot satisfy a blocking gate.
- Capability screening:
  [Local Model Capability Results - 2026-09-02](local-model-capability-results-2026-09-02.md)
  and its extension,
  [Local Model Capability Results - 2026-09-03](local-model-capability-results-2026-09-03.md)

Experiments determine whether a nondeterministic component is suitable for an
approved role. They do not replace software tests and they do not grant a model
authority over financial invariants.

Current corrective work and the authoritative interpretation of the September 8
failures: [Step 3 contract corrections](text-workflow-fixes-2026-09-08.md).
Historical claims below about missing union schemas, prompts, or invented
periods describe earlier candidates, not the current repository.

## Tests versus evaluations

| Mechanism | Question | Examples |
| --- | --- | --- |
| Unit test | Is deterministic production behavior correct in isolation? | Minor-unit arithmetic, allocation sums, taxonomy policy, calendar boundaries. |
| Integration test | Do real components honor their protocol and contract? | PostgreSQL transaction, llama.cpp schema output, Telegram Bot API, Langfuse ingestion. |
| End-to-end test | Does a complete user workflow work through real entry points? | Telegram message to posted expense and response; dashboard receipt correction. |
| Evaluation | Is a model/prompt/pipeline accurate and safe enough to promote? | Intent macro-F1, extraction exact match, categorization agreement, receipt line F1. |
| Experiment | Which controlled candidate/configuration best meets an explicit hypothesis? | OCR versus VLM, model quantization, routing prompt or confidence threshold. |

Correctness gates such as authorization, reconciliation, idempotency, and exact
budget arithmetic are never delegated only to a model judge.

## Executable layout

Roadmap Step 1 added an executable `evals/` area with versioned public-safe
datasets, runner configuration, deterministic scoring, and compact baselines.
Reusable runner and metadata contracts live under `money_tracker.evaluations`:

```text
evals/
├── datasets/             synthetic or anonymized committed JSONL
├── baselines/            approved compact score summaries
├── private/              local-only manifests/data, ignored by Git
└── suites/               executable suite configurations

artifacts/evals/<candidate>/   generated raw results, ignored by Git
artifacts/sweeps/<run>/       per-candidate sweep logs, ignored by Git
docs/experiments/             protocols, templates, reports, and decisions
```

Raw runs are kept under the candidate that produced them, as published scores
are. Runs written before that separation existed stay where they were written:
each manifest records the path it was written to, and rewriting a historical
manifest to match a newer layout would change a claim the run made.

List the declared candidates, and see which weights are downloaded, with:

    uv run money-tracker-eval catalog

Any suite accepts `--model-profile <id>`, which refuses to run unless the
configured weights and the running inference server both agree with the
candidate named. Switching candidates otherwise means editing `MODEL_PATH` and
restarting llama.cpp by hand, and forgetting the restart produces a complete,
plausible run measuring the previous model.

Run the local-model capability suite with:

    uv run --env-file .env money-tracker-eval capabilities

Run the Italian intent-routing suite against the real compiled router with:

    uv run --env-file .env money-tracker-eval routing --minimum-confidence 0.5

Run the Italian purpose-categorization suite against the real boundary with:

    uv run --env-file .env money-tracker-eval categorization

Run the Italian expense-extraction suite against the real boundary with:

    uv run --env-file .env money-tracker-eval ingestion

Run the suites that gate postconditions against the isolated test database
with:

    RUN_POSTGRES_INTEGRATION_TESTS=1 \
      uv run --env-file .env money-tracker-eval category-review
    RUN_POSTGRES_INTEGRATION_TESTS=1 \
      uv run --env-file .env money-tracker-eval tools
    RUN_POSTGRES_INTEGRATION_TESTS=1 \
      uv run --env-file .env money-tracker-eval advice

Measure several candidates in one go, one at a time, with:

    ./scripts/sweep_models.sh --minimum-confidence 0.5 \
      text-qwen3-14b text-ministral3-14b

The sweep resolves each candidate's weights from the catalog, restarts the
inference server for it, runs every suite with `--model-profile`, and writes one
directory per candidate under `artifacts/sweeps/<run>/`. A failing blocking gate
is a measurement, so the sweep records it and continues; only a run that could
not execute counts as a failure. A server already serving the requested
candidate is reused as is; one serving different weights is taken over and put
back before the sweep exits, including when it is interrupted, so a sweep needs
no manual step around the server that is already running. Pass
`--keep-running-server` to refuse the takeover instead, which is worth doing
when something other than this project answers on that port.

These write real rows, so they refuse any database but local
`money_tracker_test` on port 5433 and reset it once at the start of a run. The
category-review model port is scripted, because every branch it gates is
deterministic and a real endpoint could only let a model failure mask a wiring
failure. The tool and advice suites do reach the real endpoint, because what
they measure is a model judgment.

Calibrate the routing confidence bar on a held-out split with:

    uv run --env-file .env money-tracker-eval calibrate-routing

The bar is applied deterministically after the model has answered, so this makes
one pass over the dataset, records each message's requested intent and reported
confidence, and then evaluates every candidate bar offline. Sweeping by
re-running the model would cost one full pass per candidate and would confound
the bar's effect with run-to-run variation. The split is derived from a hash of
each case identifier, so it is stable across runs, independent of dataset order,
and cannot be shifted by rerunning; the selection rule is fixed in code before
any score is seen, and the holdout is scored once at the chosen bar. A
calibration promotes nothing on its own: `routing/intent-it` remains the suite
that gates routing.

The confidence bar is required and has no default, because none is promoted and
a routing score is meaningless without the bar that produced it. The bar, the
dataset content hash, and the instruction-text hash are all recorded in the run
configuration, so a score can never be read apart from the settings that
produced it.

The command writes a manifest and deterministic score report even when the real
endpoint is unavailable. A blocking pass additionally requires the exact model
alias, GGUF hash and quantization, llama.cpp executable hash/version, chat
template hash, context size, and projector metadata when vision is required.
Set `--publish-langfuse` only when aggregate redacted scores should be copied to
the optional local observability service.

Do not create committed data by copying personal messages, receipts, merchant
identifiers, Telegram IDs, tokens, or Langfuse exports.

## Suite catalog

- `capabilities/local-model`: strict JSON schema, repair behavior, tool calling,
  tool/result sequencing, image input, deterministic temperature/seed support,
  and context-limit behavior. Suite 1.2.0 adds the two capabilities Step 3
  measured a need for: leaving an optional field alone when the message states
  nothing for it, and answering with a discriminated union. Both are screened
  under two boundary shapes, because the failure may belong to the shape rather
  than to the model. Against the Qwen3 14B profile omission fails in both shapes
  and union output passes in both.
- `routing/intent-it`: Italian intent classification, ambiguity, negation,
  compound requests, callbacks, non-finance text, and query-versus-write
  safety. Executable; dataset `routing-intent-it-v1.1` reaches its minimum and
  currently fails three blocking gates.
- `ingestion/expense-it`: amount, currency, occurrence time, merchant,
  description, funding source, split payments, refund, and missing-field
  extraction. Executable; dataset `ingestion-expense-it-v1` reaches its minimum
  and currently fails three blocking gates, all of them the model filling a
  field the message leaves empty.
- `categorization/purpose-it`: existing fit, alias resolution, abstention,
  purpose-versus-merchant rules, new-category proposal precision, and
  calibrated auto-post subset. Executable; dataset
  `categorization-purpose-it-v1` reaches its minimum and currently fails four
  blocking gates.
- `workflow/category-review-it`: every approval/reuse/named/re-proposal/cancel,
  unrelated-message, multi-pending, retry, and resume branch. Executable;
  dataset `category-review-it-v1` reaches its minimum and passes every blocking
  gate. It found two defects on its first run, both since fixed. Its model port
  is scripted and its promotion effect is none: it establishes that the workflow
  is correct and says nothing about any model.
- `tools/selection-and-use`: permitted tool name, arguments, order, result
  handling, forbidden mutation rate, execution postconditions, and adversarial
  authorization/provenance pressure. Executable; immutable dataset
  `tools-selection-and-use-it-v1.2` has 127 cases, including eight prompt-
  injection cases. Qwen3 14B preserves all eight expected refusals; Ministral 3
  14B preserves seven and fails the zero-tolerance injection gate on
  `TOO-INJ-008`. Both still fail their existing selection and execution gates.
- `advice/grounding-it`: budget/status math, period comparisons, numeric
  provenance, insufficient-data behavior, human usefulness, and adversarial
  authorization/provenance pressure. Executable; immutable dataset
  `advice-grounding-it-v1.1` has 56 cases, including four prompt-injection
  cases. Both 14B candidates preserve all four expected refusals and keep every
  numeric statement traceable to tool output. Interpretation still fails, and
  human usefulness stays unmeasured until the owner reviews it.
- `receipts/extraction-it`: merchant/date/total/lines/discounts, line matching,
  category allocation, reconciliation, image quality, and prompt injection.
- `messaging/recovery`: ordered ingestion, duplicate updates, crash windows,
  retry, dead letter, replay, pending-action correlation, and exactly-once
  finance postconditions.
- `workflows/e2e-it`: representative complete Telegram and dashboard journeys.
- `routing/confidence-bar-calibration`: a sweep rather than a gate. It selects a
  confidence bar on a held-out split and promotes nothing; `routing/intent-it`
  remains the suite that gates routing. Executed; no usable bar exists, because
  every bar below 0.95 scores identically and the first one meeting the safety
  gate clarifies 95% of messages.

## Dataset policy

Each item contains:

- stable case ID and dataset version;
- Italian input plus source channel/media metadata needed by the case;
- expected structured result or observable postcondition;
- fixture profile, taxonomy version, funding sources, timezone, and frozen clock;
- difficulty, ambiguity, safety, and domain tags;
- annotation provenance and reviewer status;
- no secret or personal identifier.

Committed datasets are synthetic or irreversibly anonymized. Private real
receipts remain on the workstation in an ignored location and receive only a
content hash and aggregate case counts in committed reports. A dataset change
creates a new version; it never rewrites a prior run's meaning.

Initial minimum dataset targets are:

| Suite | Minimum before promotion |
| --- | ---: |
| Intent routing | 200 cases |
| Text expense/refund extraction | 150 cases |
| Categorization and abstention | 150 cases |
| Category HITL | 30 multi-turn scenarios covering all branches |
| Tool selection/use | 100 cases including adversarial and forbidden-tool cases |
| Advice grounding | 50 fixture-backed questions plus human usefulness review |
| Receipts | 30 privately annotated receipts across merchants/layouts/quality, plus synthetic safety fixtures |
| Messaging recovery | Every enumerated fault window and retry transition |

Larger datasets are welcome; a smaller dataset cannot justify production
promotion unless the matrix is amended through a documented decision.

## Experiment lifecycle

1. Convert a product risk or failure into one falsifiable hypothesis.
2. Copy [Experiment Template](experiment-template.md) and pre-register candidate,
   baseline, fixed variables, datasets, graders, gates, and tie-break policy.
3. Validate dataset version and prevent train/test leakage or prompt tuning on
   the final holdout.
4. Run the baseline and candidate with complete
   [run manifests](run-manifest-template.yaml).
5. Compute exact/rule-based scores first. Calibrate any semantic or model judge
   against human labels.
6. Inspect failures by tags and safety class, not only aggregate averages.
7. Record pass/fail and artifacts. Do not tune the same run after seeing its
   holdout result; create a new candidate/run.
8. Apply the matrix tie-break only among candidates that pass every blocking
   gate.
9. Record promote, reject, retain-baseline, or remain-experimental in the
   [Decision Log](decision-log.md).
10. Add every meaningful production failure as a regression case after privacy
    review.

## Promotion and rollback

- Production prompts, Pydantic schemas, tool definitions, taxonomy seed, and
  routing policy are code-owned and changed through reviewed commits.
- Langfuse may store local traces, dataset copies, scores, and visual comparison,
  but daily runtime never fetches an unreviewed `latest` prompt.
- A promoted configuration pins model alias, GGUF hash/quantization, llama.cpp
  build, chat template, context, prompt/schema/tool/taxonomy versions, and
  thresholds.
- Rollback selects the last passing pinned configuration and records why.
- A missing or unavailable Langfuse instance cannot prevent evaluation from
  producing local machine-readable results.

## Grader policy

Preferred order:

1. schema validation and exact match;
2. deterministic normalization and rule-based comparison;
3. database/tool observable postconditions;
4. calibrated human semantic labels;
5. model-as-judge only for subjective usefulness or semantic equivalence.

A model judge must be pinned, versioned, independently calibrated, and never be
the sole grader for money, authorization, idempotency, reconciliation, or tool
safety.

## Privacy and trace policy

- Never store Telegram tokens, user/chat IDs, file download URLs, image bytes,
  Restic secrets, or database passwords in run manifests or traces.
- Use pseudonymous test profile/session/workflow identifiers.
- Record hashes and local relative artifact references, not sensitive contents.
- Do not commit private raw results. Commit only reviewed aggregate reports and
  baselines that cannot reconstruct personal data.
- Langfuse tracing of private experiments remains local and optional.
