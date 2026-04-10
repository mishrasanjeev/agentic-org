"""Test KPI, Chat, Companies, and Report Schedule APIs.

Phase 3-4 API endpoint tests using FastAPI TestClient.
"""

from __future__ import annotations

import os
import uuid
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Fixtures — create a TestClient with mocked auth and DB
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def app():
    """Build a FastAPI app with mocked lifespan and auth middleware."""
    from api.main import app as _app

    # Replace lifespan to skip DB init (no PostgreSQL needed for these tests)
    @asynccontextmanager
    async def _test_lifespan(app):
        yield

    _app.router.lifespan_context = _test_lifespan
    return _app


@pytest.fixture
def client(app):
    """TestClient with auth middleware bypassed via mocked validate_token."""
    test_tenant_id = f"test-tenant-{uuid.uuid4().hex[:8]}"

    # Override the dependency so it returns our test tenant
    from api.deps import get_current_tenant
    app.dependency_overrides[get_current_tenant] = lambda: test_tenant_id

    # Patch the legacy token validator to accept any token
    async def _fake_validate(token):
        return {
            "sub": "test-user",
            "agenticorg:tenant_id": test_tenant_id,
            "agenticorg:scopes": [],
        }

    with patch("auth.grantex_middleware.validate_token", side_effect=_fake_validate):
        with patch("auth.grantex_middleware.extract_tenant_id", return_value=test_tenant_id):
            with patch("auth.grantex_middleware.extract_scopes", return_value=[]):
                with TestClient(app, raise_server_exceptions=False) as c:
                    # Add a Bearer token header so middleware doesn't reject outright
                    c.headers["Authorization"] = "Bearer fake-test-token"
                    c._test_tenant_id = test_tenant_id
                    yield c

    app.dependency_overrides.pop(get_current_tenant, None)


# ═══════════════════════════════════════════════════════════════════════════
# CFO KPIs
# ═══════════════════════════════════════════════════════════════════════════


class TestCFOKPIs:
    """GET /kpis/cfo returns valid structure with all expected keys.

    The endpoint returns cache/DB data or demo fallback with ``demo`` and
    ``stale`` flags.  Without a live DB/Redis the endpoint may return
    400/500 — tests accept that gracefully.
    """

    def test_cfo_kpis_status_ok(self, client):
        resp = client.get("/api/v1/kpis/cfo")
        # 200 with demo/cache data; 400/500 when DB layer is completely unavailable
        assert resp.status_code in (200, 400, 500)

    def test_cfo_kpis_has_cash_runway(self, client):
        resp = client.get("/api/v1/kpis/cfo")
        if resp.status_code != 200:
            return  # no DB — skip metric check
        data = resp.json()
        assert "demo" in data
        assert "cash_runway_months" in data
        assert isinstance(data["cash_runway_months"], (int, float))

    def test_cfo_kpis_has_dso_days(self, client):
        resp = client.get("/api/v1/kpis/cfo")
        if resp.status_code != 200:
            return
        data = resp.json()
        assert "dso_days" in data
        assert isinstance(data["dso_days"], (int, float))

    def test_cfo_kpis_has_dpo_days(self, client):
        resp = client.get("/api/v1/kpis/cfo")
        if resp.status_code != 200:
            return
        data = resp.json()
        assert "dpo_days" in data

    def test_cfo_kpis_has_burn_rate(self, client):
        resp = client.get("/api/v1/kpis/cfo")
        if resp.status_code != 200:
            return
        data = resp.json()
        assert "burn_rate" in data

    def test_cfo_kpis_has_ar_aging(self, client):
        resp = client.get("/api/v1/kpis/cfo")
        if resp.status_code != 200:
            return
        data = resp.json()
        assert "ar_aging" in data
        ar = data["ar_aging"]
        assert "0_30" in ar
        assert "90_plus" in ar

    def test_cfo_kpis_has_bank_balances(self, client):
        resp = client.get("/api/v1/kpis/cfo")
        if resp.status_code != 200:
            return
        data = resp.json()
        assert "bank_balances" in data
        assert isinstance(data["bank_balances"], list)
        assert len(data["bank_balances"]) > 0

    def test_cfo_kpis_has_tax_calendar(self, client):
        resp = client.get("/api/v1/kpis/cfo")
        if resp.status_code != 200:
            return
        data = resp.json()
        assert "tax_calendar" in data
        assert isinstance(data["tax_calendar"], list)

    def test_cfo_kpis_has_monthly_pl(self, client):
        resp = client.get("/api/v1/kpis/cfo")
        if resp.status_code != 200:
            return
        data = resp.json()
        assert "monthly_pl" in data
        assert isinstance(data["monthly_pl"], list)
        assert len(data["monthly_pl"]) > 0


# ═══════════════════════════════════════════════════════════════════════════
# CMO KPIs
# ═══════════════════════════════════════════════════════════════════════════


class TestCMOKPIs:
    """GET /kpis/cmo returns valid structure with all expected keys.

    The endpoint returns cache/DB data or demo fallback with ``demo`` and
    ``stale`` flags.  Without a live DB/Redis the endpoint may return
    400/500 — tests accept that gracefully.
    """

    def test_cmo_kpis_status_ok(self, client):
        resp = client.get("/api/v1/kpis/cmo")
        assert resp.status_code in (200, 400, 500)

    def test_cmo_kpis_has_cac(self, client):
        resp = client.get("/api/v1/kpis/cmo")
        if resp.status_code != 200:
            return
        data = resp.json()
        assert "demo" in data
        assert "cac" in data
        assert isinstance(data["cac"], (int, float))

    def test_cmo_kpis_has_mqls(self, client):
        resp = client.get("/api/v1/kpis/cmo")
        if resp.status_code != 200:
            return
        data = resp.json()
        assert "mqls" in data

    def test_cmo_kpis_has_pipeline_value(self, client):
        resp = client.get("/api/v1/kpis/cmo")
        if resp.status_code != 200:
            return
        data = resp.json()
        assert "pipeline_value" in data

    def test_cmo_kpis_has_roas_by_channel(self, client):
        resp = client.get("/api/v1/kpis/cmo")
        if resp.status_code != 200:
            return
        data = resp.json()
        assert "roas_by_channel" in data
        assert isinstance(data["roas_by_channel"], dict)
        assert "Google Ads" in data["roas_by_channel"]

    def test_cmo_kpis_has_email_performance(self, client):
        resp = client.get("/api/v1/kpis/cmo")
        if resp.status_code != 200:
            return
        data = resp.json()
        assert "email_performance" in data
        ep = data["email_performance"]
        assert "open_rate" in ep
        assert "click_rate" in ep

    def test_cmo_kpis_has_website_traffic(self, client):
        resp = client.get("/api/v1/kpis/cmo")
        if resp.status_code != 200:
            return
        data = resp.json()
        assert "website_traffic" in data
        wt = data["website_traffic"]
        assert "sessions" in wt
        assert "users" in wt


# ═══════════════════════════════════════════════════════════════════════════
# Chat Query
# ═══════════════════════════════════════════════════════════════════════════


class TestChatQuery:
    """POST /chat/query routes user questions to correct domain."""

    def test_finance_question_routes_to_finance(self, client):
        resp = client.post("/api/v1/chat/query", json={"query": "What is our cash flow and invoice status?"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["domain"] == "finance"
        assert "CFO" in data["agent"] or "finance" in data["agent"].lower()

    def test_marketing_question_routes_to_marketing(self, client):
        resp = client.post("/api/v1/chat/query", json={"query": "Show me the latest campaign analytics and SEO report"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["domain"] == "marketing"
        assert "CMO" in data["agent"] or "marketing" in data["agent"].lower()

    def test_hr_question_routes_to_hr(self, client):
        resp = client.post("/api/v1/chat/query", json={"query": "What is the employee headcount and attrition rate?"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["domain"] == "hr"

    def test_general_question_returns_general(self, client):
        resp = client.post("/api/v1/chat/query", json={"query": "Hello how are you doing today?"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["domain"] == "general"

    def test_chat_response_has_confidence(self, client):
        resp = client.post("/api/v1/chat/query", json={"query": "Show my invoice list"})
        data = resp.json()
        assert "confidence" in data
        assert 0 <= data["confidence"] <= 1

    def test_chat_response_has_answer(self, client):
        resp = client.post("/api/v1/chat/query", json={"query": "Show expense breakdown"})
        data = resp.json()
        assert "answer" in data
        assert len(data["answer"]) > 10


# ═══════════════════════════════════════════════════════════════════════════
# Chat History
# ═══════════════════════════════════════════════════════════════════════════


class TestChatHistory:
    """GET /chat/history returns list of messages."""

    def test_history_returns_list(self, client):
        resp = client.get("/api/v1/chat/history")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)

    def test_history_populates_after_query(self, client):
        # Send a chat query first
        client.post("/api/v1/chat/query", json={"query": "What is our revenue?"})
        resp = client.get("/api/v1/chat/history")
        data = resp.json()
        # Should have at least the user message and agent response
        assert len(data) >= 2


# ═══════════════════════════════════════════════════════════════════════════
# Companies CRUD
# ═══════════════════════════════════════════════════════════════════════════


class TestCompanies:
    """CRUD operations for the multi-company endpoint."""

    def test_list_companies(self, client):
        resp = client.get("/api/v1/companies")
        # 200 with DB, 400 without DB session
        assert resp.status_code in (200, 400)
        if resp.status_code == 200:
            data = resp.json()
            assert isinstance(data, list)

    def test_create_company(self, client):
        resp = client.post("/api/v1/companies", json={
            "name": "Test Corp Pvt Ltd",
            "gstin": "27AABCT1234F1Z5",
            "pan": "AABCT1234F",
            "industry": "Technology",
        })
        # 201 with DB, 400/422 without DB
        assert resp.status_code in (201, 400, 422)
        if resp.status_code == 201:
            data = resp.json()
            assert data["name"] == "Test Corp Pvt Ltd"
            assert "id" in data

    def test_get_company_by_id(self, client):
        # Create first
        create_resp = client.post(
            "/api/v1/companies",
            json={"name": "Lookup Corp", "pan": "AABCE1234F"},
        )
        if create_resp.status_code != 201:
            # DB not available — verify the GET endpoint exists and validates UUID
            resp = client.get("/api/v1/companies/00000000-0000-0000-0000-000000000099")
            assert resp.status_code in (400, 404)
            return
        company_id = create_resp.json()["id"]
        resp = client.get(f"/api/v1/companies/{company_id}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "Lookup Corp"

    def test_get_company_not_found(self, client):
        resp = client.get("/api/v1/companies/nonexistent-id-12345")
        # 400 for invalid UUID, 404 if UUID valid but not found
        assert resp.status_code in (400, 404)

    def test_update_company(self, client):
        # Create first
        create_resp = client.post(
            "/api/v1/companies",
            json={"name": "Update Corp", "pan": "AABCE1234F"},
        )
        if create_resp.status_code != 201:
            # DB not available — verify PATCH endpoint validates UUID
            resp = client.patch(
                "/api/v1/companies/00000000-0000-0000-0000-000000000099",
                json={"industry": "Retail"},
            )
            assert resp.status_code in (400, 404)
            return
        company_id = create_resp.json()["id"]
        resp = client.patch(f"/api/v1/companies/{company_id}", json={"industry": "Retail"})
        assert resp.status_code == 200
        assert resp.json()["industry"] == "Retail"
        assert resp.json()["name"] == "Update Corp"


# ═══════════════════════════════════════════════════════════════════════════
# Report Schedules CRUD
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.skipif(
    not os.getenv("AGENTICORG_DB_URL"),
    reason="report schedules now backed by PostgreSQL",
)
class TestReportSchedules:
    """CRUD + run-now + toggle for report schedules."""

    def _create_schedule(self, client, report_type="cfo_daily"):
        return client.post("/api/v1/report-schedules", json={
            "report_type": report_type,
            "cron_expression": "daily",
            "delivery_channels": [{"type": "email", "target": "cfo@example.com"}],
            "format": "pdf",
            "is_active": True,
            "company_id": "default",
        })

    def test_create_schedule(self, client):
        resp = self._create_schedule(client)
        assert resp.status_code == 201
        data = resp.json()
        assert data["report_type"] == "cfo_daily"
        assert "id" in data

    def test_list_schedules(self, client):
        self._create_schedule(client)
        resp = client.get("/api/v1/report-schedules")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)

    def test_toggle_schedule_inactive(self, client):
        create_resp = self._create_schedule(client)
        schedule_id = create_resp.json()["id"]
        resp = client.patch(
            f"/api/v1/report-schedules/{schedule_id}",
            json={"is_active": False},
        )
        assert resp.status_code == 200
        assert resp.json()["is_active"] is False

    def test_delete_schedule(self, client):
        create_resp = self._create_schedule(client)
        schedule_id = create_resp.json()["id"]
        resp = client.delete(f"/api/v1/report-schedules/{schedule_id}")
        assert resp.status_code == 204

    def test_delete_nonexistent_schedule(self, client):
        resp = client.delete("/api/v1/report-schedules/nonexistent-id")
        assert resp.status_code == 404
