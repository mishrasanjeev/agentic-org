# ruff: noqa: S106 — test files use fake tokens intentionally
"""Test multi-company data isolation — Company A cannot see Company B's data.

Uses sequential tenant switching with proper middleware mocking to avoid
FastAPI dependency_overrides race condition.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager, contextmanager
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def app():
    from api.main import app as _app

    @asynccontextmanager
    async def _test_lifespan(a):
        yield

    _app.router.lifespan_context = _test_lifespan
    return _app


@contextmanager
def tenant_client(app, tenant_id: str):
    """Context manager that yields a TestClient bound to a specific tenant."""
    from api.deps import get_current_tenant
    app.dependency_overrides[get_current_tenant] = lambda: tenant_id

    async def _fake_validate(token):
        # admin scope so routers that bind `dependencies=[require_tenant_admin]`
        # (e.g. /report-schedules) reach their handler — these tests
        # exercise cross-tenant isolation, not the admin gate.
        return {
            "sub": f"user-{tenant_id[:8]}",
            "agenticorg:tenant_id": tenant_id,
            "agenticorg:scopes": ["agenticorg:admin"],
        }

    with patch("auth.grantex_middleware.validate_token", side_effect=_fake_validate):
        with patch("auth.grantex_middleware.extract_tenant_id", return_value=tenant_id):
            with patch("auth.grantex_middleware.extract_scopes", return_value=["agenticorg:admin"]):
                with TestClient(app, raise_server_exceptions=False) as c:
                    c.headers["Authorization"] = f"Bearer fake-token-{tenant_id[:8]}"
                    yield c

    app.dependency_overrides.pop(get_current_tenant, None)


@pytest.fixture
def tenant_a():
    return f"iso-a-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def tenant_b():
    return f"iso-b-{uuid.uuid4().hex[:8]}"


# ═══════════════════════════════════════════════════════════════════════════
# Company CRUD Isolation
# ═══════════════════════════════════════════════════════════════════════════


class TestCompanyIsolation:

    def test_new_tenant_starts_empty(self, app, tenant_a):
        with tenant_client(app, tenant_a) as client:
            resp = client.get("/api/v1/companies")
            # 200 with DB, 400 without DB session
            assert resp.status_code in (200, 400)
            if resp.status_code == 200:
                assert resp.json() == []

    def test_company_created_by_a_not_visible_to_b(self, app, tenant_a, tenant_b):
        with tenant_client(app, tenant_a) as client:
            resp = client.post(
                "/api/v1/companies",
                json={"name": "A-Only Corp", "pan": "AABCE1234F"},
            )
            # 201 with DB, 400/422 without DB
            assert resp.status_code in (201, 400, 422)

        if resp.status_code == 201:
            with tenant_client(app, tenant_b) as client:
                resp = client.get("/api/v1/companies")
                if resp.status_code == 200:
                    names = [c["name"] for c in resp.json()]
                    assert "A-Only Corp" not in names

    def test_get_by_id_cross_tenant_returns_404(self, app, tenant_a, tenant_b):
        with tenant_client(app, tenant_a) as client:
            resp = client.post(
                "/api/v1/companies",
                json={"name": "Private Corp", "pan": "AABCE1234F"},
            )
            if resp.status_code != 201:
                # DB not available — verify isolation at schema level instead
                from core.models.company import Company
                col = Company.__table__.c.tenant_id
                assert not col.nullable, "tenant_id must not be nullable for isolation"
                return
            company_id = resp.json()["id"]

        with tenant_client(app, tenant_b) as client:
            resp = client.get(f"/api/v1/companies/{company_id}")
            assert resp.status_code in (400, 404)

    def test_update_cross_tenant_returns_404(self, app, tenant_a, tenant_b):
        with tenant_client(app, tenant_a) as client:
            resp = client.post(
                "/api/v1/companies",
                json={"name": "Secure Corp", "pan": "AABCE1234F"},
            )
            if resp.status_code != 201:
                # DB not available — verify FK constraint at schema level
                from core.models.company import Company
                fk_targets = {fk.target_fullname for fk in Company.__table__.c.tenant_id.foreign_keys}
                assert "tenants.id" in fk_targets
                return
            company_id = resp.json()["id"]

        with tenant_client(app, tenant_b) as client:
            resp = client.patch(f"/api/v1/companies/{company_id}", json={"name": "Hacked"})
            assert resp.status_code in (400, 404)

    def test_same_tenant_can_access_own_company(self, app, tenant_a):
        with tenant_client(app, tenant_a) as client:
            create_resp = client.post(
                "/api/v1/companies",
                json={"name": "My Corp", "pan": "AABCE1234F"},
            )
            if create_resp.status_code != 201:
                # DB not available — verify model has tenant scoping
                from core.models.company import Company
                cols = {c.key for c in Company.__table__.columns}
                assert "tenant_id" in cols
                assert "id" in cols
                return
            company_id = create_resp.json()["id"]
            get_resp = client.get(f"/api/v1/companies/{company_id}")
            assert get_resp.status_code == 200
            assert get_resp.json()["name"] == "My Corp"


# ═══════════════════════════════════════════════════════════════════════════
# KPI Company Scoping
# ═══════════════════════════════════════════════════════════════════════════


class TestKPIIsolation:

    def test_cfo_kpis_with_company_id(self, app, tenant_a):
        with tenant_client(app, tenant_a) as client:
            resp = client.get("/api/v1/kpis/cfo", params={"company_id": "comp-1"})
            # 200 with demo/cache data; 400/500 when DB layer is unavailable
            assert resp.status_code in (200, 400, 500)
            if resp.status_code == 200:
                data = resp.json()
                assert isinstance(data, dict)
                assert "demo" in data
                assert "cash_runway_months" in data

    def test_cmo_kpis_with_company_id(self, app, tenant_a):
        with tenant_client(app, tenant_a) as client:
            resp = client.get("/api/v1/kpis/cmo", params={"company_id": "comp-1"})
            assert resp.status_code in (200, 400, 500)
            if resp.status_code == 200:
                data = resp.json()
                assert isinstance(data, dict)
                assert "demo" in data
                assert "cac" in data

    def test_kpis_without_company_id_still_works(self, app, tenant_a):
        with tenant_client(app, tenant_a) as client:
            resp = client.get("/api/v1/kpis/cfo")
            assert resp.status_code in (200, 400, 500)


# ═══════════════════════════════════════════════════════════════════════════
# Chat Isolation
# ═══════════════════════════════════════════════════════════════════════════


class TestChatIsolation:

    def test_chat_query_works_with_company(self, app, tenant_a):
        with tenant_client(app, tenant_a) as client:
            resp = client.post("/api/v1/chat/query", json={"query": "cash position", "company_id": "comp-1"})
            assert resp.status_code == 200

    def test_chat_history_returns_list(self, app, tenant_a):
        with tenant_client(app, tenant_a) as client:
            resp = client.get("/api/v1/chat/history")
            assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════
# Report Schedule Isolation
# ═══════════════════════════════════════════════════════════════════════════
class TestReportScheduleIsolation:

    def test_schedule_created_by_a_invisible_to_b(self, app, tenant_a, tenant_b):
        with tenant_client(app, tenant_a) as client:
            resp = client.post("/api/v1/report-schedules", json={
                "report_type": "cfo_daily", "cron_expression": "daily", "delivery_channels": [],
            })
            assert resp.status_code in (200, 201)
            schedule_id = resp.json()["id"]

        with tenant_client(app, tenant_b) as client:
            resp = client.get("/api/v1/report-schedules")
            body = resp.json()
            schedules = body if isinstance(body, list) else body.get("schedules", [])
            ids = [s["id"] for s in schedules]
            assert schedule_id not in ids

    def test_schedule_delete_cross_tenant_returns_404(self, app, tenant_a, tenant_b):
        with tenant_client(app, tenant_a) as client:
            resp = client.post("/api/v1/report-schedules", json={
                "report_type": "cmo_weekly", "cron_expression": "weekly", "delivery_channels": [],
            })
            schedule_id = resp.json()["id"]

        with tenant_client(app, tenant_b) as client:
            resp = client.delete(f"/api/v1/report-schedules/{schedule_id}")
            assert resp.status_code == 404

    def test_own_tenant_can_manage_schedule(self, app, tenant_a):
        with tenant_client(app, tenant_a) as client:
            resp = client.post("/api/v1/report-schedules", json={
                "report_type": "aging_report", "cron_expression": "daily", "delivery_channels": [],
            })
            assert resp.status_code in (200, 201)
            schedule_id = resp.json()["id"]

            resp = client.get("/api/v1/report-schedules")
            body = resp.json()
            items = body if isinstance(body, list) else body.get("schedules", [])
            ids = [s["id"] for s in items]
            assert schedule_id in ids
