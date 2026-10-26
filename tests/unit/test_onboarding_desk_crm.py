# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Onboarding desk CRM intake: settings, mapping HubSpot and Salesforce contacts to leads, the sync and its routes."""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.config import settings
from core.onboarding_desk import crm, leads, senders, store
from core.onboarding_desk.store import DeskError

TENANT = uuid.uuid4()
SOURCE = uuid.uuid4()
NOW = datetime(2026, 10, 27, 17, 0, tzinfo=UTC)

RAW = {
    "source_id": str(SOURCE),
    "provider": "hubspot",
    "connector": "registry-hubspot",
    "filter_field": "hs_lead_status",
    "filter_value": "PAYMENTS_INTEREST",
    "captured_at_field": "consent_captured_at",
    "consent_fields": {"sms": "sms_opt_in", "email": "email_opt_in"},
    "attestation": {"wording": "Yes, contact me about accepting card payments. Reply STOP to opt out."},
}

HUBSPOT_CONTACT = {
    "id": "101",
    "firstname": "Avery",
    "lastname": "Example",
    "email": "Owner@Northwind.example",
    "phone": "+1 415 555 0134",
    "company": "Northwind Coffee LLC",
    "state": "California",
    "country": "United States",
    "sms_opt_in": "true",
    "email_opt_in": "false",
    "consent_captured_at": "2026-10-20T10:00:00Z",
}

SALESFORCE_CONTACT = {
    "Id": "003XYZ",
    "FirstName": "Camille",
    "LastName": "Exemple",
    "Email": "camille@nord.example",
    "Phone": None,
    "Account": {"Name": "Café Nord Inc"},
    "MailingState": "QC",
    "MailingCountry": "Canada",
    "Email_Opt_In__c": True,
    "Consent_At__c": "2026-10-19T09:00:00+00:00",
}


class TestSettings:
    def test_valid(self):
        parsed = crm.crm_settings_from(RAW)
        assert parsed.attestation.method == "crm_import" and parsed.consent_fields == {
            "sms": "sms_opt_in",
            "email": "email_opt_in",
        }
        assert crm.crm_settings_from(parsed.to_dict()) == parsed
        assert parsed.merchant_type == "general"

    @pytest.mark.parametrize(
        "change",
        [
            {"source_id": "nope"},
            {"provider": "pipedrive"},
            {"connector": "hubspot"},
            {"filter_field": "x; DROP"},
            {"filter_field": "Account.Name"},
            {"filter_value": ""},
            {"filter_value": "x" * 201},
            {"consent_fields": {}},
            {"consent_fields": {"fax": "f"}},
            {"consent_fields": {"sms": "bad field"}},
            {"captured_at_field": ""},
            {"attestation": "yes"},
            {"attestation": {"wording": "ok"}},
            {"attestation": {"wording": "Yes, contact me about card payments.", "method": "telepathy"}},
            {"merchant_type": "Bad Type"},
            {"unknown": 1},
        ],
    )
    def test_invalid(self, change):
        with pytest.raises(DeskError) as caught:
            crm.crm_settings_from({**RAW, **change})
        assert caught.value.code == "crm_settings_invalid"
        with pytest.raises(DeskError):
            crm.crm_settings_from([])


class TestMapping:
    def test_codes(self):
        assert crm.country_code("United States") == "US" and crm.country_code(" canada ") == "CA"
        assert crm.country_code("Mexico") == "" and crm.country_code(None) == ""
        assert crm.region_code("california") == "CA" and crm.region_code("Québec") == "QC"
        assert crm.region_code("ny") == "NY" and crm.region_code("Atlantis") == ""

    def test_a_hubspot_contact_becomes_a_lead_with_only_the_consents_it_records(self):
        raw = crm.lead_raw(HUBSPOT_CONTACT, crm.crm_settings_from(RAW))
        assert raw["external_ref"] == "hubspot:101" and raw["country"] == "US" and raw["region"] == "CA"
        assert raw["business_name"] == "Northwind Coffee LLC" and raw["contact_name"] == "Avery Example"
        assert [c["channel"] for c in raw["consents"]] == ["sms"]
        assert raw["consents"][0]["source_ref"] == "hubspot:101" and raw["consents"][0]["method"] == "crm_import"
        lead = leads.lead_from(raw, now=NOW)
        assert lead.email == "owner@northwind.example" and lead.phone == "+14155550134"
        assert lead.consents[0].captured_at == datetime(2026, 10, 20, 10, tzinfo=UTC)

    def test_no_capture_time_means_no_consent(self):
        contact = {**HUBSPOT_CONTACT, "consent_captured_at": None, "email_opt_in": "yes"}
        assert crm.lead_raw(contact, crm.crm_settings_from(RAW))["consents"] == []

    def test_a_salesforce_contact(self):
        settings_ = crm.crm_settings_from(
            {
                **RAW,
                "provider": "salesforce",
                "connector": "registry-salesforce",
                "filter_field": "LeadSource",
                "captured_at_field": "Consent_At__c",
                "consent_fields": {"email": "Email_Opt_In__c"},
                "merchant_type": "cafe",
            }
        )
        raw = crm.lead_raw(SALESFORCE_CONTACT, settings_)
        assert raw["business_name"] == "Café Nord Inc" and raw["region"] == "QC" and raw["country"] == "CA"
        lead = leads.lead_from(raw, now=NOW)
        assert lead.locale == "fr" and lead.merchant_type == "cafe" and lead.consents[0].channel == "email"
        nameless = crm.lead_raw({"Id": "1", "Account": "not a dict"}, settings_)
        assert nameless["business_name"] == "" and nameless["external_ref"] == "salesforce:1"


class FakeConnector:
    def __init__(self, result=None, error=None, disconnect_error=False):
        self.result = result
        self.error = error
        self.disconnect_error = disconnect_error
        self.calls = []

    async def search_contacts(self, **params):
        self.calls.append(("search_contacts", params))
        if self.error:
            raise self.error
        return self.result

    async def query(self, **params):
        self.calls.append(("query", params))
        if self.error:
            raise self.error
        return self.result

    async def disconnect(self):
        if self.disconnect_error:
            raise RuntimeError("socket")


def use(monkeypatch, connector):
    async def resolve(tenant_id, sender):
        assert sender.connector.startswith("registry-")
        return connector

    monkeypatch.setattr(senders, "_connector", resolve)


class TestFetch:
    def test_hubspot(self, monkeypatch):
        connector = FakeConnector({"contacts": [HUBSPOT_CONTACT, "junk"]}, disconnect_error=True)
        use(monkeypatch, connector)
        records = asyncio.run(crm.fetch(TENANT, crm.crm_settings_from(RAW), limit=500))
        assert records == [HUBSPOT_CONTACT]
        name, params = connector.calls[0]
        assert name == "search_contacts" and params["operator"] == "EQ" and params["limit"] == 100
        assert params["property_name"] == "hs_lead_status" and params["query"] == "PAYMENTS_INTEREST"
        assert "sms_opt_in" in params["properties"] and "consent_captured_at" in params["properties"]
        assert "id" not in params["properties"]

    def test_salesforce_keeps_the_filter_value_a_literal(self, monkeypatch):
        connector = FakeConnector({"records": [SALESFORCE_CONTACT]})
        use(monkeypatch, connector)
        settings_ = crm.crm_settings_from(
            {
                **RAW,
                "provider": "salesforce",
                "connector": "registry-salesforce",
                "filter_field": "LeadSource",
                "filter_value": "x' OR Name != '",
                "captured_at_field": "Consent_At__c",
                "consent_fields": {"email": "Email_Opt_In__c"},
            }
        )
        assert asyncio.run(crm.fetch(TENANT, settings_, limit=50)) == [SALESFORCE_CONTACT]
        soql = connector.calls[0][1]["q"]
        assert "WHERE LeadSource = 'x\\' OR Name != \\'' ORDER BY LastModifiedDate DESC LIMIT 50" in soql
        assert soql.startswith("SELECT Id, FirstName, LastName, Email, Phone, Account.Name, MailingState")
        assert "Email_Opt_In__c, Consent_At__c FROM Contact" in soql

    def test_failures(self, monkeypatch):
        settings_ = crm.crm_settings_from(RAW)
        use(monkeypatch, None)
        with pytest.raises(DeskError) as caught:
            asyncio.run(crm.fetch(TENANT, settings_))
        assert caught.value.code == "crm_connector_not_configured"
        use(monkeypatch, FakeConnector(error=TimeoutError()))
        with pytest.raises(DeskError) as caught:
            asyncio.run(crm.fetch(TENANT, settings_))
        assert caught.value.code == "crm_unavailable" and "TimeoutError" in caught.value.message
        use(monkeypatch, FakeConnector({"error": "401 unauthorized"}))
        with pytest.raises(DeskError, match="401 unauthorized"):
            asyncio.run(crm.fetch(TENANT, settings_))


@pytest.fixture
def db(monkeypatch):
    def install(*results):
        rows = [list(r) for r in results]
        added = []

        class Result:
            def __init__(self, items):
                self.items = items

            def scalar_one_or_none(self):
                return self.items[0] if self.items else None

        class Session:
            async def execute(self, statement):
                return Result(rows.pop(0) if rows else [])

            def add(self, row):
                added.append(row)

        @asynccontextmanager
        async def fake(tenant_id):
            yield Session()

        monkeypatch.setattr(store, "_session", fake)
        return added

    return install


class TestStoredSettingsAndSync:
    def test_settings_need_a_crm_source(self, db, monkeypatch):
        sources = [SimpleNamespace(id=SOURCE, kind="crm")]

        async def list_sources(tid):
            return sources

        monkeypatch.setattr(store, "list_sources", list_sources)
        added = db([])
        asyncio.run(crm.put_crm_settings(TENANT, RAW, actor="u"))
        assert added[0].settings["crm"]["provider"] == "hubspot"
        row = SimpleNamespace(settings={"branding": {"partner_name": "P"}}, updated_by="", updated_at=None)
        db([row])
        asyncio.run(crm.put_crm_settings(TENANT, RAW, actor="u"))
        assert row.settings["branding"] == {"partner_name": "P"} and row.settings["crm"]["source_id"] == str(SOURCE)
        db([row])
        assert asyncio.run(crm.get_crm_settings(TENANT)).provider == "hubspot"
        db([])
        assert asyncio.run(crm.get_crm_settings(TENANT)) is None
        sources[0] = SimpleNamespace(id=SOURCE, kind="webhook")
        with pytest.raises(DeskError, match="kind crm"):
            asyncio.run(crm.put_crm_settings(TENANT, RAW, actor="u"))

    def test_sync(self, monkeypatch):
        stored = []

        async def configured(tid):
            return crm.crm_settings_from(RAW)

        async def fetch(tid, settings_, *, limit):
            return [
                HUBSPOT_CONTACT,
                {**HUBSPOT_CONTACT, "id": "102"},
                {**HUBSPOT_CONTACT, "id": "103", "country": "Mexico"},
            ]

        async def create(tid, lead, *, source_id, source_kind, actor):
            stored.append((lead.external_ref, source_id, source_kind))
            return SimpleNamespace(created=lead.external_ref == "hubspot:101")

        monkeypatch.setattr(crm, "get_crm_settings", configured)
        monkeypatch.setattr(crm, "fetch", fetch)
        monkeypatch.setattr(store, "create_lead", create)
        result = asyncio.run(crm.sync(TENANT, actor="u"))
        assert result["fetched"] == 3 and result["created"] == 1 and result["existing"] == 1
        assert result["rejected"] == 1 and result["errors"][0]["external_ref"] == "hubspot:103"
        assert result["errors"][0]["field"] == "country"
        assert stored[0] == ("hubspot:101", SOURCE, "crm")

        async def unconfigured(tid):
            return None

        monkeypatch.setattr(crm, "get_crm_settings", unconfigured)
        with pytest.raises(DeskError) as caught:
            asyncio.run(crm.sync(TENANT, actor="u"))
        assert caught.value.code == "crm_not_configured"


class TestRoutes:
    def test_settings_and_sync(self, monkeypatch):
        from api import deps
        from api.v1 import onboarding_desk as api

        app = FastAPI()
        app.include_router(api.router, prefix="/api/v1")
        app.dependency_overrides[deps.get_current_tenant] = lambda: str(TENANT)
        app.dependency_overrides[deps.get_current_user] = lambda: {"sub": str(uuid.uuid4())}
        app.dependency_overrides[deps.require_tenant_admin.dependency] = lambda: None
        monkeypatch.setattr(settings, "onboarding_desk_enabled", True)
        client = TestClient(app)
        state = {"settings": None}

        async def get(tid):
            return state["settings"]

        async def put(tid, raw, *, actor):
            state["settings"] = crm.crm_settings_from(raw)
            return state["settings"]

        async def run(tid, *, actor, limit):
            if state["settings"] is None:
                raise DeskError(409, "crm_not_configured", "x")
            return {"fetched": limit}

        monkeypatch.setattr(crm, "get_crm_settings", get)
        monkeypatch.setattr(crm, "put_crm_settings", put)
        monkeypatch.setattr(crm, "sync", run)
        base = "/api/v1/onboarding-desk"
        assert client.get(f"{base}/crm-settings").json() == {"configured": False}
        assert client.post(f"{base}/crm/sync", json={}).status_code == 409
        assert client.put(f"{base}/crm-settings", json=RAW).json()["provider"] == "hubspot"
        assert client.put(f"{base}/crm-settings", json={**RAW, "provider": "x"}).status_code == 422
        assert client.get(f"{base}/crm-settings").json()["configured"] is True
        assert client.post(f"{base}/crm/sync", json={"limit": 5}).json() == {"fetched": 5}
        assert client.post(f"{base}/crm/sync", json={"limit": 500}).status_code == 422

        async def broken(tid):
            raise DeskError(422, "crm_settings_invalid", "stored settings are invalid")

        monkeypatch.setattr(crm, "get_crm_settings", broken)
        assert client.get(f"{base}/crm-settings").status_code == 422
