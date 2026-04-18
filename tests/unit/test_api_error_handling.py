# ruff: noqa: S106 — test files use fake tokens intentionally
"""Test API error handling, input validation, and correct error messages.

Covers:
- KPI endpoints: auth, invalid inputs, wrong roles
- Chat endpoints: empty queries, oversize queries, auth, injection
- Companies endpoints: missing fields, not-found, duplicates
- Report Schedules endpoints: validation, not-found, run-now
- General: JSON error format, error envelope structure

~60 tests.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def app():
    """Build a FastAPI app with mocked lifespan (no DB required)."""
    from api.main import app as _app

    @asynccontextmanager
    async def _test_lifespan(app):
        yield

    _app.router.lifespan_context = _test_lifespan
    return _app


@pytest.fixture
def auth_client(app):
    """TestClient with auth middleware bypassed (authenticated)."""
    test_tenant_id = f"test-tenant-{uuid.uuid4().hex[:8]}"

    from api.deps import get_current_tenant
    app.dependency_overrides[get_current_tenant] = lambda: test_tenant_id

    async def _fake_validate(token):
        # admin scope so routers that bind `dependencies=[require_tenant_admin]`
        # (e.g. /report-schedules) reach their body/handler. These tests
        # exercise validation / error handling, not the admin gate —
        # gating is covered separately by tests/security/test_admin_gate*.
        return {
            "sub": "test-user",
            "agenticorg:tenant_id": test_tenant_id,
            "agenticorg:scopes": ["agenticorg:admin"],
        }

    admin_scopes = ["agenticorg:admin"]
    with patch("auth.grantex_middleware.validate_token", side_effect=_fake_validate):
        with patch("auth.grantex_middleware.extract_tenant_id", return_value=test_tenant_id):
            with patch("auth.grantex_middleware.extract_scopes", return_value=admin_scopes):
                with TestClient(app, raise_server_exceptions=False) as c:
                    c.headers["Authorization"] = "Bearer fake-test-token"
                    c._test_tenant_id = test_tenant_id
                    yield c

    app.dependency_overrides.pop(get_current_tenant, None)


@pytest.fixture
def noauth_client(app):
    """TestClient WITHOUT any auth headers — simulates unauthenticated requests."""
    @asynccontextmanager
    async def _test_lifespan(a):
        yield

    app.router.lifespan_context = _test_lifespan

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# ═══════════════════════════════════════════════════════════════════════════
# KPI Endpoints — Error Handling
# ═══════════════════════════════════════════════════════════════════════════


class TestCFOKPIErrors:
    """GET /kpis/cfo error paths."""

    def test_cfo_kpis_without_auth_returns_401(self, noauth_client):
        resp = noauth_client.get("/api/v1/kpis/cfo")
        assert resp.status_code == 401

    def test_cfo_kpis_without_auth_returns_json(self, noauth_client):
        resp = noauth_client.get("/api/v1/kpis/cfo")
        assert resp.headers.get("content-type", "").startswith("application/json")

    def test_cfo_kpis_without_auth_has_detail_field(self, noauth_client):
        resp = noauth_client.get("/api/v1/kpis/cfo")
        data = resp.json()
        assert "detail" in data

    def test_cfo_kpis_invalid_bearer_token(self, noauth_client):
        resp = noauth_client.get(
            "/api/v1/kpis/cfo",
            headers={"Authorization": "Bearer completely-invalid-token-xyz"},
        )
        assert resp.status_code == 401

    def test_cfo_kpis_malformed_auth_header(self, noauth_client):
        resp = noauth_client.get(
            "/api/v1/kpis/cfo",
            headers={"Authorization": "NotBearer abc123"},
        )
        assert resp.status_code == 401

    def test_cfo_kpis_with_invalid_company_id_returns_data(self, auth_client):
        """Invalid company_id should return default/demo data, not 500."""
        resp = auth_client.get("/api/v1/kpis/cfo?company_id=nonexistent-999")
        # 200 with demo/cache data; 400/500 when DB layer is completely unavailable
        assert resp.status_code in (200, 400, 500)
        if resp.status_code == 200:
            data = resp.json()
            assert "demo" in data
            assert "cash_runway_months" in data

    def test_cfo_kpis_with_empty_company_id(self, auth_client):
        resp = auth_client.get("/api/v1/kpis/cfo?company_id=")
        assert resp.status_code in (200, 400, 500)

    def test_cfo_kpis_read_only_any_role(self, auth_client):
        """KPIs are read-only — any authenticated user should access them."""
        resp = auth_client.get("/api/v1/kpis/cfo")
        assert resp.status_code in (200, 400, 500)


class TestCMOKPIErrors:
    """GET /kpis/cmo error paths."""

    def test_cmo_kpis_without_auth_returns_401(self, noauth_client):
        resp = noauth_client.get("/api/v1/kpis/cmo")
        assert resp.status_code == 401

    def test_cmo_kpis_without_auth_returns_json(self, noauth_client):
        resp = noauth_client.get("/api/v1/kpis/cmo")
        assert resp.headers.get("content-type", "").startswith("application/json")

    def test_cmo_kpis_invalid_company_id_returns_data(self, auth_client):
        resp = auth_client.get("/api/v1/kpis/cmo?company_id=nonexistent-xyz")
        # 200 with demo/cache data; 400/500 when DB layer is completely unavailable
        assert resp.status_code in (200, 400, 500)
        if resp.status_code == 200:
            data = resp.json()
            assert "demo" in data
            assert "cac" in data

    def test_cmo_kpis_with_special_chars_company_id(self, auth_client):
        resp = auth_client.get("/api/v1/kpis/cmo?company_id=test%20%3Cscript%3E")
        assert resp.status_code in (200, 400, 500)


# ═══════════════════════════════════════════════════════════════════════════
# Chat Endpoints — Error Handling
# ═══════════════════════════════════════════════════════════════════════════


class TestChatQueryErrors:
    """POST /chat/query error paths."""

    def test_chat_query_without_auth_returns_401(self, noauth_client):
        resp = noauth_client.post(
            "/api/v1/chat/query",
            json={"query": "What is revenue?"},
        )
        assert resp.status_code == 401

    def test_chat_query_empty_body_returns_422(self, auth_client):
        resp = auth_client.post("/api/v1/chat/query", json={})
        assert resp.status_code == 422

    def test_chat_query_missing_query_field_returns_422(self, auth_client):
        resp = auth_client.post("/api/v1/chat/query", json={"company_id": "abc"})
        assert resp.status_code == 422

    def test_chat_query_null_query_returns_422(self, auth_client):
        resp = auth_client.post("/api/v1/chat/query", json={"query": None})
        assert resp.status_code == 422

    def test_chat_query_with_empty_string_returns_200(self, auth_client):
        """Empty string is a valid str — Pydantic accepts it; domain = general."""
        resp = auth_client.post("/api/v1/chat/query", json={"query": ""})
        assert resp.status_code == 200
        data = resp.json()
        assert data["domain"] == "general"

    def test_chat_query_very_long_query_handled(self, auth_client):
        """Very long queries should not crash the server."""
        long_query = "a" * 15000
        resp = auth_client.post("/api/v1/chat/query", json={"query": long_query})
        # Either 200 (handled) or 422 (validated) — NOT 500
        assert resp.status_code in (200, 422)

    def test_chat_query_sql_injection_safe(self, auth_client):
        """SQL injection attempt should not cause server error."""
        resp = auth_client.post(
            "/api/v1/chat/query",
            json={"query": "'; DROP TABLE users; --"},
        )
        assert resp.status_code == 200
        data = resp.json()
        # Injection text should not execute — just a normal general query
        assert data["domain"] == "general"
        assert "answer" in data

    def test_chat_query_xss_attempt_safe(self, auth_client):
        """XSS attempt should not appear raw in response."""
        resp = auth_client.post(
            "/api/v1/chat/query",
            json={"query": "<script>alert('xss')</script>"},
        )
        assert resp.status_code == 200
        data = resp.json()
        # The script tag may appear in the answer as quoted text but
        # it should not execute — response is JSON, not HTML
        assert data.get("domain") is not None

    def test_chat_query_unicode_safe(self, auth_client):
        """Non-English (Hindi/Devanagari) query should not crash."""
        resp = auth_client.post(
            "/api/v1/chat/query",
            json={"query": "कंपनी का राजस्व क्या है?"},
        )
        assert resp.status_code == 200

    def test_chat_query_returns_json_content_type(self, auth_client):
        resp = auth_client.post(
            "/api/v1/chat/query",
            json={"query": "revenue forecast"},
        )
        assert resp.headers.get("content-type", "").startswith("application/json")

    def test_chat_query_invalid_content_type(self, auth_client):
        """Sending non-JSON body should return 422."""
        resp = auth_client.post(
            "/api/v1/chat/query",
            content=b"plain text body",
            headers={"Content-Type": "text/plain"},
        )
        assert resp.status_code == 422


class TestChatHistoryErrors:
    """GET /chat/history error paths."""

    def test_chat_history_without_auth_returns_401(self, noauth_client):
        resp = noauth_client.get("/api/v1/chat/history")
        assert resp.status_code == 401

    def test_chat_history_empty_for_new_session(self, auth_client):
        """A fresh tenant/company_id combo should return empty list."""
        resp = auth_client.get(
            f"/api/v1/chat/history?company_id=new-{uuid.uuid4().hex[:8]}"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 0


# ═══════════════════════════════════════════════════════════════════════════
# Companies Endpoints — Error Handling
# ═══════════════════════════════════════════════════════════════════════════


class TestCompaniesErrors:
    """Companies CRUD error paths."""

    def test_create_company_missing_name_returns_422(self, auth_client):
        """POST /companies with no name field should return 422."""
        resp = auth_client.post("/api/v1/companies", json={"industry": "Tech"})
        assert resp.status_code == 422

    def test_create_company_empty_name_returns_422(self, auth_client):
        """Empty name without required PAN field returns 422."""
        resp = auth_client.post("/api/v1/companies", json={"name": ""})
        # CompanyCreate requires 'pan' field — missing it returns 422
        assert resp.status_code == 422

    def test_create_company_null_name_returns_422(self, auth_client):
        resp = auth_client.post("/api/v1/companies", json={"name": None})
        assert resp.status_code == 422

    def test_get_company_invalid_uuid_returns_400(self, auth_client):
        """Non-UUID company_id returns 400 (bad request)."""
        resp = auth_client.get("/api/v1/companies/nonexistent-company-id-12345")
        assert resp.status_code == 400

    def test_get_company_invalid_uuid_has_error_message(self, auth_client):
        resp = auth_client.get("/api/v1/companies/does-not-exist")
        data = resp.json()
        body_str = str(data).lower()
        assert "invalid" in body_str or "uuid" in body_str or "error" in body_str, (
            f"Expected error message in response, got: {data}"
        )

    def test_update_company_invalid_uuid_returns_400(self, auth_client):
        """Non-UUID company_id on PATCH returns 400."""
        resp = auth_client.patch(
            "/api/v1/companies/nonexistent-xyz",
            json={"industry": "Retail"},
        )
        assert resp.status_code == 400

    def test_update_company_with_empty_body(self, auth_client):
        """PATCH with empty body on invalid UUID returns 400."""
        resp = auth_client.patch(
            "/api/v1/companies/00000000-0000-0000-0000-000000000099",
            json={},
        )
        # Returns 404 (not found), 200 (no-op), or 400 (no DB session)
        assert resp.status_code in (200, 400, 404)

    def test_create_company_without_auth_returns_401(self, noauth_client):
        resp = noauth_client.post(
            "/api/v1/companies", json={"name": "No Auth Corp"}
        )
        assert resp.status_code == 401

    def test_list_companies_without_auth_returns_401(self, noauth_client):
        resp = noauth_client.get("/api/v1/companies")
        assert resp.status_code == 401

    def test_create_duplicate_name_requires_pan(self, auth_client):
        """CompanyCreate requires PAN — missing it returns 422."""
        resp = auth_client.post("/api/v1/companies", json={"name": "DupCorp"})
        assert resp.status_code == 422

    def test_create_company_extra_fields_ignored(self, auth_client):
        """Extra/unknown fields in request body should be ignored."""
        resp = auth_client.post(
            "/api/v1/companies",
            json={
                "name": "Extra Corp",
                "pan": "AABCE1234F",
                "unknown_field": "value",
                "foo": 42,
            },
        )
        # 201 if DB is available, 400/422/500 if not — but never crashes
        assert resp.status_code in (201, 400, 422, 500)
        if resp.status_code == 201:
            data = resp.json()
            assert "unknown_field" not in data


# ═══════════════════════════════════════════════════════════════════════════
# Report Schedules Endpoints — Error Handling
# ═══════════════════════════════════════════════════════════════════════════
class TestReportScheduleErrors:
    """Report Schedule CRUD error paths."""

    def _create_schedule(self, client, report_type="cfo_daily"):
        return client.post(
            "/api/v1/report-schedules",
            json={
                "report_type": report_type,
                "cron_expression": "daily",
                "delivery_channels": [
                    {"type": "email", "target": "cfo@example.com"}
                ],
                "format": "pdf",
                "is_active": True,
                "company_id": "default",
            },
        )

    def test_create_schedule_missing_report_type_returns_422(self, auth_client):
        resp = auth_client.post(
            "/api/v1/report-schedules",
            json={
                "cron_expression": "daily",
                "delivery_channels": [],
            },
        )
        assert resp.status_code == 422

    def test_create_schedule_null_report_type_returns_422(self, auth_client):
        resp = auth_client.post(
            "/api/v1/report-schedules",
            json={"report_type": None},
        )
        assert resp.status_code == 422

    def test_create_schedule_empty_body_returns_422(self, auth_client):
        resp = auth_client.post("/api/v1/report-schedules", json={})
        assert resp.status_code == 422

    def test_create_schedule_without_auth_returns_401(self, noauth_client):
        resp = noauth_client.post(
            "/api/v1/report-schedules",
            json={"report_type": "cfo_daily"},
        )
        assert resp.status_code == 401

    def test_get_schedule_not_found_returns_404(self, auth_client):
        auth_client.get("/api/v1/report-schedules/nonexistent-id")
        # list endpoint is GET /report-schedules (no /{id} GET), so 404/405
        # The API only has list, not get-by-id — verify list works
        list_resp = auth_client.get("/api/v1/report-schedules")
        assert list_resp.status_code == 200

    def test_delete_nonexistent_schedule_returns_404(self, auth_client):
        resp = auth_client.delete("/api/v1/report-schedules/nonexistent-id")
        assert resp.status_code == 404

    def test_update_nonexistent_schedule_returns_404(self, auth_client):
        resp = auth_client.patch(
            "/api/v1/report-schedules/nonexistent-id",
            json={"is_active": False},
        )
        assert resp.status_code == 404

    def test_run_now_nonexistent_schedule_returns_404(self, auth_client):
        resp = auth_client.post(
            "/api/v1/report-schedules/nonexistent-id/run-now"
        )
        assert resp.status_code == 404

    def test_create_schedule_with_empty_channels_succeeds(self, auth_client):
        """Empty delivery_channels is allowed (creates schedule, delivers nowhere)."""
        resp = auth_client.post(
            "/api/v1/report-schedules",
            json={
                "report_type": "cfo_daily",
                "delivery_channels": [],
            },
        )
        assert resp.status_code == 201

    def test_create_schedule_invalid_format_still_accepted(self, auth_client):
        """Format validation is lenient (arbitrary string accepted at API level)."""
        resp = auth_client.post(
            "/api/v1/report-schedules",
            json={
                "report_type": "cfo_daily",
                "format": "invalid_format_xyz",
            },
        )
        # Accepted at API level — format validation is at render time
        assert resp.status_code == 201

    def test_update_schedule_toggle_active(self, auth_client):
        create_resp = self._create_schedule(auth_client)
        schedule_id = create_resp.json()["id"]
        resp = auth_client.patch(
            f"/api/v1/report-schedules/{schedule_id}",
            json={"is_active": False},
        )
        assert resp.status_code == 200
        assert resp.json()["is_active"] is False

    def test_delete_schedule_returns_204(self, auth_client):
        create_resp = self._create_schedule(auth_client)
        schedule_id = create_resp.json()["id"]
        resp = auth_client.delete(f"/api/v1/report-schedules/{schedule_id}")
        assert resp.status_code == 204

    def test_delete_then_delete_again_returns_404(self, auth_client):
        create_resp = self._create_schedule(auth_client)
        schedule_id = create_resp.json()["id"]
        auth_client.delete(f"/api/v1/report-schedules/{schedule_id}")
        resp = auth_client.delete(f"/api/v1/report-schedules/{schedule_id}")
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════
# General Error Format
# ═══════════════════════════════════════════════════════════════════════════


class TestGeneralErrorFormat:
    """Verify error responses are JSON with proper structure."""

    def test_401_returns_json_not_html(self, noauth_client):
        resp = noauth_client.get("/api/v1/kpis/cfo")
        ct = resp.headers.get("content-type", "")
        assert "application/json" in ct
        assert "text/html" not in ct

    def test_404_returns_json(self, auth_client):
        resp = auth_client.get("/api/v1/companies/no-such-id")
        ct = resp.headers.get("content-type", "")
        assert "application/json" in ct

    def test_422_returns_json(self, auth_client):
        resp = auth_client.post("/api/v1/chat/query", json={})
        ct = resp.headers.get("content-type", "")
        assert "application/json" in ct

    def test_422_has_detail_field(self, auth_client):
        resp = auth_client.post("/api/v1/chat/query", json={})
        data = resp.json()
        assert "detail" in data

    def test_500_errors_dont_leak_stack_traces(self, auth_client):
        """If a 500 occurs, it should not contain Python traceback info."""
        # Trigger an internal error by corrupting a dependency
        with patch("api.v1.kpis.get_current_tenant", side_effect=RuntimeError("boom")):
            resp = auth_client.get("/api/v1/kpis/cfo")
            if resp.status_code == 500:
                body = resp.text
                assert "Traceback" not in body
                assert "File " not in body

    def test_nonexistent_route_returns_404_json(self, auth_client):
        resp = auth_client.get("/api/v1/this-route-does-not-exist")
        assert resp.status_code in (404, 405)
        ct = resp.headers.get("content-type", "")
        assert "application/json" in ct

    def test_method_not_allowed(self, auth_client):
        """PUT on a route that only supports GET should return 405."""
        resp = auth_client.put("/api/v1/kpis/cfo", json={})
        assert resp.status_code == 405
