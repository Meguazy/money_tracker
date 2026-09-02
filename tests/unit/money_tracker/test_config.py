"""Unit tests for environment-based application settings."""

import os
import unittest
from collections.abc import Iterator, Mapping
from contextlib import contextmanager

from money_tracker.config import Settings


@contextmanager
def isolated_environment(values: Mapping[str, str]) -> Iterator[None]:
    """Temporarily replace the process environment with explicit values.

    Args:
        values: Complete environment visible inside the context.

    Yields:
        Control while the isolated environment is active.
    """

    original = os.environ.copy()
    os.environ.clear()
    os.environ.update(values)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(original)


class SettingsTests(unittest.TestCase):
    """Verify settings parsing against the real process environment."""

    def test_defaults_target_llama_server(self) -> None:
        """Use llama-server defaults when variables are absent."""

        with isolated_environment({}):
            settings = Settings.from_env()

        self.assertEqual(settings.llm_base_url, "http://localhost:8080/v1")
        self.assertEqual(settings.llm_model, "local-model")
        self.assertEqual(settings.llm_temperature, 0.1)

    def test_environment_overrides_are_applied(self) -> None:
        """Apply endpoint, model, and numeric overrides."""

        environment = {
            "LLM_BASE_URL": "http://localhost:11434/v1/",
            "LLM_API_KEY": "ollama",
            "LLM_MODEL": "qwen2.5:14b",
            "LLM_TEMPERATURE": "0.25",
            "LLM_TIMEOUT_SECONDS": "30",
        }
        with isolated_environment(environment):
            settings = Settings.from_env()

        self.assertEqual(settings.llm_base_url, "http://localhost:11434/v1")
        self.assertEqual(settings.llm_model, "qwen2.5:14b")
        self.assertEqual(settings.llm_temperature, 0.25)
        self.assertEqual(settings.llm_timeout_seconds, 30.0)

    def test_invalid_number_has_actionable_error(self) -> None:
        """Reject a nonnumeric sampling temperature."""

        with isolated_environment({"LLM_TEMPERATURE": "cold"}):
            with self.assertRaisesRegex(ValueError, "LLM_TEMPERATURE must be a number"):
                Settings.from_env()


if __name__ == "__main__":
    unittest.main()
