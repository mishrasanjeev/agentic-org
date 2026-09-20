# SPDX-License-Identifier: Apache-2.0
"""PRD F-1 — every agent run carries a resolved grant.

Acceptance criteria covered here:

* in ``off`` the caller's token passes through and nothing is looked up or
  minted (legacy behaviour);
* in ``warn`` / ``deny`` the run's token is the caller's, else the agent's
  configured token, else a per-run grant from the token pool;
* the token pool obtains the *first* token by delegating from the root grant
  to the agent's registered Grantex agent and scopes, and reuses it per
  tenant, agent and scope set until shortly before it expires;
* any failure to obtain a token yields an empty token with a sub-reason —
  never an exception into the run and never a silent allow;
* the LangGraph runner carries the resolved token in ``AgentState.grant_token``
  and the agent graph enforces with it.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from structlog.testing import capture_logs

from auth.grant_enforcement import EnforcementMode
from auth.run_grants import RunGrant, direct_tool_call_permitted, resolve_run_grant
from auth.token_pool import GrantMintError, RunGrantToken, TokenPool

TENANT = str(uuid.UUID(int=0x1F1A))
OTHER_TENANT = str(uuid.UUID(int=0x1F1B))
AGENT = str(uuid.UUID(int=0xA6E))
ROOT = "placeholder-root-grant"  # noqa: S105 - not a credential
MINTED = "placeholder-minted-grant"  # noqa: S105 - not a credential
SUPPLIED = "placeholder-supplied-grant"  # noqa: S105 - not a credential
REGISTERED = {"grantex_agent_id": "ag_placeholder", "grantex_scopes": ["tool:hubspot:read:get_contact"]}


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self.values[key] = value
        self.ttls[key] = ttl


def _delegating_client(expires_in: timedelta = timedelta(minutes=15)) -> MagicMock:
    client = MagicMock()
    client.grants.delegate.return_value = {
        "grantToken": MINTED,
        "grantId": "grnt_placeholder",
        "expiresAt": (datetime.now(UTC) + expires_in).isoformat().replace("+00:00", "Z"),
        "scopes": REGISTERED["grantex_scopes"],
    }
    # The pool delegates only scopes the agent's registration carries.
    client.agents.get.return_value = SimpleNamespace(scopes=tuple(REGISTERED["grantex_scopes"]))
    return client


@pytest.fixture
def root_grant(monkeypatch):
    from core.config import external_keys

    monkeypatch.setattr(external_keys, "grantex_root_grant_token", ROOT)


# ── Token pool: first token ──────────────────────────────────────────────


async def test_pool_mints_first_run_token_by_delegating_from_the_root_grant(root_grant):
    client = _delegating_client()
    pool = TokenPool(grantex_client_factory=lambda: client)

    grant = await pool.get_run_grant_token(tenant_id=TENANT, agent_id=AGENT, **_registered())

    assert grant.token == MINTED and grant.source == "minted" and grant.grant_id == "grnt_placeholder"
    kwargs = client.grants.delegate.call_args.kwargs
    assert kwargs["parent_grant_token"] == ROOT
    assert kwargs["sub_agent_id"] == "ag_placeholder"
    assert kwargs["scopes"] == ["tool:hubspot:read:get_contact"]
    assert kwargs["expires_in"] == "15m"
    assert MINTED not in repr(grant)


def _registered() -> dict[str, Any]:
    return {"grantex_agent_id": REGISTERED["grantex_agent_id"], "scopes": list(REGISTERED["grantex_scopes"])}


async def test_pool_reuses_a_cached_run_token_for_the_same_tenant_agent_and_scopes(root_grant):
    client = _delegating_client()
    pool = TokenPool(grantex_client_factory=lambda: client)
    pool.redis = _FakeRedis()  # type: ignore[assignment]

    first = await pool.get_run_grant_token(tenant_id=TENANT, agent_id=AGENT, **_registered())
    second = await pool.get_run_grant_token(tenant_id=TENANT, agent_id=AGENT, **_registered())

    assert (first.source, second.source) == ("minted", "pool_cache")
    assert second.token == MINTED
    assert client.grants.delegate.call_count == 1


async def test_pool_cache_is_tenant_bound(root_grant):
    client = _delegating_client()
    pool = TokenPool(grantex_client_factory=lambda: client)
    pool.redis = _FakeRedis()  # type: ignore[assignment]

    await pool.get_run_grant_token(tenant_id=TENANT, agent_id=AGENT, **_registered())
    other = await pool.get_run_grant_token(tenant_id=OTHER_TENANT, agent_id=AGENT, **_registered())

    assert other.source == "minted"
    assert client.grants.delegate.call_count == 2


async def test_pool_does_not_reuse_a_token_about_to_expire(root_grant):
    client = _delegating_client()
    pool = TokenPool(grantex_client_factory=lambda: client)
    redis = _FakeRedis()
    pool.redis = redis  # type: ignore[assignment]
    key = pool._run_grant_cache_key(TENANT, AGENT, "ag_placeholder", list(REGISTERED["grantex_scopes"]))
    redis.values[key] = json.dumps({"token": "placeholder-stale", "grant_id": "g", "expires_at": time.time() + 5})

    grant = await pool.get_run_grant_token(tenant_id=TENANT, agent_id=AGENT, **_registered())

    assert grant.source == "minted"


async def test_pool_refuses_to_mint_without_a_root_grant(monkeypatch):
    from core.config import external_keys

    monkeypatch.setattr(external_keys, "grantex_root_grant_token", "")
    client = _delegating_client()
    pool = TokenPool(grantex_client_factory=lambda: client)

    with pytest.raises(GrantMintError) as err:
        await pool.get_run_grant_token(tenant_id=TENANT, agent_id=AGENT, **_registered())
    assert err.value.sub_reason == "minting_unconfigured"
    client.grants.delegate.assert_not_called()


@pytest.mark.parametrize(
    "registration",
    [{"grantex_agent_id": "", "scopes": ["tool:hubspot:read:get_contact"]}, {"grantex_agent_id": "ag_x", "scopes": []}],
)
async def test_pool_refuses_to_mint_for_an_unregistered_agent(root_grant, registration):
    pool = TokenPool(grantex_client_factory=_delegating_client)
    with pytest.raises(GrantMintError) as err:
        await pool.get_run_grant_token(tenant_id=TENANT, agent_id=AGENT, **registration)
    assert err.value.sub_reason == "agent_not_registered"


async def test_pool_delegation_failure_is_a_mint_error(root_grant):
    client = MagicMock()
    client.grants.delegate.side_effect = RuntimeError("Parent grant has expired")
    # The pool delegates only scopes the agent's registration carries.
    client.agents.get.return_value = SimpleNamespace(scopes=tuple(REGISTERED["grantex_scopes"]))
    pool = TokenPool(grantex_client_factory=lambda: client)
    with pytest.raises(GrantMintError) as err:
        await pool.get_run_grant_token(tenant_id=TENANT, agent_id=AGENT, **_registered())
    assert err.value.sub_reason == "mint_failed"


async def test_pool_delegation_without_a_token_is_a_mint_error(root_grant):
    client = MagicMock()
    client.grants.delegate.return_value = {"grantId": "grnt_placeholder"}
    # The pool delegates only scopes the agent's registration carries.
    client.agents.get.return_value = SimpleNamespace(scopes=tuple(REGISTERED["grantex_scopes"]))
    pool = TokenPool(grantex_client_factory=lambda: client)
    with pytest.raises(GrantMintError) as err:
        await pool.get_run_grant_token(tenant_id=TENANT, agent_id=AGENT, **_registered())
    assert err.value.sub_reason == "mint_failed"


async def test_pool_without_grantex_client_is_unconfigured(root_grant):
    def _no_client():
        raise ValueError("GRANTEX_API_KEY is required")

    pool = TokenPool(grantex_client_factory=_no_client)
    with pytest.raises(GrantMintError) as err:
        await pool.get_run_grant_token(tenant_id=TENANT, agent_id=AGENT, **_registered())
    assert err.value.sub_reason == "minting_unconfigured"


# ── Run grant resolution ─────────────────────────────────────────────────


async def test_off_mode_passes_the_supplied_token_through_and_never_mints():
    pool = AsyncMock(side_effect=AssertionError("must not mint in off mode"))
    with patch("auth.token_pool.token_pool.get_run_grant_token", pool):
        grant = await resolve_run_grant(
            tenant_id=TENANT, agent_id=AGENT, supplied_token=SUPPLIED, mode=EnforcementMode.OFF
        )
        empty = await resolve_run_grant(tenant_id=TENANT, agent_id=AGENT, supplied_token="", mode=EnforcementMode.OFF)
    assert (grant.mode, grant.token) == (EnforcementMode.OFF, SUPPLIED)
    assert (empty.mode, empty.token, empty.missing_sub_reason) == (EnforcementMode.OFF, "", "")
    pool.assert_not_awaited()


async def test_resolution_uses_the_tenant_mode_when_none_is_given():
    with patch("auth.run_grants.resolve_enforcement_mode", AsyncMock(return_value=EnforcementMode.OFF)) as resolve:
        grant = await resolve_run_grant(tenant_id=TENANT, agent_id=AGENT, supplied_token=SUPPLIED)
    resolve.assert_awaited_once_with(TENANT)
    assert grant.token == SUPPLIED


async def test_warn_mode_prefers_the_supplied_token():
    pool = AsyncMock()
    with patch("auth.token_pool.token_pool.get_run_grant_token", pool):
        grant = await resolve_run_grant(
            tenant_id=TENANT,
            agent_id=AGENT,
            supplied_token=SUPPLIED,
            grantex_config=REGISTERED,
            mode=EnforcementMode.WARN,
        )
    assert (grant.token, grant.source) == (SUPPLIED, "supplied")
    pool.assert_not_awaited()


async def test_warn_mode_uses_the_agents_configured_token_before_minting():
    pool = AsyncMock()
    config = {**REGISTERED, "grant_token": "placeholder-configured-grant"}
    with patch("auth.token_pool.token_pool.get_run_grant_token", pool):
        grant = await resolve_run_grant(
            tenant_id=TENANT, agent_id=AGENT, grantex_config=config, mode=EnforcementMode.WARN
        )
    assert (grant.token, grant.source) == ("placeholder-configured-grant", "agent_config")
    pool.assert_not_awaited()


async def test_warn_mode_mints_a_run_grant_from_the_pool():
    minted = RunGrantToken(token=MINTED, grant_id="grnt_placeholder", expires_at=None, source="minted")
    pool = AsyncMock(return_value=minted)
    with patch("auth.token_pool.token_pool.get_run_grant_token", pool):
        grant = await resolve_run_grant(
            tenant_id=TENANT, agent_id=AGENT, grantex_config=REGISTERED, mode=EnforcementMode.WARN
        )
    assert (grant.token, grant.source, grant.grant_id) == (MINTED, "minted", "grnt_placeholder")
    pool.assert_awaited_once_with(
        tenant_id=TENANT,
        agent_id=AGENT,
        grantex_agent_id="ag_placeholder",
        scopes=["tool:hubspot:read:get_contact"],
    )


@pytest.mark.parametrize("sub_reason", ["minting_unconfigured", "agent_not_registered", "mint_failed"])
async def test_deny_mode_mint_failure_yields_no_token_and_a_reason(sub_reason):
    pool = AsyncMock(side_effect=GrantMintError(sub_reason, "no grant"))
    with patch("auth.token_pool.token_pool.get_run_grant_token", pool), capture_logs() as logs:
        grant = await resolve_run_grant(tenant_id=TENANT, agent_id=AGENT, grantex_config={}, mode=EnforcementMode.DENY)
    assert (grant.token, grant.source, grant.missing_sub_reason) == ("", "none", sub_reason)
    assert [e["sub_reason"] for e in logs if e["event"] == "grant_resolution_failed"] == [sub_reason]


async def test_unexpected_pool_error_yields_no_token_not_an_exception():
    pool = AsyncMock(side_effect=KeyError("boom"))
    with patch("auth.token_pool.token_pool.get_run_grant_token", pool):
        grant = await resolve_run_grant(
            tenant_id=TENANT, agent_id=AGENT, grantex_config=REGISTERED, mode=EnforcementMode.DENY
        )
    assert (grant.token, grant.missing_sub_reason) == ("", "mint_failed")


async def test_agent_lookup_failure_yields_no_token_not_an_exception():
    with patch("auth.run_grants._load_agent_grantex_config", AsyncMock(side_effect=RuntimeError("db down"))):
        grant = await resolve_run_grant(tenant_id=TENANT, agent_id=AGENT, mode=EnforcementMode.WARN)
    assert (grant.token, grant.missing_sub_reason) == ("", "lookup_failed")


async def test_non_uuid_agent_lookup_is_lookup_failed():
    grant = await resolve_run_grant(tenant_id=TENANT, agent_id="not-a-uuid", mode=EnforcementMode.WARN)
    assert grant.missing_sub_reason == "lookup_failed"


# ── The run carries the grant into the agent runtime ─────────────────────


async def test_runner_carries_the_resolved_token_in_agent_state_and_graph():
    from core.langgraph import runner

    grant = RunGrant(mode=EnforcementMode.WARN, token=MINTED, source="minted")
    captured: dict[str, Any] = {}

    class _Compiled:
        async def ainvoke(self, state, config=None):
            captured["state"] = state
            return {"status": "completed", "output": {}, "messages": []}

    class _Graph:
        def compile(self, checkpointer=None):
            return _Compiled()

    def _build(**kwargs):
        captured["build_kwargs"] = kwargs
        return _Graph()

    with (
        patch("core.billing.metering.gate_agent_run", AsyncMock(return_value=None)),
        patch("core.billing.metering.meter_agent_run", AsyncMock()),
        patch("core.database.get_tenant_session", MagicMock(side_effect=RuntimeError("no database in unit tests"))),
        patch.object(runner, "resolve_run_grant", AsyncMock(return_value=grant)) as resolve,
        patch.object(runner, "build_agent_graph", _build),
        patch.object(runner, "prefetch_llm_credential", AsyncMock(return_value=None)),
        patch.object(runner, "generate_explanation", AsyncMock(return_value={})),
    ):
        result = await runner.run_agent(
            agent_id=AGENT,
            agent_type="analyst",
            domain="ops",
            tenant_id=TENANT,
            system_prompt="scripted",
            authorized_tools=[],
            task_input={"action": "process"},
            grant_token="",
        )

    assert result["status"] == "completed"
    resolve.assert_awaited_once()
    assert resolve.await_args.kwargs["supplied_token"] == ""
    assert captured["state"]["grant_token"] == MINTED
    assert captured["build_kwargs"]["run_grant"] is grant


async def test_runner_uses_a_grant_the_caller_already_resolved():
    from core.langgraph import runner

    grant = RunGrant(mode=EnforcementMode.OFF, token=SUPPLIED, source="supplied")
    captured: dict[str, Any] = {}

    class _Compiled:
        async def ainvoke(self, state, config=None):
            captured["state"] = state
            return {"status": "completed", "output": {}, "messages": []}

    graph = MagicMock()
    graph.compile.return_value = _Compiled()
    with (
        patch("core.billing.metering.gate_agent_run", AsyncMock(return_value=None)),
        patch("core.billing.metering.meter_agent_run", AsyncMock()),
        patch("core.database.get_tenant_session", MagicMock(side_effect=RuntimeError("no database in unit tests"))),
        patch.object(runner, "resolve_run_grant", AsyncMock(side_effect=AssertionError("already resolved"))),
        patch.object(runner, "build_agent_graph", MagicMock(return_value=graph)),
        patch.object(runner, "prefetch_llm_credential", AsyncMock(return_value=None)),
        patch.object(runner, "generate_explanation", AsyncMock(return_value={})),
    ):
        await runner.run_agent(
            agent_id=AGENT,
            agent_type="analyst",
            domain="ops",
            tenant_id=TENANT,
            system_prompt="scripted",
            authorized_tools=[],
            task_input={"action": "process"},
            grant_token=SUPPLIED,
            run_grant=grant,
        )
    assert captured["state"]["grant_token"] == SUPPLIED


# ── Tools the platform calls directly (deterministic routes) ─────────────


async def test_direct_tool_call_is_permitted_in_off_mode_without_a_grant():
    assert await direct_tool_call_permitted(
        RunGrant(mode=EnforcementMode.OFF),
        connector="zoho_books",
        tool="calculate_tds",
        tenant_id=TENANT,
        agent_id=AGENT,
        runtime="deterministic_tds",
    )


async def test_direct_tool_call_without_a_grant_is_permitted_and_recorded_in_warn_mode():
    with capture_logs() as logs:
        permitted = await direct_tool_call_permitted(
            RunGrant(mode=EnforcementMode.WARN, source="none", missing_sub_reason="minting_unconfigured"),
            connector="zoho_books",
            tool="calculate_tds",
            tenant_id=TENANT,
            agent_id=AGENT,
            runtime="deterministic_tds",
        )
    assert permitted is True
    events = [e for e in logs if e["event"] == "grant_enforcement_would_deny"]
    assert [(e["reason"], e["runtime"], e["tool"]) for e in events] == [
        ("grant_missing", "deterministic_tds", "calculate_tds")
    ]


async def test_direct_tool_call_without_a_grant_is_refused_in_deny_mode():
    assert not await direct_tool_call_permitted(
        RunGrant(mode=EnforcementMode.DENY, source="none", missing_sub_reason="mint_failed"),
        connector="zoho_books",
        tool="calculate_tds",
        tenant_id=TENANT,
        agent_id=AGENT,
        runtime="deterministic_tds",
    )


def test_run_endpoint_checks_the_grant_before_the_deterministic_tds_route():
    import inspect

    from api.v1 import agents

    src = inspect.getsource(agents.run_agent)
    assert src.index("resolve_run_grant(") < src.index("direct_tool_call_permitted(")
    assert src.index("direct_tool_call_permitted(") < src.index("try_tds_deterministic_route(")
    assert "run_grant=run_grant" in src
