"""End-to-end tests for the packaged command-line workflow."""

import os
import subprocess
import sys
import unittest

from tests.integration.money_tracker.test_llm import required_environment


@unittest.skipUnless(
    os.getenv("RUN_E2E_TESTS") == "1",
    "set RUN_E2E_TESTS=1 to exercise the CLI and real inference service",
)
class MoneyTrackerCliEndToEndTests(unittest.TestCase):
    """Exercise the CLI, graph, adapter, and local model as one workflow."""

    def test_cli_returns_an_assistant_response(self) -> None:
        """Complete a user prompt through the module entry point."""

        environment = os.environ.copy()
        environment.update(
            {
                "LLM_BASE_URL": required_environment("E2E_LLM_BASE_URL"),
                "LLM_API_KEY": os.getenv("E2E_LLM_API_KEY", "local"),
                "LLM_MODEL": required_environment("E2E_LLM_MODEL"),
                "LLM_TEMPERATURE": "0",
            }
        )

        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "money_tracker",
                "Reply with exactly: end to end works",
            ],
            check=False,
            capture_output=True,
            env=environment,
            text=True,
            timeout=180,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(completed.stdout.strip())


if __name__ == "__main__":
    unittest.main()
