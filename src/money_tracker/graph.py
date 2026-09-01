"""LangGraph definition for the initial money-tracker assistant."""

from langchain.messages import SystemMessage
from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph

from money_tracker.llm import create_chat_model


SYSTEM_PROMPT = """You are a careful personal-finance assistant.
Help the user understand spending and make practical decisions. Do not invent
transactions, balances, categories, or dates. When data is missing, say what
you need before drawing a conclusion.
"""


def build_graph(model: BaseChatModel | None = None) -> CompiledStateGraph:
    """Build the initial personal-finance assistant graph.

    Args:
        model: Chat model used by the assistant node. When omitted, the model
            is constructed from environment settings.

    Returns:
        A compiled graph that accepts and appends LangChain messages.
    """

    chat_model = model or create_chat_model()

    def call_model(state: MessagesState) -> dict[str, list]:
        """Invoke the model with the finance-assistant system contract.

        Args:
            state: Current graph state containing the conversation messages.

        Returns:
            A state update containing the assistant response.
        """

        response = chat_model.invoke(
            [SystemMessage(content=SYSTEM_PROMPT), *state["messages"]]
        )
        return {"messages": [response]}

    builder = StateGraph(MessagesState)
    builder.add_node("assistant", call_model)
    builder.add_edge(START, "assistant")
    builder.add_edge("assistant", END)
    return builder.compile()
