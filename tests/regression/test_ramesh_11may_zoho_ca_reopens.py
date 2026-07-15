"""Regression pins for Ramesh 2026-05-11 CA/Zoho reopen report."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_zoho_books_india_url_overrides_invalid_db_base_url() -> None:
    from connectors.finance.zoho_books import (
        _ZOHO_IN_BASE,
        _ZOHO_IN_TOKEN_URL,
        ZohoBooksConnector,
    )

    connector = ZohoBooksConnector(
        {
            "base_url": "https://www.zohoapis.in/books/v3",
            "organization_id": "org-1",
        }
    )

    assert connector.base_url == _ZOHO_IN_BASE
    assert connector.config["base_url"] == _ZOHO_IN_BASE
    assert connector.config["region"] == "in"
    assert connector.config["token_url"] == _ZOHO_IN_TOKEN_URL


def test_provider_registry_zoho_india_url_matches_runtime_books_host() -> None:
    from core.connectors.provider_registry import get_provider

    provider = get_provider("zoho_books")
    assert provider is not None
    assert provider.urls_for({"region": "in"})["api_base_url"] == (
        "https://books.zoho.in/api/v3"
    )


@pytest.mark.asyncio
async def test_zoho_reconcile_transaction_uses_match_endpoint() -> None:
    from connectors.finance.zoho_books import ZohoBooksConnector

    connector = ZohoBooksConnector({"region": "in", "organization_id": "org-1"})
    calls: dict[str, object] = {}

    async def fake_put(
        path: str,
        data: dict | None = None,
        params: dict | None = None,
    ) -> dict:
        calls["path"] = path
        calls["data"] = data
        calls["params"] = params
        return {"bank_transaction": {"transaction_id": "bank-1", "status": "matched"}}

    connector._put = fake_put  # type: ignore[method-assign]

    result = await connector.reconcile_transaction(
        transaction_id="bank-1",
        match_id="book-1",
        match_type="deposit",
    )

    assert calls["path"] == "/banktransactions/bank-1/match"
    assert calls["data"] == {
        "transactions_to_be_matched": [
            {"transaction_id": "book-1", "transaction_type": "deposit"}
        ]
    }
    assert calls["params"] == {"organization_id": "org-1"}
    assert result["status"] == "matched"


def test_ca_pack_preserves_connector_qualified_tools_and_runtime_aliases() -> None:
    from core.agents.packs.installer import _normalize_tool_names, _tool_connector_map
    from core.langgraph.tool_adapter import build_tools_for_agent

    raw = ["tally:get_trial_balance", "zoho_books:get_trial_balance"]
    normalized = _normalize_tool_names(raw)
    assert normalized == raw

    mapping = _tool_connector_map(raw)
    assert mapping["tally:get_trial_balance"] == "tally"
    assert mapping["zoho_books:get_trial_balance"] == "zoho_books"
    assert mapping["get_trial_balance"] == "tally"

    tools = build_tools_for_agent(raw, {}, ["tally", "zoho_books"])
    names = {tool.name for tool in tools}
    assert "tally__get_trial_balance" in names
    assert "zoho_books__get_trial_balance" in names


def test_ca_workflows_are_scheduled_not_all_manual() -> None:
    from core.agents.packs.ca import CA_PACK
    from core.agents.packs.installer import _build_workflow_definition

    agent_specs = [
        {
            "id": "00000000-0000-0000-0000-000000000001",
            "agent_type": "gst_filing_agent",
            "authorized_tools": ["zoho_books:list_invoices"],
            "system_prompt_text": "prompt",
            "llm_model": "gpt-4o",
            "domain": "finance",
            "description": "",
        }
    ]
    definition = _build_workflow_definition(
        "ca-firm",
        CA_PACK,
        "bank_recon_daily",
        agent_specs,
    )
    assert definition["trigger_type"] == "schedule"
    assert definition["trigger_config"]["cron"] == "0 6 * * *"
    assert any(step["type"] == "human_in_loop" for step in definition["steps"])


def test_production_floor_blocks_legacy_twenty_percent_promotions() -> None:
    from api.v1.agents import (
        _MIN_PRODUCTION_SHADOW_ACCURACY,
        _active_agent_below_production_floor,
        _is_tool_authorized,
    )
    from core.models.agent import Agent

    agent = Agent(
        tenant_id="00000000-0000-0000-0000-000000000001",
        name="Acme GST",
        agent_type="gst_filing_agent",
        domain="finance",
        system_prompt_ref="industry-pack://ca-firm/gst_filing_agent",
        hitl_condition="always_before_filing",
        authorized_tools=["zoho_books:list_invoices"],
        status="active",
        version="1.0.0",
        shadow_accuracy_floor=Decimal("0.200"),
        shadow_accuracy_current=Decimal("0.240"),
    )

    assert _MIN_PRODUCTION_SHADOW_ACCURACY == Decimal("0.600")
    assert _active_agent_below_production_floor(agent) is True
    assert _is_tool_authorized(["zoho_books:list_invoices"], "list_invoices") is True


def test_connector_health_and_detail_use_live_runtime_truth() -> None:
    src = (ROOT / "api" / "v1" / "connectors.py").read_text(encoding="utf-8")
    assert "probe = await test_connector(conn_id, tenant_id, company_id)" in src
    assert "_connector_tool_functions(conn)" in src
    assert "_normalise_connector_base_url(conn.name, conn.base_url)" in src


def test_pack_agent_idempotency_migration_guards_company_agent_type() -> None:
    migration = (
        ROOT
        / "migrations"
        / "versions"
        / "v4_9_10_pack_agent_idempotency.py"
    ).read_text(encoding="utf-8")
    assert "uq_agents_industry_pack_company_type" in migration
    assert "PARTITION BY tenant_id, company_id, agent_type" in migration
    assert "config->'pack_install'->>'source' = 'industry_pack'" in migration
    # Alembic discipline: must chain off the prior head.
    assert 'revision = "v4910_pack_agent_idempotency"' in migration
    assert 'down_revision = "v499_pool_config_noop"' in migration


def test_zoho_token_placeholder_is_not_google_for_zoho_books() -> None:
    src = (ROOT / "ui" / "src" / "pages" / "ConnectorDetail.tsx").read_text(
        encoding="utf-8"
    )
    assert "https://accounts.zoho.in/oauth/v2/token" in src
    assert 'connector?.name === "zoho_books"' in src
    assert "https://accounts.google.com/o/oauth2/token" not in src
