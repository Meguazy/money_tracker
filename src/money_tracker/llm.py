"""Local LLM construction."""

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from money_tracker.config import Settings
from money_tracker.structured_logging import get_logger

_LOGGER = get_logger("money_tracker.llm")


def create_chat_model(settings: Settings | None = None) -> BaseChatModel:
    """Create a client for an OpenAI-compatible local inference server.

    Args:
        settings: Explicit runtime settings. Environment settings are used when
            this value is omitted.

    Returns:
        A LangChain chat model configured for the selected local endpoint.
    """

    resolved = settings or Settings.from_env()
    # The endpoint and model decide every inference result, so a journal that
    # cannot say which one answered cannot explain a behavior change. The API
    # key is configuration and stays out.
    _LOGGER.info(
        "inference.client.created",
        base_url=resolved.llm_base_url,
        model=resolved.llm_model,
        temperature=resolved.llm_temperature,
        timeout_seconds=resolved.llm_timeout_seconds,
    )
    return ChatOpenAI(
        model=resolved.llm_model,
        base_url=resolved.llm_base_url,
        api_key=SecretStr(resolved.llm_api_key),
        temperature=resolved.llm_temperature,
        timeout=resolved.llm_timeout_seconds,
        max_retries=2,
    )
