"""Executable probes for local structured output, tools, sequencing, and images."""

from __future__ import annotations

import base64
import json
import mimetypes
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast
from uuid import uuid4

import httpx
from pydantic import BaseModel, ConfigDict, JsonValue

from money_tracker.evaluations.metadata import (
    CapabilitySettings,
    as_object_mapping,
    collect_environment_metadata,
    collect_model_metadata,
    collect_source_metadata,
    sha256_file,
)
from money_tracker.evaluations.models import (
    CapabilityReport,
    CapabilityRunResult,
    DatasetMetadata,
    ProbeResult,
    ProbeStatus,
)


class _LineItem(BaseModel):
    """Nested item used to exercise constrained list output."""

    model_config = ConfigDict(extra="forbid")

    description: str
    amount_minor: int


class _StructuredExpense(BaseModel):
    """Representative strict schema with enum, nullable field, and nested items."""

    model_config = ConfigDict(extra="forbid")

    amount_minor: int
    currency: Literal["EUR"]
    status: Literal["draft", "posted"]
    note: str | None
    items: list[_LineItem]


class _ProbeError(RuntimeError):
    """Expected deterministic failure that contains only safe diagnostics."""


ProbeCallable = Callable[[], dict[str, JsonValue]]


def _case_by_capability(
    cases: list[dict[str, object]], capability: str
) -> dict[str, object]:
    """Return the single dataset case for a stable capability identifier."""

    matches = [case for case in cases if case.get("capability") == capability]
    if len(matches) != 1:
        raise _ProbeError(f"dataset_case_count:{capability}:{len(matches)}")
    return matches[0]


def _load_cases(path: Path) -> list[dict[str, object]]:
    """Load synthetic JSONL cases without accepting non-object records."""

    cases: list[dict[str, object]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line.strip():
            continue
        decoded = json.loads(line)
        if not isinstance(decoded, dict):
            raise _ProbeError(f"dataset_record_not_object:{line_number}")
        cases.append(decoded)
    return cases


def _dataset_version(cases: list[dict[str, object]]) -> str:
    """Return the single explicit dataset version shared by every case.

    Args:
        cases: Decoded dataset records.

    Returns:
        The version declared consistently by all cases.

    Raises:
        _ProbeError: If cases omit a version or mix multiple versions.
    """

    versions: set[str] = set()
    for case in cases:
        version = case.get("dataset_version")
        if not isinstance(version, str) or not version:
            raise _ProbeError("dataset_version_missing")
        versions.add(version)
    if len(versions) != 1:
        raise _ProbeError("dataset_version_not_unique")
    return next(iter(versions))


def _request(
    client: httpx.Client,
    settings: CapabilitySettings,
    payload: dict[str, object],
) -> tuple[Mapping[str, object], int]:
    """Send one local chat request and return decoded JSON plus latency."""

    started = time.monotonic()
    response = client.post(
        f"{settings.base_url.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {settings.api_key}"},
        json=payload,
    )
    latency_ms = round((time.monotonic() - started) * 1000)
    if response.status_code >= 400:
        raise _ProbeError(f"http_status:{response.status_code}")
    try:
        return as_object_mapping(response.json()), latency_ms
    except (ValueError, TypeError) as error:
        raise _ProbeError("response_not_json_object") from error


def _message(response: Mapping[str, object]) -> Mapping[str, object]:
    """Extract the first assistant message from an OpenAI-compatible response."""

    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise _ProbeError("missing_choices")
    choice = as_object_mapping(choices[0])
    return as_object_mapping(choice.get("message"))


def _text_content(message: Mapping[str, object]) -> str:
    """Extract plain assistant text without persisting raw response objects."""

    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise _ProbeError("missing_text_content")
    return content.strip()


def _tool_call(message: Mapping[str, object]) -> Mapping[str, object]:
    """Extract exactly one assistant tool call."""

    tool_calls = message.get("tool_calls")
    if not isinstance(tool_calls, list) or len(tool_calls) != 1:
        raise _ProbeError("expected_one_tool_call")
    return as_object_mapping(tool_calls[0])


def _structured_probe(
    client: httpx.Client,
    settings: CapabilitySettings,
    case: Mapping[str, object],
) -> dict[str, JsonValue]:
    """Require a nested, strict, extra-forbidden JSON result."""

    prompt = str(case.get("prompt", ""))
    expected = as_object_mapping(case.get("expected"))
    schema = _StructuredExpense.model_json_schema()
    payload: dict[str, object] = {
        "model": settings.model_alias,
        "temperature": 0,
        "messages": [
            {
                "role": "system",
                "content": "Return only data that matches the supplied JSON schema.",
            },
            {"role": "user", "content": prompt},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "money_tracker_capability_expense",
                "strict": True,
                "schema": schema,
            },
        },
        "max_tokens": 512,
    }
    response, latency = _request(client, settings, payload)
    content = _text_content(_message(response))
    try:
        parsed = _StructuredExpense.model_validate_json(content)
    except ValueError as error:
        raise _ProbeError("schema_validation_failed") from error
    if parsed.model_dump(mode="json") != dict(expected):
        raise _ProbeError("structured_value_mismatch")
    return {"latency_ms": latency, "schema": "strict_nested_v1"}


def _tool_definition() -> dict[str, object]:
    """Return the exact read-only tool contract used by capability probes."""

    return {
        "type": "function",
        "function": {
            "name": "get_budget_remaining",
            "description": "Return a deterministic remaining budget for a category.",
            "strict": True,
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "category_id": {"type": "string", "enum": ["subscriptions"]}
                },
                "required": ["category_id"],
            },
        },
    }


def _tool_request_payload(
    settings: CapabilitySettings,
    prompt: str,
) -> dict[str, object]:
    """Build a request that must select the one permitted read tool."""

    return {
        "model": settings.model_alias,
        "temperature": 0,
        "messages": [
            {
                "role": "system",
                "content": "Use the provided tool. Never invent its result.",
            },
            {"role": "user", "content": prompt},
        ],
        "tools": [_tool_definition()],
        "tool_choice": "required",
        "max_tokens": 256,
    }


def _validate_tool_call(call: Mapping[str, object]) -> tuple[str, dict[str, object]]:
    """Validate exact function name and JSON arguments from one tool call."""

    call_id = call.get("id")
    function = as_object_mapping(call.get("function"))
    if not isinstance(call_id, str) or not call_id:
        raise _ProbeError("missing_tool_call_id")
    if function.get("name") != "get_budget_remaining":
        raise _ProbeError("wrong_tool_name")
    arguments = function.get("arguments")
    if not isinstance(arguments, str):
        raise _ProbeError("tool_arguments_not_json_string")
    decoded = json.loads(arguments)
    if decoded != {"category_id": "subscriptions"}:
        raise _ProbeError("wrong_tool_arguments")
    return call_id, decoded


def _tool_call_probe(
    client: httpx.Client,
    settings: CapabilitySettings,
    case: Mapping[str, object],
) -> dict[str, JsonValue]:
    """Require a strict read-only tool call with exact arguments."""

    response, latency = _request(
        client,
        settings,
        _tool_request_payload(settings, str(case.get("prompt", ""))),
    )
    _, arguments = _validate_tool_call(_tool_call(_message(response)))
    return {
        "latency_ms": latency,
        "tool_name": "get_budget_remaining",
        "argument_keys": cast(list[JsonValue], sorted(arguments)),
    }


def _tool_sequence_probe(
    client: httpx.Client,
    settings: CapabilitySettings,
    case: Mapping[str, object],
) -> dict[str, JsonValue]:
    """Require a model to consume a real tool result before answering."""

    prompt = str(case.get("prompt", ""))
    first_payload = _tool_request_payload(settings, prompt)
    first_response, first_latency = _request(client, settings, first_payload)
    first_message = _message(first_response)
    call = _tool_call(first_message)
    call_id, _ = _validate_tool_call(call)

    assistant_message = {
        "role": "assistant",
        "content": first_message.get("content"),
        "tool_calls": [dict(call)],
    }
    second_payload: dict[str, object] = {
        "model": settings.model_alias,
        "temperature": 0,
        "messages": [
            {
                "role": "system",
                "content": "Answer only from tool results and include the EUR amount.",
            },
            {"role": "user", "content": prompt},
            assistant_message,
            {
                "role": "tool",
                "tool_call_id": call_id,
                "name": "get_budget_remaining",
                "content": json.dumps(
                    {
                        "category_id": "subscriptions",
                        "remaining_minor": 3700,
                        "currency": "EUR",
                        "period": "2026-09",
                    }
                ),
            },
        ],
        "tools": [_tool_definition()],
        "tool_choice": "none",
        "max_tokens": 256,
    }
    second_response, second_latency = _request(client, settings, second_payload)
    content = _text_content(_message(second_response)).lower()
    if not any(marker in content for marker in ("37", "37,00", "37.00")):
        raise _ProbeError("tool_result_not_consumed")
    return {
        "latency_ms": first_latency + second_latency,
        "tool_result_minor": 3700,
        "answer_contains_expected_amount": True,
    }


def _image_probe(
    client: httpx.Client,
    settings: CapabilitySettings,
    case: Mapping[str, object],
    image_path: Path,
) -> dict[str, JsonValue]:
    """Send a local image without retaining its bytes or path in artifacts."""

    if not image_path.is_file():
        raise _ProbeError("image_file_missing")
    media_type, _ = mimetypes.guess_type(image_path.name)
    if media_type not in {"image/jpeg", "image/png", "image/webp"}:
        raise _ProbeError("unsupported_image_type")
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    payload: dict[str, object] = {
        "model": settings.model_alias,
        "temperature": 0,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": str(case.get("prompt", ""))},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{media_type};base64,{encoded}"},
                    },
                ],
            }
        ],
        "max_tokens": 128,
    }
    response, latency = _request(client, settings, payload)
    _text_content(_message(response))
    return {"latency_ms": latency, "accepted": True, "media_type": media_type}


def _execute_probe(name: str, probe: ProbeCallable) -> ProbeResult:
    """Convert one probe into a safe result without leaking response content."""

    started = time.monotonic()
    try:
        details = probe()
    except (httpx.HTTPError, OSError, ValueError, TypeError, _ProbeError) as error:
        safe_error = (
            str(error) if isinstance(error, _ProbeError) else type(error).__name__
        )
        return ProbeResult(
            name=name,
            status=ProbeStatus.ERROR,
            latency_ms=round((time.monotonic() - started) * 1000),
            details={"error": safe_error},
        )
    latency = details.pop("latency_ms", None)
    return ProbeResult(
        name=name,
        status=ProbeStatus.PASSED,
        latency_ms=int(latency) if isinstance(latency, int) else None,
        details=details,
    )


def _server_properties(
    client: httpx.Client,
    settings: CapabilitySettings,
) -> tuple[Mapping[str, object] | None, ProbeResult]:
    """Read llama.cpp properties through its real local HTTP endpoint."""

    started = time.monotonic()
    try:
        response = client.get(
            f"{settings.server_root_url}/props",
            params={"model": settings.model_alias},
            headers={"Authorization": f"Bearer {settings.api_key}"},
        )
        if response.status_code >= 400:
            raise _ProbeError(f"http_status:{response.status_code}")
        properties = as_object_mapping(response.json())
    except (httpx.HTTPError, ValueError, TypeError, _ProbeError) as error:
        safe_error = (
            str(error) if isinstance(error, _ProbeError) else type(error).__name__
        )
        return None, ProbeResult(
            name="server_metadata",
            status=ProbeStatus.ERROR,
            latency_ms=round((time.monotonic() - started) * 1000),
            details={"error": safe_error},
        )
    raw_modalities = properties.get("modalities")
    modalities = (
        [value for value in raw_modalities if isinstance(value, str)]
        if isinstance(raw_modalities, list)
        else []
    )
    return properties, ProbeResult(
        name="server_metadata",
        status=ProbeStatus.PASSED,
        latency_ms=round((time.monotonic() - started) * 1000),
        details={
            "has_chat_template": bool(
                properties.get("chat_template_tool_use")
                or properties.get("chat_template")
            ),
            "modalities": cast(list[JsonValue], modalities),
        },
    )


def _write_json(path: Path, value: object) -> None:
    """Atomically write stable UTF-8 JSON evaluation output."""

    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def run_capability_suite(
    settings: CapabilitySettings,
    *,
    dataset_path: Path,
    output_root: Path,
    image_path: Path | None = None,
    publish_langfuse: bool = False,
) -> CapabilityRunResult:
    """Execute capability probes and always emit a local deterministic report.

    Args:
        settings: Local inference endpoint and artifact metadata settings.
        dataset_path: Committed synthetic JSONL capability dataset.
        output_root: Ignored local directory for raw run artifacts.
        image_path: Optional local image used only for a multimodal request.
        publish_langfuse: Publish a redacted aggregate trace to local Langfuse.

    Returns:
        Typed report and artifact directory. A failing server is represented in
        the report rather than silently skipped.
    """

    started = datetime.now(UTC)
    run_id = f"CAP-{started.strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:8]}"
    repository_root = Path(__file__).resolve().parents[3]
    cases = _load_cases(dataset_path)
    dataset_version = _dataset_version(cases)

    with httpx.Client(timeout=settings.timeout_seconds) as client:
        properties, metadata_probe = _server_properties(client, settings)
        probes = [metadata_probe]
        probes.append(
            _execute_probe(
                "strict_structured_output",
                lambda: _structured_probe(
                    client,
                    settings,
                    _case_by_capability(cases, "strict_structured_output"),
                ),
            )
        )
        probes.append(
            _execute_probe(
                "tool_calling",
                lambda: _tool_call_probe(
                    client,
                    settings,
                    _case_by_capability(cases, "tool_calling"),
                ),
            )
        )
        probes.append(
            _execute_probe(
                "tool_result_sequencing",
                lambda: _tool_sequence_probe(
                    client,
                    settings,
                    _case_by_capability(cases, "tool_result_sequencing"),
                ),
            )
        )
        if image_path is None:
            probes.append(
                ProbeResult(
                    name="image_input",
                    status=ProbeStatus.NOT_APPLICABLE,
                    details={"reason": "no_image_fixture_supplied"},
                )
            )
        else:
            probes.append(
                _execute_probe(
                    "image_input",
                    lambda: _image_probe(
                        client,
                        settings,
                        _case_by_capability(cases, "image_input"),
                        image_path,
                    ),
                )
            )

    model_metadata = collect_model_metadata(
        settings,
        properties,
        vision_required=image_path is not None,
    )
    required = {
        "server_metadata",
        "strict_structured_output",
        "tool_calling",
        "tool_result_sequencing",
    }
    if image_path is not None:
        required.add("image_input")
    blocking_passed = model_metadata.metadata_complete and all(
        probe.status is ProbeStatus.PASSED for probe in probes if probe.name in required
    )

    artifact_directory = output_root / run_id
    artifact_directory.mkdir(parents=True, exist_ok=False)
    try:
        artifact_reference = str(artifact_directory.relative_to(repository_root))
    except ValueError:
        artifact_reference = str(artifact_directory)
    completed = datetime.now(UTC)
    report = CapabilityReport(
        run_id=run_id,
        started_at=started.isoformat(),
        completed_at=completed.isoformat(),
        source=collect_source_metadata(repository_root),
        dataset=DatasetMetadata(
            suite="capabilities/local-model",
            dataset_id="capabilities-local-model",
            version=dataset_version,
            content_hash=sha256_file(dataset_path),
            item_count=len(cases),
        ),
        model=model_metadata,
        environment=collect_environment_metadata(),
        probes=probes,
        blocking_gates_passed=blocking_passed,
        local_artifact_path=artifact_reference,
    )

    if publish_langfuse:
        from money_tracker.evaluations.publication import publish_capability_report

        report = report.model_copy(
            update={"langfuse_published": publish_capability_report(report)}
        )

    _write_json(artifact_directory / "manifest.json", report.model_dump(mode="json"))
    _write_json(
        artifact_directory / "scores.json",
        {
            "run_id": run_id,
            "blocking_gates_passed": blocking_passed,
            "metadata_complete": model_metadata.metadata_complete,
            "scores": {
                probe.name: probe.status is ProbeStatus.PASSED for probe in probes
            },
        },
    )
    return CapabilityRunResult(report=report, artifact_directory=artifact_directory)
