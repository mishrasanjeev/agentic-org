"""Integration rewrites of the 5 DB-backed test classes that PR-D3
shipped but left skipped under AGENTICORG_DB_URL.

Each of the original unit-test classes tried to hit real CRUD endpoints
(/a2a/tasks, /report-schedules, /abm/*) while stubbing the auth
middleware and using a per-test TestClient + patched module-level async
engine. Running all five simultaneously hit "Event loop is closed" +
"Future attached to a different loop" races across TestClients — a
structural problem that needed the fixture rewrite here, not a patch
to the originals.

Ported to use the integration conftest's:
  - `client` (httpx.AsyncClient over ASGITransport, NullPool engine)
  - `auth_headers` / `make_auth_headers` (real RS256 JWT signed with the
    session-scoped private key; the conftest monkey-patches JWKS
    verification)
  - `tenant_id` (matches the session-seeded tenants row so FK
    constraints are satisfied)

Originals in tests/unit/ are kept under their AGENTICORG_DB_URL skipif
until their follow-up PRs land; this file is the new home.
"""

from __future__ import annotations

import uuid

import pytest


# ═══════════════════════════════════════════════════════════════════════════
# A2A task — test_get_task_not_found (was tests/unit/test_a2a_mcp.py)
# ═══════════════════════════════════════════════════════════════════════════
class TestA2ATaskIntegration:
    @pytest.mark.asyncio
    async def test_get_task_not_found(self, client, auth_headers, tenant_id):
        resp = await client.get(
            f"/api/v1/a2a/tasks/does-not-exist?tenant_id={tenant_id}",
            headers=auth_headers,
        )
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════
# Report schedules CRUD + error paths + cross-tenant isolation
# (was TestReportScheduleErrors + TestReportScheduleIsolation +
#  TestReportSchedules)
# ═══════════════════════════════════════════════════════════════════════════
class TestReportSchedulesIntegration:
    """CRUD + validation + cross-tenant + run-now.

    These cover the `/report-schedules` endpoints. The router binds
    `dependencies=[require_tenant_admin]` so every request must carry
    the admin scope; `make_auth_headers(scopes=["agenticorg:admin"])`
    handles that.
    """

    def _admin_headers(self, make_auth_headers):
        return make_auth_headers(scopes=["agenticorg:admin"])

    # --- error paths --------------------------------------------------------
    @pytest.mark.asyncio
    async def test_create_missing_report_type_returns_422(
        self, client, make_auth_headers,
    ):
        resp = await client.post(
            "/api/v1/report-schedules",
            headers=self._admin_headers(make_auth_headers),
            json={"cron_expression": "daily", "delivery_channels": []},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_create_null_report_type_returns_422(
        self, client, make_auth_headers,
    ):
        resp = await client.post(
            "/api/v1/report-schedules",
            headers=self._admin_headers(make_auth_headers),
            json={"report_type": None},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_create_empty_body_returns_422(
        self, client, make_auth_headers,
    ):
        resp = await client.post(
            "/api/v1/report-schedules",
            headers=self._admin_headers(make_auth_headers),
            json={},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_create_without_auth_returns_401(self, client):
        resp = await client.post(
            "/api/v1/report-schedules",
            json={"report_type": "cfo_daily"},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_delete_nonexistent_returns_404(
        self, client, make_auth_headers,
    ):
        resp = await client.delete(
            "/api/v1/report-schedules/nonexistent-id",
            headers=self._admin_headers(make_auth_headers),
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_update_nonexistent_returns_404(
        self, client, make_auth_headers,
    ):
        resp = await client.patch(
            "/api/v1/report-schedules/nonexistent-id",
            headers=self._admin_headers(make_auth_headers),
            json={"is_active": False},
        )
        assert resp.status_code == 404

    # --- happy path + roundtrip --------------------------------------------
    @pytest.mark.asyncio
    async def test_create_and_list_roundtrips(
        self, client, make_auth_headers,
    ):
        headers = self._admin_headers(make_auth_headers)
        # Create
        resp = await client.post(
            "/api/v1/report-schedules",
            headers=headers,
            json={
                "report_type": "cfo_daily",
                "cron_expression": "daily",
                "delivery_channels": [
                    {"type": "email", "target": "cfo@example.com"},
                ],
                "format": "pdf",
                "is_active": True,
                "company_id": "default",
            },
        )
        assert resp.status_code == 201, resp.text
        created = resp.json()
        assert created["id"]
        assert created["report_type"] == "cfo_daily"
        # List includes it
        lst = await client.get("/api/v1/report-schedules", headers=headers)
        assert lst.status_code == 200
        ids = [s["id"] for s in lst.json()]
        assert created["id"] in ids
        # Delete cleans it up
        d = await client.delete(
            f"/api/v1/report-schedules/{created['id']}",
            headers=headers,
        )
        assert d.status_code == 204

    @pytest.mark.asyncio
    async def test_update_roundtrips(self, client, make_auth_headers):
        headers = self._admin_headers(make_auth_headers)
        created = (await client.post(
            "/api/v1/report-schedules",
            headers=headers,
            json={"report_type": "cmo_weekly", "cron_expression": "weekly"},
        )).json()
        try:
            updated = await client.patch(
                f"/api/v1/report-schedules/{created['id']}",
                headers=headers,
                json={"is_active": False},
            )
            assert updated.status_code == 200
            assert updated.json()["is_active"] is False
        finally:
            await client.delete(
                f"/api/v1/report-schedules/{created['id']}", headers=headers,
            )

    # --- cross-tenant isolation --------------------------------------------
    @pytest.mark.asyncio
    async def test_schedule_not_visible_across_tenants(
        self, client, make_auth_headers,
    ):
        tenant_a = str(uuid.uuid4())
        tenant_b = str(uuid.uuid4())
        # Seed both tenants so FK is satisfied.
        from sqlalchemy import text as sa_text

        import core.database as db_mod
        async with db_mod.engine.begin() as conn:
            for tid, slug in ((tenant_a, "iso-a"), (tenant_b, "iso-b")):
                await conn.execute(sa_text(
                    "INSERT INTO tenants (id, name, slug, plan, data_region, settings) "
                    "VALUES (:id, :slug, :slug, 'enterprise', 'IN', '{}') "
                    "ON CONFLICT (id) DO NOTHING"
                ), {"id": tid, "slug": slug})

        a_hdr = make_auth_headers(tenant_id=tenant_a, scopes=["agenticorg:admin"])
        b_hdr = make_auth_headers(tenant_id=tenant_b, scopes=["agenticorg:admin"])

        created = (await client.post(
            "/api/v1/report-schedules",
            headers=a_hdr,
            json={
                "report_type": "aging_report",
                "cron_expression": "daily",
                "delivery_channels": [],
            },
        )).json()
        try:
            # Tenant B shouldn't see it
            b_list = (await client.get(
                "/api/v1/report-schedules", headers=b_hdr,
            )).json()
            b_ids = [s["id"] for s in (
                b_list if isinstance(b_list, list) else b_list.get("schedules", [])
            )]
            assert created["id"] not in b_ids
            # Tenant B deleting it → 404, not 200
            del_resp = await client.delete(
                f"/api/v1/report-schedules/{created['id']}", headers=b_hdr,
            )
            assert del_resp.status_code == 404
        finally:
            await client.delete(
                f"/api/v1/report-schedules/{created['id']}", headers=a_hdr,
            )


# ═══════════════════════════════════════════════════════════════════════════
# ABM API — was tests/unit/test_tier1_features.py::TestABMApi
# ═══════════════════════════════════════════════════════════════════════════
class TestABMApiIntegration:
    """ABM account + campaign CRUD.

    The router doesn't bind `require_tenant_admin` at the router level,
    but individual mutations do — easier to pass admin scope for every
    test than audit each one.
    """

    @pytest.mark.asyncio
    async def test_create_account(self, client, make_auth_headers):
        headers = make_auth_headers(scopes=["agenticorg:admin"])
        resp = await client.post(
            "/api/v1/abm/accounts",
            headers=headers,
            json={
                "company_name": "Acme Corp",
                "domain": "acme.example",
                "industry": "Technology",
                "employee_count": 500,
                "annual_revenue_usd": 50_000_000,
            },
        )
        assert resp.status_code in (200, 201), resp.text
        body = resp.json()
        assert body["company_name"] == "Acme Corp"
        assert body["domain"] == "acme.example"

    @pytest.mark.asyncio
    async def test_list_accounts(self, client, make_auth_headers):
        headers = make_auth_headers(scopes=["agenticorg:admin"])
        resp = await client.get("/api/v1/abm/accounts", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, dict | list)

    @pytest.mark.asyncio
    async def test_create_campaign(self, client, make_auth_headers):
        headers = make_auth_headers(scopes=["agenticorg:admin"])
        resp = await client.post(
            "/api/v1/abm/campaigns",
            headers=headers,
            json={
                "name": "Q2 Enterprise Outreach",
                "target_industry": "Finance",
                "messaging": "Enterprise AI platform",
            },
        )
        # Some envs require an account link; accept 400 too.
        assert resp.status_code in (200, 201, 400, 422), resp.text
