# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import httpx
import pytest

from core.decisioning.contracts import (
    DecisionProviderError,
    DecisionQuestion,
    DecisionRequest,
)
from core.decisioning.jev import JevDecisionProvider
from core.decisioning.runtime import build_jev_provider


def _request() -> DecisionRequest:
    return DecisionRequest(
        state={"tool": "catalog.search", "task": "find a laptop"},
        purpose="tool_routing_shadow",
        questions={
            "route": DecisionQuestion(
                type="choice",
                instructions="Which route is appropriate?",
                criteria={"catalog": "Product discovery", "human": "Needs review"},
            ),
            "confidence_check": DecisionQuestion(
                type="noul",
                instructions="The proposed route is appropriate for this task.",
            ),
        },
    )


@pytest.mark.asyncio
async def test_runtime_builder_requires_explicit_server_side_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)

    with pytest.raises(ValueError, match="TYPESAFE_API_KEY is required"):
        await build_jev_provider()


@pytest.mark.asyncio
async def test_runtime_builder_constructs_jev_from_platform_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-only-platform-secret")

    provider = await build_jev_provider(tenant_id="tenant-1")

    assert isinstance(provider, JevDecisionProvider)
    assert provider.provider_name == "jev"


@pytest.mark.asyncio
async def test_jev_sends_typed_request_and_parses_answers() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://api.typesafe.ai/v1/systemone"
        captured.append(request)
        return httpx.Response(
            200,
            json={
                "model": "jev-1.13.0",
                "answers": {
                    "route": {
                        "type": "choice",
                        "choice": "catalog",
                        "confidence": 0.91,
                        "probabilities": {"catalog": 0.91, "human": 0.09},
                    },
                    "confidence_check": {"type": "noul", "noul": 0.98},
                },
                "usage": {"input_tokens": 12, "output_tokens": 4},
            },
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        response = await JevDecisionProvider("test-only-secret", client=client).decide(_request())

    assert response.provider == "jev"
    assert response.answers["route"].value == "catalog"
    assert response.answers["route"].confidence == 0.91
    assert response.minimum_confidence == 0.91
    assert captured[0].headers["Authorization"] == "Bearer test-only-secret"
    payload = captured[0].content
    assert b"tool_routing_shadow" not in payload


def test_request_validates_atomic_question_shapes() -> None:
    with pytest.raises(ValueError, match="criteria mapping"):
        DecisionQuestion("choice", "Choose", criteria=["not", "a", "mapping"]).to_payload()

    with pytest.raises(ValueError, match="at least two"):
        DecisionQuestion("score", "Score", criteria=["only one"]).to_payload()

    with pytest.raises(ValueError, match="do not accept criteria"):
        DecisionQuestion("noul", "Check", criteria={"yes": "yes"}).to_payload()

    with pytest.raises(ValueError, match="unsupported decision question type"):
        DecisionQuestion("unknown", "Check").to_payload()  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_jev_provider_redacts_upstream_error_details() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="secret upstream payload", request=request)

    with pytest.raises(DecisionProviderError, match="Jev decision request failed") as exc_info:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await JevDecisionProvider("test-only-secret", client=client).decide(_request())

    assert "secret upstream payload" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_invalid_response_fails_closed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"model": "jev-1.13.0", "answers": []},
            request=request,
        )

    with pytest.raises(DecisionProviderError, match="invalid decision response"):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await JevDecisionProvider("test-only-secret", client=client).decide(_request())


@pytest.mark.asyncio
async def test_unsupported_answer_type_fails_closed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"answers": {"route": {"type": "freeform", "text": "catalog"}}},
            request=request,
        )

    with pytest.raises(DecisionProviderError, match="unsupported answer type"):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await JevDecisionProvider("test-only-secret", client=client).decide(_request())
