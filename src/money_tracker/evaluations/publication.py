"""Optional publication of aggregate evaluation evidence to the local Langfuse.

Two bodies of evidence can be published, both aggregate and both reconstructible
from committed files:

- a capability screen's probe outcomes, under `capabilities --publish-langfuse`;
- a promotion's evidence, under `publish-promotion`: the promoted configuration
  and, for every run the decision log names, the published gates, metrics,
  dataset identity, instruction identity, and source commit.

A promotion publication is what makes a promoted version traceable to the runs
that promoted it: its trace carries the same `promotion_decision` and run
identifiers that every runtime trace composing that configuration carries in its
version set, so filtering the store by the decision finds both.

Publication follows the tracing rules. The command's flag or subcommand is the
explicit request, the store must be loopback with both keys configured, the
SDK's cloud default is never used, and this module never imports the SDK: the
tracing package owns that. Evidence goes to the `evaluation` environment, apart
from runtime traces and their retention. Local artifacts and committed scores
remain authoritative whatever happens here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from money_tracker.evaluations.models import CapabilityReport, ProbeStatus
from money_tracker.structured_logging import get_logger
from money_tracker.tracing import (
    EvaluationPublication,
    EvaluationPublisher,
    EvaluationRecord,
    open_evaluation_publisher,
)
from money_tracker.workflows.promotion import PromotionRecord

_LOGGER = get_logger(__name__)

EVALUATION_ENVIRONMENT = "evaluation"
"""Langfuse environment holding published evaluation evidence."""

PROMOTION_TRACE = "evaluation-promotion"
"""Trace name of one published promotion's evidence."""

CAPABILITY_TRACE = "evaluation-capabilities"
"""Trace name of one published capability screen."""


class PromotionEvidenceError(ValueError):
    """Committed evaluation files do not support the promotion record.

    The message names the suite and the rule, never file contents.
    """


@dataclass(frozen=True, slots=True)
class PromotionPublication:
    """Outcome of publishing one promotion's evidence.

    Attributes:
        decision: Decision identifier that was published.
        trace_reference: Reference the published trace is derived from.
        run_ids: Published run identifier per suite.
        published: Whether the store accepted everything within the bound.
    """

    decision: str
    trace_reference: str
    run_ids: dict[str, str]
    published: bool


def publish_capability_report(
    report: CapabilityReport,
    *,
    publisher: EvaluationPublisher | None = None,
) -> bool:
    """Publish aggregate capability scores without private inputs or credentials.

    Args:
        report: Completed local capability report.
        publisher: Publisher to use; opened for the evaluation environment when
            omitted.

    Returns:
        True when the local store accepted the aggregate trace within the
        export bound; false when configuration is missing or refused, or
        delivery failed. Local artifacts remain authoritative in either case.
    """

    publisher = publisher or open_evaluation_publisher(
        environment=EVALUATION_ENVIRONMENT
    )
    if publisher is None:
        return False
    publication = EvaluationPublication(
        trace_seed=f"evaluation:{report.run_id}",
        trace_name=CAPABILITY_TRACE,
        root=EvaluationRecord(
            name="money-tracker-capability-evaluation",
            input={"run_id": report.run_id, "suite": report.dataset.suite},
            output={
                "blocking_gates_passed": report.blocking_gates_passed,
                "metadata_complete": report.model.metadata_complete,
                "probe_statuses": {
                    probe.name: probe.status.value for probe in report.probes
                },
            },
            metadata={
                "model_alias": report.model.model_alias,
                "gguf_sha256": report.model.gguf_sha256,
                "chat_template_sha256": report.model.chat_template_sha256,
                "server_version": report.model.server_version,
            },
            scores={
                f"capability/{probe.name}": (
                    1.0 if probe.status is ProbeStatus.PASSED else 0.0
                )
                for probe in report.probes
            },
        ),
    )
    try:
        published = publisher.publish(publication)
    except Exception as error:
        _LOGGER.warning(
            "evaluation.publication.failed", exception_type=type(error).__name__
        )
        return False
    _LOGGER.info(
        "evaluation.publication.completed",
        run_id=report.run_id,
        published=published,
    )
    return published


def promotion_publication(
    record: PromotionRecord, *, out_root: Path
) -> EvaluationPublication:
    """Assemble a promotion's evidence from the committed published runs.

    Every run the record names must be published under the promoted model's
    directory, carry the same identifier and weights digest, and have been
    eligible for promotion. Anything else means the record describes evidence
    the repository does not hold, which is refused rather than published.

    Args:
        record: Promotion to publish.
        out_root: Directory holding published scores, normally
            `out/evaluations`.

    Returns:
        One trace of evidence: the promoted configuration, and one child per
        run with its gates, metrics, and dataset and instruction identity.

    Raises:
        PromotionEvidenceError: If a run is missing or does not support the
            promotion.
    """

    children = []
    for suite, run_id in record.evaluation_runs.items():
        run = _published_run(out_root, record, suite, run_id)
        passed = run.get("blocking_gates_passed") is True
        configuration = run.get("configuration") or {}
        children.append(
            EvaluationRecord(
                name=f"run:{suite}",
                input={
                    "run_id": run_id,
                    "suite": suite,
                    "dataset": run.get("dataset"),
                    "instructions_version": configuration.get("instructions_version"),
                    "instructions_sha256": configuration.get("instructions_sha256"),
                    "selection_policy_version": configuration.get(
                        "selection_policy_version"
                    ),
                    "minimum_confidence": configuration.get("minimum_confidence"),
                    "source_commit": run.get("source_commit"),
                    "completed_at": run.get("completed_at"),
                },
                output={
                    "blocking_gates_passed": passed,
                    "eligible_for_promotion": run.get("eligible_for_promotion"),
                    "case_totals": run.get("case_totals"),
                    "metrics": [
                        {
                            key: metric.get(key)
                            for key in (
                                "name",
                                "value",
                                "threshold",
                                "comparison",
                                "blocking",
                                "passed",
                            )
                        }
                        for metric in run.get("metrics", [])
                    ],
                },
                metadata={
                    "promotion_decision": record.decision,
                    "suite": suite,
                    "run_id": run_id,
                },
                scores={f"gate/{suite}": 1.0 if passed else 0.0},
            )
        )
    return EvaluationPublication(
        trace_seed=f"promotion:{record.decision}:{uuid4()}",
        trace_name=PROMOTION_TRACE,
        root=EvaluationRecord(
            name=f"promotion:{record.decision}",
            input={
                "decision": record.decision,
                "decided_on": record.decided_on,
                "model_profile": record.model_profile,
                "gguf_sha256": record.gguf_sha256,
                "versions": dict(record.versions),
            },
            output={
                "evaluation_runs": dict(record.evaluation_runs),
                "all_blocking_gates_passed": all(
                    child.output["blocking_gates_passed"] is True for child in children
                ),
            },
            metadata={
                "promotion_decision": record.decision,
                "promotion_model_profile": record.model_profile,
                "promotion_evaluation_runs": dict(record.evaluation_runs),
            },
        ),
        children=tuple(children),
    )


def publish_promotion(
    record: PromotionRecord,
    *,
    out_root: Path,
    publisher: EvaluationPublisher | None = None,
) -> PromotionPublication:
    """Publish one promotion's evidence as one trace in the local store.

    Each call publishes a new trace, because Langfuse deletes asynchronously
    and replacing a trace under the same identifier could lose the new copy.

    Args:
        record: Promotion to publish.
        out_root: Directory holding published scores.
        publisher: Publisher to use; opened for the evaluation environment when
            omitted.

    Returns:
        What was published and whether the store accepted it.

    Raises:
        PromotionEvidenceError: If the committed runs do not support the record.
    """

    publication = promotion_publication(record, out_root=out_root)
    run_ids = dict(record.evaluation_runs)
    publisher = publisher or open_evaluation_publisher(
        environment=EVALUATION_ENVIRONMENT
    )
    published = False
    if publisher is not None:
        try:
            published = publisher.publish(publication)
        except Exception as error:
            _LOGGER.warning(
                "evaluation.publication.failed", exception_type=type(error).__name__
            )
    _LOGGER.info(
        "evaluation.promotion.published",
        decision=record.decision,
        runs=len(run_ids),
        published=published,
    )
    return PromotionPublication(
        decision=record.decision,
        trace_reference=publication.trace_seed,
        run_ids=run_ids,
        published=published,
    )


def _published_run(
    out_root: Path, record: PromotionRecord, suite: str, run_id: str
) -> dict[str, Any]:
    """Read one committed run and require it to support the promotion."""

    path = out_root / suite / record.model_profile / f"{run_id}.json"
    try:
        run = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise PromotionEvidenceError(
            f"{suite}: the published run named by {record.decision} is unreadable"
        ) from error
    if not isinstance(run, dict) or run.get("run_id") != run_id:
        raise PromotionEvidenceError(f"{suite}: the published run identity differs")
    if run.get("eligible_for_promotion") is not True:
        raise PromotionEvidenceError(f"{suite}: the run was not eligible for promotion")
    model = run.get("model") or {}
    if model.get("gguf_sha256") != record.gguf_sha256:
        raise PromotionEvidenceError(f"{suite}: the run measured other weights")
    return run


__all__ = [
    "CAPABILITY_TRACE",
    "EVALUATION_ENVIRONMENT",
    "PROMOTION_TRACE",
    "PromotionEvidenceError",
    "PromotionPublication",
    "promotion_publication",
    "publish_capability_report",
    "publish_promotion",
]
