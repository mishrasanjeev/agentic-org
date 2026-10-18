# SPDX-License-Identifier: Apache-2.0
"""Provider request regressions; scripted responses are not provider accuracy proof."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

MODEL = "gemini-3.5-flash-lite"
TENANT = "00000000-0000-4000-8000-000000000001"
TASK = (
    "QA-AGENT-20261018 synthetic arithmetic only. There are 7, 11 and 13 items. "
    "Remove the 11-item box, then remove 2 more items. How many remain? "
    "State the calculation and final count in at most two short sentences. "
    "Use no tools or external data; do not send messages, make calls, execute "
    "payments or perform any other action."
)
PROMPT = (
    "You are a synthetic arithmetic acceptance agent. Use only the task text. "
    "Do not use tools, external services, memory, documents, calls or payments. "
    "Return only valid JSON with numeric answer and numeric confidence between 0 and 1. "
    "Keep output under 30 words."
)


@pytest.mark.parametrize("model", [MODEL, "models/" + MODEL])
def test_langgraph_builds_reasoning_request_not_minimal_default(monkeypatch, model):
    from langchain_google_genai import ChatGoogleGenerativeAI

    from core.langgraph import llm_factory

    credential = MagicMock(return_value="synthetic-key")
    monkeypatch.setattr(llm_factory, "_resolve_cloud_api_key", credential)
    # Use the installed adapter's real request serializer, without network access.
    llm = llm_factory._build_gemini_model(model, 0.1, 4096, TENANT)
    assert isinstance(llm, ChatGoogleGenerativeAI)
    config = llm._prepare_params(stop=None).model_dump(exclude_none=True)
    assert str(config["thinking_config"]["thinking_level"]).lower().endswith("medium")
    assert "temperature" not in config
    assert config["max_output_tokens"] == 4096
    credential.assert_called_once_with("gemini", TENANT)


def test_older_gemini_keeps_its_supported_sampling_parameters(monkeypatch):
    from core.langgraph import llm_factory

    monkeypatch.setattr(llm_factory, "_resolve_cloud_api_key", lambda *_: "synthetic-key")
    llm = llm_factory._build_gemini_model("gemini-2.5-flash", 0.2, 1234, TENANT)
    config = llm._prepare_params(stop=None).model_dump(exclude_none=True)
    assert config["temperature"] == 0.2
    assert config["max_output_tokens"] == 1234
    assert "thinking_config" not in config


@pytest.mark.asyncio
@pytest.mark.parametrize("spend_enabled", [False, True])
async def test_native_gemini_matches_reasoning_and_counts_thought_cost_once(monkeypatch, spend_enabled):
    from google import genai

    from core.langgraph.runner import _build_user_message, _with_reference_resolution_guidance
    from core.llm import router
    from core.spend.tokens import from_router

    response = SimpleNamespace(
        text='{"answer":18,"confidence":1.0}',
        candidates=[],
        usage_metadata=SimpleNamespace(
            prompt_token_count=410, candidates_token_count=19, thoughts_token_count=592
        ),
    )
    generate = AsyncMock(return_value=response)
    client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate)))
    factory = MagicMock(return_value=client)
    credential = AsyncMock(return_value="synthetic-key")
    cap = AsyncMock()
    monkeypatch.setattr(genai, "Client", factory)
    monkeypatch.setattr(router.LLMRouter, "_provider_secret", credential)
    monkeypatch.setattr(router, "assert_under_gemini_cap", cap)
    monkeypatch.setattr(router.spend, "enabled", lambda: spend_enabled)
    messages = [
        {"role": "system", "content": _with_reference_resolution_guidance(PROMPT)},
        {"role": "user", "content": _build_user_message({"action": "run", "inputs": {"task": TASK}, "context": {}})},
    ]
    result = await router.LLMRouter()._call_gemini(MODEL, messages, 0.1, 4096, 0.0, tenant_id=TENANT)
    kwargs = generate.call_args.kwargs
    assert kwargs["config"]["thinking_config"] == {"thinking_level": "medium"}
    assert "temperature" not in kwargs["config"]
    assert kwargs["contents"][0]["parts"][0]["text"] == messages[1]["content"]
    assert kwargs["config"]["system_instruction"] == messages[0]["content"]
    assert result.content == response.text
    assert result.tokens_used == 410 + 19 + 592
    assert result.cost_usd == pytest.approx((410 * 0.30 + 611 * 2.50) / 1_000_000)
    # The detailed spend meter already adds thoughts to candidate output separately.
    assert result.output_tokens == 19
    details = from_router(result, None, messages)
    assert (details.extra_output_tokens if details else None) == (592 if spend_enabled else None)
    credential.assert_awaited_once_with("gemini", TENANT)
    cap.assert_awaited_once_with(tenant_id=TENANT)
    generate.assert_awaited_once()


def test_exact_task_keeps_every_operand_after_assembly_and_redaction():
    from core.langgraph.runner import _build_user_message
    from core.pii.redactor import PIIRedactor

    message, _ = PIIRedactor().redact(_build_user_message({"action": "run", "inputs": {"task": TASK}, "context": {}}))
    for phrase in ("7, 11 and 13 items", "11-item box", "2 more items"):
        assert phrase in message


def test_missing_tenant_credential_still_fails_closed(monkeypatch):
    from core.langgraph import llm_factory
    from core.llm.router import LLMProviderConfigurationError

    monkeypatch.setattr(llm_factory, "_resolve_cloud_api_key", lambda *_: "")
    with pytest.raises(LLMProviderConfigurationError, match="not configured"):
        llm_factory._build_gemini_model(MODEL, 0.1, 4096, TENANT)
