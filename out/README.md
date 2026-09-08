# Evaluation output

This directory holds the numbers the evaluation suites actually produced. It is
tracked by Git, unlike the raw run manifests under `artifacts/evals/`, so a
score can be read and compared without rerunning a suite.

- `evaluations/SUMMARY.md` is regenerated on every published run: one row per
  suite, naming the model that produced it and which blocking gates failed. A
  row is its own suite's most recent run, so rows can come from different
  models; when they do, the summary says so rather than letting the table read
  as a comparison.
- `evaluations/<suite>/latest.json` is that suite's most recent published run.
- `evaluations/<suite>/<run id>.json` is the history beside it, so a boundary
  change can be read as a before and after.

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
