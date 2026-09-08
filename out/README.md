# Evaluation output

This directory holds the numbers the evaluation suites actually produced. It is
tracked by Git, unlike the raw run manifests under `artifacts/evals/`, so a
score can be read and compared without rerunning a suite.

- `evaluations/SUMMARY.md` is regenerated on every published run: one row per
  suite, showing which blocking gates failed.
- `evaluations/<suite>/latest.json` is that suite's most recent published run.
- `evaluations/<suite>/<run id>.json` is the history beside it, so a boundary
  change can be read as a before and after.

Each file carries the dataset identity and content hash, the exact model, the
instruction candidate under test, every metric with its gate and its
numerator over denominator, and whether the run met the dataset minimum. It
carries no per-case record: those stay in the raw manifest, because they hold
labels and tags that belong with the run rather than with a summary.

Only the command-line runner publishes here, and only for a dataset that
reaches the minimum the experiment process requires. A suite run from a test
against a scripted model writes to its own temporary directory and never
overwrites a real score.

Publication does not promote anything. A promotion needs the decision log.
