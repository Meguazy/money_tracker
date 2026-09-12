"""Same-origin browser protections for the loopback FastAPI adapter."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from hmac import compare_digest

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from money_tracker.api.contracts import PROBLEM_MEDIA_TYPE, ProblemDetails

CSRF_COOKIE_NAME = "money_tracker_csrf"
CSRF_HEADER_NAME = "x-csrf-token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


@dataclass(frozen=True, slots=True)
class BrowserSecurityPolicy:
    """Exact HTTP authority accepted by the local browser boundary.

    Attributes:
        allowed_hosts: Exact Host header names, without ports.
        allowed_origins: Exact serialized origins accepted from browsers.
    """

    allowed_hosts: frozenset[str]
    allowed_origins: frozenset[str]

    def __post_init__(self) -> None:
        """Require a closed, non-empty policy."""

        if not self.allowed_hosts or not self.allowed_origins:
            raise ValueError("browser security policy must be closed and non-empty")


class LocalBrowserProtectionMiddleware:
    """Reject DNS rebinding, cross-origin reads, and CSRF mutations.

    This middleware deliberately does not emit CORS permission headers. The
    dashboard and API share one origin, and a different origin has no supported
    browser access path.
    """

    def __init__(self, app: ASGIApp, *, policy: BrowserSecurityPolicy) -> None:
        """Wrap an ASGI application with one immutable security policy."""

        self._app = app
        self._policy = policy

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Apply the policy to each HTTP request and harden its response."""

        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        host = _host_name(headers.get("host", ""))
        if host not in self._policy.allowed_hosts:
            await _send_problem(scope, receive, send, "host_not_allowed")
            return
        origin = headers.get("origin")
        if origin is not None and origin not in self._policy.allowed_origins:
            await _send_problem(scope, receive, send, "origin_not_allowed")
            return
        method = str(scope.get("method", "GET")).upper()
        if method not in SAFE_METHODS:
            cookie_token = _cookie(headers.get("cookie", ""), CSRF_COOKIE_NAME)
            header_token = headers.get(CSRF_HEADER_NAME)
            if (
                origin is None
                or cookie_token is None
                or header_token is None
                or not compare_digest(cookie_token, header_token)
            ):
                await _send_problem(scope, receive, send, "csrf_validation_failed")
                return

        async def hardened_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_headers = list(message.get("headers", []))
                response_headers.extend(
                    (
                        (b"content-security-policy", _content_security_policy()),
                        (b"referrer-policy", b"no-referrer"),
                        (b"x-content-type-options", b"nosniff"),
                        (b"x-frame-options", b"DENY"),
                    )
                )
                if str(scope.get("path", "")).startswith("/api/") and not any(
                    name.lower() == b"cache-control" for name, _ in response_headers
                ):
                    response_headers.append((b"cache-control", b"no-store"))
                message["headers"] = response_headers
            await send(message)

        await self._app(scope, receive, hardened_send)


def issue_csrf_token() -> str:
    """Create a high-entropy per-browser CSRF value.

    Returns:
        URL-safe random token suitable for the header/cookie double-submit
        contract. It conveys no server-side configuration secret.
    """

    return secrets.token_urlsafe(32)


def _host_name(value: str) -> str:
    """Extract the normalized host name from a Host header."""

    return value.rsplit(":", 1)[0].lower() if ":" in value else value.lower()


def _cookie(value: str, name: str) -> str | None:
    """Read one exact cookie without accepting malformed pairs."""

    for pair in value.split(";"):
        key, separator, item = pair.strip().partition("=")
        if separator and key == name and item:
            return item
    return None


def _content_security_policy() -> bytes:
    """Return the policy required by the bundled React/Mantine dashboard."""

    return (
        b"default-src 'self'; base-uri 'none'; connect-src 'self'; "
        b"font-src 'self'; frame-ancestors 'none'; img-src 'self' data:; "
        b"object-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'"
    )


async def _send_problem(
    scope: Scope,
    receive: Receive,
    send: Send,
    code: str,
) -> None:
    """Return one bounded problem without reflecting an untrusted header."""

    response = JSONResponse(
        ProblemDetails(
            title="Richiesta non consentita",
            status=403,
            detail="La richiesta non rispetta i controlli del servizio locale.",
            instance=str(scope.get("path", "/")),
            code=code,
        ).model_dump(mode="json"),
        status_code=403,
        media_type=PROBLEM_MEDIA_TYPE,
    )
    await response(scope, receive, _hardened_problem_send(send))


def _hardened_problem_send(send: Send) -> Send:
    """Add security headers to a response rejected before the wrapped app."""

    async def hardened(message: Message) -> None:
        if message["type"] == "http.response.start":
            headers = list(message.get("headers", []))
            headers.extend(
                (
                    (b"content-security-policy", _content_security_policy()),
                    (b"referrer-policy", b"no-referrer"),
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                )
            )
            message["headers"] = headers
        await send(message)

    return hardened


__all__ = [
    "BrowserSecurityPolicy",
    "CSRF_COOKIE_NAME",
    "CSRF_HEADER_NAME",
    "LocalBrowserProtectionMiddleware",
    "issue_csrf_token",
]
