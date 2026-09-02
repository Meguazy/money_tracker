"""Command-line entry point for executable Money Tracker evaluations."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from money_tracker.evaluations.capabilities import run_capability_suite
from money_tracker.evaluations.metadata import CapabilitySettings


def _repository_root() -> Path:
    """Return the repository root containing the committed evaluation layout."""

    return Path(__file__).resolve().parents[3]


def _parser() -> argparse.ArgumentParser:
    """Build the evaluation CLI parser.

    Returns:
        Parser exposing only suites implemented in the active roadmap step.
    """

    repository_root = _repository_root()
    parser = argparse.ArgumentParser(description="Run Money Tracker evaluations")
    subparsers = parser.add_subparsers(dest="suite", required=True)
    capabilities = subparsers.add_parser(
        "capabilities",
        help="probe the configured local OpenAI-compatible model/server",
    )
    capabilities.add_argument(
        "--dataset",
        type=Path,
        default=(
            repository_root
            / "evals"
            / "datasets"
            / "capabilities-local-model-v1.1.jsonl"
        ),
    )
    capabilities.add_argument(
        "--output-root",
        type=Path,
        default=repository_root / "artifacts" / "evals",
    )
    capabilities.add_argument(
        "--image",
        type=Path,
        help="optional local JPEG/PNG/WebP fixture; bytes and path are not recorded",
    )
    capabilities.add_argument(
        "--publish-langfuse",
        action="store_true",
        help="publish only aggregate scores to the configured local Langfuse",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    """Run one evaluation suite and print a compact machine-readable summary.

    Args:
        argv: Optional arguments used instead of process arguments.

    Raises:
        SystemExit: With status one when a blocking gate or metadata gate fails.
    """

    args = _parser().parse_args(argv)
    if args.suite != "capabilities":
        raise SystemExit(f"unsupported suite: {args.suite}")

    result = run_capability_suite(
        CapabilitySettings.from_env(),
        dataset_path=args.dataset,
        output_root=args.output_root,
        image_path=args.image,
        publish_langfuse=args.publish_langfuse,
    )
    print(
        json.dumps(
            {
                "run_id": result.report.run_id,
                "blocking_gates_passed": result.report.blocking_gates_passed,
                "artifact_directory": str(result.artifact_directory),
            },
            sort_keys=True,
        )
    )
    if not result.report.blocking_gates_passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
