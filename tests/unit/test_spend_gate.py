# SPDX-License-Identifier: Apache-2.0
"""The spend request gate: 404 while off before the body is read, and the import body bound."""

from __future__ import annotations

import json

import pytest

from api.middleware import spend_gate
from api.middleware.spend_gate import IMPORT_BODY_LIMIT, SpendRequestGate
from core.config import settings


class _Sent:
    def __init__(self):
        self.messages = []

    async def __call__(self, message):
        self.messages.append(message)

    @property
    def status(self):
        return next((m["status"] for m in self.messages if m["type"] == "http.response.start"), None)

    @property
    def detail(self):
        body = b"".join(m.get("body", b"") for m in self.messages if m["type"] == "http.response.body")
        return json.loads(body)["detail"] if body else None


async def _never_receive():
    raise AssertionError("the body must not be read")


def _scope(path, method="POST", headers=()):
    return {"type": "http", "path": path, "method": method, "headers": list(headers)}


class _App:
    """An inner app that records the call and, when asked, reads the whole body like a form parser."""

    def __init__(self, read_body=False):
        self.calls = 0
        self.read_body = read_body
        self.body = b""

    async def __call__(self, scope, receive, send):
        self.calls += 1
        if self.read_body:
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    raise RuntimeError("client disconnected")
                self.body += message.get("body", b"")
                if not message.get("more_body"):
                    break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})


@pytest.mark.asyncio
async def test_gate_answers_404_while_off_before_reading_the_body(monkeypatch):
    monkeypatch.setattr(settings, "spend_intelligence_enabled", False)
    app = _App()
    gate = SpendRequestGate(app)
    for path in ("/api/v1/spend/org-nodes/import", "/api/v1/spend/rate-cards", "/api/v1/spend"):
        sent = _Sent()
        await gate(_scope(path), _never_receive, sent)
        assert sent.status == 404 and sent.detail["error"] == "spend_disabled"
    assert app.calls == 0
    sent = _Sent()
    await gate(_scope("/api/v1/spend/status", "GET"), _never_receive, sent)
    assert sent.status == 200 and app.calls == 1


@pytest.mark.asyncio
async def test_gate_refuses_declared_oversize_import(monkeypatch):
    monkeypatch.setattr(settings, "spend_intelligence_enabled", True)
    app = _App()
    gate = SpendRequestGate(app)
    sent = _Sent()
    headers = [(b"content-length", str(IMPORT_BODY_LIMIT + 1).encode())]
    await gate(_scope("/api/v1/spend/rate-cards/import", headers=headers), _never_receive, sent)
    assert sent.status == 413 and sent.detail["error"] == "import_too_large" and app.calls == 0
    assert spend_gate._declared_length(_scope("/x", headers=[(b"Content-Length", b"abc")])) is None


@pytest.mark.asyncio
async def test_gate_counts_chunked_body(monkeypatch):
    monkeypatch.setattr(settings, "spend_intelligence_enabled", True)
    chunk = b"x" * 65_536

    def chunks(total):
        sent_bytes = 0

        async def receive():
            nonlocal sent_bytes
            sent_bytes += len(chunk)
            return {"type": "http.request", "body": chunk, "more_body": sent_bytes < total}

        return receive

    app = _App(read_body=True)
    sent = _Sent()
    await SpendRequestGate(app)(_scope("/api/v1/spend/org-nodes/import"), chunks(IMPORT_BODY_LIMIT * 2), sent)
    assert sent.status == 413 and sent.detail["error"] == "import_too_large"
    assert [m["status"] for m in sent.messages if m["type"] == "http.response.start"] == [413]

    small = _App(read_body=True)
    sent = _Sent()
    await SpendRequestGate(small)(_scope("/api/v1/spend/org-nodes/import"), chunks(len(chunk) * 2), sent)
    assert sent.status == 200 and len(small.body) == len(chunk) * 2

    class _Quiet(_App):
        async def __call__(self, scope, receive, send):
            while (await receive())["type"] != "http.disconnect":
                pass
            await send({"type": "http.response.start", "status": 400, "headers": []})
            await send({"type": "http.response.body", "body": b"{}"})

    sent = _Sent()
    await SpendRequestGate(_Quiet())(_scope("/api/v1/spend/fx-rates/import"), chunks(IMPORT_BODY_LIMIT * 2), sent)
    assert sent.status == 413  # the app's own answer to the cut body is replaced

    class _Broken(_App):
        async def __call__(self, scope, receive, send):
            raise ValueError("unrelated failure")

    with pytest.raises(ValueError):
        await SpendRequestGate(_Broken())(_scope("/api/v1/spend/fx-rates/import"), chunks(10), _Sent())


@pytest.mark.asyncio
async def test_gate_passes_non_spend_paths_and_options(monkeypatch):
    monkeypatch.setattr(settings, "spend_intelligence_enabled", False)
    app = _App()
    gate = SpendRequestGate(app)
    for scope in (
        _scope("/api/v1/agents"),
        _scope("/api/v1/spending"),
        _scope("/api/v1/spend/rate-cards", "OPTIONS"),
        {"type": "websocket", "path": "/api/v1/spend/x"},
    ):
        await gate(scope, _never_receive, _Sent())
    assert app.calls == 4
    monkeypatch.setattr(settings, "spend_intelligence_enabled", True)
    sent = _Sent()
    await gate(_scope("/api/v1/spend/rate-cards", "POST"), _never_receive, sent)
    assert sent.status == 200 and app.calls == 5  # not an import: passed without counting


def test_gate_runs_inside_authentication_and_cors():
    from api.main import app

    order = [m.cls.__name__ for m in app.user_middleware]
    assert order[0] == "RequestIDMiddleware" and order[-1] == "SpendRequestGate"
    assert order.index("CORSMiddleware") == len(order) - 2 and order.index("GrantexAuthMiddleware") < len(order) - 1
