# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Google credential checks never place a customer key in a URL."""

import httpx
import pytest

from core.ai_providers import health


@pytest.mark.parametrize("status", [200, 401, 429])
async def test_gemini_probe_uses_key_header_not_url(monkeypatch, status):
    credential = "synthetic-authorization-key"

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert str(request.url) == "https://generativelanguage.googleapis.com/v1beta/models"
        assert credential not in str(request.url)
        assert request.headers["x-goog-api-key"] == credential
        return httpx.Response(status, json={"models": []})

    monkeypatch.setattr(health, "build_pinned_async_transport", lambda **kwargs: httpx.MockTransport(respond))
    result = await health._probe_gemini(credential)
    assert result["ok"] is (status == 200)
    assert result["raw_status"] == status
    assert credential not in repr(result)
