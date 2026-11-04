# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Insights writes refused every role but the administrator (found 4 November 2026).

The ``insights`` route family mapped its write side to ``agenticorg:admin``,
and every other role holds only ``insights:read``. Route enforcement runs in
``enforce`` mode in production, so every non-GET Insights route answered 403
"Missing scope: agenticorg:admin" to the roles its handler admits: a CFO could
not acknowledge an alert, save a view or start a background export (the finance
pack, a view PDF), and an auditor could not sign a period evidence pack.

The family's write side is now ``insights:write``, held by every Insights role
except the auditor, who stays read-only (no ``:write`` scope). POSTs that change
no organisation state (a background export of figures the caller may read, the
caller's own saved views and last visit, an auditor's signature on a pack) are
the ``insights_personal`` family and need ``insights:read``. Each handler still
decides who may act: value and BI changes need an active human administrator
(and ``agenticorg:admin`` at the route), acknowledging needs the alert's owner.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from api.route_enforcement import SCOPE_FAMILIES, _expand_granted, enforce_route_metadata, required_scopes_for
from api.route_metadata import ROUTE_METADATA_ATTR, route_meta
from api.v1 import insights_alerts, insights_exports, insights_saved_views
from core.rbac import ROLE_SCOPES

READERS = sorted(role for role, scopes in ROLE_SCOPES.items() if "insights:read" in scopes)
WRITERS = sorted(set(READERS) - {"auditor"})

# The roles each handler admits (the handler checks; route enforcement must let them reach it).
HANDLER_ROLES: dict[tuple[str, str], set[str]] = {
    ("POST", "/api/v1/insights/alerts/{alert_id}/ack"): {"cfo", "chro", "cmo", "coo"},
    ("POST", "/api/v1/insights/exports"): set(READERS),
    ("POST", "/api/v1/insights/exports/{export_id}/sign"): {"auditor"},
    # Acknowledging is a change to the organisation's alert: insights:write (the owner roles hold it).
    ("POST", "/api/v1/insights/saved-views"): set(READERS),
    ("PATCH", "/api/v1/insights/saved-views/{view_id}"): set(READERS),
    ("DELETE", "/api/v1/insights/saved-views/{view_id}"): set(READERS),
    ("POST", "/api/v1/insights/changes/seen"): set(READERS),
}


def _routers() -> list[Any]:
    """Every Insights router as api/main.py mounts it (under /api/v1); read directly, not from a shared app that
    other tests may have rebuilt."""
    from api.v1 import insights, insights_bi, insights_explore, insights_value

    return [
        insights.router,
        insights_alerts.router,
        insights_bi.router,
        insights_explore.router,
        insights_exports.router,
        insights_saved_views.router,
        insights_value.router,
    ]


@pytest.fixture(scope="module")
def app() -> list[Any]:
    return _routers()


def _insights_writes(routers: list[Any]) -> dict[tuple[str, str], dict[str, Any]]:
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for router in routers:
        for route in router.routes:
            if not isinstance(route, APIRoute):
                continue
            meta = getattr(route.endpoint, ROUTE_METADATA_ATTR, None)
            if not isinstance(meta, dict) or not str(meta.get("scope") or "").startswith("insights"):
                continue
            for method in route.methods - {"GET", "HEAD", "OPTIONS"}:
                out[(method, "/api/v1" + route.path)] = meta
    return out


def test_every_insights_router_is_mounted_under_api_v1() -> None:
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2] / "api" / "main.py").read_text(encoding="utf-8")
    for name in ("insights", "insights_alerts", "insights_bi", "insights_explore", "insights_exports",
                 "insights_saved_views", "insights_value"):
        assert f'app.include_router({name}.router, prefix="/api/v1")' in source, name


def test_every_role_a_handler_admits_passes_route_enforcement(app) -> None:
    writes = _insights_writes(app)
    assert set(HANDLER_ROLES) <= set(writes), sorted(set(HANDLER_ROLES) - set(writes))
    for key, roles in HANDLER_ROLES.items():
        required = set(required_scopes_for(writes[key]["scope"], key[0]))
        for role in roles:
            assert required & _expand_granted(ROLE_SCOPES[role]), (key, role, required)


def test_the_insights_family_writes_with_insights_write_and_auditors_stay_read_only() -> None:
    assert SCOPE_FAMILIES["insights"] == ("insights:read", "insights:write")
    assert SCOPE_FAMILIES["insights_personal"] == ("insights:read", "insights:read")
    assert SCOPE_FAMILIES["insights_admin"] == ("insights:read", "agenticorg:admin")
    for role in WRITERS:
        assert "insights:write" in ROLE_SCOPES[role], role
    assert ROLE_SCOPES["auditor"] == ["audit:read", "insights:read"]
    assert ROLE_SCOPES["admin"] == ["agenticorg:admin"]


def _client(scopes: list[str]) -> TestClient:
    app = FastAPI(dependencies=[Depends(enforce_route_metadata)])

    @app.middleware("http")
    async def fake_auth(request: Request, call_next):
        request.state.auth_mode = "legacy"
        request.state.scopes = list(scopes)
        request.state.tenant_id = "t-1"
        return await call_next(request)

    @app.post("/ack")
    @route_meta(**getattr(insights_alerts.ack_alert, ROUTE_METADATA_ATTR))
    async def ack():
        return {"ok": True}

    @app.post("/sign")
    @route_meta(**getattr(insights_exports.sign_export, ROUTE_METADATA_ATTR))
    async def sign():
        return {"ok": True}

    @app.post("/export")
    @route_meta(**getattr(insights_exports.create_export, ROUTE_METADATA_ATTR))
    async def export():
        return {"ok": True}

    @app.post("/save")
    @route_meta(**getattr(insights_saved_views.create_saved_view, ROUTE_METADATA_ATTR))
    async def save():
        return {"ok": True}

    return TestClient(app)


@pytest.mark.parametrize(
    ("role", "path"),
    [
        ("cfo", "/ack"),
        ("cfo", "/export"),
        ("cfo", "/save"),
        ("auditor", "/sign"),
        ("auditor", "/export"),
        ("auditor", "/save"),
        ("analyst", "/save"),
    ],
)
def test_a_non_admin_role_reaches_the_insights_write_handler(role: str, path: str) -> None:
    from api import route_enforcement

    with (
        patch.object(route_enforcement, "_check_rate_limit", AsyncMock(return_value=None)),
        patch.object(route_enforcement.settings, "route_enforcement_mode", "enforce"),
    ):
        response = _client(ROLE_SCOPES[role]).post(path)
    assert response.status_code == 200, response.text


def test_a_read_only_credential_cannot_acknowledge_an_alert() -> None:
    from api import route_enforcement

    with (
        patch.object(route_enforcement, "_check_rate_limit", AsyncMock(return_value=None)),
        patch.object(route_enforcement.settings, "route_enforcement_mode", "enforce"),
    ):
        response = _client(ROLE_SCOPES["auditor"]).post("/ack")
        nothing = _client(["audit:read"]).post("/export")
    assert response.status_code == 403 and "insights:write" in response.text
    assert nothing.status_code == 403 and "insights:read" in nothing.text


def test_value_and_bi_changes_stay_the_administrators_at_the_route(app) -> None:
    writes = _insights_writes(app)
    admin_only = {key: meta for key, meta in writes.items() if "/bi-access" in key[1] or "/value/" in key[1]}
    assert len(admin_only) == 8, sorted(admin_only)
    for key, meta in admin_only.items():
        assert meta["scope"].startswith("insights_admin."), key
        assert required_scopes_for(meta["scope"], key[0]) == ("agenticorg:admin",), key
        for role in READERS:
            required = set(required_scopes_for(meta["scope"], key[0]))
            assert not (required & _expand_granted(ROLE_SCOPES[role])), (key, role)
