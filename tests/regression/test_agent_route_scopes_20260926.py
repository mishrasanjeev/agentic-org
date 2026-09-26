# SPDX-License-Identifier: Apache-2.0
"""FINDINGS A-64: a tenant admin can grant an agent route scopes.

Route scope checks apply to Grantex agent tokens (H-1), but registration only ever gave an
agent tool scopes and ``agenticorg:{domain}:read``, so an agent token was refused on every
route family. ``PATCH /agents/{id}`` now accepts ``route_scopes``:

* only a human tenant admin may set them; only canonical route-family scopes are accepted
  (never ``agenticorg:admin``, aliases or unenforced scopes);
* a registered agent's Grantex registration carries tool scopes plus route scopes, Grantex
  first and storage after; storage keeps them apart so run grants never carry them;
* a tools PATCH and the scope backfill keep the route scopes on the registration;
* every change is audited with before and after.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from api.route_enforcement import GRANTABLE_ROUTE_SCOPES, SCOPE_FAMILIES, validate_route_scopes
from core.models.audit import AuditLog
from core.ownership import Caller

TENANT = str(uuid.UUID(int=0x1F9A))
TOOLS = ["agenticorg:sales:read", "tool:hubspot:read:list_contacts"]
ADMIN = Caller(user_id=uuid.UUID(int=0xAD), role="admin", domains=None, is_admin=True, is_machine=False)


def _agent(grantex: dict[str, Any] | None) -> MagicMock:
    agent = MagicMock()
    agent.id = uuid.UUID(int=0xA1)
    agent.tenant_id = uuid.UUID(TENANT)
    agent.company_id = None
    agent.domain = "sales"
    agent.status = "shadow"
    agent.visibility = "tenant"
    agent.owner_user_id = None
    agent.connector_ids = []
    agent.authorized_tools = ["list_contacts"]
    agent.system_prompt_text = "prompt"
    agent.config = {"k": 1, **({"grantex": grantex} if grantex is not None else {})}
    return agent


def _registered(route_scopes: list[str] | None = None) -> dict[str, Any]:
    grantex: dict[str, Any] = {"grantex_agent_id": "ag_1", "grantex_did": "did:x", "grantex_scopes": list(TOOLS)}
    if route_scopes is not None:
        grantex["route_scopes"] = list(route_scopes)
    return grantex


async def _patch(
    agent: MagicMock,
    update: dict[str, Any],
    *,
    caller: Any = ADMIN,
    client: Any = None,
    tool_scopes: list[str] | None = None,
) -> tuple[dict[str, Any], MagicMock]:
    from api.v1 import agents

    result = MagicMock()
    result.scalar_one_or_none.return_value = agent
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    body = MagicMock()
    body.model_dump.return_value = dict(update)
    with (
        patch.object(agents, "get_tenant_session", return_value=ctx),
        patch.object(agents, "_validate_authorized_tools", return_value=[]),
        patch.object(agents, "_resolve_connector_configs", AsyncMock(return_value=({}, ["hubspot"]))),
        patch("auth.grantex_registration._tools_to_scopes", return_value=list(tool_scopes or TOOLS)),
        patch("auth.grantex_registration._get_grantex_client", return_value=client),
        patch("api.v1.agents.asyncio.to_thread", AsyncMock(side_effect=lambda fn, *a, **k: fn(*a, **k))),
    ):
        out = await agents.update_agent(agent_id=agent.id, body=body, tenant_id=TENANT, caller=caller)
    return out, session


def _audits(session: MagicMock) -> list[AuditLog]:
    return [c.args[0] for c in session.add.call_args_list if isinstance(c.args[0], AuditLog)]


# ── Validation ─────────────────────────────────────────────────────────────


def test_grantable_scopes_are_exactly_the_route_family_scopes() -> None:
    assert GRANTABLE_ROUTE_SCOPES == {scope for pair in SCOPE_FAMILIES.values() for scope in pair}
    assert "agenticorg:admin" not in GRANTABLE_ROUTE_SCOPES


@pytest.mark.parametrize(
    "bad",
    [
        ["agenticorg:admin"],
        ["agents:run"],  # a legacy alias, not the canonical scope
        ["connectors:read"],
        ["mcp:call"],  # an unenforced family
        ["agents:read", "tool:hubspot:read:list_contacts"],
        ["AGENTS:READ"],
        [" agents:read"],
    ],
)
def test_validation_refuses_anything_but_canonical_route_scopes(bad: list[str]) -> None:
    with pytest.raises(ValueError, match="cannot be granted"):
        validate_route_scopes(bad)


@pytest.mark.parametrize("bad", [None, "agents:read", ["agents:read", 1]])
def test_validation_refuses_a_non_list(bad: object) -> None:
    with pytest.raises(ValueError, match="must be a list"):
        validate_route_scopes(bad)


def test_validation_sorts_and_deduplicates() -> None:
    assert validate_route_scopes(["workflows:write", "agents:read", "agents:read"]) == [
        "agents:read",
        "workflows:write",
    ]
    assert validate_route_scopes([]) == []


# ── Who may set them ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "caller",
    [
        Caller(user_id=uuid.UUID(int=0xB0), role="sales_manager", domains=["sales"], is_admin=False, is_machine=False),
        Caller(user_id=None, role="", domains=None, is_admin=True, is_machine=True),  # an admin API key
    ],
    ids=["non_admin_human", "admin_machine"],
)
async def test_only_a_human_tenant_admin_may_grant_route_scopes(caller: Caller) -> None:
    agent = _agent(_registered())
    client = MagicMock()
    with pytest.raises(HTTPException) as info:
        await _patch(agent, {"route_scopes": ["agents:read"]}, caller=caller, client=client)
    assert info.value.status_code == 403
    client._http.patch.assert_not_called()
    assert "route_scopes" not in agent.config["grantex"]


async def test_an_invalid_scope_is_refused_with_422_and_nothing_changes() -> None:
    agent = _agent(_registered())
    client = MagicMock()
    with pytest.raises(HTTPException) as info:
        await _patch(agent, {"route_scopes": ["agenticorg:admin"]}, client=client)
    assert info.value.status_code == 422
    assert "agenticorg:admin" in info.value.detail
    client._http.patch.assert_not_called()
    assert agent.config["grantex"] == _registered()


# ── Registration and storage ───────────────────────────────────────────────


async def test_granting_route_scopes_updates_the_registration_then_stores_them_apart() -> None:
    agent = _agent(_registered())
    client = MagicMock()
    _, session = await _patch(agent, {"route_scopes": ["workflows:write", "agents:read"]}, client=client)

    expected = {"scopes": [*TOOLS, "agents:read", "workflows:write"]}
    client._http.patch.assert_called_once_with("/v1/agents/ag_1", expected)
    grantex = agent.config["grantex"]
    assert grantex["grantex_scopes"] == TOOLS  # run grants are minted from these only
    assert grantex["route_scopes"] == ["agents:read", "workflows:write"]
    [audit] = _audits(session)
    assert audit.event_type == "agent.route_scopes.updated"
    assert audit.details == {"before": [], "after": ["agents:read", "workflows:write"]}
    assert audit.actor_id == str(ADMIN.user_id)


async def test_when_grantex_refuses_nothing_is_stored() -> None:
    agent = _agent(_registered())
    client = MagicMock(**{"_http.patch.side_effect": RuntimeError("refused")})
    with pytest.raises(HTTPException) as info:
        await _patch(agent, {"route_scopes": ["agents:read"]}, client=client)
    assert info.value.status_code == 502
    assert agent.config["grantex"] == _registered()


async def test_revoking_route_scopes_removes_them_from_the_registration() -> None:
    agent = _agent(_registered(["agents:read"]))
    client = MagicMock()
    _, session = await _patch(agent, {"route_scopes": []}, client=client)
    client._http.patch.assert_called_once_with("/v1/agents/ag_1", {"scopes": TOOLS})
    assert agent.config["grantex"]["route_scopes"] == []
    assert _audits(session)[0].details == {"before": ["agents:read"], "after": []}


async def test_resending_the_same_route_scopes_changes_nothing() -> None:
    agent = _agent(_registered(["agents:read"]))
    client = MagicMock()
    _, session = await _patch(agent, {"route_scopes": ["agents:read"]}, client=client)
    client._http.patch.assert_not_called()
    assert _audits(session) == []


async def test_a_tools_patch_keeps_the_route_scopes_on_the_registration() -> None:
    agent = _agent(_registered(["audit:read"]))
    client = MagicMock()
    new_tools = ["agenticorg:sales:read", "tool:hubspot:read:get_contact"]
    await _patch(agent, {"authorized_tools": ["get_contact"]}, client=client, tool_scopes=new_tools)
    client._http.patch.assert_called_once_with("/v1/agents/ag_1", {"scopes": [*new_tools, "audit:read"]})
    assert agent.config["grantex"]["grantex_scopes"] == new_tools
    assert agent.config["grantex"]["route_scopes"] == ["audit:read"]


async def test_route_scopes_on_an_unregistered_agent_are_stored_for_later() -> None:
    agent = _agent(None)
    await _patch(agent, {"route_scopes": ["agents:read"]}, client=None)
    assert agent.config == {"k": 1, "grantex": {"route_scopes": ["agents:read"]}}


async def test_route_scopes_count_toward_the_registration_limit() -> None:
    agent = _agent(_registered())
    client = MagicMock()
    many_tools = [f"tool:hubspot:read:t{i}" for i in range(99)]
    with pytest.raises(HTTPException) as info:
        await _patch(
            agent,
            {"authorized_tools": ["many"], "route_scopes": ["agents:read", "audit:read"]},
            client=client,
            tool_scopes=many_tools,
        )
    assert (info.value.status_code, info.value.detail["reason_code"]) == (422, "scope_limit_exceeded")
    client._http.patch.assert_not_called()


# ── Backfill and run grants ────────────────────────────────────────────────


async def test_the_backfill_keeps_route_scopes_on_the_registration_but_not_in_storage() -> None:
    from scripts.refresh_grantex_scopes import refresh_agent_scopes

    persist = AsyncMock()
    client = MagicMock()
    new_tools = ["agenticorg:sales:read", "tool:hubspot:read:get_contact"]
    with (
        patch("auth.grantex_registration._tools_to_scopes", return_value=list(new_tools)),
        patch(
            "scripts.refresh_grantex_scopes.asyncio.to_thread",
            AsyncMock(side_effect=lambda fn, *a, **k: fn(*a, **k)),
        ),
    ):
        result = await refresh_agent_scopes(
            agent_id="agent-1",
            domain="sales",
            authorized_tools=["get_contact"],
            config={"grantex": _registered(["agents:read"])},
            connector_names=["hubspot"],
            grantex_client=client,
            apply=True,
            persist=persist,
        )
    assert result.outcome == "updated"
    client._http.patch.assert_called_once_with("/v1/agents/ag_1", {"scopes": [*new_tools, "agents:read"]})
    assert persist.await_args.args[0] == new_tools


def test_run_grants_and_delegation_use_tool_scopes_only() -> None:
    """The token pool and delegation read ``grantex_scopes``; route scopes are stored
    elsewhere so a run grant or a delegated child grant never carries them."""
    import inspect

    from api.v1 import agents
    from auth import run_grants, token_pool

    for module in (run_grants, token_pool):
        assert "route_scopes" not in inspect.getsource(module)
    delegate = inspect.getsource(agents.delegate_to_agent)
    assert 'child_grantex.get("grantex_scopes"' in delegate
    assert "route_scopes" not in delegate
