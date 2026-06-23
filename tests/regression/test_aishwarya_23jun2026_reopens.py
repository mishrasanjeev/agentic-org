"""Aishwarya 23 June 2026 workbook regression pins."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

NOW = datetime(2026, 6, 23, 12, 0, tzinfo=UTC)


def _connector_config(name: str, config: dict) -> SimpleNamespace:
    return SimpleNamespace(
        connector_name=name,
        display_name=None,
        auth_type="oauth2",
        credentials_encrypted={"_encrypted": "enc"},
        config=config,
        status="active",
        last_health_check=NOW,
        health_status="healthy",
        last_sync_at=NOW - timedelta(hours=1),
        sync_error=None,
    )


def _hubspot_scopes() -> list[str]:
    return [
        "crm.objects.contacts.read",
        "crm.objects.deals.read",
        "crm.objects.companies.read",
        "crm.objects.owners.read",
        "automation",
    ]


def _hubspot_tools() -> list[object]:
    return [
        "hubspot.list_contacts",
        {"name": "hubspot.search_contacts"},
        {"function": {"name": "hubspot:list_deals"}},
        "hubspot.validate_crm_access",
    ]


def test_hubspot_contract_uses_configured_scopes_and_registered_tools() -> None:
    from core.marketing.connector_contracts import build_marketing_connector_contracts
    from core.marketing.connector_setup import build_marketing_connector_setup

    config = _connector_config(
        "hubspot",
        {
            "account_id": "sandbox-portal",
            "health": {"scopes": _hubspot_scopes()},
            "registered_tools": _hubspot_tools(),
            "marketing_connector_contract": {
                "status": "healthy",
                "registered_tools": _hubspot_tools(),
                "retry_budget": {"max_attempts": 1, "idempotency_supported": True},
            },
        },
    )

    setup = build_marketing_connector_setup([config], now=NOW)
    setup_row = next(row for row in setup if row["key"] == "hubspot")
    contracts = build_marketing_connector_contracts(setup, [config], now=NOW)
    contract = next(row for row in contracts if row["connector_key"] == "hubspot")

    assert setup_row["health_status"] == "healthy"
    assert setup_row["missing_scopes"] == []
    assert contract["read_status"] == "ready"
    assert contract["write_status"] == "ready"
    assert contract["missing_read_scopes"] == []
    assert contract["missing_write_scopes"] == []
    assert contract["granted_scopes"] == _hubspot_scopes()


def test_legacy_hubspot_read_contract_accepts_configured_scope_payloads() -> None:
    from core.marketing.connector_contracts import evaluate_hubspot_crm_read_contract

    contract = evaluate_hubspot_crm_read_contract(
        connector_status="active",
        health_status="healthy",
        tool_functions=_hubspot_tools(),
        config={"configured_scopes": _hubspot_scopes()},
    )

    assert contract.status == "ready"
    assert contract.missing_scopes == ()
    assert contract.missing_tools == ()


class _StatsResponse:
    status_code = 200
    headers: dict[str, str] = {}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> list[dict]:
        return [{"date": "2026-06-18", "stats": [{"metrics": {"delivered": 8}}]}]


class _StatsClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def get(self, path: str, params: dict):
        self.calls.append({"path": path, "params": dict(params)})
        return _StatsResponse()


@pytest.mark.asyncio
async def test_sendgrid_get_stats_accepts_json_tool_input_wrapper() -> None:
    from connectors.comms.sendgrid import SendgridConnector

    connector = SendgridConnector({"api_key": "SG.test"})
    client = _StatsClient()
    connector._client = client

    result = await connector.get_stats(
        tool_input=(
            '{"parameters":{"start_date":"2026-06-18",'
            '"end_date":"2026-06-19","aggregated_by":"day"}}'
        )
    )

    assert result["stats"][0]["metrics"]["delivered"] == 8
    assert client.calls == [
        {
            "path": "/stats",
            "params": {
                "start_date": "2026-06-18",
                "end_date": "2026-06-19",
                "aggregated_by": "day",
            },
        }
    ]


@pytest.mark.asyncio
async def test_google_ads_campaign_performance_validates_dates_before_vendor_call() -> None:
    from connectors.marketing.google_ads import GoogleAdsConnector

    connector = GoogleAdsConnector({"customer_id": "123-456-7890"})
    calls: list[str] = []

    async def fake_gaql_search(query: str) -> list[dict]:
        calls.append(query)
        return []

    connector._gaql_search = fake_gaql_search

    result = await connector.get_campaign_performance(
        prompt="Use get_campaign_performance for this healthy Google Ads connector"
    )

    assert result["error"] == "validation_failed"
    assert result["missing_parameters"] == ["start_date", "end_date"]
    assert calls == []


@pytest.mark.asyncio
async def test_google_ads_campaign_performance_accepts_prompt_dates_and_campaign_id() -> None:
    from connectors.marketing.google_ads import GoogleAdsConnector

    connector = GoogleAdsConnector({"customer_id": "123-456-7890"})
    captured: dict[str, str] = {}

    async def fake_gaql_search(query: str) -> list[dict]:
        captured["query"] = query
        return [{"campaign": {"id": "9876543210", "name": "Brand"}}]

    connector._gaql_search = fake_gaql_search

    result = await connector.get_campaign_performance(
        prompt=(
            "Use get_campaign_performance with start_date=2026-06-18 "
            "end_date=2026-06-19 campaign_id=9876543210"
        )
    )

    assert result["date_range"] == {"start": "2026-06-18", "end": "2026-06-19"}
    assert "segments.date BETWEEN '2026-06-18' AND '2026-06-19'" in captured["query"]
    assert "campaign.id = 9876543210" in captured["query"]
    assert "segments.date" in captured["query"]


@pytest.mark.asyncio
async def test_retrieve_hubspot_contacts_workflow_alias_executes_connector_tool(monkeypatch) -> None:
    from core.langgraph import tool_adapter
    from workflows.step_types import execute_step

    captured: dict[str, object] = {}

    async def fake_execute_connector_tool(connector, tool, params, config):
        captured.update(
            {
                "connector": connector,
                "tool": tool,
                "params": params,
                "config": config,
            }
        )
        return {"contacts": [{"id": "1", "email": "qa@example.com"}]}

    monkeypatch.setattr(tool_adapter, "_execute_connector_tool", fake_execute_connector_tool)

    result = await execute_step(
        {
            "id": "retrieve_hubspot_contacts",
            "type": "agent",
            "agent": "crm_intelligence",
            "action": "process",
            "inputs": {"limit": 5},
            "connector_config": {"access_token": "pat-test"},
        },
        {"tenant_id": "22222222-2222-2222-2222-222222222222"},
    )

    assert result["status"] == "completed"
    assert captured == {
        "connector": "hubspot",
        "tool": "list_contacts",
        "params": {"limit": 5},
        "config": {"access_token": "pat-test"},
    }


class _FakeScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalar_one(self):
        return self.value


def _install_fake_tenant_sessions(monkeypatch, values: list[object]):
    from api.v1 import approvals

    class FakeSession:
        async def execute(self, _query):
            if not values:
                raise AssertionError("Unexpected query")
            return _FakeScalarResult(values.pop(0))

    @asynccontextmanager
    async def fake_get_tenant_session(_tenant_id):
        yield FakeSession()

    monkeypatch.setattr(approvals, "get_tenant_session", fake_get_tenant_session)


@pytest.mark.asyncio
async def test_approval_resume_missing_engine_context_marks_run_failed(monkeypatch) -> None:
    from api.v1.approvals import _resume_workflow_bg

    tenant_id = uuid4()
    workflow_run_id = uuid4()
    run = SimpleNamespace(
        id=workflow_run_id,
        workflow_def_id=uuid4(),
        context={},
        status="waiting_hitl",
        error=None,
        completed_at=None,
    )
    workflow_def = SimpleNamespace(definition={"steps": [{"id": "approval"}]})
    _install_fake_tenant_sessions(monkeypatch, [run, workflow_def, run])

    await _resume_workflow_bg(tenant_id, workflow_run_id, {"decision": "approve"})

    assert run.status == "failed"
    assert run.error["code"] == "workflow_resume_context_missing"
    assert run.completed_at is not None


@pytest.mark.asyncio
async def test_approval_resume_missing_engine_state_marks_run_failed(monkeypatch) -> None:
    from api.v1.approvals import _resume_workflow_bg
    from workflows import engine as engine_module
    from workflows import state_store as state_store_module

    tenant_id = uuid4()
    workflow_run_id = uuid4()
    run = SimpleNamespace(
        id=workflow_run_id,
        workflow_def_id=uuid4(),
        context={"_engine_run_id": "wfr_resume_missing_state"},
        status="waiting_hitl",
        error=None,
        completed_at=None,
    )
    workflow_def = SimpleNamespace(definition={"steps": [{"id": "approval"}]})
    _install_fake_tenant_sessions(monkeypatch, [run, workflow_def, run])

    class FakeStateStore:
        async def init(self):
            return None

        async def load(self, _run_id):
            return None

        async def close(self):
            return None

    class FakeEngine:
        def __init__(self, _state_store):
            pass

        async def resume_from_hitl(self, run_id, decision):
            assert run_id == "wfr_resume_missing_state"
            assert decision == {"decision": "approve"}
            return {"status": "completed"}

    monkeypatch.setattr(state_store_module, "WorkflowStateStore", FakeStateStore)
    monkeypatch.setattr(engine_module, "WorkflowEngine", FakeEngine)

    await _resume_workflow_bg(tenant_id, workflow_run_id, {"decision": "approve"})

    assert run.status == "failed"
    assert run.error["code"] == "workflow_resume_state_missing"
    assert run.completed_at is not None


def test_approval_decide_passes_engine_run_id_hint_variants() -> None:
    from pathlib import Path

    src = Path("api/v1/approvals.py").read_text(encoding="utf-8")
    assert 'ctx.get("_engine_run_id")' in src
    assert 'ctx.get("engine_run_id")' in src
    assert 'ctx.get("workflow_engine_run_id")' in src
