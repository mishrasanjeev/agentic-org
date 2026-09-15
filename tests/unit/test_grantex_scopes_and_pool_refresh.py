# SPDX-License-Identifier: Apache-2.0
"""Registered Grantex scopes that enforcement can satisfy, and a pool refresh Grantex serves.

Acceptance criteria covered here:

* registration and ``PATCH /agents/{id}`` scopes carry the permission level
  ``grantex.enforce`` understands (never ``execute``), from the shipped
  manifest where there is one; a grant with those scopes allows the agent's
  read tools and refuses write tools it was not registered for;
* already-registered agents are backfilled: Grantex first, then storage, with
  a report-only default;
* the token pool refreshes an agent token by delegating from the root grant
  (``grants.delegate``), not an OAuth grant type Grantex does not serve.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from grantex import Grantex
from grantex.manifest import Permission

from auth.grantex_registration import _tools_to_scopes

ROOT = "placeholder-root-grant"  # noqa: S105 - not a credential


def test_registered_scopes_use_manifest_permission_levels_never_execute():
    scopes = _tools_to_scopes(
        ["list_contacts", "hubspot:create_contact", "salesforce:query"], "sales", connector_names=["hubspot"]
    )
    assert scopes == [
        "agenticorg:sales:read",
        "tool:hubspot:read:list_contacts",
        "tool:hubspot:write:create_contact",
        "tool:salesforce:read:query",
    ]
    assert not any(":execute:" in scope for scope in scopes)


def test_unresolved_and_fallback_tools_also_get_a_permission_level():
    scopes = _tools_to_scopes(["definitely_not_a_registered_tool"], "ops")
    assert scopes == ["agenticorg:ops:read", "tool:agenticorg:read:definitely_not_a_registered_tool"]


@pytest.mark.parametrize(
    ("tools", "tool", "allowed"),
    [
        (["list_contacts"], "list_contacts", True),
        (["list_contacts"], "create_contact", False),
        (["list_contacts", "create_contact"], "create_contact", True),
    ],
)
def test_the_sdk_permission_check_accepts_registered_scopes(tools, tool, allowed):
    import grantex.manifests.hubspot as hubspot

    scopes = _tools_to_scopes(tools, "sales", connector_names=["hubspot"])
    granted = Grantex._resolve_granted_permission(scopes, "hubspot")
    required = hubspot.manifest.get_permission(tool)
    assert granted is not None
    assert Permission.covers(granted, required) is allowed


def test_old_execute_scopes_grant_nothing_to_the_sdk():
    assert Grantex._resolve_granted_permission(["tool:hubspot:execute:list_contacts"], "hubspot") is None


# ── Backfill ─────────────────────────────────────────────────────────────


def _config(scopes: list[str], grantex_agent_id: str = "ag_1") -> dict[str, Any]:
    return {"grantex": {"grantex_agent_id": grantex_agent_id, "grantex_scopes": scopes, "grantex_did": "did:x"}, "k": 1}


async def _refresh(config: dict[str, Any], *, apply: bool, client: MagicMock | None = None):
    from scripts.refresh_grantex_scopes import refresh_agent_scopes

    persist = AsyncMock()
    client = client or MagicMock()
    result = await refresh_agent_scopes(
        agent_id="agent-1",
        domain="sales",
        authorized_tools=["list_contacts"],
        config=config,
        connector_names=["hubspot"],
        grantex_client=client,
        apply=apply,
        persist=persist,
    )
    return result, client, persist


async def test_backfill_reports_by_default_and_changes_nothing():
    result, client, persist = await _refresh(_config(["tool:hubspot:execute:list_contacts"]), apply=False)
    assert result.outcome == "would_update"
    client.agents.update.assert_not_called()
    persist.assert_not_awaited()
    assert '"execute_scopes_before": 1' in result.as_json()


async def test_backfill_updates_grantex_then_storage():
    result, client, persist = await _refresh(_config(["tool:hubspot:execute:list_contacts"]), apply=True)
    assert result.outcome == "updated"
    client.agents.update.assert_called_once_with(
        "ag_1", scopes=["agenticorg:sales:read", "tool:hubspot:read:list_contacts"]
    )
    stored = persist.await_args.args[0]
    assert stored["grantex"]["grantex_scopes"] == ["agenticorg:sales:read", "tool:hubspot:read:list_contacts"]
    assert stored["grantex"]["grantex_did"] == "did:x" and stored["k"] == 1


async def test_backfill_leaves_storage_alone_when_grantex_refuses():
    client = MagicMock()
    client.agents.update.side_effect = RuntimeError("403")
    result, _, persist = await _refresh(_config(["tool:hubspot:execute:list_contacts"]), apply=True, client=client)
    assert result.outcome == "grantex_failed"
    persist.assert_not_awaited()


async def test_backfill_skips_current_and_unregistered_agents():
    current, client, _ = await _refresh(
        _config(["agenticorg:sales:read", "tool:hubspot:read:list_contacts"]), apply=True
    )
    unregistered, _, _ = await _refresh({"grantex": {}}, apply=True)
    assert (current.outcome, unregistered.outcome) == ("unchanged", "not_registered")
    client.agents.update.assert_not_called()


# ── Pool refresh ─────────────────────────────────────────────────────────


async def test_pool_refresh_delegates_from_the_root_grant(monkeypatch):
    from auth.token_pool import TokenPool
    from core.config import external_keys

    monkeypatch.setattr(external_keys, "grantex_root_grant_token", ROOT)
    client = MagicMock()
    client.grants.delegate.return_value = {
        "grantToken": "placeholder-refreshed",
        "grantId": "grnt_r",
        "expiresAt": (datetime.now(UTC) + timedelta(minutes=15)).isoformat(),
    }
    pool = TokenPool(grantex_client_factory=lambda: client)
    pool.redis = AsyncMock()
    pool._schedule_refresh = MagicMock()
    pool.set_agent_config_resolver(
        AsyncMock(return_value={"grantex_agent_id": "ag_1", "scopes": ["tool:hubspot:read:list_contacts"]})
    )

    await pool._refresh_after("agent-1", 0)

    kwargs = client.grants.delegate.call_args.kwargs
    assert (kwargs["parent_grant_token"], kwargs["sub_agent_id"]) == (ROOT, "ag_1")
    key, ttl, payload = pool.redis.setex.await_args.args
    assert key == "agent:agent-1:token" and 0 < ttl <= 900 and "placeholder-refreshed" in payload


async def test_pool_refresh_for_an_unregistered_agent_removes_the_stale_token():
    from auth.token_pool import TokenPool

    pool = TokenPool(grantex_client_factory=MagicMock(side_effect=AssertionError("no delegation")))
    pool.redis = AsyncMock()
    pool.set_agent_config_resolver(AsyncMock(return_value={"agent_type": "finance", "scopes": ["read"]}))
    await pool._refresh_after("agent-1", 0)
    pool.redis.delete.assert_awaited_with("agent:agent-1:token")
    assert time.time() > 0


def test_the_pool_no_longer_uses_the_unserved_oauth_grant_type():
    import inspect

    from auth import token_pool

    assert "delegate_agent_token" not in inspect.getsource(token_pool.TokenPool._refresh_after).split('"""')[-1]
    assert not hasattr(token_pool, "grantex_client")
