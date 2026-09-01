"""Integration tests for a real OpenAI-compatible inference service."""

import os
import unittest

from langchain.messages import HumanMessage

from money_tracker.config import Settings
from money_tracker.llm import create_chat_model


def required_environment(name: str) -> str:
    """Read a required integration-test setting.

    Args:
        name: Name of the required environment variable.

    Returns:
        The configured non-empty value.

    Raises:
        RuntimeError: If the variable is absent or empty.
    """

    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is required for real-service tests")
    return value


@unittest.skipUnless(
    os.getenv("RUN_INTEGRATION_TESTS") == "1",
    "set RUN_INTEGRATION_TESTS=1 to exercise a real inference service",
)
class LocalInferenceIntegrationTests(unittest.TestCase):
    """Exercise the production model adapter against a running service."""

    def test_chat_model_completes_a_real_request(self) -> None:
        """Return a non-empty response through the real HTTP API."""

        settings = Settings(
            llm_base_url=required_environment("INTEGRATION_LLM_BASE_URL"),
            llm_api_key=os.getenv("INTEGRATION_LLM_API_KEY", "local"),
            llm_model=required_environment("INTEGRATION_LLM_MODEL"),
            llm_temperature=0.0,
            llm_timeout_seconds=120.0,
        )

        response = create_chat_model(settings).invoke(
            [HumanMessage(content="Reply with exactly: integration works")]
        )

        self.assertTrue(response.text.strip())


if __name__ == "__main__":
    unittest.main()
