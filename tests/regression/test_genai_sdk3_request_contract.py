# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Exercise the installed SDK serializer against an in-process HTTP transport."""

import importlib
import json
from unittest.mock import AsyncMock

import pytest
from google import genai

from core.llm import router


@pytest.mark.asyncio
@pytest.mark.parametrize("transport_name", ["httpx", "httpx2"])
@pytest.mark.parametrize(("configured", "expected"), [(None, "v1beta"), ("beta", "v1beta"), ("v1", "v1")])
async def test_native_sdk_preserves_preview_default_and_explicit_version(
    monkeypatch, transport_name, configured, expected
):
    http = importlib.import_module(transport_name)
    monkeypatch.delenv("GOOGLE_GENAI_USE_VERTEXAI", raising=False)
    monkeypatch.delenv("GOOGLE_GENAI_USE_ENTERPRISE", raising=False)
    monkeypatch.delenv("GOOGLE_GENAI_API_VERSION", raising=False)
    if configured is not None:
        monkeypatch.setenv("GOOGLE_GENAI_API_VERSION", configured)
    requests = []

    def reply(request):
        requests.append(request)
        return http.Response(
            200,
            json={
                "candidates": [{"content": {"role": "model", "parts": [{"text": "synthetic result"}]}}],
                "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 2, "totalTokenCount": 5},
            },
        )

    transport = http.MockTransport(reply)
    sdk_factory = genai.Client
    clients = []
    with http.Client(transport=transport) as sync_http:
        async with http.AsyncClient(transport=transport) as async_http:
            def create(*, api_key, http_options):
                client = sdk_factory(
                    api_key=api_key,
                    http_options={**http_options, "httpx_client": sync_http, "httpx_async_client": async_http},
                )
                clients.append(client)
                return client

            monkeypatch.setattr(genai, "Client", create)
            cap = AsyncMock()
            secret = AsyncMock(return_value="synthetic-key")
            monkeypatch.setattr(router, "assert_under_gemini_cap", cap)
            monkeypatch.setattr(router.LLMRouter, "_provider_secret", secret)
            try:
                result = await router.LLMRouter()._call_gemini(
                    "gemini-flash-latest",
                    [{"role": "user", "content": "synthetic input"}],
                    0.2,
                    64,
                    0.0,
                    tenant_id="00000000-0000-4000-8000-000000000001",
                )
            finally:
                for client in clients:
                    await client.aio.aclose()
                    client.close()
    assert result.content == "synthetic result"
    assert result.tokens_used == 5
    assert len(requests) == 1
    request = requests[0]
    assert request.url.path == f"/{expected}/models/gemini-flash-latest:generateContent"
    body = json.loads(request.content)
    assert body["contents"] == [{"role": "user", "parts": [{"text": "synthetic input"}]}]
    assert body["generationConfig"]["temperature"] == 0.2
    assert body["generationConfig"]["maxOutputTokens"] == 64
    cap.assert_awaited_once_with(tenant_id="00000000-0000-4000-8000-000000000001")
    secret.assert_awaited_once_with("gemini", "00000000-0000-4000-8000-000000000001")


@pytest.mark.parametrize(
    ("enterprise", "vertex", "expected"),
    [
        (None, "true", {}),
        (None, "1", {}),
        ("true", "false", {}),
        ("false", "true", {"api_version": "v1beta"}),
    ],
)
@pytest.mark.asyncio
async def test_native_sdk_respects_vertex_and_enterprise_precedence(monkeypatch, enterprise, vertex, expected):
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", vertex)
    monkeypatch.delenv("GOOGLE_GENAI_USE_ENTERPRISE", raising=False)
    if enterprise is not None:
        monkeypatch.setenv("GOOGLE_GENAI_USE_ENTERPRISE", enterprise)
    monkeypatch.delenv("GOOGLE_GENAI_API_VERSION", raising=False)
    factory = AsyncMock()
    monkeypatch.setattr(router, "assert_under_gemini_cap", factory)
    monkeypatch.setattr(router.LLMRouter, "_provider_secret", AsyncMock(return_value="synthetic-key"))

    def refuse_network(*, api_key, http_options):
        assert api_key == "synthetic-key"
        assert http_options == expected
        raise RuntimeError("synthetic transport boundary")

    monkeypatch.setattr(genai, "Client", refuse_network)
    with pytest.raises(RuntimeError, match="synthetic transport boundary"):
        await router.LLMRouter()._call_gemini("synthetic-model", [], 0.2, 64, 0.0)
    factory.assert_awaited_once()
