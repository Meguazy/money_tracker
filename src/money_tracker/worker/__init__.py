"""Sequential processing of durable channel work units.

The worker is the only component that joins durable messaging state to the
Step 3 dispatcher. It is a separate process from the gateway on purpose: the
gateway must keep accepting Telegram updates while inference is slow or down,
and this side must be able to stop without losing anything Telegram already
handed over.
"""

from money_tracker.worker.sequential import (
    SequentialWorker,
    WorkerConfigurationReader,
    WorkflowDispatchPort,
)

__all__ = [
    "SequentialWorker",
    "WorkerConfigurationReader",
    "WorkflowDispatchPort",
]
