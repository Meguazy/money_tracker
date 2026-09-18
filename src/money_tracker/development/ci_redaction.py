"""Redact generated environment values before retaining failed CI evidence.

CI jobs that start real services generate a disposable dotenv file with
``scripts/generate_local_env.sh``. When such a job fails, its service logs and
JUnit reports are kept as a short-lived artifact, and nothing in that artifact
may repeat a generated credential. The redactor reads the same dotenv file and
replaces every credential-bearing value, including the random hexadecimal runs
embedded in values such as a database URL, with a stable marker. It then
applies the shared Bot API token redaction as a second, content-independent
defence.
"""

from __future__ import annotations

import argparse
import re
from collections.abc import Sequence
from pathlib import Path

from money_tracker.logging_safety import redact_credentials
from money_tracker.structured_logging import configure_logging, get_logger

_LOGGER = get_logger(__name__)
REDACTION_MARKER = "[generated-value-redacted]"
_MINIMUM_VALUE_LENGTH = 8
_CREDENTIAL_NAME = re.compile(r"PASSWORD|SECRET|SALT|TOKEN|KEY", re.IGNORECASE)
# `openssl rand -hex` output. Sixteen characters keeps identifiers such as
# container digests in logs only when they are not generated credentials.
_GENERATED_RUN = re.compile(r"[0-9a-f]{16,}", re.IGNORECASE)


def environment_values(contents: str) -> tuple[str, ...]:
    """Return credential-bearing values from dotenv contents, longest first.

    A value is selected when its name marks it as a credential or when it
    contains a generated hexadecimal run. Every such run is also selected on
    its own, so a password is redacted both inside a URL and where it appears
    alone. Other values such as hosts, database names, and switches stay
    readable because replacing them would corrupt paths and test names.

    Args:
        contents: Dotenv text generated for the disposable CI runner.

    Returns:
        Unique values of at least eight characters, ordered so that a value is
        replaced before any shorter value it contains.
    """

    values: set[str] = set()
    for raw_line in contents.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        runs = _GENERATED_RUN.findall(value)
        if len(value) >= _MINIMUM_VALUE_LENGTH and (
            _CREDENTIAL_NAME.search(name) or runs
        ):
            values.add(value)
        values.update(runs)
    return tuple(sorted(values, key=len, reverse=True))


def redact_generated_values(contents: str, dotenv: str) -> str:
    """Replace every generated credential and token-shaped run in evidence.

    Args:
        contents: Failure evidence that may contain disposable credentials.
        dotenv: Generated dotenv contents that must never be retained.

    Returns:
        Redacted evidence safe to upload after a failed CI job.
    """

    redacted = contents
    for value in environment_values(dotenv):
        redacted = redacted.replace(value, REDACTION_MARKER)
    return redact_credentials(redacted)


def _parser() -> argparse.ArgumentParser:
    """Build the CI-redaction command-line parser."""

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    """Redact one CI evidence file without emitting its contents.

    The output file is written only after the whole input has been redacted, so
    a failure never leaves partially redacted evidence for upload.

    Args:
        argv: Optional arguments used instead of process arguments.

    Raises:
        SystemExit: If the dotenv file or the evidence cannot be read, or the
            redacted evidence cannot be written.
    """

    configure_logging()
    args = _parser().parse_args(argv)
    try:
        dotenv = args.env_file.read_text(encoding="utf-8")
        evidence = args.input.read_text(encoding="utf-8", errors="replace")
        redacted = redact_generated_values(evidence, dotenv)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(redacted, encoding="utf-8")
    except OSError as error:
        _LOGGER.exception(
            "ci_evidence.redaction_failed", error_type=type(error).__name__
        )
        raise SystemExit(1) from error
    _LOGGER.info(
        "ci_evidence.redacted",
        input_characters=len(evidence),
        output_characters=len(redacted),
    )


if __name__ == "__main__":
    main()
