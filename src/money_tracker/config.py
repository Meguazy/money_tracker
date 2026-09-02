"""Application settings loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _float_from_env(name: str, default: float) -> float:
    """Read a floating-point environment setting.

    Args:
        name: Name of the environment variable.
        default: Value returned when the variable is absent.

    Returns:
        The parsed environment value or the supplied default.

    Raises:
        ValueError: If the configured value is not numeric.
    """

    raw_value = os.getenv(name)
    if raw_value is None:
        return default

    try:
        return float(raw_value)
    except ValueError as error:
        raise ValueError(f"{name} must be a number, got {raw_value!r}") from error


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime settings for the OpenAI-compatible local model server.

    Attributes:
        llm_base_url: Base URL of the local OpenAI-compatible API.
        llm_api_key: API credential expected by the local server.
        llm_model: Model identifier sent with inference requests.
        llm_temperature: Sampling temperature used for generation.
        llm_timeout_seconds: Maximum duration of an inference request.
    """

    llm_base_url: str
    llm_api_key: str
    llm_model: str
    llm_temperature: float
    llm_timeout_seconds: float

    @classmethod
    def from_env(cls) -> Settings:
        """Build settings from the process environment.

        Returns:
            A settings instance containing environment overrides and local
            defaults.

        Raises:
            ValueError: If a numeric environment setting cannot be parsed.
        """

        return cls(
            llm_base_url=os.getenv("LLM_BASE_URL", "http://localhost:8080/v1").rstrip(
                "/"
            ),
            llm_api_key=os.getenv("LLM_API_KEY", "local"),
            llm_model=os.getenv("LLM_MODEL", "local-model"),
            llm_temperature=_float_from_env("LLM_TEMPERATURE", 0.1),
            llm_timeout_seconds=_float_from_env("LLM_TIMEOUT_SECONDS", 120.0),
        )
