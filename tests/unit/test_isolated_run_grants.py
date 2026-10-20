# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Separate delegated authority without changing compatibility defaults."""

import asyncio
import time
import uuid
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from auth.grant_enforcement import EnforcementMode, deployment_default_mode
from auth.run_grants import refresh_run_grant, resolve_run_grant
from auth.token_pool import GrantMintError, RunGrantToken, TokenPool
from core.config import settings


async def test_separate_run_contexts_do_not_share_a_cached_grant():
    pool = TokenPool()
    pool.lazy_redis = False
    pool._mint_run_grant = AsyncMock(side_effect=[
        RunGrantToken("synthetic-a", "grnt_a", time.time()+900, "minted", 900),
        RunGrantToken("synthetic-b", "grnt_b", time.time()+900, "minted", 900),
    ])
    kwargs = {
        "tenant_id": "tenant-a", "agent_id": "agent-a", "grantex_agent_id": "registered-a", "scopes": ["data:read"],
    }
    key_a, key_b = uuid.uuid4().hex, uuid.uuid4().hex
    first = await pool.get_run_grant_token(**kwargs, isolation_key=key_a)
    same_run = await pool.get_run_grant_token(**kwargs, isolation_key=key_a)
    other_run = await pool.get_run_grant_token(**kwargs, isolation_key=key_b)
    assert first.grant_id == same_run.grant_id
    assert first.grant_id != other_run.grant_id
    assert pool._mint_run_grant.await_count == 2


async def test_isolation_requires_a_valid_server_created_key():
    pool = TokenPool()
    pool.lazy_redis = False
    with pytest.raises(GrantMintError, match="server-created UUID"):
        await pool.get_run_grant_token(
            tenant_id="tenant-a", agent_id="agent-a", grantex_agent_id="registered-a", scopes=["data:read"],
            isolation_key="caller-controlled-case-name",
        )


async def test_isolated_resolution_forces_deny_and_ignores_static_agent_token(monkeypatch):
    monkeypatch.setattr(settings, "grants_isolate_run_tokens", True)
    mint = AsyncMock(return_value=RunGrantToken("synthetic-fresh", "grnt_fresh", time.time()+900, "minted", 900))
    with patch("auth.token_pool.token_pool.get_run_grant_token", mint):
        first = await resolve_run_grant(
            tenant_id="tenant-a", agent_id="agent-a", mode=EnforcementMode.OFF,
            grantex_config={
                "grant_token": "synthetic-static", "grantex_agent_id": "registered-a", "grantex_scopes": ["data:read"],
            },
        )
        second = await resolve_run_grant(
            tenant_id="tenant-a", agent_id="agent-a", mode=EnforcementMode.WARN,
            grantex_config={"grantex_agent_id": "registered-a", "grantex_scopes": ["data:read"]},
        )
    assert first.mode is EnforcementMode.DENY and second.mode is EnforcementMode.DENY
    assert first.token == "synthetic-fresh"
    assert first.isolation_key and first.isolation_key != second.isolation_key
    assert mint.await_args_list[0].kwargs["isolation_key"] == first.isolation_key


async def test_external_caller_still_restricts_a_separate_run_grant(monkeypatch):
    monkeypatch.setattr(settings, "grants_isolate_run_tokens", True)
    mint = AsyncMock(return_value=RunGrantToken("synthetic-run", "grnt_run", time.time()+900, "minted", 900))
    with patch("auth.token_pool.token_pool.get_run_grant_token", mint):
        grant = await resolve_run_grant(
            tenant_id="tenant-a", agent_id="agent-a", supplied_token="synthetic-caller",
            mode=EnforcementMode.DENY,
            grantex_config={"grantex_agent_id": "registered-a", "grantex_scopes": ["data:read"]},
        )
    assert grant.token == "synthetic-run"
    assert grant.caller_token == "synthetic-caller"


async def test_refresh_keeps_original_run_isolation(monkeypatch):
    monkeypatch.setattr(settings, "grants_isolate_run_tokens", True)
    mint = AsyncMock(side_effect=[
        RunGrantToken("synthetic-first", "grnt_first", time.time()+30, "minted", 900),
        RunGrantToken("synthetic-next", "grnt_next", time.time()+900, "minted", 900),
    ])
    with patch("auth.token_pool.token_pool.get_run_grant_token", mint):
        first = await resolve_run_grant(
            tenant_id="tenant-a", agent_id="agent-a", mode=EnforcementMode.DENY,
            grantex_config={"grantex_agent_id": "registered-a", "grantex_scopes": ["data:read"]},
        )
        refreshed = await refresh_run_grant(first)
    assert refreshed.grant_id == "grnt_next"
    assert refreshed.isolation_key == first.isolation_key
    assert mint.await_args_list[1].kwargs["isolation_key"] == first.isolation_key


async def test_parallel_calls_share_only_their_own_context_and_tenant():
    pool = TokenPool()
    pool.lazy_redis = False
    pool._mint_run_grant = AsyncMock(side_effect=[
        RunGrantToken(f"synthetic-{i}", f"grnt_{i}", time.time() + 900, "minted", 900) for i in range(3)
    ])
    key = uuid.uuid4().hex

    async def request(tenant):
        return await pool.get_run_grant_token(
            tenant_id=tenant, agent_id="agent-a", grantex_agent_id="registered-a", scopes=["data:read"],
            isolation_key=key,
        )

    first, second = await asyncio.gather(request("tenant-a"), request("tenant-a"))
    other = await request("tenant-b")
    assert first.grant_id == second.grant_id
    assert first.grant_id != other.grant_id
    assert pool._mint_run_grant.await_count == 2
    cached_key = next(k for k in pool._local_grants if "tenant-a" in k)
    pool._local_grants[cached_key] = replace(pool._local_grants[cached_key], expires_at=time.time() - 1)
    refreshed = await request("tenant-a")
    assert refreshed.grant_id not in {first.grant_id, other.grant_id}


async def test_isolated_mint_failure_stays_closed_even_with_a_static_grant(monkeypatch):
    monkeypatch.setattr(settings, "grants_isolate_run_tokens", True)
    mint = AsyncMock(side_effect=GrantMintError("mint_failed", "synthetic"))
    with patch("auth.token_pool.token_pool.get_run_grant_token", mint):
        grant = await resolve_run_grant(
            tenant_id="tenant-a", agent_id="agent-a", mode=EnforcementMode.OFF,
            grantex_config={
                "grant_token": "synthetic-static", "grantex_agent_id": "registered-a", "grantex_scopes": ["data:read"],
            },
        )
    assert grant.mode is EnforcementMode.DENY
    assert not grant.token
    assert grant.missing_sub_reason == "mint_failed"


@pytest.mark.parametrize("default_mode", ["off", "warn", "deny"])
@pytest.mark.parametrize("isolated", [False, True])
def test_isolation_is_a_deployment_minimum_before_entry_point_routing(monkeypatch, default_mode, isolated):
    monkeypatch.setattr(settings, "grants_isolate_run_tokens", isolated)
    monkeypatch.setattr(settings, "grants_enforce_closed", default_mode)
    assert deployment_default_mode() is (EnforcementMode.DENY if isolated else EnforcementMode(default_mode))


@pytest.mark.parametrize("runtime", ["a2a", "mcp"])
@pytest.mark.parametrize("default_mode", ["off", "warn"])
async def test_type_routes_select_and_isolate_authority_before_legacy_shortcut(monkeypatch, runtime, default_mode):
    from api.v1 import agents

    monkeypatch.setattr(settings, "grants_isolate_run_tokens", True)
    monkeypatch.setattr(settings, "grants_enforce_closed", default_mode)
    tenant_id, agent_id = str(uuid.uuid4()), str(uuid.uuid4())
    row = SimpleNamespace(id=agent_id, config={"grantex": {
        "grant_token": "synthetic-static", "grantex_agent_id": "registered-a", "grantex_scopes": ["data:read"],
    }})
    select = AsyncMock(return_value=row)
    mint = AsyncMock(side_effect=[
        RunGrantToken(f"synthetic-{i}", f"grnt_{i}", time.time() + 900, "minted", 900) for i in range(2)
    ])
    with (
        patch("core.feature_flags.load_flag_rows_strict", AsyncMock(return_value=SimpleNamespace(
            global_row=None, tenant_row=None,
        ))),
        patch.object(agents, "_select_agent_for_type", select),
        patch("auth.token_pool.token_pool.get_run_grant_token", mint),
    ):
        grants = [await agents._resolve_run_grant_for_type(
            tenant_id=tenant_id, agent_type="analyst", company_id=None,
            caller_token="synthetic-caller", caller_agent_id="caller-agent", runtime=runtime,
        ) for _ in range(2)]
    assert select.await_count == 2
    assert mint.await_count == 2
    assert all(g.mode is EnforcementMode.DENY and g.agent_id == agent_id for g in grants)
    assert [g.token for g in grants] == ["synthetic-0", "synthetic-1"]
    assert all(g.caller_token == "synthetic-caller" for g in grants)
    assert grants[0].isolation_key != grants[1].isolation_key


@pytest.mark.parametrize("runtime", ["a2a", "mcp"])
@pytest.mark.parametrize("lookup_failure", [False, True])
async def test_isolated_type_routes_fail_closed_without_a_selected_agent(monkeypatch, runtime, lookup_failure):
    from api.v1 import agents

    monkeypatch.setattr(settings, "grants_isolate_run_tokens", True)
    monkeypatch.setattr(settings, "grants_enforce_closed", "off")
    select = (
        AsyncMock(side_effect=RuntimeError("synthetic lookup failure"))
        if lookup_failure else AsyncMock(return_value=None)
    )
    mint = AsyncMock()
    with (
        patch("core.feature_flags.load_flag_rows_strict", AsyncMock(return_value=SimpleNamespace(
            global_row=None, tenant_row=None,
        ))),
        patch.object(agents, "_select_agent_for_type", select),
        patch("auth.token_pool.token_pool.get_run_grant_token", mint),
    ):
        grant = await agents._resolve_run_grant_for_type(
            tenant_id=str(uuid.uuid4()), agent_type="analyst", company_id=None,
            caller_token="", caller_agent_id="", runtime=runtime,
        )
    select.assert_awaited_once()
    mint.assert_not_awaited()
    assert grant.mode is EnforcementMode.DENY
    assert not grant.token
    assert grant.missing_sub_reason == ("lookup_failed" if lookup_failure else "no_agent")
