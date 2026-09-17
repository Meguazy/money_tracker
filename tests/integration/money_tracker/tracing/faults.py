"""A fault-injection proxy in front of the real local Langfuse.

Degradation tests need the store slow or refusing without touching the Compose
stack, and a stopped store without stopping a container other sessions may be
using. The proxy sits where the real Langfuse would be: it holds every request
past the export bound and then drops it, or refuses every request with 503, and
a closed loopback port stands for a stopped store.

A held request is dropped rather than forwarded late. The exporter has given up
on it by then, and forwarding would store traces the test can neither name nor
delete. The real store is still reached by every other tracing test.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

MODES = ("stopped", "slow", "refusing")
"""Store failures every degradation test is run against."""

SLOW_SECONDS = 6.0
"""How long the slow store holds each request, well past the export bound."""


@dataclass
class FaultProxy:
    """Loopback proxy whose behaviour is one fault mode.

    Attributes:
        mode: `slow` or `refusing`.
        requests: Requests the proxy received.
    """

    mode: str
    requests: int = 0


def _handler(proxy: FaultProxy) -> type[BaseHTTPRequestHandler]:
    """Build a handler applying the proxy's fault to every request."""

    class Handler(BaseHTTPRequestHandler):
        """Hold or refuse one request; nothing reaches a store."""

        def _serve(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(length)
            proxy.requests += 1
            if proxy.mode == "refusing":
                self._reply(503, b'{"message":"refused by fault injection"}')
                return
            time.sleep(SLOW_SECONDS)
            self.close_connection = True

        def _reply(self, status: int, content: bytes) -> None:
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            except OSError:
                # The exporter may already have given up; its timeout is the
                # behaviour under test, not a proxy failure.
                pass

        def do_GET(self) -> None:  # noqa: N802 - name fixed by http.server
            """Apply the fault to a read."""

            self._serve()

        def do_POST(self) -> None:  # noqa: N802 - name fixed by http.server
            """Apply the fault to an export."""

            self._serve()

        def log_message(self, format: str, *args: Any) -> None:
            """Stay silent; the request count is the evidence."""

    return Handler


def closed_port_url() -> str:
    """Return a loopback base URL nothing listens on, standing for a stopped store."""

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
    return f"http://127.0.0.1:{port}"


@contextmanager
def faulty_store(mode: str) -> Iterator[tuple[str, FaultProxy | None]]:
    """Provide a store base URL exhibiting one failure mode.

    Args:
        mode: `stopped`, `slow`, or `refusing`.

    Yields:
        The loopback base URL to configure and the proxy, when one runs.
    """

    if mode == "stopped":
        yield closed_port_url(), None
        return
    proxy = FaultProxy(mode=mode)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(proxy))
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", proxy
    finally:
        server.shutdown()
        server.server_close()
