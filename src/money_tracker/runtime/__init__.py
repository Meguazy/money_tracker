"""LangGraph workflow runtime: the only component that binds a framework.

`money_tracker.workflows` stays framework-independent so its routing,
extraction, resolution, and retry policy can be verified without LangGraph, an
inference server, or a database. This package holds the parts that cannot:
the local structured-output adapter and the compiled root-router graph.
"""

from money_tracker.runtime.model import LocalStructuredOutputModel
from money_tracker.runtime.router_graph import (
    CLASSIFY_NODE,
    PRE_ROUTE_NODE,
    build_router_graph,
)

__all__ = [
    "CLASSIFY_NODE",
    "PRE_ROUTE_NODE",
    "LocalStructuredOutputModel",
    "build_router_graph",
]
