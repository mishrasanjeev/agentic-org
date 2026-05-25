"""Regression coverage for Uday CA Firms report from 25-May-2026."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


def _mock_client_response(json_data=None, status_code: int = 200):
    resp = MagicMock()
    resp.json.return_value = json_data or {}
    resp.raise_for_status = MagicMock()
    resp.status_code = status_code
    return resp


def _async_response(json_data=None, status_code: int = 200):
    return AsyncMock(return_value=_mock_client_response(json_data, status_code))


@pytest.mark.asyncio
async def test_zoho_downstream_tools_honor_explicit_organization_id() -> None:
    from connectors.finance.zoho_books import ZohoBooksConnector

    connector = ZohoBooksConnector(
        {"access_token": "fake", "organization_id": "stale-org"}
    )
    connector._client = MagicMock()
    connector._client.get = _async_response({"contacts": []})

    await connector.list_vendors(organization_id=60072428145, search_text="Acme")

    call = connector._client.get.call_args
    assert call.args[0] == "/contacts"
    assert call.kwargs["params"]["organization_id"] == "60072428145"
    assert call.kwargs["params"]["contact_type"] == "vendor"
    assert "org_id" not in call.kwargs["params"]
    assert connector.config["organization_id"] == "60072428145"


@pytest.mark.asyncio
async def test_zoho_bill_mutation_keeps_org_id_in_query_not_body() -> None:
    from connectors.finance.zoho_books import ZohoBooksConnector

    connector = ZohoBooksConnector({"access_token": "fake", "organization_id": "old"})
    connector._client = MagicMock()
    connector._client.put = _async_response({"bill": {"bill_id": "B1"}})

    await connector.update_bill(
        bill_id="B1",
        organization_id="60072428145",
        status="open",
    )

    call = connector._client.put.call_args
    assert call.args[0] == "/bills/B1"
    assert call.kwargs["params"]["organization_id"] == "60072428145"
    assert call.kwargs["json"] == {"status": "open"}


@pytest.mark.asyncio
async def test_zoho_get_organization_lists_without_stale_org_injection() -> None:
    from connectors.finance.zoho_books import ZohoBooksConnector

    connector = ZohoBooksConnector(
        {"access_token": "fake", "organization_id": "stale-org"}
    )
    connector._client = MagicMock()
    connector._client.get = _async_response(
        {"organizations": [{"organization_id": "60072428145", "name": "Acme"}]}
    )

    result = await connector.get_organization()

    assert result["organizations"][0]["organization_id"] == "60072428145"
    call = connector._client.get.call_args
    assert call.args[0] == "/organizations"
    assert call.kwargs["params"] is None


@pytest.mark.asyncio
async def test_zoho_get_organization_accepts_numeric_explicit_org_id() -> None:
    from connectors.finance.zoho_books import ZohoBooksConnector

    connector = ZohoBooksConnector({"access_token": "fake"})
    connector._client = MagicMock()
    connector._client.get = _async_response(
        {"organizations": [{"organization_id": "60072428145", "name": "Acme"}]}
    )

    await connector.get_organization(organization_id=60072428145)

    call = connector._client.get.call_args
    assert call.kwargs["params"]["organization_id"] == "60072428145"
    assert connector.config["organization_id"] == "60072428145"


def test_crm_connectors_register_production_crud_and_validation_tools() -> None:
    from connectors.marketing.hubspot import HubspotConnector
    from connectors.marketing.salesforce import SalesforceConnector

    expected = {
        "search_companies",
        "get_company",
        "update_company",
        "delete_company",
        "search_deals",
        "delete_deal",
        "delete_contact",
        "create_task",
        "create_note",
        "list_associations",
        "create_association",
        "validate_crm_access",
    }
    assert expected.issubset(HubspotConnector({})._tool_registry)

    salesforce_expected = {
        "list_accounts",
        "search_accounts",
        "create_account",
        "update_account",
        "delete_account",
        "list_contacts",
        "search_contacts",
        "get_contact",
        "create_contact",
        "update_contact",
        "delete_contact",
        "search_opportunities",
        "get_opportunity",
        "create_opportunity",
        "delete_opportunity",
        "create_note",
        "create_opportunity_contact_role",
        "validate_crm_access",
    }
    assert salesforce_expected.issubset(SalesforceConnector({})._tool_registry)


@pytest.mark.asyncio
async def test_hubspot_crm_gap_tools_use_real_v3_and_v4_paths() -> None:
    from connectors.marketing.hubspot import HubspotConnector

    connector = HubspotConnector({"access_token": "fake"})
    connector._client = MagicMock()

    connector._client.post = _async_response(
        {"results": [{"id": "201", "properties": {"name": "Acme"}}], "total": 1}
    )
    search = await connector.search_companies(query="Acme")
    assert search["companies"][0]["id"] == "201"
    assert connector._client.post.call_args.args[0] == (
        "/crm/v3/objects/companies/search"
    )

    connector._client.patch = _async_response({"id": "201"})
    await connector.update_company(company_id="201", industry="Software")
    assert connector._client.patch.call_args.args[0] == "/crm/v3/objects/companies/201"

    connector._client.request = _async_response({}, status_code=204)
    deleted = await connector.delete_contact(contact_id="101")
    assert deleted == {"status": "deleted", "object_type": "contacts", "id": "101"}
    assert connector._client.request.call_args.args[:2] == (
        "DELETE",
        "/crm/v3/objects/contacts/101",
    )

    connector._client.request = _async_response({"id": "assoc"})
    await connector.create_association(
        from_object_type="contacts",
        from_object_id="101",
        to_object_type="companies",
        to_object_id="201",
    )
    assert connector._client.request.call_args.args[:2] == (
        "PUT",
        "/crm/v4/objects/contacts/101/associations/default/companies/201",
    )


@pytest.mark.asyncio
async def test_salesforce_crm_gap_tools_use_real_sobject_and_query_paths() -> None:
    from connectors.marketing.salesforce import SalesforceConnector

    connector = SalesforceConnector({"access_token": "fake"})
    connector._client = MagicMock()
    connector._client.get = _async_response({"records": [], "totalSize": 0})

    await connector.search_contacts(query="alice")
    params = connector._client.get.call_args.kwargs["params"]
    assert connector._client.get.call_args.args[0] == "/query"
    assert "FROM Contact" in params["q"]
    assert "Email LIKE '%alice%'" in params["q"]

    connector._client.post = _async_response({"id": "0031", "success": True})
    await connector.create_contact(LastName="Tester", Email="tester@example.com")
    assert connector._client.post.call_args.args[0] == "/sobjects/Contact"

    connector._client.delete = _async_response({}, status_code=204)
    deleted = await connector.delete_contact(contact_id="0031")
    assert deleted == {"status": "deleted", "object": "Contact", "id": "0031"}
    assert connector._client.delete.call_args.args[0] == "/sobjects/Contact/0031"
