# SPDX-License-Identifier: Apache-2.0
"""Repository SDK A2A wire contract without publishing either package."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import httpx
import pytest


def test_python_sdk_sends_a2a_message_with_scoped_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    path = Path(__file__).resolve().parents[2] / "sdk" / "agenticorg" / "client.py"
    spec = importlib.util.spec_from_file_location("_a2a_sdk_under_test", path)
    assert spec is not None and spec.loader is not None
    sdk = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = sdk
    spec.loader.exec_module(sdk)
    observed: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        if request.url.path == "/api/v1/a2a/message:send":
            return httpx.Response(200, json={"message": {"parts": [{"text": "sourced answer"}]}})
        if request.url.path == "/api/v1/a2a/commerce/buyer-access":
            return httpx.Response(200, json={"id": "access-1", "token": "ao_buyer_example"})
        return httpx.Response(200, json={"skills": []})

    original = sdk.httpx.Client
    monkeypatch.setattr(sdk.httpx, "Client", lambda *args, **kw: original(
        *args, transport=httpx.MockTransport(handler), **kw,
    ))
    with sdk.AgenticOrg(api_key="ao_buyer_example", base_url="https://seller.test") as buyer:
        assert buyer.a2a.standard_agent_card() == {"skills": []}
        assert buyer.a2a.extended_agent_card() == {"skills": []}
        assert buyer.a2a.send_message("Is the tote available?")["message"]["parts"][0]["text"] == "sourced answer"
    sent = observed[-1]
    assert sent.headers["authorization"] == "Bearer ao_buyer_example"
    assert sent.headers["content-type"] == "application/a2a+json"
    assert sent.headers["a2a-version"] == "1.0"
    payload = json.loads(sent.content)
    assert payload["message"]["role"] == "ROLE_USER"
    assert payload["message"]["parts"] == [{"text": "Is the tote available?"}]
    with sdk.AgenticOrg(api_key="admin", base_url="https://seller.test") as admin:
        assert admin.a2a.create_buyer_access(
            merchant_id="m", seller_agent_id="s", buyer_agent_id="b",
        )["id"] == "access-1"
        assert admin.a2a.revoke_buyer_access("access-1") == {"skills": []}
    assert json.loads(observed[-2].content)["buyer_agent_id"] == "b"
