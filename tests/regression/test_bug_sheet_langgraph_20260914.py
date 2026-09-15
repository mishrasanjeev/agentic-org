"""Regression tests for the 2026-09-14 bug sheet — LangGraph runtime items.

Each test replays the failing input from the sheet (failing before the
fix, passing after):

#14  Connector-qualified tool refs: ``gmail.send_email`` was dropped from
     ``authorized_tools`` (index had no ``.`` alias); ``gmail:send_email``
     and ``gmail__send_email`` registered under different LLM-facing
     names; a tool call spelled ``gmail.send_email`` hit ToolNode's
     exact-match "is not a valid tool".
#15  Tool adapter parameter contract: the LLM saw one opaque ``kwargs``
     object instead of the handler's real parameters, and top-level keys
     were silently dropped / unknown keys TypeError'd in the handler.
#36  HITL-triggered runs reported ``llm_tokens_used: 0`` even though the
     ``reason`` node had already spent tokens; ``resume_agent`` returned
     no ``performance`` block. On langgraph 1.x ``ainvoke`` returns
     ``__interrupt__`` instead of raising, so the run was also reported
     as ``completed`` with no ``thread_id``.
#40  Azure OpenAI health probe used ``Authorization: Bearer`` against
     ``/v1/models``; Azure needs the ``api-key`` header and
     ``/openai/models?api-version=``.
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.errors import GraphInterrupt
from langgraph.types import Interrupt

from auth.run_grants import NO_RUN_GRANT_FOR_TESTS
from core.langgraph import runner
from core.langgraph.agent_graph import (
    _rewrite_tool_call_names,
    _tool_call_alias_map,
    build_agent_graph,
)
from core.langgraph.tool_adapter import (
    _build_tool_index,
    _parse_authorized_tool_ref,
    _split_connector_tool_ref,
    build_tools_for_agent,
)


def _schema_of(tool: Any) -> dict[str, Any]:
    schema = tool.tool_call_schema
    return schema if isinstance(schema, dict) else schema.model_json_schema()


class _ScriptedChatModel(BaseChatModel):
    """Minimal chat model that replays scripted AI messages in order."""

    script: list[AIMessage]

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Any, **_kwargs: Any) -> _ScriptedChatModel:  # type: ignore[override]
        return self

    def _generate(self, messages: Any, stop: Any = None, run_manager: Any = None, **_kwargs: Any) -> ChatResult:
        message = self.script.pop(0)
        return ChatResult(generations=[ChatGeneration(message=message)])


# ---------------------------------------------------------------------------
# #14 — connector-qualified tool names
# ---------------------------------------------------------------------------
class TestSheet14ConnectorQualifiedToolNames:
    def test_split_connector_tool_ref_accepts_dot_spelling(self) -> None:
        assert _split_connector_tool_ref("gmail.send_email") == ("gmail", "send_email")
        assert _split_connector_tool_ref("gmail:send_email") == ("gmail", "send_email")
        # Grantex scopes stay untouched, bare names stay bare.
        assert _split_connector_tool_ref("tool:gmail:execute:send_email") == (None, "tool:gmail:execute:send_email")
        assert _split_connector_tool_ref("send_email") == (None, "send_email")

    def test_every_spelling_normalises_to_the_same_pair(self) -> None:
        expected = ("gmail", "send_email")
        for ref in ("gmail.send_email", "gmail:send_email", "gmail__send_email", "tool:gmail:execute:send_email"):
            assert _parse_authorized_tool_ref(ref) == expected, ref
        assert _parse_authorized_tool_ref("send_email") == (None, "send_email")
        assert _parse_authorized_tool_ref("") is None

    def test_tool_index_carries_dot_alias(self) -> None:
        index = _build_tool_index(connector_names=["gmail"], include_connector_aliases=True)
        assert index["gmail.send_email"] == index["gmail:send_email"] == index["gmail__send_email"]
        assert index["gmail.send_email"][0] == "gmail"

    def test_dot_ref_is_no_longer_dropped_from_authorized_tools(self) -> None:
        """Tester's input: authorized_tools=['gmail.send_email'] → zero tools before the fix."""
        tools = build_tools_for_agent(["gmail.send_email"], {}, ["gmail"])
        assert [t.name for t in tools] == ["gmail__send_email"]

    def test_all_spellings_register_one_deterministic_tool(self) -> None:
        tools = build_tools_for_agent(
            ["gmail.send_email", "gmail:send_email", "gmail__send_email"],
            {},
            ["gmail"],
        )
        assert [t.name for t in tools] == ["gmail__send_email"]
        assert tools[0].metadata == {"connector": "gmail", "tool": "send_email"}

    def test_dunder_spelling_no_longer_registers_as_bare_name(self) -> None:
        # Before: ``gmail__send_email`` registered as bare ``send_email`` while
        # ``gmail:send_email`` registered as ``gmail__send_email``.
        tools = build_tools_for_agent(["gmail__send_email"], {}, ["gmail"])
        assert [t.name for t in tools] == ["gmail__send_email"]

    def test_bare_and_qualified_refs_to_same_tool_register_once(self) -> None:
        # Before: ``["send_email", "gmail:send_email"]`` registered two tools
        # (``send_email`` and ``gmail__send_email``) for the same handler.
        tools = build_tools_for_agent(["send_email", "gmail:send_email"], {}, ["gmail"])
        assert [t.name for t in tools] == ["gmail__send_email"]

    def test_bare_names_keep_their_historical_registration(self) -> None:
        tools = build_tools_for_agent(["fetch_bank_statement"])
        assert [t.name for t in tools] == ["fetch_bank_statement"]

    def test_unresolved_ref_is_logged_not_silently_skipped(self) -> None:
        with patch("core.langgraph.tool_adapter.logger") as fake_logger:
            tools = build_tools_for_agent(["gmail.no_such_tool", "nonexistent_tool"], {}, ["gmail"])
        assert tools == []
        events = [call.args[0] for call in fake_logger.warning.call_args_list]
        assert events.count("authorized_tool_unresolved") == 2

    def test_alias_map_covers_every_spelling_and_drops_ambiguous_bare_names(self) -> None:
        tools = build_tools_for_agent(
            ["gmail:send_email", "sendgrid:send_email", "slack:send_message", "list_invoices"],
            {},
            ["gmail", "sendgrid", "slack", "zoho_books"],
        )
        aliases = _tool_call_alias_map(tools)
        assert aliases["gmail.send_email"] == "gmail__send_email"
        assert aliases["gmail:send_email"] == "gmail__send_email"
        # Bare ``send_email`` is claimed by two connectors → not aliased.
        assert "send_email" not in aliases
        # Unambiguous bare alias is kept.
        assert aliases["send_message"] == "slack__send_message"
        # A bare-registered tool gains its qualified spellings.
        assert aliases["zoho_books.list_invoices"] == "list_invoices"
        assert aliases["zoho_books__list_invoices"] == "list_invoices"

    def test_rewrite_tool_call_names_updates_tool_calls_and_tool_use_blocks(self) -> None:
        message = AIMessage(
            content=[{"type": "tool_use", "id": "c1", "name": "gmail.send_email", "input": {}}],
            tool_calls=[{"name": "gmail.send_email", "args": {"to": "x"}, "id": "c1"}],
        )
        rewritten = _rewrite_tool_call_names(message, {"gmail.send_email": "gmail__send_email"})
        assert rewritten is not message
        assert rewritten.tool_calls[0]["name"] == "gmail__send_email"
        assert rewritten.content[0]["name"] == "gmail__send_email"
        # Untouched message is returned as the same object.
        plain = AIMessage(content="", tool_calls=[{"name": "gmail__send_email", "args": {}, "id": "c2"}])
        assert _rewrite_tool_call_names(plain, {"gmail.send_email": "gmail__send_email"}) is plain

    @pytest.mark.asyncio
    async def test_graph_dispatches_dot_spelled_tool_call_and_fails_closed_on_unknown(self) -> None:
        """End-to-end replay: model emits ``gmail.send_email`` for a tool
        registered as ``gmail__send_email``. Before the fix ToolNode
        answered "gmail.send_email is not a valid tool"."""
        script = [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "gmail.send_email", "args": {"to": "a@b.c", "subject": "hi", "body": "x"}, "id": "c1"},
                    {"name": "totally_unknown_tool", "args": {}, "id": "c2"},
                ],
            ),
            AIMessage(content='{"status": "completed", "confidence": 0.95}'),
        ]
        executed = AsyncMock(return_value={"id": "msg-1", "status": "sent"})
        with (
            patch("core.langgraph.agent_graph.create_chat_model", return_value=_ScriptedChatModel(script=script)),
            patch("core.langgraph.tool_adapter._execute_connector_tool", new=executed),
        ):
            graph = build_agent_graph(
                system_prompt="test",
                authorized_tools=["gmail:send_email"],
                connector_config={},
                connector_names=["gmail"],
                confidence_floor=0.5,
                run_grant=NO_RUN_GRANT_FOR_TESTS,
            )
            compiled = graph.compile()
            result = await compiled.ainvoke(
                {
                    "messages": [SystemMessage(content="test"), HumanMessage(content="send it")],
                    "agent_id": "a",
                    "agent_type": "t",
                    "domain": "ops",
                    "tenant_id": "",
                    "grant_token": "",
                    "confidence": 0.0,
                    "status": "running",
                    "output": {},
                    "reasoning_trace": [],
                    "tool_calls_log": [],
                    "hitl_trigger": "",
                    "error": "",
                }
            )

        tool_messages = {m.tool_call_id: m for m in result["messages"] if isinstance(m, ToolMessage)}
        assert tool_messages["c1"].name == "gmail__send_email"
        assert "not a valid tool" not in str(tool_messages["c1"].content)
        assert "msg-1" in str(tool_messages["c1"].content)
        executed.assert_awaited_once()
        # Handler defaults (cc/bcc="") are filled from the derived schema (#15).
        sent = {"to": "a@b.c", "subject": "hi", "body": "x", "cc": "", "bcc": ""}
        assert executed.await_args.args[:3] == ("gmail", "send_email", sent)
        # Unknown names still fail closed with ToolNode's error.
        assert "not a valid tool" in str(tool_messages["c2"].content)
        # The AI message in state now carries the registered name (same id).
        ai_with_calls = next(m for m in result["messages"] if isinstance(m, AIMessage) and m.tool_calls)
        assert ai_with_calls.tool_calls[0]["name"] == "gmail__send_email"


# ---------------------------------------------------------------------------
# #15 — tool adapter parameter contract
# ---------------------------------------------------------------------------
class TestSheet15ToolAdapterParameterContract:
    def test_gmail_send_email_schema_exposes_handler_parameters(self) -> None:
        (tool,) = build_tools_for_agent(["gmail:send_email"], {}, ["gmail"])
        schema = _schema_of(tool)
        assert set(schema["properties"]) == {"to", "subject", "body", "cc", "bcc"}
        assert "kwargs" not in schema["properties"]
        assert schema["properties"]["to"]["type"] == "string"

    @pytest.mark.parametrize(
        ("ref", "connector", "doc_fragment"),
        [
            ("slack:send_message", "slack", "channel"),
            ("zoho_books:list_invoices", "zoho_books", "customer_id"),
            ("stripe:create_payment_intent", "stripe", "amount"),
        ],
    )
    def test_var_kw_handlers_get_open_schema_and_full_docstring(
        self, ref: str, connector: str, doc_fragment: str
    ) -> None:
        (tool,) = build_tools_for_agent([ref], {}, [connector])
        schema = _schema_of(tool)
        assert schema["type"] == "object"
        assert schema["properties"] == {}
        assert schema["additionalProperties"] is True
        assert "kwargs" not in schema["properties"]
        # The docstring that lists the params now reaches the model.
        assert doc_fragment in tool.description

    @pytest.mark.asyncio
    async def test_top_level_keys_reach_var_kw_handler(self) -> None:
        """Before: ``{"channel": ..}`` against the inferred ``kwargs`` schema
        was silently reduced to ``{}`` before the connector ran."""
        (tool,) = build_tools_for_agent(["slack:send_message"], {}, ["slack"])
        executed = AsyncMock(return_value={"ok": True})
        with patch("core.langgraph.tool_adapter._execute_connector_tool", new=executed):
            await tool.ainvoke({"channel": "C1", "text": "hello"})
        assert executed.await_args.args[:3] == ("slack", "send_message", {"channel": "C1", "text": "hello"})

    @pytest.mark.asyncio
    async def test_legacy_kwargs_wrapper_still_unwrapped(self) -> None:
        (tool,) = build_tools_for_agent(["slack:send_message"], {}, ["slack"])
        executed = AsyncMock(return_value={"ok": True})
        with patch("core.langgraph.tool_adapter._execute_connector_tool", new=executed):
            await tool.ainvoke({"kwargs": {"channel": "C1", "text": "hello"}})
        assert executed.await_args.args[2] == {"channel": "C1", "text": "hello"}

    @pytest.mark.asyncio
    async def test_legacy_kwargs_wrapper_unwrapped_for_named_param_handler(self) -> None:
        # With a real schema LangChain adds handler defaults beside the
        # wrapper; the wrapped values must still reach the handler.
        (tool,) = build_tools_for_agent(["gmail:send_email"], {}, ["gmail"])
        executed = AsyncMock(return_value={"id": "m1"})
        with patch("core.langgraph.tool_adapter._execute_connector_tool", new=executed):
            await tool.ainvoke({"kwargs": {"to": "a@b.c", "subject": "s"}})
        params = executed.await_args.args[2]
        assert "kwargs" not in params
        assert params["to"] == "a@b.c"
        assert params["subject"] == "s"

    @pytest.mark.asyncio
    async def test_unknown_keys_dropped_and_logged_instead_of_type_error(self) -> None:
        """A handler without ``**kwargs`` used to TypeError inside
        ``BaseConnector.execute_tool`` (``handler(**params)``) on any key the
        model invented. No registered handler is strict today (gmail's
        ``send_email`` takes ``**_extra``), so the strict shape is injected."""

        async def strict_send_email(to: str = "", subject: str = "", body: str = "") -> dict[str, Any]:
            """Send an email."""
            return {"id": "m1", "to": to, "subject": subject, "body": body}

        with patch(
            "core.langgraph.tool_adapter._connector_tool_handlers",
            return_value={"send_email": strict_send_email},
        ):
            (tool,) = build_tools_for_agent(["gmail:send_email"], {}, ["gmail"])

        async def call_handler(_cn: str, _tn: str, params: dict[str, Any], *_a: Any, **_kw: Any) -> dict[str, Any]:
            return await strict_send_email(**params)  # same call shape as BaseConnector.execute_tool

        executed = AsyncMock(side_effect=call_handler)
        with (
            patch("core.langgraph.tool_adapter._execute_connector_tool", new=executed),
            patch("core.langgraph.tool_adapter.logger") as fake_logger,
        ):
            result = await tool.ainvoke({"to": "a@b.c", "subject": "s", "priority": "high"})
        params = executed.await_args.args[2]
        assert "priority" not in params
        assert params["to"] == "a@b.c"
        assert params["subject"] == "s"
        assert result["id"] == "m1"
        dropped = [c for c in fake_logger.warning.call_args_list if c.args[0] == "tool_args_unknown_keys_dropped"]
        assert len(dropped) == 1
        assert dropped[0].kwargs["dropped"] == ["priority"]
        # Values never hit the log line — only key names.
        assert "high" not in repr(dropped[0].kwargs)

    def test_unknown_annotation_becomes_any_without_losing_other_fields(self) -> None:
        class Opaque:
            pass

        async def handler(to: str = "", client: Opaque | None = None, limit: int = 10) -> dict[str, Any]:
            """Handler with one annotation that has no JSON schema."""
            return {}

        with patch("core.langgraph.tool_adapter._connector_tool_handlers", return_value={"send_email": handler}):
            (tool,) = build_tools_for_agent(["gmail:send_email"], {}, ["gmail"])
        props = _schema_of(tool)["properties"]
        assert set(props) == {"to", "client", "limit"}
        assert props["to"]["type"] == "string"
        assert props["limit"]["type"] == "integer"
        assert "type" not in props["client"]  # Any


# ---------------------------------------------------------------------------
# #36 — HITL-triggered runs report real usage
# ---------------------------------------------------------------------------
def _ai(tokens_in: int, tokens_out: int) -> AIMessage:
    return AIMessage(
        content="reasoning",
        usage_metadata={"input_tokens": tokens_in, "output_tokens": tokens_out, "total_tokens": tokens_in + tokens_out},
    )


def _run_agent_with(fake_compiled: MagicMock) -> dict[str, Any]:
    fake_graph = MagicMock()
    fake_graph.compile = MagicMock(return_value=fake_compiled)
    with (
        patch.object(runner, "build_agent_graph", return_value=fake_graph),
        patch.object(runner, "generate_explanation", new=AsyncMock(return_value={})),
    ):
        return asyncio.run(
            runner.run_agent(
                agent_id="a-id",
                agent_type="t",
                domain="ops",
                tenant_id="00000000-0000-0000-0000-000000000000",
                system_prompt="hi",
                authorized_tools=["list_invoices"],
                task_input={"action": "process", "inputs": {}, "context": {}},
                thread_id="thread-36",
            )
        )


class TestSheet36HitlUsage:
    def test_sum_usage_counts_langchain_and_gemini_metadata(self) -> None:
        gemini = AIMessage(content="x", response_metadata={"usage_metadata": {"total_token_count": 50}})
        tokens, cost = runner._sum_usage([HumanMessage(content="h"), _ai(100, 20), gemini])
        assert tokens == 170
        assert cost == round(170 * 0.000375 / 1000, 6)
        assert runner._sum_usage([]) == (0, 0)

    def test_interrupt_returned_in_state_reports_hitl_with_real_tokens(self) -> None:
        """langgraph 1.x replay: ``ainvoke`` returns ``__interrupt__``. Before
        the fix the run came back ``completed`` with tokens summed but no
        hitl_trigger / thread_id; the dead ``except GraphInterrupt`` branch
        would have reported 0 tokens."""
        fake_state = {
            "messages": [HumanMessage(content="task"), _ai(300, 40)],
            "status": "completed",  # evaluate() set this before the gate paused
            "output": {"data": "ok"},
            "confidence": 0.4,
            "reasoning_trace": ["Calling LLM"],
            "tool_calls_log": [],
            "hitl_trigger": "",
            "error": "",
            "__interrupt__": [
                Interrupt(value={"type": "hitl_approval", "hitl_trigger": "confidence 0.400 < floor 0.88"}),
            ],
        }
        fake_compiled = MagicMock()
        fake_compiled.ainvoke = AsyncMock(return_value=fake_state)

        result = _run_agent_with(fake_compiled)

        assert result["status"] == "hitl_triggered"
        assert result["hitl_trigger"] == "confidence 0.400 < floor 0.88"
        assert result["thread_id"] == "tenant:00000000-0000-0000-0000-000000000000:thread-36"
        assert result["performance"]["llm_tokens_used"] == 340
        assert result["performance"]["llm_cost_usd"] == round(340 * 0.000375 / 1000, 6)
        assert result["performance"]["total_latency_ms"] >= 0
        assert result["tool_calls"] == result["tool_calls_log"] == []

    def test_graph_interrupt_exception_path_reports_real_tokens(self) -> None:
        fake_compiled = MagicMock()
        fake_compiled.ainvoke = AsyncMock(side_effect=GraphInterrupt([Interrupt(value={"hitl_trigger": "manual"})]))
        fake_compiled.get_state = MagicMock(
            return_value=SimpleNamespace(
                values={
                    "messages": [HumanMessage(content="task"), _ai(120, 30)],
                    "status": "completed",
                    "output": {"x": 1},
                    "confidence": 0.3,
                    "reasoning_trace": [],
                    "tool_calls_log": [],
                }
            )
        )

        result = _run_agent_with(fake_compiled)

        assert result["status"] == "hitl_triggered"
        assert result["hitl_trigger"] == "manual"
        assert result["thread_id"] == "tenant:00000000-0000-0000-0000-000000000000:thread-36"
        assert result["performance"]["llm_tokens_used"] == 150
        assert result["performance"]["llm_cost_usd"] == round(150 * 0.000375 / 1000, 6)

    def test_completed_run_still_reports_usage_and_no_thread_id(self) -> None:
        fake_state = {
            "messages": [HumanMessage(content="task"), _ai(10, 5)],
            "status": "completed",
            "output": {},
            "confidence": 0.9,
            "reasoning_trace": [],
            "tool_calls_log": [],
            "hitl_trigger": "",
            "error": "",
        }
        fake_compiled = MagicMock()
        fake_compiled.ainvoke = AsyncMock(return_value=fake_state)
        result = _run_agent_with(fake_compiled)
        assert result["status"] == "completed"
        assert result["performance"]["llm_tokens_used"] == 15
        assert "thread_id" not in result

    def test_resume_agent_returns_performance_block(self) -> None:
        fake_state = {
            "messages": [HumanMessage(content="task"), _ai(200, 50), _ai(80, 20)],
            "status": "completed",
            "output": {"approved": True},
            "confidence": 0.9,
            "reasoning_trace": ["HITL decision: approve"],
        }
        fake_compiled = MagicMock()
        fake_compiled.ainvoke = AsyncMock(return_value=fake_state)
        fake_graph = MagicMock()
        fake_graph.compile = MagicMock(return_value=fake_compiled)
        with patch.object(runner, "build_agent_graph", return_value=fake_graph):
            result = asyncio.run(
                runner.resume_agent(
                    agent_id="a-id",
                    thread_id="thread-36",
                    decision={"action": "approve"},
                    system_prompt="hi",
                    authorized_tools=[],
                )
            )
        assert result["status"] == "completed"
        assert result["performance"]["llm_tokens_used"] == 350
        assert result["performance"]["llm_cost_usd"] == round(350 * 0.000375 / 1000, 6)
        assert result["performance"]["total_latency_ms"] >= 0


# ---------------------------------------------------------------------------
# #40 — Azure OpenAI health probe
# ---------------------------------------------------------------------------
class TestSheet40AzureOpenAIProbe:
    @staticmethod
    def _install_transport(monkeypatch: pytest.MonkeyPatch, status_code: int) -> list[httpx.Request]:
        from core.ai_providers import health

        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(status_code, json={"data": []})

        monkeypatch.setattr(health, "build_pinned_async_transport", lambda **_kw: httpx.MockTransport(handler))
        # Keep the public-HTTPS check but skip live DNS in the unit test.
        monkeypatch.setattr(health, "validate_public_url", lambda url, **_kw: SimpleNamespace(url=url))
        return seen

    @pytest.mark.asyncio
    async def test_probe_uses_api_key_header_and_azure_models_endpoint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from core.ai_providers import health

        seen = self._install_transport(monkeypatch, 200)
        result = await health._probe_azure_openai("az-secret", "https://myres.openai.azure.com/", "2024-10-21")

        assert result["ok"] is True
        assert result["raw_status"] == 200
        (request,) = seen
        assert str(request.url) == "https://myres.openai.azure.com/openai/models?api-version=2024-10-21"
        assert request.headers["api-key"] == "az-secret"
        assert "authorization" not in request.headers

    @pytest.mark.asyncio
    async def test_probe_defaults_api_version_and_maps_401_to_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from core.ai_providers import health

        seen = self._install_transport(monkeypatch, 401)
        result = await health._probe_azure_openai("bad", "https://myres.openai.azure.com", None)

        assert result["ok"] is False
        assert result["raw_status"] == 401
        assert "401" in result["error"]
        assert str(seen[0].url).endswith(f"?api-version={health._AZURE_OPENAI_DEFAULT_API_VERSION}")

    def test_malformed_api_version_fails_closed(self) -> None:
        from core.ai_providers import health

        with pytest.raises(ValueError):
            health._azure_openai_models_url("https://myres.openai.azure.com", "latest; DROP")

    @pytest.mark.asyncio
    async def test_probe_provider_routes_azure_to_azure_probe(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from core.ai_providers import health

        seen = self._install_transport(monkeypatch, 200)

        async def resolved(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
            return SimpleNamespace(
                secret="az-secret",
                provider_config={"base_url": "https://myres.openai.azure.com", "api_version": "2025-01-01-preview"},
            )

        monkeypatch.setattr(health, "get_provider_credential", resolved)
        openai_probe = AsyncMock()
        monkeypatch.setattr(health, "_probe_openai", openai_probe)

        result = await health.probe_provider(uuid.uuid4(), "azure_openai", "llm")

        assert result["ok"] is True
        openai_probe.assert_not_awaited()
        assert str(seen[0].url) == "https://myres.openai.azure.com/openai/models?api-version=2025-01-01-preview"
        assert seen[0].headers["api-key"] == "az-secret"

    @pytest.mark.asyncio
    async def test_probe_provider_azure_malformed_version_returns_value_error_only(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from core.ai_providers import health

        self._install_transport(monkeypatch, 200)

        async def resolved(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
            return SimpleNamespace(
                secret="az-secret",
                provider_config={"base_url": "https://myres.openai.azure.com", "api_version": "bogus"},
            )

        monkeypatch.setattr(health, "get_provider_credential", resolved)
        result = await health.probe_provider(uuid.uuid4(), "azure_openai", "llm")
        assert result == {"ok": False, "error": "ValueError"}


# ---------------------------------------------------------------------------
# #31 / #38 runtime wiring — pinned LLM provider reaches the factory
# ---------------------------------------------------------------------------
def _minimal_state() -> dict[str, Any]:
    return {
        "messages": [SystemMessage(content="test"), HumanMessage(content="hi")],
        "agent_id": "a",
        "agent_type": "t",
        "domain": "ops",
        "tenant_id": "",
        "grant_token": "",
        "confidence": 0.0,
        "status": "running",
        "output": {},
        "reasoning_trace": [],
        "tool_calls_log": [],
        "hitl_trigger": "",
        "error": "",
    }


class TestPinnedLlmProviderRuntimeWiring:
    @pytest.mark.asyncio
    async def test_openai_o1_mini_reaches_openai_builder_through_graph(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Before: ``build_agent_graph`` called ``create_chat_model(model=...)``
        without the provider, so ``o1-mini`` (no ``gpt`` substring) fell back
        to the Gemini default even on an agent pinned to OpenAI."""
        from core.langgraph import llm_factory

        monkeypatch.setenv("AGENTICORG_LLM_MODE", "cloud")
        tenant = "00000000-0000-0000-0000-0000000000aa"
        model = _ScriptedChatModel(script=[AIMessage(content='{"status": "completed", "confidence": 0.95}')])
        openai_builder = MagicMock(return_value=model)
        gemini_builder = MagicMock(side_effect=AssertionError("must not fall back to Gemini"))
        monkeypatch.setattr(llm_factory, "_build_openai_model", openai_builder)
        monkeypatch.setattr(llm_factory, "_build_gemini_model", gemini_builder)

        graph = build_agent_graph(
            system_prompt="test",
            authorized_tools=[],
            llm_model="o1-mini",
            confidence_floor=0.5,
            tenant_id=tenant,
            llm_provider="openai",
            run_grant=NO_RUN_GRANT_FOR_TESTS,
        )
        result = await graph.compile().ainvoke(_minimal_state())

        assert result["status"] == "completed"
        openai_builder.assert_called_once()
        assert openai_builder.call_args.args[0] == "o1-mini"
        assert openai_builder.call_args.args[3] == tenant
        gemini_builder.assert_not_called()

    def test_run_agent_and_resume_agent_thread_llm_provider_into_graph(self) -> None:
        fake_state = {
            "messages": [HumanMessage(content="task"), _ai(1, 1)],
            "status": "completed",
            "output": {},
            "confidence": 0.9,
            "reasoning_trace": [],
            "tool_calls_log": [],
            "hitl_trigger": "",
            "error": "",
        }
        fake_compiled = MagicMock()
        fake_compiled.ainvoke = AsyncMock(return_value=fake_state)
        fake_graph = MagicMock()
        fake_graph.compile = MagicMock(return_value=fake_compiled)
        with (
            patch.object(runner, "build_agent_graph", return_value=fake_graph) as build,
            patch.object(runner, "generate_explanation", new=AsyncMock(return_value={})),
        ):
            asyncio.run(
                runner.run_agent(
                    agent_id="a-id",
                    agent_type="t",
                    domain="ops",
                    tenant_id="00000000-0000-0000-0000-000000000000",
                    system_prompt="hi",
                    authorized_tools=[],
                    task_input={"action": "process", "inputs": {}, "context": {}},
                    llm_model="o1-mini",
                    llm_provider="openai",
                )
            )
            assert build.call_args.kwargs["llm_provider"] == "openai"
            assert build.call_args.kwargs["tenant_id"] == "00000000-0000-0000-0000-000000000000"

            asyncio.run(
                runner.resume_agent(
                    agent_id="a-id",
                    thread_id="t-1",
                    decision={"action": "approve"},
                    system_prompt="hi",
                    authorized_tools=[],
                    llm_model="o1-mini",
                    llm_provider="openai",
                )
            )
            assert build.call_args.kwargs["llm_provider"] == "openai"

    def test_legacy_callers_without_llm_provider_default_to_none(self) -> None:
        import inspect as _inspect

        for fn in (runner.run_agent, runner.resume_agent, build_agent_graph):
            assert _inspect.signature(fn).parameters["llm_provider"].default is None
