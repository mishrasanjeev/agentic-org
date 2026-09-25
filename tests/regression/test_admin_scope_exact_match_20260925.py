# SPDX-License-Identifier: Apache-2.0
"""Only the exact ``agenticorg:admin`` scope is admin (FINDINGS A-69).

Six admin checks matched ``startswith("agenticorg:admin")``. Registration gives
every agent ``agenticorg:{domain}:read`` and an agent's domain is free text, so
an agent in a domain such as ``administration`` carried a scope those checks
read as admin: its grant token passed ``require_scope`` - and with it
``require_tenant_admin``, which guards API-key creation - and set
``Caller.is_admin``. Free-form API-key scopes had the same effect.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from core.rbac import ADMIN_SCOPE, has_admin_scope

LOOKALIKES = [
    "agenticorg:administration:read",
    "agenticorg:admin:read",
    "agenticorg:admin:full",
    "agenticorg:admins",
    "agenticorg:admin ",
]
REPO = Path(__file__).resolve().parents[2]


def _request(scopes: list[str], auth_mode: str = "grantex", **claims: Any) -> SimpleNamespace:
    return SimpleNamespace(
        state=SimpleNamespace(
            scopes=scopes,
            auth_mode=auth_mode,
            claims={"sub": "agent:x", "agenticorg:agent_id": "a1", "grantex:scopes": scopes, **claims},
            tenant_id="t-1",
        )
    )


@pytest.mark.parametrize("scope", LOOKALIKES)
def test_a_scope_that_only_starts_like_admin_is_not_admin(scope: str) -> None:
    assert has_admin_scope([scope]) is False


def test_the_admin_scope_is_admin() -> None:
    assert ADMIN_SCOPE == "agenticorg:admin"
    assert has_admin_scope(["tool:mock:read", "agenticorg:admin"]) is True
    assert has_admin_scope([]) is False
    assert has_admin_scope([None, 3]) is False  # type: ignore[list-item]


def test_an_agent_in_an_admin_looking_domain_registers_no_admin_scope() -> None:
    from auth.grantex_registration import _tools_to_scopes

    scopes = _tools_to_scopes([], "administration")
    assert "agenticorg:administration:read" in scopes
    assert has_admin_scope(scopes) is False


@pytest.mark.parametrize("scope", LOOKALIKES)
def test_require_scope_refuses_a_lookalike(scope: str) -> None:
    from api.deps import require_scope

    checker = require_scope("agenticorg:admin").dependency
    with pytest.raises(HTTPException) as denied:
        checker(_request([scope]))
    assert denied.value.status_code == 403
    checker(_request([ADMIN_SCOPE]))


@pytest.mark.parametrize("scope", LOOKALIKES)
def test_caller_is_not_admin_with_a_lookalike(scope: str) -> None:
    from core.ownership import caller_from_request

    assert caller_from_request(_request([scope])).is_admin is False
    assert caller_from_request(_request([ADMIN_SCOPE])).is_admin is True


@pytest.mark.parametrize("scope", LOOKALIKES)
def test_a_role_less_session_with_a_lookalike_is_not_unrestricted(scope: str) -> None:
    from api.deps import get_user_domains

    request = _request([scope], auth_mode="legacy")
    request.state.claims = {"sub": "someone@example.com"}
    assert get_user_domains(request) is not None
    request.state.scopes = [ADMIN_SCOPE]
    assert get_user_domains(request) is None


@pytest.mark.parametrize("scope", LOOKALIKES)
def test_report_schedules_admin_needs_the_exact_scope(scope: str) -> None:
    from api.v1.report_schedules import _is_admin_claims

    assert _is_admin_claims({"grantex:scopes": [scope]}) is False
    assert _is_admin_claims({"grantex:scopes": [ADMIN_SCOPE]}) is True


@pytest.mark.parametrize("scope", LOOKALIKES)
def test_merchant_config_write_refuses_a_lookalike(scope: str) -> None:
    from api.v1.commerce_runtime import require_merchant_commerce_config_write

    with pytest.raises(HTTPException):
        require_merchant_commerce_config_write(_request([scope]))
    require_merchant_commerce_config_write(_request([ADMIN_SCOPE]))


def _prefix_admin_checks(path: Path) -> list[int]:
    """Lines calling ``.startswith``/``startsWith`` with a literal beginning ``agenticorg:admin``."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    lines = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "startswith"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and node.args[0].value.startswith("agenticorg:admin")
        ):
            lines.append(node.lineno)
    return lines


def test_no_production_module_matches_the_admin_scope_by_prefix() -> None:
    """Covers the RPA admin gate too, which lives inside an async route handler."""
    offenders = []
    for top in ("api", "auth", "core", "connectors", "workflows", "rpa"):
        for path in sorted((REPO / top).rglob("*.py")):
            offenders += [f"{path.relative_to(REPO)}:{line}" for line in _prefix_admin_checks(path)]
    assert offenders == [], f"match the admin scope exactly (core.rbac.has_admin_scope): {offenders}"
