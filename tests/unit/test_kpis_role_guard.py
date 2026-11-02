# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""KPI dashboards: per-page role guard, company-scoped cache, COO domains.

Before this change every authenticated tenant user could read every CxO
board (the ``kpis`` scope family was unmapped and the handlers had no role
check), the KPI cache was read tenant-wide even for a company-scoped request,
and the COO board missed agents registered under the ``ops`` domain.
"""

from __future__ import annotations

import re
import uuid
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from core.rbac import ROLE_SCOPES

TENANT = str(uuid.UUID("00000000-0000-4000-8000-0000000000a1"))
COMPANY = str(uuid.UUID("00000000-0000-4000-8000-0000000000c1"))
PAGES = ("ceo", "cfo", "chro", "cmo", "coo", "cbo")
REPO_ROOT = Path(__file__).resolve().parents[2]

# Who may open each page: admin everywhere, each CxO role on its own page.
EXPECTED_ALLOWED: dict[str, set[str]] = {
    "ceo": {"admin"},
    "cbo": {"admin"},
    "cfo": {"admin", "cfo"},
    "chro": {"admin", "chro"},
    "cmo": {"admin", "cmo"},
    "coo": {"admin", "coo"},
}
ROLES = ("admin", "cfo", "chro", "cmo", "coo", "auditor", "domain_lead", "analyst", "developer", "merchant")


@pytest.fixture(scope="module")
def app():
    from api.main import app as _app

    @asynccontextmanager
    async def _test_lifespan(_app_):
        yield

    _app.router.lifespan_context = _test_lifespan
    return _app


async def _fake_kpi_response(tenant_id: str, role: str, company_id: str) -> dict[str, Any]:
    return {"role": role, "company_id": company_id, "demo": True, "total_tasks_30d": 0}


async def _fake_cmo_response(tenant_id: str, company_id: str) -> dict[str, Any]:
    return {"role": "cmo", "company_id": company_id, "demo": True}


@contextmanager
def _client(app, *, role: str | None, scopes: list[str]):
    claims: dict[str, Any] = {
        "sub": "kpi-reader@example.test",
        "agenticorg:tenant_id": TENANT,
        "agenticorg:user_id": str(uuid.uuid4()),
        "grantex:scopes": scopes,
    }
    if role is not None:
        claims["role"] = role
        claims["agenticorg:domains"] = None if role == "admin" else ["finance"]

    async def _fake_validate(_token):
        return claims

    async def _noop(*_a, **_k):
        return None

    with (
        patch("auth.grantex_middleware.is_ip_blocked", return_value=False),
        patch("auth.grantex_middleware.record_auth_failure", side_effect=_noop),
        patch("auth.grantex_middleware.clear_auth_failures", side_effect=_noop),
        patch("auth.grantex_middleware.validate_token", side_effect=_fake_validate),
        patch("auth.grantex_middleware.check_user_session_state", AsyncMock(return_value=None)),
        patch("auth.grantex_middleware.extract_tenant_id", return_value=TENANT),
        patch("auth.grantex_middleware.extract_scopes", return_value=scopes),
        patch("core.auth_state.check_window_rate", AsyncMock(return_value=False)),
        patch("core.auth_state._get_redis", AsyncMock(return_value=None)),
        patch("core.auth_state._load_user_state_from_db", AsyncMock(side_effect=RuntimeError("no db in test"))),
        patch("api.v1.kpis._build_kpi_response", side_effect=_fake_kpi_response),
        patch("api.v1.kpis._build_cmo_kpi_response", side_effect=_fake_cmo_response),
        patch("api.v1.kpis._get_recent_escalations", AsyncMock(return_value=[])),
        patch("api.v1.kpis._get_tax_calendar", AsyncMock(return_value=[])),
        patch("api.v1.kpis._count_pending_approvals", AsyncMock(return_value=0)),
    ):
        with TestClient(app, raise_server_exceptions=False) as c:
            c.headers["Authorization"] = "Bearer fake-test-token"
            yield c


# ── Role matrix over HTTP ──────────────────────────────────────────────


@pytest.mark.parametrize("role", ROLES)
def test_role_matrix(app, role: str) -> None:
    with _client(app, role=role, scopes=list(ROLE_SCOPES[role])) as client:
        for page in PAGES:
            resp = client.get(f"/api/v1/kpis/{page}")
            expected = 200 if role in EXPECTED_ALLOWED[page] else 403
            assert resp.status_code == expected, (role, page, resp.status_code, resp.text)


def test_analyst_is_refused_the_cfo_board(app) -> None:
    with _client(app, role="analyst", scopes=list(ROLE_SCOPES["analyst"])) as client:
        assert client.get("/api/v1/kpis/cfo").status_code == 403


def test_cfo_reads_own_board_but_not_chro(app) -> None:
    with _client(app, role="cfo", scopes=list(ROLE_SCOPES["cfo"])) as client:
        own = client.get("/api/v1/kpis/cfo")
        assert own.status_code == 200, own.text
        assert own.json()["role"] == "cfo"
        other = client.get("/api/v1/kpis/chro")
        assert other.status_code == 403
        assert other.json()["detail"]["error"] == "kpis_role_forbidden"


def test_admin_api_key_without_role_passes(app) -> None:
    with _client(app, role=None, scopes=["agenticorg:admin"]) as client:
        for page in PAGES:
            assert client.get(f"/api/v1/kpis/{page}").status_code == 200, page


def test_kpis_read_without_a_role_fails_closed(app) -> None:
    # A machine credential holding kpis:read but no role claim is not a CxO.
    with _client(app, role=None, scopes=["kpis:read"]) as client:
        for page in PAGES:
            resp = client.get(f"/api/v1/kpis/{page}")
            assert resp.status_code == 403, page
            assert resp.json()["detail"]["error"] == "kpis_role_forbidden"


def test_cfo_with_stale_token_lacking_kpis_read_is_refused(app) -> None:
    # The global scope check runs before the page guard.
    scopes = [s for s in ROLE_SCOPES["cfo"] if s != "kpis:read"]
    with _client(app, role="cfo", scopes=scopes) as client:
        assert client.get("/api/v1/kpis/cfo").status_code == 403


def test_company_id_reaches_the_builder(app) -> None:
    with _client(app, role="cfo", scopes=list(ROLE_SCOPES["cfo"])) as client:
        resp = client.get("/api/v1/kpis/cfo", params={"company_id": COMPANY})
        assert resp.status_code == 200
        assert resp.json()["company_id"] == COMPANY


# ── Guard wiring and pins ──────────────────────────────────────────────


def test_every_kpi_route_carries_its_page_guard() -> None:
    from api.v1 import kpis

    routes = {r.path: r for r in kpis.router.routes}
    assert set(routes) == {f"/kpis/{p}" for p in PAGES}
    assert {p: set(r) for p, r in kpis.KPI_PAGE_ROLES.items()} == EXPECTED_ALLOWED
    for page in PAGES:
        guards = [d for d in routes[f"/kpis/{page}"].dependant.dependencies if d.call.__name__ == "checker"]
        assert len(guards) == 1, page


def test_page_roles_match_the_ui_routes() -> None:
    from api.v1 import kpis

    app_tsx = (REPO_ROOT / "ui" / "src" / "App.tsx").read_text(encoding="utf-8")
    for page in PAGES:
        match = re.search(
            rf'path="/dashboard/{page}"\s*element={{\s*<ProtectedRoute allowedRoles={{\[([^\]]*)\]}}',
            app_tsx,
        )
        assert match, page
        ui_roles = {r.strip().strip('"') for r in match.group(1).split(",") if r.strip()}
        assert ui_roles == set(kpis.KPI_PAGE_ROLES[page]), page


def test_scope_family_and_rate_class_are_pinned() -> None:
    from api.route_enforcement import (
        GRANTABLE_ROUTE_SCOPES,
        RATE_LIMIT_CLASSES,
        SCOPE_FAMILIES,
        required_scopes_for,
        unmapped_scope_families,
    )

    assert SCOPE_FAMILIES["kpis"] == ("kpis:read", "agenticorg:admin")
    assert RATE_LIMIT_CLASSES["business-metrics-read"] == (120, 60)
    assert required_scopes_for("kpis.executive.sensitive.cfo", "GET") == ("kpis:read",)
    assert "kpis" not in unmapped_scope_families(["kpis.executive.sensitive.ceo"])
    # The admin write side of the family never becomes grantable to an agent.
    assert "kpis:read" in GRANTABLE_ROUTE_SCOPES
    assert "agenticorg:admin" not in GRANTABLE_ROUTE_SCOPES


def test_kpis_read_is_held_by_the_cxo_roles_only() -> None:
    holders = {role for role, scopes in ROLE_SCOPES.items() if "kpis:read" in scopes}
    assert holders == {"cfo", "chro", "cmo", "coo"}


def test_every_kpi_route_rate_class_exists() -> None:
    from api.route_enforcement import RATE_LIMIT_CLASSES
    from api.route_metadata import ROUTE_METADATA_ATTR
    from api.v1 import kpis

    for route in kpis.router.routes:
        meta = getattr(route.endpoint, ROUTE_METADATA_ATTR)
        assert meta["rate_limit"] in RATE_LIMIT_CLASSES, route.path


# ── Company-scoped cache and COO domains ───────────────────────────────


class _RecordingCache:
    calls: list[tuple[Any, ...]] = []

    async def get_all_for_role(self, tenant_id, role, company_id=None):
        type(self).calls.append((tenant_id, role, company_id))
        return {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("company_id", "expected"),
    [(COMPANY, COMPANY), ("default", None), ("all", None), ("", None), ("not-a-uuid", None)],
)
async def test_cache_is_read_for_the_requested_company(company_id: str, expected: str | None) -> None:
    from api.v1 import kpis

    _RecordingCache.calls = []
    with (
        patch.object(kpis, "KPICache", _RecordingCache),
        patch.object(kpis, "_compute_basic_metrics", AsyncMock(return_value={"total_tasks_30d": 0})),
    ):
        await kpis._build_kpi_response(TENANT, "cfo", company_id)
    assert _RecordingCache.calls == [(TENANT, "cfo", expected)]


@pytest.mark.asyncio
async def test_company_cache_hit_is_returned() -> None:
    from api.v1 import kpis

    class _HitCache:
        async def get_all_for_role(self, tenant_id, role, company_id=None):
            assert company_id == COMPANY
            return {"cash_runway_months": {"value": 7, "stale": False}}

    compute = AsyncMock()
    with patch.object(kpis, "KPICache", _HitCache), patch.object(kpis, "_compute_basic_metrics", compute):
        out = await kpis._build_kpi_response(TENANT, "cfo", COMPANY)
    assert out["cash_runway_months"] == 7
    assert out["demo"] is False
    compute.assert_not_awaited()


@pytest.mark.asyncio
async def test_coo_board_aggregates_ops_and_operations() -> None:
    from api.v1 import kpis

    compute = AsyncMock(return_value={"total_tasks_30d": 0})
    with patch.object(kpis, "KPICache", _RecordingCache), patch.object(kpis, "_compute_basic_metrics", compute):
        await kpis._build_kpi_response(TENANT, "coo", "default")
    domains = compute.await_args.args[2]
    assert "ops" in domains and "operations" in domains


def test_role_domain_maps_cover_the_rbac_domain() -> None:
    # The KPI map may add related domains, but must include the RBAC domain
    # each CxO role is bound to (the drift that hid ops agents from the COO).
    from api.v1 import kpis
    from core.rbac import ROLE_DOMAIN_MAP as RBAC_DOMAINS

    for role in ("cfo", "chro", "cmo", "coo"):
        for domain in RBAC_DOMAINS[role] or []:
            assert domain in kpis.ROLE_DOMAIN_MAP[role], (role, domain)
    assert "ops" in kpis.ROLE_DOMAIN_MAP["ceo"]
