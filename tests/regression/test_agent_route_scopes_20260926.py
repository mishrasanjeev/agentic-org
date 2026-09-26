# SPDX-License-Identifier: Apache-2.0
"""FINDINGS A-64: a tenant admin can grant an agent route scopes.

Route scope checks apply to Grantex agent tokens (H-1), but registration only ever gave an
agent tool scopes and ``agenticorg:{domain}:read``, so an agent token was refused on every
route family. ``PATCH /agents/{id}`` now accepts ``route_scopes``:

* only an active human tenant admin, confirmed from the user row, may set them, only on a
  shared agent registered on Grantex; only canonical route-family scopes are accepted
  (never ``agenticorg:admin``, aliases or unenforced scopes);
* the registration carries tool scopes plus route scopes, Grantex first and storage after;
  an explicit ``route_scopes`` PATCH always writes the registration so drift can be repaired;
* storage keeps route scopes apart, so run grants and delegated grants never carry them;
* a tools PATCH and the scope backfill keep them on the registration, the backfill re-reading
  them just before its push;
* every change is audited with before and after.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, Request

from api.deps import ActiveHumanAdmin
from api.route_enforcement import GRANTABLE_ROUTE_SCOPES, SCOPE_FAMILIES, validate_route_scopes
from core.models.audit import AuditLog
from core.ownership import Caller

TENANT = str(uuid.UUID(int=0x1F9A))
TOOLS = ["agenticorg:sales:read", "tool:hubspot:read:list_contacts"]
NEW_TOOLS = ["agenticorg:sales:read", "tool:hubspot:read:get_contact"]
ADMIN = Caller(user_id=uuid.UUID(int=0xAD), role="admin", domains=None, is_admin=True, is_machine=False)
DB_ADMIN = ActiveHumanAdmin(
    user_id=uuid.UUID(int=0xAD), tenant_id=uuid.UUID(TENANT), email="admin@example.com", role="admin"
)


def _request() -> Request:
    return Request({"type": "http", "method": "PATCH", "path": "/", "headers": []})


def _agent(grantex: dict[str, Any] | None, *, visibility: str = "tenant", owner: uuid.UUID | None = None) -> MagicMock:
    agent = MagicMock()
    agent.id = uuid.UUID(int=0xA1)
    agent.tenant_id = uuid.UUID(TENANT)
    agent.company_id = None
    agent.domain = "sales"
    agent.status = "shadow"
    agent.visibility = visibility
    agent.owner_user_id = owner
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


def _session_for(*agents: MagicMock) -> tuple[MagicMock, MagicMock]:
    results = []
    for agent in agents:
        result = MagicMock()
        result.scalar_one_or_none.return_value = agent
        results.append(result)
    session = MagicMock()
    session.execute = AsyncMock(side_effect=results)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return session, ctx


async def _patch(
    agent: MagicMock,
    update: dict[str, Any],
    *,
    caller: Any = ADMIN,
    client: Any = None,
    tool_scopes: list[str] | None = None,
    db_admin: Any = DB_ADMIN,
    http_request: Any = None,
) -> tuple[dict[str, Any], MagicMock]:
    from api.v1 import agents

    session, ctx = _session_for(agent)
    body = MagicMock()
    body.model_dump.return_value = dict(update)
    if isinstance(db_admin, BaseException):
        admin_check = AsyncMock(side_effect=db_admin)
    else:
        admin_check = AsyncMock(return_value=db_admin)
    with (
        patch.object(agents, "get_tenant_session", return_value=ctx),
        patch.object(agents, "get_active_human_admin", admin_check),
        patch.object(agents, "_validate_authorized_tools", return_value=[]),
        patch.object(agents, "_resolve_connector_configs", AsyncMock(return_value=({}, ["hubspot"]))),
        patch("auth.grantex_registration._tools_to_scopes", return_value=list(tool_scopes or TOOLS)),
        patch("auth.grantex_registration._get_grantex_client", return_value=client),
        patch("api.v1.agents.asyncio.to_thread", AsyncMock(side_effect=lambda fn, *a, **k: fn(*a, **k))),
    ):
        out = await agents.update_agent(
            agent_id=agent.id,
            body=body,
            tenant_id=TENANT,
            caller=caller,
            http_request=_request() if http_request is None else http_request,
        )
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


async def test_a_null_route_scopes_is_refused_with_422() -> None:
    agent = _agent(_registered())
    with pytest.raises(HTTPException) as info:
        await _patch(agent, {"route_scopes": None}, client=MagicMock())
    assert info.value.status_code == 422


# ── Who may set them, and on which agents ─────────────────────────────────


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


async def test_an_admin_claim_is_revalidated_against_the_user_row() -> None:
    """A session whose user was demoted still carries the admin claim; the user row says no."""
    agent = _agent(_registered())
    client = MagicMock()
    with pytest.raises(HTTPException) as info:
        await _patch(
            agent,
            {"route_scopes": ["agents:read"]},
            client=client,
            db_admin=HTTPException(403, "A human tenant administrator is required"),
        )
    assert info.value.status_code == 403
    client._http.patch.assert_not_called()


async def test_a_direct_call_without_a_request_is_refused() -> None:
    from fastapi.params import Depends as DependsParam

    agent = _agent(_registered())
    with pytest.raises(HTTPException) as info:
        await _patch(agent, {"route_scopes": ["agents:read"]}, client=MagicMock(), http_request=DependsParam())
    assert info.value.status_code == 403


async def test_a_personal_agent_cannot_be_granted_route_scopes() -> None:
    agent = _agent(_registered(), visibility="personal", owner=uuid.UUID(int=0xB0))
    client = MagicMock()
    with pytest.raises(HTTPException) as info:
        await _patch(agent, {"route_scopes": ["agents:read"]}, client=client)
    assert info.value.status_code == 403
    assert "shared agent" in info.value.detail
    client._http.patch.assert_not_called()


async def test_an_unregistered_agent_is_refused_rather_than_storing_dead_scopes() -> None:
    agent = _agent(None)
    with pytest.raises(HTTPException) as info:
        await _patch(agent, {"route_scopes": ["agents:read"]}, client=None)
    assert info.value.status_code == 409
    assert agent.config == {"k": 1}


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
    assert audit.actor_id == str(DB_ADMIN.user_id)


async def test_scope_changes_lock_the_agent_row() -> None:
    agent = _agent(_registered())
    _, session = await _patch(agent, {"route_scopes": ["agents:read"]}, client=MagicMock())
    statement = session.execute.await_args_list[0].args[0]
    assert statement._for_update_arg is not None


async def test_other_changes_do_not_lock_the_agent_row() -> None:
    agent = _agent(_registered())
    _, session = await _patch(agent, {"designation": "Analyst"}, client=MagicMock())
    statement = session.execute.await_args_list[0].args[0]
    assert statement._for_update_arg is None


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


async def test_resending_route_scopes_repairs_the_registration_without_an_audit_event() -> None:
    """Storage already says [], but the registration may still carry a revoked scope (a
    concurrent backfill pushed it back). Re-sending the list must write the registration."""
    agent = _agent(_registered([]))
    client = MagicMock()
    _, session = await _patch(agent, {"route_scopes": []}, client=client)
    client._http.patch.assert_called_once_with("/v1/agents/ag_1", {"scopes": TOOLS})
    assert _audits(session) == []


async def test_a_tools_patch_keeps_the_route_scopes_on_the_registration() -> None:
    agent = _agent(_registered(["audit:read"]))
    client = MagicMock()
    await _patch(agent, {"authorized_tools": ["get_contact"]}, client=client, tool_scopes=NEW_TOOLS)
    client._http.patch.assert_called_once_with("/v1/agents/ag_1", {"scopes": [*NEW_TOOLS, "audit:read"]})
    assert agent.config["grantex"]["grantex_scopes"] == NEW_TOOLS
    assert agent.config["grantex"]["route_scopes"] == ["audit:read"]


async def test_tools_and_route_scopes_in_one_patch_make_one_registration_write() -> None:
    agent = _agent(_registered())
    client = MagicMock()
    _, session = await _patch(
        agent,
        {"authorized_tools": ["get_contact"], "route_scopes": ["agents:read"]},
        client=client,
        tool_scopes=NEW_TOOLS,
    )
    client._http.patch.assert_called_once_with("/v1/agents/ag_1", {"scopes": [*NEW_TOOLS, "agents:read"]})
    assert agent.config["grantex"]["grantex_scopes"] == NEW_TOOLS
    assert agent.config["grantex"]["route_scopes"] == ["agents:read"]
    assert len(_audits(session)) == 1


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


# ── Backfill ───────────────────────────────────────────────────────────────


async def _backfill(route_scopes_now: list[str] | None) -> tuple[Any, MagicMock, AsyncMock]:
    from scripts.refresh_grantex_scopes import refresh_agent_scopes

    persist = AsyncMock()
    client = MagicMock()
    with (
        patch("auth.grantex_registration._tools_to_scopes", return_value=list(NEW_TOOLS)),
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
            current_route_scopes=None if route_scopes_now is None else AsyncMock(return_value=route_scopes_now),
        )
    return result, client, persist


async def test_the_backfill_keeps_route_scopes_on_the_registration_but_not_in_storage() -> None:
    result, client, persist = await _backfill(None)
    assert result.outcome == "updated"
    client._http.patch.assert_called_once_with("/v1/agents/ag_1", {"scopes": [*NEW_TOOLS, "agents:read"]})
    assert persist.await_args.args[0] == NEW_TOOLS


async def test_the_backfill_pushes_route_scopes_read_at_push_time_not_the_listing_snapshot() -> None:
    """An admin revoked the route scopes after the backfill listed the agent."""
    _, client, _ = await _backfill([])
    client._http.patch.assert_called_once_with("/v1/agents/ag_1", {"scopes": NEW_TOOLS})


# ── Run grants and delegation never carry route scopes ─────────────────────


async def test_the_token_pool_delegates_only_stored_tool_scopes_when_the_registration_has_route_scopes() -> None:
    from auth.token_pool import TokenPool

    client = MagicMock()
    client.agents.get.return_value = SimpleNamespace(scopes=[*TOOLS, "agents:write", "audit:read"])
    with patch("auth.token_pool.asyncio.to_thread", AsyncMock(side_effect=lambda fn, *a, **k: fn(*a, **k))):
        delegated = await TokenPool._registered_scopes(client, "ag_1", list(TOOLS))
    assert delegated == TOOLS


async def test_run_grants_are_requested_with_tool_scopes_only() -> None:
    from auth import run_grants
    from auth.grant_enforcement import EnforcementMode

    captured: dict[str, Any] = {}

    async def fake_get_run_grant_token(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return SimpleNamespace(token="t", source="pool", grant_id="g", expires_at=None, ttl_seconds=60)

    with patch("auth.token_pool.token_pool.get_run_grant_token", side_effect=fake_get_run_grant_token):
        await run_grants._resolve_run_agent_grant(
            mode=EnforcementMode.DENY,
            tenant_id=TENANT,
            agent_id="agent-1",
            supplied="",
            grantex_config=_registered(["agents:write", "audit:read"]),
            runtime="test",
        )
    assert captured["scopes"] == TOOLS


async def test_delegation_passes_tool_scopes_only() -> None:
    from api.v1 import agents

    child = _agent(_registered(["agents:write"]))
    child.parent_agent_id = uuid.UUID(int=0xA2)
    parent = _agent({"grantex_agent_id": "ag_parent", "grantex_did": "did:p", "grantex_scopes": TOOLS})
    parent.id = uuid.UUID(int=0xA2)
    _, ctx = _session_for(child, parent)
    setup = MagicMock(return_value={})
    with (
        patch.object(agents, "get_tenant_session", return_value=ctx),
        patch("auth.grantex_registration.setup_delegation", setup),
    ):
        result = await agents.delegate_to_agent(
            agent_id=child.id, body={"parent_grant_token": "p"}, tenant_id=TENANT, caller=ADMIN
        )
    assert result["status"] == "delegated"
    assert setup.call_args.kwargs["child_scopes"] == TOOLS


async def test_a_backfill_route_scope_read_failure_is_reported_and_nothing_is_pushed() -> None:
    from scripts.refresh_grantex_scopes import FAILED_OUTCOMES, refresh_agent_scopes

    persist = AsyncMock()
    client = MagicMock()
    with patch("auth.grantex_registration._tools_to_scopes", return_value=list(NEW_TOOLS)):
        result = await refresh_agent_scopes(
            agent_id="agent-1",
            domain="sales",
            authorized_tools=["get_contact"],
            config={"grantex": _registered(["agents:read"])},
            connector_names=["hubspot"],
            grantex_client=client,
            apply=True,
            persist=persist,
            current_route_scopes=AsyncMock(side_effect=RuntimeError("db down")),
        )
    assert result.outcome == "route_scope_read_failed"
    assert result.outcome in FAILED_OUTCOMES
    client._http.patch.assert_not_called()
    persist.assert_not_awaited()

