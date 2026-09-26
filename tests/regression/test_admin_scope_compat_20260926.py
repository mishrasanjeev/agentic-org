# SPDX-License-Identifier: Apache-2.0
"""API keys that were administrators under the prefix match stay administrators.

The exact-match admin check (``core.rbac.has_admin_scope``) stopped treating
``agenticorg:admin:<sub>`` as admin. Revision ``v6z29_admin_scope_compat`` gives every
key holding such a colon-delimited sub-scope the exact ``agenticorg:admin`` scope, so
those keys keep the access they had; look-alikes such as ``agenticorg:administration``
stay non-admin. Key creation refuses the ambiguous forms so they cannot be minted again.
The migration's effect on real rows is checked against Postgres in
``tests/integration/test_alembic_e2e.py``.
"""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api.v1 import api_keys
from core.rbac import ADMIN_SCOPE, has_admin_scope

_ROOT = Path(__file__).resolve().parents[2]


class _PastValidationError(Exception):
    """Raised by the patched session factory: the request got past scope validation."""


def _create(monkeypatch: pytest.MonkeyPatch, scopes: list[str]) -> None:
    def session_factory():
        raise _PastValidationError

    monkeypatch.setattr(api_keys, "async_session_factory", session_factory)
    monkeypatch.setattr(api_keys, "_get_tenant_id", lambda _request: "00000000-0000-0000-0000-000000000001")
    monkeypatch.setattr(api_keys, "_get_user_sub", lambda _request: "admin@example.com")
    body = api_keys.CreateKeyRequest(name="example", scopes=scopes)
    asyncio.run(api_keys.create_api_key(body, SimpleNamespace()))


@pytest.mark.parametrize(
    "scope",
    [
        "agenticorg:admin:full",
        "agenticorg:admin:read",
        "agenticorg:administration:read",
        "agenticorg:adminx",
        "agenticorg.admin",
        "agenticorg.admin:full",
        "AgenticOrg:Admin",
        " agenticorg:admin",
        "agenticorg:admin ",
    ],
)
def test_key_creation_refuses_an_ambiguous_admin_scope(monkeypatch: pytest.MonkeyPatch, scope: str) -> None:
    with pytest.raises(HTTPException) as info:
        _create(monkeypatch, ["agents:read", scope])
    assert info.value.status_code == 422
    assert scope in info.value.detail
    assert f"'{ADMIN_SCOPE}'" in info.value.detail


@pytest.mark.parametrize("scopes", [[ADMIN_SCOPE], ["agents:read", "connectors.read"], []])
def test_key_creation_accepts_exact_and_ordinary_scopes(monkeypatch: pytest.MonkeyPatch, scopes: list[str]) -> None:
    with pytest.raises(_PastValidationError):
        _create(monkeypatch, scopes)


def _migration():
    path = _ROOT / "migrations" / "versions" / "v6_z29_admin_scope_compat.py"
    spec = importlib.util.spec_from_file_location("v6_z29_admin_scope_compat", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_is_the_single_head_after_case_excerpts() -> None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    module = _migration()
    assert module.revision == "v6z29_admin_scope_compat"
    assert module.down_revision == "v6z28_case_excerpts"
    heads = ScriptDirectory.from_config(Config(str(_ROOT / "alembic.ini"))).get_heads()
    assert heads == ["v6z29_admin_scope_compat"]


def test_migration_is_bounded_to_old_colon_sub_scopes_of_admin_owners() -> None:
    """The restore must cover only keys that were admin under the prefix rule and whose
    owner is still an admin; its behaviour on rows is tested against Postgres."""
    import re

    sql = _migration().RESTORE_ADMIN_SQL
    assert re.findall(r"LIKE '([^']+)'", sql) == ["agenticorg:admin:%"]
    assert "NOT ('agenticorg:admin' = ANY(scopes))" in sql
    assert "created_at < TIMESTAMPTZ '2026-09-25 05:58:53+00'" in sql
    assert "u.role = 'admin'" in sql
    assert "row_security" not in sql


def test_migration_refuses_to_run_with_a_tenant_context() -> None:
    module = _migration()
    bind = SimpleNamespace(execute=lambda *_a, **_k: SimpleNamespace(scalar=lambda: "tenant-1"))
    module.op = SimpleNamespace(get_bind=lambda: bind)
    with pytest.raises(RuntimeError, match="without a tenant context"):
        module.upgrade()


@pytest.mark.parametrize(
    ("scopes", "migrated_is_admin"),
    [
        (["agenticorg:admin:full"], True),
        (["agents:read", "agenticorg:admin:read"], True),
        (["agenticorg:administration:read"], False),
        (["agenticorg:adminx"], False),
        (["agents:read"], False),
    ],
)
def test_migrated_scopes_restore_prior_admin_only_for_sub_scopes(scopes: list[str], migrated_is_admin: bool) -> None:
    """Mirror of the migration's rule, checked against the exact-match admin predicate."""
    migrated = list(scopes)
    if ADMIN_SCOPE not in migrated and any(s.startswith(f"{ADMIN_SCOPE}:") for s in migrated):
        migrated.append(ADMIN_SCOPE)
    assert has_admin_scope(migrated) is migrated_is_admin
