"""Request correlation middleware (bug sheet 2026-09-14 #8).

Pure ASGI (not ``BaseHTTPMiddleware``) so it adds no extra task hop and
the contextvars it binds are inherited by every downstream middleware and
the route handler. Registered as the OUTERMOST middleware in ``api/main.py``.

* Accepts a caller-supplied ``X-Request-ID`` when it is at most 128 chars of
  ``[A-Za-z0-9._-]``; anything else is replaced with a fresh uuid4 so log
  fields never carry attacker-shaped strings.
* Binds ``request_id``, ``method`` and ``path`` via
  ``structlog.contextvars`` for the lifetime of the request, then clears
  them so worker tasks/keep-alive connections never leak a stale id.
* Echoes the effective ``X-Request-ID`` on the response so clients and
  support can correlate a failure with its container log lines.
"""

from __future__ import annotations

import re
import uuid

import structlog.contextvars
from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_ID_HEADER = "x-request-id"
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def normalize_request_id(raw: str | None) -> str:
    """Return *raw* when it is a safe correlation id, else a new uuid4."""
    candidate = (raw or "").strip()
    if candidate and _REQUEST_ID_RE.fullmatch(candidate):
        return candidate
    return str(uuid.uuid4())


def _incoming_request_id(scope: Scope) -> str | None:
    for key, value in scope.get("headers", ()):
        if key == b"x-request-id":
            return value.decode("latin-1", errors="replace")
    return None


class RequestIDMiddleware:
    """Bind a per-request correlation id into structlog contextvars."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        request_id = normalize_request_id(_incoming_request_id(scope))
        scope.setdefault("state", {})["request_id"] = request_id

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                headers = [(k, v) for k, v in headers if k.lower() != b"x-request-id"]
                headers.append((b"x-request-id", request_id.encode("ascii")))
                message["headers"] = headers
            await send(message)

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=scope.get("method", scope["type"]),
            path=scope.get("path", ""),
        )
        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            structlog.contextvars.clear_contextvars()
