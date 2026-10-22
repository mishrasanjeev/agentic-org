# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0

"""Regression coverage for the 2026-09-21 AgenticOrg bug sheet.

These tests pin the producer contracts behind the two reported failures:

* connector readiness and connector config resolution use the same tenant
  global fallback when an agent carries a company binding;
* natural-language agent generation forwards the authenticated tenant to the
  provider resolver, so a tenant-owned Gemini/Claude/OpenAI credential is
  actually eligible for use.

The doubles are deliberately tenant-scoped and never contain real secrets.
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

TENANT_ID = uuid.UUID("49ca24aa-c6e7-4124-91af-059023295da4")
COMPANY_ID = uuid.UUID("56e585f7-b283-4743-9889-7e264cf4515e")
OTHER_COMPANY_ID = uuid.UUID("9c7b4e3f-7f5f-4b9f-bd9f-7f9dc4e7f6b1")


def _valid_generation_response() -> MagicMock:
    response = MagicMock()
    response.content = json.dumps(
        {
            "suggestions": [
                {
                    "confidence": 0.95,
                    "agent_type": "ap_processor",
                    "domain": "finance",
                    "employee_name": "Test AP Agent",
                    "designation": "AP Processing Specialist",
                    "suggested_tools": ["fetch_bank_statement"],
                    "system_prompt": "Process invoices and flag discrepancies for review.",
                    "confidence_floor": 0.88,
                    "hitl_condition": "confidence < 0.88",
                    "specialization": "Invoice review",
                }
            ]
        }
    )
    response.model = "gemini-2.5-flash"
    response.tokens_used = 1
    return response


@pytest.mark.asyncio
async def test_agent_generation_forwards_authenticated_tenant_to_router() -> None:
    """BUG-02: generation must resolve the caller tenant's LLM credential."""
    from core.agent_generator import generate_agent_config

    llm = AsyncMock()
    llm.complete = AsyncMock(return_value=_valid_generation_response())

    result = await generate_agent_config(
        "Create an accounts-payable employee who reviews invoices",
        llm=llm,
        tenant_id=str(TENANT_ID),
    )

    assert result["suggestions"][0]["domain"] == "finance"
    call = llm.complete.await_args
    assert call.kwargs["tenant_id"] == str(TENANT_ID)


@pytest.mark.asyncio
async def test_router_resolves_tenant_owned_provider_credential() -> None:
    """BUG-02: router credential lookup must be tenant-aware and secret-safe."""
    from core.ai_providers.resolver import ResolvedCredential
    from core.llm.router import LLMRouter

    resolved = ResolvedCredential(
        secret="synthetic-tenant-secret",
        provider="gemini",
        kind="llm",
        source="tenant",
    )
    with patch(
        "core.ai_providers.resolver.get_provider_credential",
        new=AsyncMock(return_value=resolved),
    ) as resolver:
        secret = await LLMRouter()._provider_secret("gemini", str(TENANT_ID))

    assert secret == "synthetic-tenant-secret"
    resolver.assert_awaited_once_with(str(TENANT_ID), "gemini", "llm")


@pytest.mark.asyncio
async def test_router_refuses_missing_tenant_provider_credential() -> None:
    """BUG-02: a missing tenant credential fails closed without env fallback."""
    from core.ai_providers.resolver import ProviderNotConfigured
    from core.llm.router import LLMProviderConfigurationError, LLMRouter

    with patch(
        "core.ai_providers.resolver.get_provider_credential",
        new=AsyncMock(side_effect=ProviderNotConfigured("synthetic missing credential")),
    ):
        with pytest.raises(LLMProviderConfigurationError, match="synthetic missing credential"):
            await LLMRouter()._provider_secret("gemini", str(TENANT_ID))


@pytest.mark.asyncio
async def test_router_refuses_missing_environment_provider_credential(monkeypatch) -> None:
    """The legacy no-tenant path still refuses an absent environment key."""
    from core.llm import router as router_module
    from core.llm.router import LLMProviderConfigurationError, LLMRouter

    monkeypatch.setattr(router_module.external_keys, "google_gemini_api_key", "")
    with pytest.raises(LLMProviderConfigurationError, match="Gemini provider is not configured"):
        await LLMRouter()._provider_secret("gemini", None)


@pytest.mark.asyncio
async def test_router_dispatches_claude_and_openai_models() -> None:
    """Provider dispatch stays explicit for non-Gemini configured models."""
    from core.llm.router import LLMResponse, LLMRouter

    router = LLMRouter()
    claude = LLMResponse(content="claude", model="claude-test")
    openai = LLMResponse(content="openai", model="gpt-test")
    with (
        patch.object(router, "_call_claude", new=AsyncMock(return_value=claude)) as claude_call,
        patch.object(router, "_call_openai", new=AsyncMock(return_value=openai)) as openai_call,
    ):
        assert (await router._call_provider("claude-test", [], 0.2, 32, 0.0, None)).content == "claude"
        assert (await router._call_provider("gpt-test", [], 0.2, 32, 0.0, None)).content == "openai"

    claude_call.assert_awaited_once()
    openai_call.assert_awaited_once()


@pytest.mark.asyncio
async def test_router_calls_gemini_with_tenant_credential(monkeypatch) -> None:
    """BUG-02: Gemini client construction uses the resolved tenant secret."""
    from google import genai

    from core.llm import router as router_module

    response = SimpleNamespace(
        text="generated",
        candidates=["synthetic-candidate"],
        usage_metadata=SimpleNamespace(prompt_token_count=2, candidates_token_count=3),
    )
    generate = AsyncMock(return_value=response)
    client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate)))
    client_factory = MagicMock(return_value=client)

    monkeypatch.setattr(genai, "Client", client_factory)
    monkeypatch.setattr(router_module, "assert_under_gemini_cap", AsyncMock())
    monkeypatch.setattr(router_module.LLMRouter, "_provider_secret", AsyncMock(return_value="synthetic-gemini-secret"))

    result = await router_module.LLMRouter()._call_gemini(
        "gemini-2.5-flash",
        [
            {"role": "system", "content": "Be concise."},
            {"role": "user", "content": "Summarize this synthetic request."},
        ],
        0.2,
        64,
        0.0,
        tenant_id=str(TENANT_ID),
    )

    assert result.content == "generated"
    client_factory.assert_called_once_with(api_key="synthetic-gemini-secret")
    generate.assert_awaited_once()


@pytest.mark.asyncio
async def test_router_calls_openai_with_tenant_credential(monkeypatch) -> None:
    """Tenant-owned OpenAI credentials are passed to the async client."""
    import openai

    from core.llm import router as router_module

    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="generated"))],
        usage=SimpleNamespace(total_tokens=5),
    )
    response.model_dump = lambda: {"id": "synthetic-response"}
    create = AsyncMock(return_value=response)
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    client_factory = MagicMock(return_value=client)

    monkeypatch.setattr(openai, "AsyncOpenAI", client_factory)
    monkeypatch.setattr(router_module.LLMRouter, "_provider_secret", AsyncMock(return_value="synthetic-openai-secret"))

    result = await router_module.LLMRouter()._call_openai(
        "gpt-4o",
        [{"role": "user", "content": "Summarize this synthetic request."}],
        0.2,
        64,
        0.0,
        tenant_id=str(TENANT_ID),
    )

    assert result.content == "generated"
    client_factory.assert_called_once_with(api_key="synthetic-openai-secret")
    create.assert_awaited_once()


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _RlsConnectorStore:
    """ConnectorConfig rows behind the v6z6 policy: a session sees only rows of its exact company.

    The earlier fake returned the tenant-global row to a company-scoped session, which the
    real policy never does; that is how BUG-01's fallback shipped unreachable. Sessions from
    this store apply the policy, so the fallback must read through a tenant-scoped session.
    """

    def __init__(self, *rows):
        self.rows = list(rows)
        self.connector = SimpleNamespace(name="hubspot", status="active")
        self.sessions: list[uuid.UUID | None] = []

    @staticmethod
    def row(company_id):
        return SimpleNamespace(
            id=uuid.uuid4(),
            company_id=company_id,
            connector_name="hubspot",
            status="configured",
            health_status="healthy",
            auth_type="api_key",
            config={},
            credentials_encrypted={"_encrypted": "synthetic-encrypted-value"},
        )

    def session(self, company_id=None):
        self.sessions.append(company_id)
        store = self

        class _Session:
            async def execute(self, statement):
                entity = statement.column_descriptions[0]["entity"]
                name = getattr(entity, "__name__", str(entity))
                if name == "ConnectorConfig":
                    params = statement.compile().params
                    wanted = next(
                        (value for key, value in params.items() if "company_id" in key and value is not None),
                        None,
                    )
                    visible = [r for r in store.rows if r.company_id == company_id]  # the RLS policy
                    return _Result(next((r for r in visible if r.company_id == wanted), None))
                if name == "Company":
                    return _Result(COMPANY_ID)
                if name == "Connector":
                    return _Result(store.connector)
                return _Result(None)

        return _Session()

    def factory(self):
        store = self

        class _Context:
            def __init__(self, company_id=None):
                self.company_id = company_id

            async def __aenter__(self):
                return store.session(self.company_id)

            async def __aexit__(self, *_args):
                return False

        return lambda _tenant_id, company_id=None: _Context(company_id)


@pytest.mark.asyncio
async def test_dispatch_readiness_falls_back_to_tenant_global_config() -> None:
    """BUG-01: a company-bound agent can use the tenant-global binding, read in a tenant session."""
    from api.v1.agents import _assert_connectors_ready_for_activation

    store = _RlsConnectorStore(_RlsConnectorStore.row(None))
    with (
        patch("api.v1.agents.get_tenant_session", side_effect=store.factory()),
        patch("core.crypto.decrypt_for_tenant", return_value=json.dumps({"api_key": "synthetic-key"})),
    ):
        await _assert_connectors_ready_for_activation(
            store.session(COMPANY_ID),
            TENANT_ID,
            ["registry-hubspot"],
            COMPANY_ID,
        )

    # The company session could not see the global row; the tenant-scoped session did.
    assert store.sessions == [COMPANY_ID, None]


@pytest.mark.asyncio
async def test_runtime_config_resolution_uses_the_same_global_fallback() -> None:
    """BUG-01: tool config resolution must match the dispatch readiness gate."""
    from api.v1.agents import _resolve_connector_configs

    store = _RlsConnectorStore(_RlsConnectorStore.row(None))
    with (
        patch("core.database.get_tenant_session", side_effect=store.factory()),
        patch("api.v1.agents.get_tenant_session", side_effect=store.factory()),
        patch("core.crypto.decrypt_for_tenant", return_value=json.dumps({"api_key": "synthetic-key"})),
    ):
        config, names = await _resolve_connector_configs(
            tenant_id=str(TENANT_ID),
            connector_ids=["registry-hubspot"],
            company_id=COMPANY_ID,
        )

    assert config == {"api_key": "synthetic-key"}
    assert names == ["hubspot"]
    assert None in store.sessions


@pytest.mark.asyncio
async def test_exact_company_binding_wins_over_the_tenant_global_one() -> None:
    """BUG-01 step 1: with both rows present, the company's own binding is used."""
    from api.v1.agents import _resolve_connector_configs

    company_row = _RlsConnectorStore.row(COMPANY_ID)
    company_row.credentials_encrypted = {"_encrypted": "company"}
    global_row = _RlsConnectorStore.row(None)
    global_row.credentials_encrypted = {"_encrypted": "global"}
    store = _RlsConnectorStore(company_row, global_row)

    def _decrypt(value):
        return json.dumps({"api_key": f"{value}-key"})

    with (
        patch("core.database.get_tenant_session", side_effect=store.factory()),
        patch("api.v1.agents.get_tenant_session", side_effect=store.factory()),
        patch("core.crypto.decrypt_for_tenant", side_effect=_decrypt),
    ):
        config, _names = await _resolve_connector_configs(
            tenant_id=str(TENANT_ID),
            connector_ids=["registry-hubspot"],
            company_id=COMPANY_ID,
        )

    assert config == {"api_key": "company-key"}


@pytest.mark.asyncio
async def test_dispatch_readiness_never_falls_back_to_another_company() -> None:
    """BUG-01 safety boundary: a different company's config remains blocked."""
    from api.v1.agents import _assert_connectors_ready_for_activation

    store = _RlsConnectorStore(_RlsConnectorStore.row(OTHER_COMPANY_ID))
    with (
        patch("api.v1.agents.get_tenant_session", side_effect=store.factory()),
        pytest.raises(HTTPException) as exc_info,
    ):
        await _assert_connectors_ready_for_activation(
            store.session(COMPANY_ID),
            TENANT_ID,
            ["registry-hubspot"],
            COMPANY_ID,
        )

    assert "connector_not_ready_for_activation" in str(exc_info.value)
