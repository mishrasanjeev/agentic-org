"""Regression pins for Uday 2026-05-30 CA/CMO connector report."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]


def _mock_client_response(json_data=None):
    resp = MagicMock()
    resp.json.return_value = json_data or {}
    resp.raise_for_status = MagicMock()
    resp.status_code = 200
    resp.content = b"{}"
    return resp


def _async_response(json_data=None):
    return AsyncMock(return_value=_mock_client_response(json_data))


def test_zoho_india_uses_zohoapis_host_in_runtime_and_registration() -> None:
    from api.v1.connectors import _normalise_connector_base_url
    from connectors.finance.zoho_books import ZohoBooksConnector

    assert (
        _normalise_connector_base_url("zoho_books", "https://www.zohoapis.in/books/v3")
        == "https://www.zohoapis.in/books/v3"
    )
    connector = ZohoBooksConnector(
        {"base_url": "https://www.zohoapis.in/books/v3", "organization_id": "org-1"}
    )
    assert connector.base_url == "https://www.zohoapis.in/books/v3"
    assert connector.config["base_url"] == "https://www.zohoapis.in/books/v3"


@pytest.mark.asyncio
async def test_zoho_vendor_item_bill_workflows_are_real_registered_tools() -> None:
    from connectors.finance.zoho_books import ZohoBooksConnector

    connector = ZohoBooksConnector({"access_token": "fake", "organization_id": "org-1"})
    assert {"list_vendors", "create_vendor", "create_item", "create_bill"} <= set(
        connector._tool_registry
    )
    connector._client = MagicMock()

    connector._client.get = _async_response({"contacts": [{"contact_id": "v1"}]})
    vendors = await connector.list_vendors(page=1)
    assert vendors["vendors"][0]["contact_id"] == "v1"
    assert connector._client.get.call_args.args[0] == "/contacts"
    assert connector._client.get.call_args.kwargs["params"]["contact_type"] == "vendor"

    connector._client.post = _async_response({"contact": {"contact_id": "v2"}})
    vendor = await connector.create_vendor(
        vendor_name="New Bharat Accountant Firm",
        email="accounts@example.com",
    )
    assert vendor["contact_id"] == "v2"
    assert connector._client.post.call_args.args[0] == "/contacts"
    assert connector._client.post.call_args.kwargs["json"]["contact_type"] == "vendor"

    connector._client.post = _async_response({"item": {"item_id": "i1"}})
    item = await connector.create_item(name="Audit Services", rate=5000, vendor_id="v2")
    assert item["item_id"] == "i1"
    assert connector._client.post.call_args.args[0] == "/items"

    connector._client.post = _async_response({"bill": {"bill_id": "b1"}})
    bill = await connector.create_bill(
        vendor_id="v2",
        line_items=[{"item_id": "i1", "rate": 5000, "quantity": 1}],
    )
    assert bill["bill_id"] == "b1"
    assert connector._client.post.call_args.args[0] == "/bills"


@pytest.mark.asyncio
async def test_hubspot_create_contact_accepts_native_properties_payload() -> None:
    from connectors.marketing.hubspot import HubspotConnector

    connector = HubspotConnector({"access_token": "fake"})
    connector._client = MagicMock()
    connector._client.post = _async_response({"id": "101"})

    result = await connector.create_contact(
        properties={
            "email": "rajeev@example.com",
            "firstname": "Rajeev",
            "lastname": "Sharma",
            "phone": "7827443304",
            "company": "New Bharat Accountant Firm",
        }
    )

    assert result["id"] == "101"
    assert connector._client.post.call_args.args[0] == "/crm/v3/objects/contacts"
    payload = connector._client.post.call_args.kwargs["json"]
    assert payload["properties"]["email"] == "rajeev@example.com"
    assert payload["properties"]["company"] == "New Bharat Accountant Firm"


@pytest.mark.asyncio
async def test_hubspot_crud_association_and_owner_tools_use_real_paths() -> None:
    from connectors.marketing.hubspot import HubspotConnector

    connector = HubspotConnector({"access_token": "fake"})
    connector._client = MagicMock()

    connector._client.patch = _async_response({"id": "101"})
    await connector.assign_contact_owner(contact_id="101", owner_id="77")
    assert connector._client.patch.call_args.args[0] == "/crm/v3/objects/contacts/101"
    assert connector._client.patch.call_args.kwargs["json"]["properties"]["hubspot_owner_id"] == "77"

    connector._client.request = _async_response({})
    assoc = await connector.associate_contact_to_company(contact_id="101", company_id="201")
    assert connector._client.request.call_args.args == (
        "PUT",
        "/crm/v4/objects/contacts/101/associations/default/companies/201",
    )
    assert assoc["status"] == "associated"
    assert assoc["contact_id"] == "101"
    assert assoc["company_id"] == "201"

    connector._client.get = _async_response({"results": [{"id": "77"}]})
    owners = await connector.list_owners(limit=10)
    assert owners["owners"][0]["id"] == "77"
    assert connector._client.get.call_args.args[0] == "/crm/v3/owners"

    connector._client.request = _async_response({})
    await connector.delete_contact(contact_id="101")
    assert connector._client.request.call_args.args == (
        "DELETE",
        "/crm/v3/objects/contacts/101",
    )


def test_connector_tool_adapter_preserves_provider_error_reason() -> None:
    from core.langgraph.tool_adapter import _connector_exception_payload

    request = httpx.Request("POST", "https://api.hubapi.com/crm/v3/objects/contacts")
    response = httpx.Response(
        401,
        json={"message": "Invalid access token"},
        request=request,
    )
    exc = httpx.HTTPStatusError("401 Unauthorized", request=request, response=response)

    payload = _connector_exception_payload(
        exc,
        connector_name="hubspot",
        tool_name="create_contact",
    )

    assert payload["error"] == "invalid_access_token"
    assert payload["http_status"] == 401
    assert "Invalid access token" in payload["message"]
    assert payload["connector"] == "hubspot"
    assert payload["tool"] == "create_contact"


def test_agent_defaults_expose_new_ca_and_cmo_tools_without_drift() -> None:
    from api.v1.agents import _AGENT_TYPE_DEFAULT_TOOLS as API_DEFAULTS
    from core.agent_generator import _AGENT_TYPE_DEFAULT_TOOLS as GENERATOR_DEFAULTS
    from core.langgraph.agents.ap_processor import AP_PROCESSOR_TOOLS
    from core.langgraph.agents.crm_intelligence import DEFAULT_TOOLS as CRM_TOOLS

    for tool in ("list_vendors", "create_vendor", "create_item", "create_bill"):
        assert tool in API_DEFAULTS["ap_processor"]
        assert tool in GENERATOR_DEFAULTS["ap_processor"]
        assert tool in AP_PROCESSOR_TOOLS

    for tool in (
        "update_contact",
        "delete_contact",
        "assign_contact_owner",
        "associate_contact_to_company",
        "list_owners",
    ):
        assert tool in API_DEFAULTS["crm_intelligence"]
        assert tool in GENERATOR_DEFAULTS["crm_intelligence"]
        assert tool in CRM_TOOLS


def test_ca_cmo_prompt_guide_documents_exact_shapes() -> None:
    guide = (ROOT / "docs" / "CA_CMO_CONNECTOR_USER_GUIDE_2026-05-30.md").read_text(
        encoding="utf-8"
    )
    assert "https://www.zohoapis.in/books/v3" in guide
    assert '"properties":{"email"' in guide
    assert "Create bill on Zoho Books" in guide
    assert "invalid_payload" in guide
