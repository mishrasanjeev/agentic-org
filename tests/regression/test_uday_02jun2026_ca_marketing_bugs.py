"""Regression pins for Uday 2026-06-02 CA/Marketing bug list."""

from __future__ import annotations

import inspect
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


def _mock_client_response(json_data=None):
    resp = MagicMock()
    resp.json.return_value = json_data or {}
    resp.raise_for_status = MagicMock()
    resp.status_code = 200
    resp.content = b"{}"
    return resp


@pytest.mark.asyncio
async def test_zoho_get_bill_by_id_resolves_visible_bill_number_first() -> None:
    from connectors.finance.zoho_books import ZohoBooksConnector

    connector = ZohoBooksConnector({"access_token": "fake", "organization_id": "org-1"})
    connector._client = MagicMock()
    connector._client.get = AsyncMock(
        side_effect=[
            _mock_client_response(
                {
                    "bills": [
                        {
                            "bill_id": "46000000001001",
                            "bill_number": "BILL-2025-789",
                        }
                    ]
                }
            ),
            _mock_client_response(
                {
                    "bill": {
                        "bill_id": "46000000001001",
                        "bill_number": "BILL-2025-789",
                    }
                }
            ),
        ]
    )

    result = await connector.get_bill_by_id(bill_id="BILL-2025-789")

    assert result["bill_id"] == "46000000001001"
    assert result["resolved_from_reference"] == "BILL-2025-789"
    search_call, detail_call = connector._client.get.call_args_list
    assert search_call.args[0] == "/bills"
    assert search_call.kwargs["params"]["search_text"] == "BILL-2025-789"
    assert detail_call.args[0] == "/bills/46000000001001"


@pytest.mark.asyncio
async def test_zoho_get_bill_by_id_keeps_internal_id_direct() -> None:
    from connectors.finance.zoho_books import ZohoBooksConnector

    connector = ZohoBooksConnector({"access_token": "fake", "organization_id": "org-1"})
    connector._client = MagicMock()
    connector._client.get = AsyncMock(
        return_value=_mock_client_response({"bill": {"bill_id": "b1"}})
    )

    result = await connector.get_bill_by_id(bill_id="b1")

    assert result["bill_id"] == "b1"
    connector._client.get.assert_called_once()
    call = connector._client.get.call_args
    assert call.args[0] == "/bills/b1"


@pytest.mark.asyncio
async def test_zoho_get_invoice_by_id_resolves_visible_invoice_number_first() -> None:
    from connectors.finance.zoho_books import ZohoBooksConnector

    connector = ZohoBooksConnector({"access_token": "fake", "organization_id": "org-1"})
    connector._client = MagicMock()
    connector._client.get = AsyncMock(
        side_effect=[
            _mock_client_response(
                {
                    "invoices": [
                        {
                            "invoice_id": "46000000002001",
                            "invoice_number": "INV-445",
                        }
                    ]
                }
            ),
            _mock_client_response(
                {
                    "invoice": {
                        "invoice_id": "46000000002001",
                        "invoice_number": "INV-445",
                    }
                }
            ),
        ]
    )

    result = await connector.get_invoice_by_id(invoice_id="INV-445")

    assert result["invoice_id"] == "46000000002001"
    assert result["resolved_from_reference"] == "INV-445"
    search_call, detail_call = connector._client.get.call_args_list
    assert search_call.args[0] == "/invoices"
    assert search_call.kwargs["params"]["search_text"] == "INV-445"
    assert detail_call.args[0] == "/invoices/46000000002001"


def test_zoho_reference_resolution_tools_are_exposed_to_agents() -> None:
    from api.v1.agents import _AGENT_TYPE_DEFAULT_TOOLS as API_DEFAULTS
    from connectors.finance.zoho_books import ZohoBooksConnector
    from core.agent_generator import _AGENT_TYPE_DEFAULT_TOOLS as GENERATOR_DEFAULTS
    from core.langgraph.agents.ap_processor import AP_PROCESSOR_TOOLS
    from core.langgraph.agents.ar_collections import DEFAULT_TOOLS as AR_TOOLS
    from core.langgraph.runner import REFERENCE_RESOLUTION_GUIDANCE

    zoho_tools = set(ZohoBooksConnector({"access_token": "fake"})._tool_registry)
    for tool in ("list_bills", "list_vendor_bills", "search_bills", "get_bill_by_id"):
        assert tool in zoho_tools
        assert tool in API_DEFAULTS["ap_processor"]
        assert tool in GENERATOR_DEFAULTS["ap_processor"]
        assert tool in AP_PROCESSOR_TOOLS

    for tool in ("search_invoices", "get_invoice_by_id"):
        assert tool in zoho_tools
        assert tool in API_DEFAULTS["ar_collections"]
        assert tool in GENERATOR_DEFAULTS["ar_collections"]
        assert tool in AR_TOOLS

    assert "BILL-2025-789 ka details dikhao" in REFERENCE_RESOLUTION_GUIDANCE
    assert "use the matching search_* or list_* tool first" in REFERENCE_RESOLUTION_GUIDANCE


def test_agent_creation_paths_preserve_selected_company_scope() -> None:
    from api.v1 import agents as agents_module
    from api.v1 import sop as sop_module

    import_src = inspect.getsource(agents_module.import_agents_csv)
    assert "company_id: str | None = None" in import_src
    assert "company_uuid = _parse_company_id(company_id)" in import_src
    assert "company_id=company_uuid" in import_src
    assert "Agent.company_id == company_uuid" in import_src

    generate_src = inspect.getsource(agents_module.generate_agent)
    assert 'company_uuid = _parse_company_id(body.get("company_id")) if deploy else None' in generate_src
    assert "company_id=company_uuid" in generate_src

    sop_src = inspect.getsource(sop_module.deploy_sop_agent)
    assert 'company_id = body.get("company_id") or config.get("company_id")' in sop_src
    assert "company_id=company_id" in sop_src

    repo_root = Path(__file__).resolve().parents[2]
    agents_page = (repo_root / "ui/src/pages/Agents.tsx").read_text(encoding="utf-8")
    agent_create = (repo_root / "ui/src/pages/AgentCreate.tsx").read_text(encoding="utf-8")
    sop_upload = (repo_root / "ui/src/pages/SOPUpload.tsx").read_text(encoding="utf-8")
    api_ts = (repo_root / "ui/src/lib/api.ts").read_text(encoding="utf-8")

    assert "params.company_id = selectedCompanyId" in agents_page
    assert "agentsApi.importCsv(importFile, params)" in agents_page
    assert 'api.post("/agents/generate"' in agent_create
    assert "company_id: selectedCompanyId || undefined" in agent_create
    assert "company_id: selectedCompanyId || undefined" in sop_upload
    assert 'api.post("/agents/import-csv", fd, { params })' in api_ts


class _GSTNAuthClient:
    def __init__(self):
        self.posts: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def post(self, url: str, *, json: dict, headers: dict):
        self.posts.append({"url": url, "json": json, "headers": headers})
        return _mock_client_response({"access_token": "gstn-token"})


@pytest.mark.asyncio
async def test_gstn_auth_uses_api_key_alias_with_api_secret(monkeypatch) -> None:
    import connectors.finance.gstn as gstn_module
    from connectors.finance.gstn import GstnConnector

    recording = _GSTNAuthClient()
    monkeypatch.setattr(gstn_module.httpx, "AsyncClient", lambda **_kwargs: recording)
    connector = GstnConnector(
        {
            "api_key": "asp-id",
            "api_secret": "app-secret",
            "gstin": "27ABCDE1234F1Z5",
        }
    )

    await connector._authenticate()

    assert recording.posts[0]["url"].endswith("/authenticate?grant_type=token")
    assert recording.posts[0]["json"] == {}
    headers = recording.posts[0]["headers"]
    assert headers["gspappid"] == "asp-id"
    assert headers["gspappsecret"] == "app-secret"
    assert "aspid" not in headers
    assert "clientid" not in headers
    assert connector._auth_headers["Authorization"] == "Bearer gstn-token"
    assert "auth-token" not in connector._auth_headers


@pytest.mark.asyncio
async def test_gstn_auth_uses_client_credentials_as_gsp_headers(monkeypatch) -> None:
    import connectors.finance.gstn as gstn_module
    from connectors.finance.gstn import GstnConnector

    recording = _GSTNAuthClient()
    monkeypatch.setattr(gstn_module.httpx, "AsyncClient", lambda **_kwargs: recording)
    connector = GstnConnector(
        {
            "client_id": "gstn-client",
            "client_secret": "gstn-secret",
            "gstin": "27ABCDE1234F1Z5",
        }
    )

    await connector._authenticate()

    headers = recording.posts[0]["headers"]
    assert headers["gspappid"] == "gstn-client"
    assert headers["gspappsecret"] == "gstn-secret"
    assert "state-cd" not in headers
    assert "client-secret" not in headers
    assert "aspid" not in headers
    assert connector._auth_headers["Authorization"] == "Bearer gstn-token"
    assert connector._auth_headers["gstin"] == "27ABCDE1234F1Z5"
    assert "auth-token" not in connector._auth_headers
