# Experiments and Evaluations

- Status: accepted process; Step 1 executable foundation complete
- Executable infrastructure: capability runner and deterministic local reports
- Decision authority: [Evaluation Matrix](evaluation-matrix.md) and
  [Decision Log](decision-log.md)
- Current hardware shortlist:
  [Local Model Shortlist - 2026-09-01](local-model-shortlist-2026-09-01.md)
- Capability screening:
  [Local Model Capability Results - 2026-09-02](local-model-capability-results-2026-09-02.md)

Experiments determine whether a nondeterministic component is suitable for an
approved role. They do not replace software tests and they do not grant a model
authority over financial invariants.

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

artifacts/evals/          generated raw results, ignored by Git
docs/experiments/         protocols, templates, reports, and decisions
```

Run the local-model capability suite with:

    uv run --env-file .env money-tracker-eval capabilities

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
  and context-limit behavior.
- `routing/intent-it`: Italian intent classification, ambiguity, negation,
  compound requests, callbacks, non-finance text, and query-versus-write safety.
- `ingestion/expense-it`: amount, currency, occurrence time, merchant,
  description, funding source, split payments, refund, and missing-field
  extraction.
- `categorization/purpose-it`: existing fit, alias resolution, abstention,
  purpose-versus-merchant rules, new-category proposal precision, and calibrated
  auto-post subset.
- `workflow/category-review-it`: every approval/reuse/named/re-proposal/cancel,
  unrelated-message, multi-pending, retry, and resume branch.
- `tools/selection-and-use`: permitted tool name, arguments, order, result
  handling, forbidden mutation rate, and execution postconditions.
- `advice/grounding-it`: budget/status math, period comparisons, numeric
  provenance, insufficient-data behavior, and human usefulness.
- `receipts/extraction-it`: merchant/date/total/lines/discounts, line matching,
  category allocation, reconciliation, image quality, and prompt injection.
- `messaging/recovery`: ordered ingestion, duplicate updates, crash windows,
  retry, dead letter, replay, pending-action correlation, and exactly-once
  finance postconditions.
- `workflows/e2e-it`: representative complete Telegram and dashboard journeys.

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
