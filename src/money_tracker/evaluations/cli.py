"""Command-line entry point for executable Money Tracker evaluations."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from money_tracker.evaluations.advice import (
    HUMAN_USEFULNESS_RUBRIC_VERSION,
    run_advice_suite,
)
from money_tracker.evaluations.calibration import run_calibration
from money_tracker.evaluations.capabilities import run_capability_suite
from money_tracker.evaluations.catalog import (
    CATALOG_RELATIVE_PATH,
    CatalogError,
    ModelProfile,
    load_catalog,
    profile_mismatches,
)
from money_tracker.evaluations.categorization import (
    CATEGORY_FIT_CANDIDATES,
    run_categorization_suite,
)
from money_tracker.evaluations.category_review import run_category_review_suite
from money_tracker.evaluations.human_review import (
    reuse_or_serve_human_usefulness_review,
    serve_human_usefulness_review,
)
from money_tracker.evaluations.ingestion import (
    EXTRACTION_CANDIDATES,
    run_ingestion_suite,
)
from money_tracker.evaluations.metadata import CapabilitySettings
from money_tracker.evaluations.publication import (
    PromotionEvidenceError,
    publish_promotion,
)
from money_tracker.evaluations.routing import run_routing_suite
from money_tracker.evaluations.suites import (
    SuiteReport,
    loaded_model_filename,
    model_row,
    publish_capability_scores,
    publish_scores,
)
from money_tracker.evaluations.tool_use import run_tool_use_suite
from money_tracker.persistence.database import database_url_from_env
from money_tracker.runtime.read_graph import SELECTION_BOUNDARIES
from money_tracker.structured_logging import configure_logging, get_logger
from money_tracker.workflows import (
    DEFAULT_READ_SELECTION_POLICY,
    ReadSelectionPolicyVersion,
)
from money_tracker.workflows.promotion import PROMOTIONS
from money_tracker.workflows.prompts import (
    CATEGORY_FIT,
    CATEGORY_LEAF,
    CATEGORY_SUBTREE,
    EXTRACTION,
    ROUTER,
    TOOL_SELECTION,
    default_prompt,
    prompt,
    prompt_versions,
)
from money_tracker.workflows.two_stage import TwoStageCategoryFit

_LOGGER = get_logger("money_tracker.evaluations.cli")


def _version_choices(boundary: str) -> list[str]:
    """Return the version names a run may select for one boundary."""

    return [version.short_name for version in prompt_versions(boundary)]


def _repository_root() -> Path:
    """Return the repository root containing the committed evaluation layout."""

    return Path(__file__).resolve().parents[3]


def _profile_choices(repository_root: Path) -> list[str] | None:
    """Return the candidate identifiers a run may declare.

    Args:
        repository_root: Repository holding the committed candidate catalog.

    Returns:
        Declared identifiers, or None when the catalog cannot be read, so that
        an unreadable catalog costs a late error rather than an unusable CLI.
    """

    try:
        return load_catalog(repository_root / CATALOG_RELATIVE_PATH).identifiers()
    except CatalogError:
        return None


def _add_model_profile(
    parser: argparse.ArgumentParser, choices: list[str] | None
) -> None:
    """Let a run declare which catalogued candidate it is measuring.

    Args:
        parser: Subcommand parser to extend.
        choices: Declared candidate identifiers, when the catalog is readable.
    """

    parser.add_argument(
        "--model-profile",
        choices=choices,
        help=(
            "catalogued candidate this run must be measuring; the run is "
            "refused when the configured weights or the running server "
            "disagree with it"
        ),
    )


def _require_declared_model(
    identifier: str | None, settings: CapabilitySettings
) -> None:
    """Refuse a run that is not measuring the candidate it declared.

    Switching candidates means editing the environment and restarting the
    inference server, and forgetting the restart produces a complete, plausible
    run that attributes one candidate's answers to another. Nothing downstream
    can detect that, so it is refused here.

    Args:
        identifier: Declared catalog profile, or None to measure whatever is
            configured. Leaving it out stays valid: an exploratory run against
            an uncatalogued model is legitimate, and the report records the
            weights either way.
        settings: Local endpoint configuration under test.

    Raises:
        SystemExit: When the catalog cannot be read, the profile is unknown, or
            the configured weights and the running server do not both agree
            with it.
    """

    if identifier is None:
        return
    try:
        catalog = load_catalog(_repository_root() / CATALOG_RELATIVE_PATH)
        profile = catalog.profile(identifier)
    except CatalogError as error:
        raise SystemExit(str(error)) from error
    problems = profile_mismatches(
        profile,
        configured_path=settings.model_path,
        server_filename=loaded_model_filename(settings),
    )
    if problems:
        raise SystemExit(
            "\n".join(
                [f"this run is not measuring {identifier}:"]
                + [f"  - {problem}" for problem in problems]
            )
        )


def _publish_promotion(decision: str, scores_root: Path) -> None:
    """Publish one recorded promotion's evidence and report the trace to find.

    Args:
        decision: Decision identifier with a code-owned promotion record.
        scores_root: Committed published scores the evidence is read from.

    Raises:
        SystemExit: With status one when the committed runs do not support the
            record or the store did not accept the evidence.
    """

    try:
        outcome = publish_promotion(PROMOTIONS[decision], out_root=scores_root)
    except PromotionEvidenceError as error:
        _LOGGER.error("evaluation.promotion.evidence_rejected", decision=decision)
        raise SystemExit(f"promotion evidence refused: {error}") from None
    print(
        json.dumps(
            {
                "decision": outcome.decision,
                "published": outcome.published,
                "evaluation_runs": outcome.run_ids,
                "trace_reference": outcome.trace_reference,
            },
            sort_keys=True,
        )
    )
    if not outcome.published:
        raise SystemExit(1)


def _parser() -> argparse.ArgumentParser:
    """Build the evaluation CLI parser.

    Returns:
        Parser exposing only suites implemented in the active roadmap step.
    """

    repository_root = _repository_root()
    profiles = _profile_choices(repository_root)
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
            / "capabilities-local-model-v1.2.jsonl"
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
            repository_root / "evals" / "datasets" / "routing-intent-it-v1.2.jsonl"
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
    routing.add_argument(
        "--instructions",
        choices=_version_choices(ROUTER),
        default=default_prompt(ROUTER).short_name,
        help="router instruction candidate under test; recorded with the score",
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
            / "categorization-purpose-it-v1.1.jsonl"
        ),
    )
    categorization.add_argument(
        "--instructions",
        choices=_version_choices(CATEGORY_FIT),
        default=default_prompt(CATEGORY_FIT).short_name,
        help="category-fit boundary under test; recorded with the score",
    )
    categorization.add_argument(
        "--two-stage",
        action="store_true",
        help=(
            "run the two-stage judgment from AD-015 instead of the single "
            "category-fit call; --instructions is ignored when it is set"
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
        "--instructions",
        choices=_version_choices(EXTRACTION),
        default=default_prompt(EXTRACTION).short_name,
        help="extraction boundary under test; recorded with the score",
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
    tools = subparsers.add_parser(
        "tools",
        help="score read-tool selection and execution against real PostgreSQL",
    )
    tools.add_argument(
        "--dataset",
        type=Path,
        default=(
            repository_root
            / "evals"
            / "datasets"
            / "tools-selection-and-use-it-v1.2.jsonl"
        ),
    )
    tools.add_argument(
        "--instructions",
        choices=_version_choices(TOOL_SELECTION),
        default=default_prompt(TOOL_SELECTION).short_name,
        help="read-tool selection boundary under test; recorded with the score",
    )
    tools.add_argument(
        "--selection-policy",
        choices=[version.value for version in ReadSelectionPolicyVersion],
        default=DEFAULT_READ_SELECTION_POLICY.value,
        help="versioned post-model selection policy under test",
    )
    tools.add_argument(
        "--output-root",
        type=Path,
        default=repository_root / "artifacts" / "evals",
    )
    advice = subparsers.add_parser(
        "advice",
        help="score advice grounding and interpretation against real PostgreSQL",
    )
    advice.add_argument(
        "--dataset",
        type=Path,
        default=(
            repository_root / "evals" / "datasets" / "advice-grounding-it-v1.1.jsonl"
        ),
    )
    advice.add_argument(
        "--instructions",
        choices=_version_choices(TOOL_SELECTION),
        default=default_prompt(TOOL_SELECTION).short_name,
        help="read-tool selection boundary under test; recorded with the score",
    )
    advice.add_argument(
        "--selection-policy",
        choices=[version.value for version in ReadSelectionPolicyVersion],
        default=DEFAULT_READ_SELECTION_POLICY.value,
        help="versioned post-model selection policy under test",
    )
    advice.add_argument(
        "--output-root",
        type=Path,
        default=repository_root / "artifacts" / "evals",
    )
    usefulness = advice.add_mutually_exclusive_group()
    usefulness.add_argument(
        "--review-human-usefulness",
        action="store_true",
        help="open a loopback browser review for all non-injection results",
    )
    advice.add_argument(
        "--reuse-human-usefulness-review",
        type=Path,
        help=(
            "prior completed owner-review JSON; unchanged results reuse their "
            "scores and only changed results open in the browser"
        ),
    )
    advice.add_argument(
        "--human-review-port",
        type=int,
        default=8765,
        help="loopback port for the temporary human-review page (default: 8765)",
    )
    usefulness.add_argument(
        "--defer-human-usefulness",
        action="store_true",
        help="record an explicit owner decision to defer this gate to Step 5",
    )
    calibration = subparsers.add_parser(
        "calibrate-routing",
        help="sweep the routing confidence bar and score a held-out split once",
    )
    calibration.add_argument(
        "--dataset",
        type=Path,
        default=(
            repository_root / "evals" / "datasets" / "routing-intent-it-v1.2.jsonl"
        ),
    )
    calibration.add_argument(
        "--output-root",
        type=Path,
        default=repository_root / "artifacts" / "evals",
    )
    catalog = subparsers.add_parser(
        "catalog",
        help="show the declared local model candidates and where they are",
    )
    catalog.add_argument(
        "--profile",
        choices=profiles,
        help="report only this candidate",
    )
    catalog.add_argument(
        "--field",
        choices=("id", "modality", "filename", "path", "projector", "sha256"),
        help=(
            "print one bare field of the selected candidate instead of JSON, "
            "so a shell can resolve a path without parsing"
        ),
    )
    promotion = subparsers.add_parser(
        "publish-promotion",
        help=(
            "publish a recorded promotion's committed run evidence to the "
            "local Langfuse"
        ),
    )
    promotion.add_argument(
        "--decision",
        required=True,
        choices=sorted(PROMOTIONS),
        help="decision identifier from the experiment decision log",
    )
    promotion.add_argument(
        "--scores-root",
        type=Path,
        default=repository_root / "out" / "evaluations",
        help="committed published scores the evidence is read from",
    )
    for suite in (
        capabilities,
        routing,
        categorization,
        ingestion,
        review,
        tools,
        advice,
        calibration,
    ):
        _add_model_profile(suite, profiles)
    return parser


def _catalog_entry(profile: ModelProfile) -> dict[str, object]:
    """Describe one declared candidate, including whether it is downloaded.

    Args:
        profile: Declared candidate.

    Returns:
        Its declared identity plus whether the weights are present locally,
        which is what separates a candidate that can be measured now from one
        the catalog only describes.
    """

    return {
        "id": profile.id,
        "modality": profile.modality,
        "filename": profile.filename,
        "path": str(profile.path),
        "projector": str(profile.projector_path) if profile.projector_path else None,
        "sha256": profile.sha256,
        "quantization": profile.quantization,
        "present": profile.path.is_file(),
    }


def _run_catalog(profile_id: str | None, field: str | None) -> None:
    """Report the declared candidates, or one field of one candidate.

    Args:
        profile_id: Candidate to report, or None for every declared candidate.
        field: Single field to print bare, for a shell to consume.

    Raises:
        SystemExit: When the catalog cannot be read, the candidate is unknown,
            or a field is requested without naming a candidate.
    """

    try:
        catalog = load_catalog(_repository_root() / CATALOG_RELATIVE_PATH)
        selected = (
            [catalog.profile(profile_id)]
            if profile_id
            else list(catalog.profiles.values())
        )
    except CatalogError as error:
        raise SystemExit(str(error)) from error
    if field is not None:
        if profile_id is None:
            raise SystemExit("--field needs --profile, because it prints one value")
        value = _catalog_entry(selected[0])[field]
        if value is None:
            raise SystemExit(f"{profile_id} declares no {field}")
        print(value)
        return
    print(
        json.dumps(
            [_catalog_entry(profile) for profile in selected],
            indent=2,
            sort_keys=True,
        )
    )


def _two_stage_candidate() -> TwoStageCategoryFit:
    """Compose the two boundaries AD-015 proposes from their catalogs."""

    subtree = default_prompt(CATEGORY_SUBTREE)
    leaf = default_prompt(CATEGORY_LEAF)
    return TwoStageCategoryFit(
        subtree_version=subtree.id,
        leaf_version=leaf.id,
        subtree_instructions=subtree.text,
        leaf_instructions=leaf.text,
    )


def _command_summary(report: SuiteReport) -> dict[str, object]:
    """Render the summary one suite run prints when it finishes.

    The model is part of the summary because the terminal is where a run is
    read first, and a score without the model that produced it cannot be
    compared with the run before it.

    Args:
        report: Completed suite report.

    Returns:
        A JSON-serializable summary carrying no owner text or per-case record.
    """

    return {
        "run_id": report.run_id,
        "suite": report.suite,
        "model": model_row(report.model),
        "blocking_gates_passed": report.blocking_gates_passed,
        "meets_dataset_minimum": report.meets_dataset_minimum,
        "eligible_for_promotion": report.eligible_for_promotion,
        "promotion_blockers": report.promotion_blockers,
        "metrics": {m.name: m.value if m.denominator else None for m in report.metrics},
        "artifact_directory": report.local_artifact_path,
    }


def _print_and_exit(report: SuiteReport) -> None:
    """Print one suite summary and fail the process on a blocking gate.

    Publishing the numbers happens here rather than in the report writer, so
    that only a run someone asked for on the command line can overwrite a
    published score.

    Args:
        report: Completed suite report.

    Raises:
        SystemExit: With status one when a blocking gate failed.
    """

    publish_scores(report)
    print(json.dumps(_command_summary(report), sort_keys=True))
    if not report.blocking_gates_passed:
        raise SystemExit(1)


def main(argv: Sequence[str] | None = None) -> None:
    """Run one evaluation suite and print a compact machine-readable summary.

    Args:
        argv: Optional arguments used instead of process arguments.

    Raises:
        SystemExit: With status one when a blocking gate or metadata gate fails.
    """

    configure_logging()
    parser = _parser()
    args = parser.parse_args(argv)
    _LOGGER.info("evaluation.command.started", suite=args.suite)
    if getattr(args, "reuse_human_usefulness_review", None) is not None and not getattr(
        args, "review_human_usefulness", False
    ):
        parser.error(
            "--reuse-human-usefulness-review requires --review-human-usefulness"
        )
    if args.suite == "catalog":
        _run_catalog(args.profile, args.field)
        return
    if args.suite == "publish-promotion":
        _publish_promotion(args.decision, args.scores_root)
        return
    settings = CapabilitySettings.from_env()
    _require_declared_model(args.model_profile, settings)
    if args.suite == "categorization":
        _print_and_exit(
            run_categorization_suite(
                settings,
                dataset_path=args.dataset,
                output_root=args.output_root,
                repository_root=_repository_root(),
                candidate=CATEGORY_FIT_CANDIDATES[args.instructions],
                two_stage=_two_stage_candidate() if args.two_stage else None,
            )
        )
        return
    if args.suite == "category-review":
        _print_and_exit(
            run_category_review_suite(
                settings,
                dataset_path=args.dataset,
                output_root=args.output_root,
                repository_root=_repository_root(),
                database_url=database_url_from_env(test_database=True),
            )
        )
        return
    if args.suite == "calibrate-routing":
        _print_and_exit(
            run_calibration(
                settings,
                dataset_path=args.dataset,
                output_root=args.output_root,
                repository_root=_repository_root(),
            )
        )
        return
    if args.suite == "advice":
        _print_and_exit(
            run_advice_suite(
                settings,
                dataset_path=args.dataset,
                output_root=args.output_root,
                repository_root=_repository_root(),
                database_url=database_url_from_env(test_database=True),
                boundary=SELECTION_BOUNDARIES[args.instructions],
                instructions=prompt(TOOL_SELECTION, args.instructions).text,
                selection_policy=ReadSelectionPolicyVersion(args.selection_policy),
                human_reviewer=(
                    (
                        lambda run_id, items: (
                            reuse_or_serve_human_usefulness_review(
                                run_id,
                                items,
                                review_path=args.reuse_human_usefulness_review,
                                rubric_version=HUMAN_USEFULNESS_RUBRIC_VERSION,
                                port=args.human_review_port,
                            )
                            if args.reuse_human_usefulness_review is not None
                            else serve_human_usefulness_review(
                                run_id, items, port=args.human_review_port
                            )
                        )
                    )
                    if args.review_human_usefulness
                    else None
                ),
                human_usefulness_deferred=args.defer_human_usefulness,
            )
        )
        return
    if args.suite == "tools":
        _print_and_exit(
            run_tool_use_suite(
                settings,
                dataset_path=args.dataset,
                output_root=args.output_root,
                repository_root=_repository_root(),
                database_url=database_url_from_env(test_database=True),
                boundary=SELECTION_BOUNDARIES[args.instructions],
                instructions=prompt(TOOL_SELECTION, args.instructions).text,
                selection_policy=ReadSelectionPolicyVersion(args.selection_policy),
            )
        )
        return
    if args.suite == "ingestion":
        _print_and_exit(
            run_ingestion_suite(
                settings,
                dataset_path=args.dataset,
                output_root=args.output_root,
                repository_root=_repository_root(),
                candidate=EXTRACTION_CANDIDATES[args.instructions],
            )
        )
        return
    if args.suite == "routing":
        _print_and_exit(
            run_routing_suite(
                settings,
                dataset_path=args.dataset,
                output_root=args.output_root,
                minimum_confidence=args.minimum_confidence,
                repository_root=_repository_root(),
                instructions=prompt(ROUTER, args.instructions).text,
                instructions_version=prompt(ROUTER, args.instructions).id,
            )
        )
        return
    if args.suite != "capabilities":
        raise SystemExit(f"unsupported suite: {args.suite}")

    result = run_capability_suite(
        settings,
        dataset_path=args.dataset,
        output_root=args.output_root,
        image_path=args.image,
        publish_langfuse=args.publish_langfuse,
    )
    # Published here rather than in the runner, so that only a screen someone
    # asked for on the command line can overwrite a published verdict.
    published = publish_capability_scores(
        result.report, modality="vision" if args.image is not None else "text"
    )
    print(
        json.dumps(
            {
                "run_id": result.report.run_id,
                "model": model_row(result.report.model),
                "blocking_gates_passed": result.report.blocking_gates_passed,
                "artifact_directory": str(result.artifact_directory),
                "published_to": str(published) if published else None,
            },
            sort_keys=True,
        )
    )
    if not result.report.blocking_gates_passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
