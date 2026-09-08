# Executable Evaluations

- Status: Step 1 foundation complete
- Promotion authority: [evaluation matrix](../docs/experiments/evaluation-matrix.md)
- Runner: `money-tracker-eval`
- Candidate catalog: [`models/local-candidates.yaml`](models/local-candidates.yaml),
  read by the runner rather than only by a reader

This directory contains only synthetic or irreversibly anonymized committed
inputs. Raw outputs are written to the ignored `artifacts/evals/` directory.
Private datasets belong under [`private/`](private/README.md) and never enter
Git.

## Layout

- [`datasets/`](datasets/) contains immutable, public-safe JSONL cases.
- [`suites/`](suites/) contains versioned executable suite configuration.
- [`baselines/`](baselines/) contains reviewed compact aggregate summaries only.
- [`private/`](private/README.md) documents local-only data handling.

## Local model capability suite

Load the private environment and run:

```bash
uv run --env-file .env money-tracker-eval capabilities
```

Add `--image /private/path/to/synthetic-or-private-fixture.png` only when the
configured model/server/projector combination is intended to support images.
Neither image bytes nor its path are copied into the manifest. Add
`--publish-langfuse` to send redacted aggregate scores to the optional local
Langfuse instance; local JSON remains authoritative.

Every run emits:

```text
artifacts/evals/CAP-.../
├── manifest.json
└── scores.json
```

Add `--model-profile <id>` to any suite to declare which catalogued candidate
the run is measuring. The run is refused unless the configured weights and the
running server both agree with it, which is what makes switching candidates
safe: editing the environment without restarting the server otherwise produces
a complete, plausible run measuring the previous model. Leaving it out stays
valid for an exploratory run against an uncatalogued model, and the report
records the weights either way.

The command exits non-zero when the endpoint is absent, a probe fails, or exact
GGUF/server/chat-template metadata is incomplete. A successful capability run
does not promote a model; product-quality suites and a recorded decision remain
required.
