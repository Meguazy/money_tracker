"""Command-line entry point for executable Money Tracker evaluations."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from money_tracker.evaluations.capabilities import run_capability_suite
from money_tracker.evaluations.categorization import run_categorization_suite
from money_tracker.evaluations.category_review import run_category_review_suite
from money_tracker.evaluations.ingestion import run_ingestion_suite
from money_tracker.evaluations.metadata import CapabilitySettings
from money_tracker.evaluations.routing import run_routing_suite
from money_tracker.evaluations.suites import SuiteReport
from money_tracker.persistence.database import database_url_from_env


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
    routing = subparsers.add_parser(
        "routing",
        help="score Italian intent routing through the real compiled router",
    )
    routing.add_argument(
        "--dataset",
        type=Path,
        default=(
            repository_root / "evals" / "datasets" / "routing-intent-it-v1.1.jsonl"
        ),
    )
    routing.add_argument(
        "--output-root",
        type=Path,
        default=repository_root / "artifacts" / "evals",
    )
    routing.add_argument(
        "--minimum-confidence",
        type=float,
        required=True,
        help="confidence bar under test; no value is promoted, so it has no default",
    )
    categorization = subparsers.add_parser(
        "categorization",
        help="score Italian purpose categorization through the real boundary",
    )
    categorization.add_argument(
        "--dataset",
        type=Path,
        default=(
            repository_root
            / "evals"
            / "datasets"
            / "categorization-purpose-it-v1.jsonl"
        ),
    )
    categorization.add_argument(
        "--output-root",
        type=Path,
        default=repository_root / "artifacts" / "evals",
    )
    ingestion = subparsers.add_parser(
        "ingestion",
        help="score Italian expense extraction through the real boundary",
    )
    ingestion.add_argument(
        "--dataset",
        type=Path,
        default=(
            repository_root / "evals" / "datasets" / "ingestion-expense-it-v1.jsonl"
        ),
    )
    ingestion.add_argument(
        "--output-root",
        type=Path,
        default=repository_root / "artifacts" / "evals",
    )
    review = subparsers.add_parser(
        "category-review",
        help="score every owner category-review branch against real PostgreSQL",
    )
    review.add_argument(
        "--dataset",
        type=Path,
        default=(
            repository_root / "evals" / "datasets" / "category-review-it-v1.jsonl"
        ),
    )
    review.add_argument(
        "--output-root",
        type=Path,
        default=repository_root / "artifacts" / "evals",
    )
    return parser


def _print_and_exit(report: SuiteReport) -> None:
    """Print one suite summary and fail the process on a blocking gate.

    Args:
        report: Completed suite report.

    Raises:
        SystemExit: With status one when a blocking gate failed.
    """

    print(
        json.dumps(
            {
                "run_id": report.run_id,
                "suite": report.suite,
                "blocking_gates_passed": report.blocking_gates_passed,
                "meets_dataset_minimum": report.meets_dataset_minimum,
                "eligible_for_promotion": report.eligible_for_promotion,
                "metrics": {m.name: m.value for m in report.metrics},
                "artifact_directory": report.local_artifact_path,
            },
            sort_keys=True,
        )
    )
    if not report.blocking_gates_passed:
        raise SystemExit(1)


def main(argv: Sequence[str] | None = None) -> None:
    """Run one evaluation suite and print a compact machine-readable summary.

    Args:
        argv: Optional arguments used instead of process arguments.

    Raises:
        SystemExit: With status one when a blocking gate or metadata gate fails.
    """

    args = _parser().parse_args(argv)
    if args.suite == "categorization":
        _print_and_exit(
            run_categorization_suite(
                CapabilitySettings.from_env(),
                dataset_path=args.dataset,
                output_root=args.output_root,
                repository_root=_repository_root(),
            )
        )
        return
    if args.suite == "category-review":
        _print_and_exit(
            run_category_review_suite(
                CapabilitySettings.from_env(),
                dataset_path=args.dataset,
                output_root=args.output_root,
                repository_root=_repository_root(),
                database_url=database_url_from_env(test_database=True),
            )
        )
        return
    if args.suite == "ingestion":
        _print_and_exit(
            run_ingestion_suite(
                CapabilitySettings.from_env(),
                dataset_path=args.dataset,
                output_root=args.output_root,
                repository_root=_repository_root(),
            )
        )
        return
    if args.suite == "routing":
        _print_and_exit(
            run_routing_suite(
                CapabilitySettings.from_env(),
                dataset_path=args.dataset,
                output_root=args.output_root,
                minimum_confidence=args.minimum_confidence,
                repository_root=_repository_root(),
            )
        )
        return
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
