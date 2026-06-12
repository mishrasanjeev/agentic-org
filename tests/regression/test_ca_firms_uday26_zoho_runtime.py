"""Regression pins for Uday CA Firms 2026-05-26 Zoho/runtime reopens."""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[2]


def _mock_response(json_data: dict, *, status_code: int = 200) -> MagicMock:
    response = MagicMock()
    response.json.return_value = json_data
    response.raise_for_status = MagicMock()
    response.status_code = status_code
    response.text = str(json_data)
    return response


def test_provider_registry_uses_real_zoho_india_books_host() -> None:
    from core.connectors.provider_registry import get_provider

    provider = get_provider("zoho_books")
    assert provider is not None
    assert provider.urls_for({"region": "in"})["api_base_url"] == (
        "https://books.zoho.in/api/v3"
    )


@pytest.mark.asyncio
async def test_zoho_http_200_error_envelope_raises_not_empty_success() -> None:
    from connectors.finance.zoho_books import ZohoBooksConnector

    connector = ZohoBooksConnector(
        {"access_token": "fake", "organization_id": "org123"}
    )
    connector._client = MagicMock()
    connector._client.get = AsyncMock(
        return_value=_mock_response({"code": 57, "message": "Invalid Authorization"})
    )

    with pytest.raises(RuntimeError, match="57: Invalid Authorization"):
        await connector.get_organization()


@pytest.mark.asyncio
async def test_zoho_get_organization_returns_statutory_company_fields() -> None:
    from connectors.finance.zoho_books import ZohoBooksConnector

    connector = ZohoBooksConnector(
        {"access_token": "fake", "organization_id": "org123"}
    )
    connector._client = MagicMock()
    connector._client.get = AsyncMock(
        return_value=_mock_response(
            {
                "organizations": [
                    {"organization_id": "other", "name": "Wrong Org"},
                    {
                        "organization_id": "org123",
                        "name": "Acme CA Firm",
                        "gst_no": "27ABCDE1234F1Z5",
                        "pan": "ABCDE1234F",
                        "tan": "MUMA12345B",
                        "currency_code": "INR",
                        "fiscal_year_start_month": 4,
                        "address": {
                            "address_line1": "1 MG Road",
                            "city": "Mumbai",
                            "state": "MH",
                            "zip": "400001",
                            "country": "India",
                        },
                    },
                ]
            }
        )
    )

    result = await connector.get_organization()

    assert len(result["organizations"]) == 1
    org = result["organizations"][0]
    assert org["organization_id"] == "org123"
    assert org["name"] == "Acme CA Firm"
    assert org["gstin"] == "27ABCDE1234F1Z5"
    assert org["pan_number"] == "ABCDE1234F"
    assert org["tan_number"] == "MUMA12345B"
    assert org["address"]["city"] == "Mumbai"


@pytest.mark.asyncio
async def test_zoho_india_gst_report_raises_and_never_falls_back_to_invoices() -> None:
    from connectors.finance.zoho_books import ZohoBooksConnector

    connector = ZohoBooksConnector(
        {"region": "in", "access_token": "fake", "organization_id": "org123"}
    )
    connector.list_invoices = AsyncMock(
        side_effect=AssertionError("invoice fallback must not run")
    )

    with pytest.raises(RuntimeError, match="GSTN connector"):
        await connector.generate_gst_report(from_date="2026-04-01", to_date="2026-04-30")
    connector.list_invoices.assert_not_called()


def test_zoho_http_status_errors_include_method_url_and_zoho_code() -> None:
    from connectors.finance.zoho_books import ZohoBooksConnector

    request = httpx.Request("GET", "https://books.zoho.in/api/v3/organizations")
    response = httpx.Response(
        401,
        request=request,
        json={"code": 57, "message": "Invalid Authorization"},
    )
    exc = httpx.HTTPStatusError("Unauthorized", request=request, response=response)

    message = str(ZohoBooksConnector._runtime_error_from_http_status(exc))

    assert "HTTP 401" in message
    assert "GET https://books.zoho.in/api/v3/organizations" in message
    assert "Zoho code: 57 - Invalid Authorization" in message


@pytest.mark.asyncio
async def test_dispatch_gate_returns_actionable_connector_not_ready_error() -> None:
    from api.v1.agents import _assert_connectors_ready_for_dispatch

    class _Result:
        def scalar_one_or_none(self):
            return None

    class _EmptyTenantSession:
        async def execute(self, _stmt):
            return _Result()

    with pytest.raises(HTTPException) as exc:
        await _assert_connectors_ready_for_dispatch(
            _EmptyTenantSession(),
            uuid.UUID("49ca24aa-c6e7-4124-91af-059023295da4"),
            ["registry-zoho_books"],
        )

    assert exc.value.status_code == 409
    assert exc.value.detail["error"] == "connector_not_ready_for_dispatch"
    assert exc.value.detail["connectors"] == [
        {"connector": "zoho_books", "reason": "missing_connector_config"}
    ]


def test_dispatch_preflight_is_wired_on_all_langgraph_entrypoints() -> None:
    files = [
        "api/v1/agents.py",
        "api/v1/chat.py",
        "api/v1/a2a.py",
        "api/v1/mcp.py",
    ]
    for rel in files:
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert "_assert_connectors_ready_for_dispatch" in src, rel


def test_tds_prompt_pins_no_reask_no_vendor_discovery_and_empty_result_guard() -> None:
    src = (
        ROOT
        / "core"
        / "agents"
        / "packs"
        / "ca"
        / "prompts"
        / "tds_compliance.prompt.txt"
    ).read_text(encoding="utf-8")

    assert "do not call list_vendors when vendor_id is present" in src
    assert "use that id as-is" in src
    assert "If any tool returns empty data" in src


def test_ca_tds_agent_authorizes_create_tds_entry_and_chart_of_accounts() -> None:
    from core.agents.packs.ca import CA_PACK

    tds_agent = next(
        agent for agent in CA_PACK["agents"] if agent["name"] == "TDS Compliance Agent"
    )
    tools = set(tds_agent["tools"])
    assert "zoho_books:create_tds_entry" in tools
    assert "zoho_books:list_chartofaccounts" in tools


def test_runtime_alert_rules_cover_reopen_patterns() -> None:
    from observability.alerting import PRD_THRESHOLDS, Severity

    rules = {rule.name: rule for rule in PRD_THRESHOLDS}
    assert rules["shadow_accuracy_low"].threshold == 0.70
    assert rules["shadow_accuracy_low"].consecutive_violations == 3
    assert rules["shadow_accuracy_low"].severity == Severity.CRITICAL
    assert rules["tool_success_rate_zero"].metric_name == "agenticorg_tool_success_rate"
    assert rules["tool_success_rate_zero"].consecutive_violations == 5
    assert rules["agent_confidence_all_low"].metric_name == (
        "agenticorg_agent_confidence_avg"
    )
    assert rules["agent_confidence_all_low"].consecutive_violations == 5
