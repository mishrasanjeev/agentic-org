# SPDX-License-Identifier: Apache-2.0
"""The request gate in front of every spend route.

FastAPI reads a multipart body before it resolves any dependency, so
neither the route metadata, the administrator dependency nor the flag
check can stop an oversize upload. This pure ASGI middleware answers
before the route does:

* a path outside ``/api/v1/spend/`` passes unchanged, as does ``OPTIONS``;
* while ``spend_intelligence_enabled`` is off, every spend path except
  ``/api/v1/spend/status`` is answered 404 ``spend_disabled`` without
  reading the body;
* a ``POST .../import`` whose declared ``Content-Length`` passes the import
  bound is answered 413 ``import_too_large``; otherwise its body is counted
  as it streams and the request is refused with 413 once the count passes
  the bound (a chunked body declares no length).

Registered inside CORS and authentication (``api/main.py``), so an
unauthenticated caller is refused first and CORS headers are on its answers.
"""

from __future__ import annotations

import json
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from core import spend
from core.spend.imports import MAX_IMPORT_BYTES

SPEND_PREFIX = "/api/v1/spend"
STATUS_PATH = "/api/v1/spend/status"
# The file bound plus room for the multipart envelope around it.
IMPORT_BODY_LIMIT = MAX_IMPORT_BYTES + 65_536


async def _answer(send: Send, status: int, detail: dict[str, Any]) -> None:
    body = json.dumps({"detail": detail}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": body})


def _declared_length(scope: Scope) -> int | None:
    for name, value in scope.get("headers") or ():
        if name.lower() == b"content-length":
            try:
                return int(value.decode("latin-1").strip())
            except ValueError:
                return None
    return None


def _too_large() -> dict[str, Any]:
    return {"error": "import_too_large", "message": f"an import body is at most {IMPORT_BODY_LIMIT} bytes"}


class SpendRequestGate:
    """404 for spend paths while the feature is off; a byte bound on spend imports while it is on."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = str(scope.get("path") or "")
        if scope["type"] != "http" or not (path == SPEND_PREFIX or path.startswith(SPEND_PREFIX + "/")):
            await self.app(scope, receive, send)
            return
        method = str(scope.get("method") or "").upper()
        if method == "OPTIONS":
            await self.app(scope, receive, send)
            return
        if not spend.enabled() and path.rstrip("/") != STATUS_PATH:
            await _answer(
                send,
                404,
                {
                    "error": "spend_disabled",
                    "message": "AI spend intelligence is off (AGENTICORG_SPEND_INTELLIGENCE_ENABLED).",
                },
            )
            return
        if method != "POST" or not path.rstrip("/").endswith("/import"):
            await self.app(scope, receive, send)
            return
        declared = _declared_length(scope)
        if declared is not None and declared > IMPORT_BODY_LIMIT:
            await _answer(send, 413, _too_large())
            return
        await self._bounded(scope, receive, send)

    async def _bounded(self, scope: Scope, receive: Receive, send: Send) -> None:
        state = {"received": 0, "exceeded": False, "started": False}

        async def counted_receive() -> Message:
            if state["exceeded"]:
                return {"type": "http.disconnect"}
            message = await receive()
            if message.get("type") == "http.request":
                state["received"] += len(message.get("body") or b"")
                if state["received"] > IMPORT_BODY_LIMIT:
                    state["exceeded"] = True
                    return {"type": "http.disconnect"}
            return message

        async def guarded_send(message: Message) -> None:
            if state["exceeded"]:
                return
            if message.get("type") == "http.response.start":
                state["started"] = True
            await send(message)

        try:
            await self.app(scope, counted_receive, guarded_send)
        # enterprise-gate: broad-except-ok reason=oversize-body-refuses-with-413-and-reraises-every-other-error
        except Exception:
            if not state["exceeded"]:
                raise
        if state["exceeded"] and not state["started"]:
            await _answer(send, 413, _too_large())
