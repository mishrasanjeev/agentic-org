# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
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
    llm = llm_factory._build_gemini_model(model, 0.1, 4096, TENANT, thinking_level="medium")
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
        usage_metadata=SimpleNamespace(prompt_token_count=410, candidates_token_count=19, thoughts_token_count=592),
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
    result = await router.LLMRouter()._call_gemini(
        MODEL, messages, 0.1, 4096, 0.0, tenant_id=TENANT, thinking_level="medium"
    )
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


def test_unspecified_profile_preserves_provider_default_in_both_clients(monkeypatch):
    from core.langgraph import llm_factory
    from core.llm.gemini_config import generation_config

    monkeypatch.setattr(llm_factory, "_resolve_cloud_api_key", lambda *_: "synthetic-key")
    llm = llm_factory._build_gemini_model(MODEL, 0.1, 4096, TENANT)
    config = llm._prepare_params(stop=None).model_dump(exclude_none=True)
    assert "thinking_config" not in config
    assert "thinking_config" not in generation_config(MODEL, 0.1, 4096)


@pytest.mark.parametrize("level", ["high", "low", "", 123, True])
def test_api_refuses_unavailable_profiles(level):
    from pydantic import ValidationError

    from core.schemas.api import LLMConfig

    with pytest.raises(ValidationError):
        LLMConfig(model=MODEL, thinking_level=level)


def test_saved_profile_is_optional_validated_and_snapshot_for_resume():
    from pydantic import ValidationError

    from api.v1.agents import _run_resume_spec, _thinking_profile
    from core.schemas.api import AgentUpdate, LLMConfig

    assert LLMConfig(model=MODEL).thinking_level is None
    assert LLMConfig(model=f" {MODEL} ", provider=" Gemini ", thinking_level="medium").model == MODEL
    saved = AgentUpdate(llm={"model": MODEL, "provider": "gemini", "thinking_level": "medium"})
    agent = {"llm_model": MODEL, "llm_config": saved.llm.model_dump()}
    assert _thinking_profile(agent) == {"thinking_level": "medium"}
    review = {"effective_confidence_floor": 0.88, "confidence_condition_suppressed": False}
    assert _run_resume_spec(agent, review, [], [])["thinking_level"] == "medium"
    assert _thinking_profile({"inputs": {"thinking_level": "medium"}}) == {}
    assert "thinking_level" not in _run_resume_spec({}, review, [], [])
    for model in ("gpt-4o", "gemini-2.5-flash", "not-a-model"):
        with pytest.raises(ValidationError):
            AgentUpdate(llm={"model": model, "thinking_level": "medium"})
    assert AgentUpdate(llm={"model": MODEL, "thinking_level": None}).llm.thinking_level is None


def test_profile_is_explicit_at_factory_and_cannot_cross_provider_or_local_mode(monkeypatch):
    from core.langgraph import llm_factory

    monkeypatch.setenv("AGENTICORG_LLM_MODE", "cloud")
    monkeypatch.setenv("AGENTICORG_MODEL_MODE", "live")
    builder = MagicMock()
    monkeypatch.setattr(llm_factory, "_build_gemini_model", builder)
    llm_factory.create_chat_model(model=MODEL, provider="gemini", tenant_id=TENANT, thinking_level="medium")
    builder.assert_called_once_with(MODEL, 0.1, 4096, TENANT, thinking_level="medium")
    with pytest.raises(ValueError, match="Gemini"):
        llm_factory.create_chat_model(model=MODEL, provider="openai_compatible", thinking_level="medium")
    monkeypatch.setenv("AGENTICORG_LLM_MODE", "local")
    with pytest.raises(ValueError, match="cloud Gemini"):
        llm_factory.create_chat_model(model=MODEL, thinking_level="medium")
    assert builder.call_count == 1


def test_replay_identity_distinguishes_profile_without_changing_default_keys():
    from langchain_core.messages import HumanMessage

    from core.model_replay import ReplayChatModel, request_key

    messages = [HumanMessage(content=TASK)]
    default = ReplayChatModel(model_name=MODEL)
    medium = ReplayChatModel(model_name=MODEL, thinking_level="medium")
    old_key = request_key(MODEL, messages, tools=[], params={"temperature": 0.1, "max_tokens": 4096, "stop": None})
    assert default._request(messages, None)[0] == old_key
    assert medium._request(messages, None)[0] != old_key


@pytest.mark.asyncio
async def test_native_replay_does_not_reuse_default_response_for_opt_in(tmp_path):
    from core.model_replay import ModelMode, cassette_scope, replay_router_call

    messages = [{"role": "user", "content": TASK}]
    live = AsyncMock(side_effect=[{"content": "default"}, {"content": "medium"}])
    with cassette_scope(tmp_path):
        for level, expected in ((None, "default"), ("medium", "medium")):
            for mode in (ModelMode.RECORD, ModelMode.REPLAY):
                result = await replay_router_call(
                    model=MODEL,
                    messages=messages,
                    temperature=0.1,
                    max_tokens=4096,
                    live_call=live,
                    mode=mode,
                    thinking_level=level,
                )
                assert result["content"] == expected
    assert live.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("level", [None, "medium"])
async def test_legacy_agent_reason_and_synthesis_keep_saved_opt_in(monkeypatch, level):
    from core.agents import base
    from core.agents.registry import AgentRegistry
    from core.llm.router import LLMResponse

    config = {
        "id": "synthetic-agent",
        "tenant_id": TENANT,
        "agent_type": "synthetic_acceptance",
        "llm_model": MODEL,
        "system_prompt_text": PROMPT,
        "llm_config": {"thinking_level": level},
    }
    agent = AgentRegistry.create_from_config(config)
    complete = AsyncMock(return_value=LLMResponse(content='{"answer":18,"confidence":1.0}', model=MODEL))
    monkeypatch.setattr(base.llm_router, "complete", complete)
    await agent._reason({"task": TASK}, [])
    await agent._synthesize_with_tools({"task": TASK}, {"answer": 18}, [{"result": "synthetic"}], [])
    assert complete.await_count == 2
    for call in complete.await_args_list:
        assert call.kwargs["tenant_id"] == TENANT
        if level is None:
            assert "thinking_level" not in call.kwargs
        else:
            assert call.kwargs["thinking_level"] == level


@pytest.mark.parametrize(
    "config,expected",
    [
        (None, {}),
        ({}, {}),
        ({"thinking_level": None}, {}),
        ({"thinking_level": "medium"}, {"thinking_level": "medium"}),
    ],
)
def test_profile_extraction_never_invents_opt_in(config, expected):
    from core.llm.gemini_config import thinking_profile

    assert thinking_profile(config) == expected
    with pytest.raises(ValueError, match="Unsupported"):
        thinking_profile({"thinking_level": "high"})


@pytest.mark.asyncio
@pytest.mark.parametrize("level", [None, "medium"])
async def test_workflow_loads_saved_profile_and_ignores_step_supplied_profile(
    monkeypatch, workflow_company_scope, level
):
    import uuid

    from core import database
    from core.agents.registry import AgentRegistry
    from core.schemas.messages import TaskResult
    from workflows import step_types

    aid = uuid.uuid4()
    row = SimpleNamespace(
        id=aid,
        tenant_id=uuid.UUID(workflow_company_scope["tenant_id"]),
        company_id=uuid.UUID(workflow_company_scope["company_id"]),
        domain="ops",
        status="shadow",
        agent_type="synthetic_acceptance",
        authorized_tools=[],
        prompt_variables={},
        hitl_condition="",
        output_schema=None,
        llm_model=MODEL,
        llm_config={"thinking_level": level},
        cost_controls={},
        system_prompt_text=PROMPT,
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = row
    session = SimpleNamespace(execute=AsyncMock(return_value=result))
    ctx = AsyncMock()
    ctx.__aenter__.return_value = session
    monkeypatch.setattr(database, "get_tenant_session", lambda *_: ctx)
    monkeypatch.setattr(step_types, "_llm_available_for_workflow", lambda: True)
    monkeypatch.setattr(step_types, "_fake_llm_allowed", lambda: False)
    create = AgentRegistry.create_from_config
    captured = []

    def build(config):
        agent = create(config)
        captured.append(agent.llm_thinking_level)

        async def execute(task):
            return TaskResult(
                message_id="synthetic-result",
                correlation_id=task.correlation_id,
                workflow_run_id=task.workflow_run_id,
                step_id=task.step_id,
                agent_id=task.target_agent.agent_id,
                status="completed",
                output={"synthetic": True},
                confidence=1,
            )

        agent.execute = execute
        return agent

    monkeypatch.setattr(AgentRegistry, "create_from_config", build)
    output = await step_types.execute_step(
        {
            "id": "synthetic",
            "type": "agent",
            "agent_id": str(aid),
            "inputs": {},
            "llm_config": {"thinking_level": "high"},
        },
        {**workflow_company_scope, "id": "synthetic-run", "context": {}},
    )
    assert output["status"] == "completed"
    assert captured == [level]
    assert session.execute.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("level", [None, "medium"])
async def test_voice_text_bridge_preserves_defaults_or_saved_opt_in_without_calling_provider(monkeypatch, level):
    from core.langgraph import runner
    from core.voice.livekit_agent import VoiceAgentWorker

    run = AsyncMock(return_value={"status": "completed", "output": {"text": "synthetic"}})
    monkeypatch.setattr(runner, "run_agent", run)
    worker = VoiceAgentWorker(
        {"agent_id": "synthetic", "tenant_id": TENANT, "llm_model": MODEL, "llm_config": {"thinking_level": level}}
    )
    assert await worker.handle_call(None, "synthetic local text turn") == "synthetic"
    kwargs = run.await_args.kwargs
    assert kwargs["tenant_id"] == TENANT
    if level is None:
        assert "thinking_level" not in kwargs and "llm_model" not in kwargs
    else:
        assert kwargs["thinking_level"] == level and kwargs["llm_model"] == MODEL
