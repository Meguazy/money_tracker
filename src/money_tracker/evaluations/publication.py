"""Optional redacted publication of evaluation aggregates to local Langfuse."""

from __future__ import annotations

import os

from money_tracker.evaluations.models import CapabilityReport, ProbeStatus


def publish_capability_report(report: CapabilityReport) -> bool:
    """Publish aggregate capability scores without private inputs or credentials.

    Args:
        report: Completed local capability report.

    Returns:
        True when the local SDK accepted and flushed the aggregate trace; false
        when credentials are absent or publication fails. Local artifacts remain
        authoritative in either case.
    """

    if not (os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY")):
        return False

    try:
        from langfuse import get_client

        client = get_client()
        with client.start_as_current_observation(
            name="money-tracker-capability-evaluation",
            as_type="evaluator",
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
        ):
            for probe in report.probes:
                client.score_current_trace(
                    name=f"capability/{probe.name}",
                    value=1.0 if probe.status is ProbeStatus.PASSED else 0.0,
                    data_type="NUMERIC",
                )
        client.flush()
    except Exception:
        return False
    return True
