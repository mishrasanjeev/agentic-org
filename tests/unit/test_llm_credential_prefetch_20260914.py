# SPDX-License-Identifier: Apache-2.0
"""Production incident 2026-09-14: every agent run failed after release 784cbd03.

The agent graph started passing ``tenant_id`` to ``create_chat_model`` (so a
pinned provider and tenant BYO keys apply). The factory resolved credentials
through ``get_provider_credential_sync``, which from inside the running event
loop runs the async resolver on a fresh loop in a worker thread. The shared
asyncpg pool is bound to the main loop, so the tenant lookup failed
("tenant_ai_credential_decrypt_failed") and the run ended with
"Gemini provider is not configured".

The runner now resolves the credential with ``await`` on its own loop before
building the graph; the factory consumes that prefetched value.
"""

from __future__ import annotations

import inspect
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from core.langgraph import llm_factory


class TestPrefetchedCredentialIsUsed:
    @pytest.mark.asyncio
    async def test_prefetched_credential_skips_the_sync_bridge(self) -> None:
        tenant = str(uuid.uuid4())
        credential = SimpleNamespace(secret="tenant-gemini-key", provider_config={})
        with (
            patch("core.ai_providers.resolver.get_provider_credential", AsyncMock(return_value=credential)),
            patch(
                "core.ai_providers.resolver.get_provider_credential_sync",
                side_effect=AssertionError("sync bridge must not run inside the event loop"),
            ),
        ):
            token = await llm_factory.prefetch_llm_credential("gemini-2.5-flash", None, tenant)
            try:
                assert llm_factory._resolve_cloud_api_key("gemini", tenant) == "tenant-gemini-key"
            finally:
                llm_factory.reset_prefetched_llm_credential(token)

    @pytest.mark.asyncio
    async def test_not_configured_is_prefetched_as_none(self) -> None:
        from core.ai_providers.resolver import ProviderNotConfigured

        tenant = str(uuid.uuid4())
        with (
            patch(
                "core.ai_providers.resolver.get_provider_credential",
                AsyncMock(side_effect=ProviderNotConfigured("none")),
            ),
            patch(
                "core.ai_providers.resolver.get_provider_credential_sync",
                side_effect=AssertionError("sync bridge must not run"),
            ),
        ):
            token = await llm_factory.prefetch_llm_credential("claude-sonnet-4-5-20250929", None, tenant)
            try:
                assert llm_factory._resolve_cloud_api_key("anthropic", tenant) == ""
            finally:
                llm_factory.reset_prefetched_llm_credential(token)

    @pytest.mark.asyncio
    async def test_reset_clears_the_prefetch(self) -> None:
        tenant = str(uuid.uuid4())
        credential = SimpleNamespace(secret="k", provider_config={})
        with patch("core.ai_providers.resolver.get_provider_credential", AsyncMock(return_value=credential)):
            token = await llm_factory.prefetch_llm_credential("gpt-4o", None, tenant)
        assert (tenant, "openai") in (llm_factory._PREFETCHED_LLM_CREDENTIALS.get() or {})
        llm_factory.reset_prefetched_llm_credential(token)
        assert (tenant, "openai") not in (llm_factory._PREFETCHED_LLM_CREDENTIALS.get() or {})

    @pytest.mark.asyncio
    async def test_no_tenant_or_local_model_does_not_prefetch(self) -> None:
        getter = AsyncMock()
        with patch("core.ai_providers.resolver.get_provider_credential", getter):
            assert await llm_factory.prefetch_llm_credential("gemini-2.5-flash", None, None) is None
        getter.assert_not_awaited()

    def test_provider_inference_matches_factory_dispatch(self) -> None:
        assert llm_factory._infer_cloud_provider("gemini-2.5-flash", None) == "gemini"
        assert llm_factory._infer_cloud_provider("claude-sonnet-4-5-20250929", None) == "anthropic"
        assert llm_factory._infer_cloud_provider("gpt-4o", None) == "openai"
        assert llm_factory._infer_cloud_provider("o1-mini", "openai") == "openai"
        assert llm_factory._infer_cloud_provider("anything", "openai_compatible") == "openai_compatible"


class TestRunnerPrefetchesBeforeBuildingTheGraph:
    @pytest.mark.parametrize("name", ["run_agent", "resume_agent"])
    def test_prefetch_precedes_build(self, name: str) -> None:
        from core.langgraph import runner

        src = inspect.getsource(getattr(runner, name))
        assert "await prefetch_llm_credential(" in src, name
        assert src.index("await prefetch_llm_credential(") < src.index("build_agent_graph("), name
        assert "reset_prefetched_llm_credential(credential_token)" in src, name


class TestLazyGraphModelUsesBuildTimeSnapshot:
    @pytest.mark.asyncio
    async def test_model_created_after_reset_still_uses_prefetch(self) -> None:
        """The graph creates the model lazily inside the reason node, after the
        runner reset its prefetch token (the first fix attempt missed this)."""
        from core.langgraph import agent_graph

        tenant = str(uuid.uuid4())
        credential = SimpleNamespace(secret="tenant-gemini-key", provider_config={})
        seen: list[str] = []

        def _fake_create_chat_model(model, tenant_id=None, provider=None, **_kw):
            seen.append(llm_factory._resolve_cloud_api_key("gemini", tenant_id))
            return SimpleNamespace(bind_tools=lambda tools: "bound")

        with (
            patch("core.ai_providers.resolver.get_provider_credential", AsyncMock(return_value=credential)),
            patch(
                "core.ai_providers.resolver.get_provider_credential_sync",
                side_effect=AssertionError("sync bridge must not run inside the event loop"),
            ),
            patch.object(agent_graph, "create_chat_model", _fake_create_chat_model),
        ):
            token = await llm_factory.prefetch_llm_credential("gemini-2.5-flash", None, tenant)
            try:
                snapshot = llm_factory.snapshot_prefetched_llm_credentials()
            finally:
                llm_factory.reset_prefetched_llm_credential(token)
            # Build-time snapshot, used after the runner's token is gone.
            with llm_factory.use_prefetched_llm_credentials(snapshot):
                agent_graph.create_chat_model(model="gemini-2.5-flash", tenant_id=tenant)
        assert seen == ["tenant-gemini-key"]

    def test_agent_graph_wraps_lazy_model_creation(self) -> None:
        from core.langgraph import agent_graph

        src = inspect.getsource(agent_graph.build_agent_graph)
        assert "prefetched_credentials = snapshot_prefetched_llm_credentials()" in src
        assert src.index("with use_prefetched_llm_credentials(prefetched_credentials):") < src.index(
            "llm = create_chat_model("
        )

