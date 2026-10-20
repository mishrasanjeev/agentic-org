# SPDX-License-Identifier: Apache-2.0
"""Routes that declare ``route_meta(auth_required=False)`` are reachable without credentials.

Bug (found during the OACP Phase 3 review, fixed with G47): the public buyer-safe seller
pages and catalog reads under ``/api/v1/public/commerce/`` (``api/v1/commerce_public.py``)
declare ``auth_required=False`` and are documented as public in
``docs/commerce-agent-bridges-guide.md``, but ``GrantexAuthMiddleware`` did not exempt
them, so every anonymous request was answered ``401 Missing session cookie or
Authorization header`` before the handler ran. The sibling sweep found the same class on
``GET /api/v1/org/invite-info`` (the accept-invite screen's preview, called before login
by ``ui/src/pages/InviteAccept.tsx``).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from api.route_metadata import ROUTE_METADATA_ATTR
from auth.grantex_middleware import GrantexAuthMiddleware
from tests.app_routes import HttpRoute, http_routes

TENANT = "11111111-1111-4111-8111-111111111111"


def _public_routes() -> list[HttpRoute]:
    from api.main import app

    out = []
    for route in http_routes(app):
        meta = getattr(route.endpoint, ROUTE_METADATA_ATTR, None)
        if isinstance(meta, dict) and meta.get("auth_required") is False:
            out.append(route)
    # The walk must see the public commerce reads; an empty walk would pass vacuously.
    assert any(route.path.startswith("/api/v1/public/commerce/") for route in out)
    return out


def _middleware_passes(path: str) -> bool:
    return path in GrantexAuthMiddleware.EXEMPT_PATHS or path.startswith(GrantexAuthMiddleware.EXEMPT_PREFIXES)


def test_every_route_declared_public_is_exempt_in_the_auth_middleware() -> None:
    unreachable = sorted(
        f"{sorted(route.methods)} {route.path}" for route in _public_routes() if not _middleware_passes(route.path)
    )
    assert unreachable == []


def test_no_authenticated_route_sits_under_the_public_commerce_prefix() -> None:
    from api.main import app

    checked = 0
    for route in http_routes(app):
        if route.path.startswith("/api/v1/public/commerce/"):
            meta = getattr(route.endpoint, ROUTE_METADATA_ATTR)
            assert meta["auth_required"] is False, route.path
            assert route.methods <= {"GET", "HEAD"}, route.path
            checked += 1
    assert checked > 0


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    from api.main import app
    from api.v1 import commerce_public

    async def ready_snapshot(**_: Any) -> dict[str, Any]:
        return {"status": "public_catalog_ready", "source_label": "s", "freshness_label": "f", "products": []}

    monkeypatch.setattr(commerce_public, "_load_public_catalog_snapshot", ready_snapshot)
    # The public-read rate window lives in Redis; the tests answer it locally.
    monkeypatch.setattr("core.auth_state.check_window_rate", AsyncMock(return_value=False))
    return TestClient(app)


def test_anonymous_catalog_read_reaches_the_handler(client: TestClient) -> None:
    # The tester's step: an anonymous GET of the buyer-safe catalog JSON.
    response = client.get(
        "/api/v1/public/commerce/sellers/merchant.example/catalog.json", params={"tenant_id": TENANT}
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "public_catalog_ready"


def test_anonymous_invite_preview_reaches_the_handler(client: TestClient) -> None:
    response = client.get("/api/v1/org/invite-info")
    # The handler's own answer (no code given), not the middleware's 401.
    assert response.status_code == 400
    assert response.json()["detail"] == "Missing invite code"


def test_public_commerce_still_refuses_a_bad_tenant_id(client: TestClient) -> None:
    response = client.get("/api/v1/public/commerce/sellers/merchant.example/catalog.json")
    assert response.status_code == 422
