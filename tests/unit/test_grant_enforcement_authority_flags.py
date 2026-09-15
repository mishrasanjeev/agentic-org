# SPDX-License-Identifier: Apache-2.0
"""PRD F-1 — tenant admins cannot lower grant enforcement.

Acceptance criteria covered here:

* the mode is the strictest of the deployment default, the global flag rows
  and the tenant's flag rows, each evaluated on its own — a disabled tenant row
  never hides an enabled global row;
* the authority flag keys are refused by the tenant feature-flag API for
  create, update and delete, even for a tenant admin;
* other flag keys still go through that API unchanged.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from auth import grant_enforcement as ge
from auth.grant_enforcement import EnforcementMode, resolve_enforcement_mode
from core.feature_flags import RESERVED_FLAG_KEYS, FlagRows, is_reserved_flag_key

TENANT = str(uuid.UUID(int=0x1F2A))


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    ge.clear_mode_cache()
    monkeypatch.setattr(ge.settings, "grants_enforce_closed", "off")
    yield
    ge.clear_mode_cache()


def _store(rows: dict[str, FlagRows]):
    async def _load(flag_key: str, **_: Any) -> FlagRows:
        return rows.get(flag_key, FlagRows(global_row=None, tenant_row=None))

    return _load


def _row(enabled: bool, rollout: int = 100) -> dict[str, Any]:
    return {"enabled": enabled, "rollout_percentage": rollout}


async def test_global_deny_with_a_disabled_tenant_row_resolves_to_deny():
    store = _store({ge.FLAG_DENY: FlagRows(global_row=_row(True), tenant_row=_row(False))})
    with patch("core.feature_flags.load_flag_rows_strict", store):
        assert await resolve_enforcement_mode(TENANT) is EnforcementMode.DENY


async def test_global_warn_with_disabled_tenant_rows_resolves_to_warn():
    store = _store(
        {
            ge.FLAG_WARN: FlagRows(global_row=_row(True), tenant_row=_row(False)),
            ge.FLAG_DENY: FlagRows(global_row=None, tenant_row=_row(False)),
        }
    )
    with patch("core.feature_flags.load_flag_rows_strict", store):
        assert await resolve_enforcement_mode(TENANT) is EnforcementMode.WARN


async def test_a_tenant_row_can_only_make_the_tenant_stricter_than_global():
    store = _store(
        {
            ge.FLAG_WARN: FlagRows(global_row=_row(True), tenant_row=None),
            ge.FLAG_DENY: FlagRows(global_row=None, tenant_row=_row(True)),
        }
    )
    with patch("core.feature_flags.load_flag_rows_strict", store):
        assert await resolve_enforcement_mode(TENANT) is EnforcementMode.DENY


async def test_a_zero_percent_global_rollout_does_not_enable_the_flag():
    store = _store({ge.FLAG_DENY: FlagRows(global_row=_row(True, rollout=0), tenant_row=None)})
    with patch("core.feature_flags.load_flag_rows_strict", store):
        assert await resolve_enforcement_mode(TENANT) is EnforcementMode.OFF


@pytest.mark.parametrize(
    "flag_key",
    [
        "grants.enforce_closed.warn",
        "grants.enforce_closed.deny",
        "GRANTS.ENFORCE_CLOSED.DENY",
        " grants.enforce_closed.deny ",
        "pseudonymisation.pre_model",
        "approvals.resume_agent_runs",
        "decisions.required",
        "caps.enforce",
        "caps.enforce.deny",
    ],
)
def test_authority_flag_keys_are_reserved(flag_key):
    assert is_reserved_flag_key(flag_key)


@pytest.mark.parametrize("flag_key", ["new_workflow_builder", "grants.enforce_closedx", "caps", "plugin_loading"])
def test_other_flag_keys_are_not_reserved(flag_key):
    assert not is_reserved_flag_key(flag_key)


def test_the_programme_authority_flags_are_all_reserved():
    assert {"grants.enforce_closed", "pseudonymisation.pre_model", "approvals.resume_agent_runs"} <= set(
        RESERVED_FLAG_KEYS
    )
    assert {"decisions.required", "caps.enforce"} <= set(RESERVED_FLAG_KEYS)


def _tenant_admin_app() -> TestClient:
    from api.v1 import feature_flags as flags_api

    app = FastAPI()

    @app.middleware("http")
    async def _tenant_admin(request: Request, call_next):
        request.state.claims = {"sub": "admin@tenant.example.com", "role": "admin"}
        request.state.scopes = ["agenticorg:admin"]
        request.state.tenant_id = TENANT
        return await call_next(request)

    app.include_router(flags_api.router, prefix="/api/v1")
    return TestClient(app)


@pytest.mark.parametrize("flag_key", ["grants.enforce_closed.deny", "grants.enforce_closed.warn", "caps.enforce"])
def test_tenant_admin_cannot_set_an_authority_flag_through_the_api(flag_key):
    session = AsyncMock(side_effect=AssertionError("the database must not be touched"))
    with patch("api.v1.feature_flags.get_tenant_session", session), _tenant_admin_app() as client:
        disabled = client.post("/api/v1/feature-flags", json={"flag_key": flag_key, "enabled": False})
        enabled = client.post("/api/v1/feature-flags", json={"flag_key": flag_key, "enabled": True})
    for response in (disabled, enabled):
        assert response.status_code == 403
        assert response.json()["detail"]["error"] == "flag_key_reserved"
    session.assert_not_called()


def test_tenant_admin_cannot_delete_an_operator_set_authority_flag():
    session = AsyncMock(side_effect=AssertionError("the database must not be touched"))
    with patch("api.v1.feature_flags.get_tenant_session", session), _tenant_admin_app() as client:
        response = client.delete("/api/v1/feature-flags/grants.enforce_closed.deny")
    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "flag_key_reserved"
    session.assert_not_called()


def test_ordinary_flags_still_reach_the_store_through_the_api():
    class _ReachedStoreError(Exception):
        pass

    def _session(*_: Any, **__: Any):
        raise _ReachedStoreError

    with patch("api.v1.feature_flags.get_tenant_session", _session), _tenant_admin_app() as client:
        with pytest.raises(_ReachedStoreError):
            client.post("/api/v1/feature-flags", json={"flag_key": "new_workflow_builder", "enabled": True})
