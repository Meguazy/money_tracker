# Evaluation output

This directory holds the numbers the evaluation suites actually produced. It is
tracked by Git, unlike the raw run manifests under `artifacts/evals/`, so a
score can be read and compared without rerunning a suite.

- `evaluations/SUMMARY.md` is regenerated on every published run: one row per
  suite and model, naming the instruction candidate it was measured on and
  which blocking gates failed.
- `evaluations/<suite>/<model>/latest.json` is that suite's most recent
  published run on that candidate.
- `evaluations/<suite>/<model>/<run id>.json` is the history beside it, so a
  boundary change can be read as a before and after.

The model segment is the catalogue profile the run's weights are declared as,
such as `text-qwen3-14b`, and falls back to the weights filename when the model
is not catalogued. Scores were once keyed by suite alone, which meant measuring
a second candidate overwrote the first; keying by model keeps both.

Each file carries the dataset identity and content hash, the exact model down
to its weights hash and whether that identity could be pinned at all, the
instruction candidate under test, every metric with its gate and its
numerator over denominator, and whether the run met the dataset minimum. It
carries no per-case record: those stay in the raw manifest, because they hold
labels and tags that belong with the run rather than with a summary.

Only the command-line runner publishes here, and only for a dataset that
reaches the minimum the experiment process requires. A suite run from a test
against a scripted model writes to its own temporary directory and never
overwrites a real score.

Publication does not promote anything. A promotion needs the decision log.
