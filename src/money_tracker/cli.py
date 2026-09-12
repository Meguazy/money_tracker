"""Command-line entry point for exercising the graph."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from langchain.messages import HumanMessage
from langchain_core.runnables import RunnableConfig

from money_tracker.graph import build_graph
from money_tracker.observability import create_callbacks, flush_traces
from money_tracker.structured_logging import (
    configure_logging,
    get_logger,
    log_context,
)

_LOGGER = get_logger("money_tracker.cli")


def _parse_prompt(argv: Sequence[str] | None = None) -> str:
    """Parse a prompt from arguments or read one interactively.

    Args:
        argv: Optional argument sequence. The process arguments are used when
            omitted.

    Returns:
        The normalized prompt, which may be empty when interactive input is
        empty.
    """

    parser = argparse.ArgumentParser(description="Run the local money-tracker graph.")
    parser.add_argument("prompt", nargs="*", help="Prompt to send to the assistant")
    args = parser.parse_args(argv)
    prompt = " ".join(args.prompt).strip()
    return prompt or input("You: ").strip()


def main(argv: Sequence[str] | None = None) -> None:
    """Run one assistant turn through the compiled graph.

    Args:
        argv: Optional command-line arguments used instead of the process
            arguments.

    Raises:
        SystemExit: If no non-empty prompt is supplied.
    """

    configure_logging()
    prompt = _parse_prompt(argv)
    if not prompt:
        _LOGGER.warning("cli.prompt.empty")
        raise SystemExit("A non-empty prompt is required.")

    callbacks = create_callbacks()
    config: RunnableConfig = {
        "run_name": "money-tracker-cli",
        "tags": ["local-inference", "money-tracker"],
    }
    if callbacks:
        config["callbacks"] = callbacks

    # The prompt is owner content and never reaches a field; its length is
    # enough to tell an empty turn from a long one while diagnosing.
    with log_context(prompt_characters=len(prompt), traced=bool(callbacks)):
        try:
            with _LOGGER.timed("cli.turn"):
                result = build_graph().invoke(
                    {"messages": [HumanMessage(content=prompt)]},
                    config=config,
                )
            print(result["messages"][-1].text)
        finally:
            flush_traces(enabled=bool(callbacks))
