# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""The partner dashboard is enforced on the server for the roles its page admits (admin, cfo, coo, auditor)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api.v1 import companies


def _request(role: str, scopes: list[str] | None = None) -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace(claims={"role": role}, scopes=scopes or []))


@pytest.mark.parametrize("role", ["admin", "cfo", "coo", "auditor"])
def test_the_partner_dashboard_roles_are_admitted(role: str) -> None:
    assert companies._partner_dashboard_reader(_request(role)) is None


@pytest.mark.parametrize("role", ["chro", "cmo", "analyst", "domain_lead", "developer", "merchant", ""])
def test_other_roles_are_refused(role: str) -> None:
    with pytest.raises(HTTPException) as refused:
        companies._partner_dashboard_reader(_request(role))
    assert refused.value.status_code == 403
    assert refused.value.detail["error"] == "partner_dashboard_forbidden"


def test_an_admin_scope_is_admitted_whatever_the_role_claim() -> None:
    assert companies._partner_dashboard_reader(_request("cmo", ["agenticorg:admin"])) is None


def test_the_route_carries_the_guard() -> None:
    route = next(r for r in companies.router.routes if getattr(r, "path", "").endswith("/partner-dashboard"))
    assert any(d.call is companies._partner_dashboard_reader for d in route.dependant.dependencies)
