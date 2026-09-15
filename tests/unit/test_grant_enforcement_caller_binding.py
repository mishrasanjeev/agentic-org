# SPDX-License-Identifier: Apache-2.0
"""PRD F-1 — a caller's Grantex token never stands in for the run agent's grant.

Acceptance criteria covered here:

* chat, A2A and MCP bind a caller token to the agent it was issued to: a token
  for the run agent is the run grant; a token for any other agent is kept
  alongside the run agent's own grant and BOTH must allow every tool call;
* a caller token is always enforced strictly (the legacy path did), the run
  agent's grant in the tenant's mode;
* the tool gateway runs the grant check and then every legacy check, and a
  token passed to it is never downgraded to warn;
* ``BaseAgent`` refreshes a pool grant before calls and log entries carry the
  agent type.
"""

from __future__ import annotations

import inspect
import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from structlog.testing import capture_logs

from auth.grant_enforcement import EnforcementMode, GrantCallContext
from auth.run_grants import RunGrant, check_run_grant, resolve_run_grant
from auth.token_pool import RunGrantToken

TENANT = str(uuid.UUID(int=0x1F4A))
RUN_AGENT = str(uuid.UUID(int=0xB1))
OTHER_AGENT = str(uuid.UUID(int=0xB2))
CALLER = "placeholder-caller-grant"  # noqa: S105 - not a credential
RUN = "placeholder-run-agent-grant"  # noqa: S105 - not a credential
REGISTERED = {"grantex_agent_id": "ag_run", "grantex_scopes": ["tool:hubspot:read:get_contact"]}
CTX = GrantCallContext(tenant_id=TENANT, agent_id=RUN_AGENT, agent_type="analyst", runtime="test")


def _minted(token: str = RUN) -> AsyncMock:
    return AsyncMock(return_value=RunGrantToken(token=token, grant_id="grnt_run", expires_at=None, source="minted"))


def _enforcer(allowed_tokens: set[str]) -> MagicMock:
    client = MagicMock()

    def _enforce(*, grant_token: str, **_: Any) -> SimpleNamespace:
        allowed = grant_token in allowed_tokens
        return SimpleNamespace(
            allowed=allowed,
            reason="" if allowed else "No scope grants access",
            reason_code="" if allowed else "tool_not_granted",
            sub_reason="",
            grant_id="grnt_" + grant_token[-4:],
        )

    client.enforce.side_effect = _enforce
    return client


# ── Resolution ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("mode", [EnforcementMode.WARN, EnforcementMode.DENY])
async def test_a_caller_token_for_another_agent_keeps_the_run_agents_own_grant(mode):
    with patch("auth.token_pool.token_pool.get_run_grant_token", _minted()) as pool:
        grant = await resolve_run_grant(
            tenant_id=TENANT,
            agent_id=RUN_AGENT,
            grantex_config=REGISTERED,
            mode=mode,
            caller_token=CALLER,
            caller_agent_id=OTHER_AGENT,
        )
    pool.assert_awaited_once()
    assert (grant.token, grant.source) == (RUN, "minted")
    assert (grant.caller_token, grant.caller_agent_id) == (CALLER, OTHER_AGENT)


async def test_a_caller_token_without_an_agent_identity_is_also_kept_separately():
    with patch("auth.token_pool.token_pool.get_run_grant_token", _minted()):
        grant = await resolve_run_grant(
            tenant_id=TENANT,
            agent_id=RUN_AGENT,
            grantex_config=REGISTERED,
            mode=EnforcementMode.DENY,
            caller_token=CALLER,
        )
    assert (grant.token, grant.caller_token) == (RUN, CALLER)


async def test_a_caller_token_issued_to_the_run_agent_is_the_run_grant():
    with patch("auth.token_pool.token_pool.get_run_grant_token", AsyncMock(side_effect=AssertionError("no mint"))):
        grant = await resolve_run_grant(
            tenant_id=TENANT,
            agent_id=RUN_AGENT,
            grantex_config=REGISTERED,
            mode=EnforcementMode.DENY,
            caller_token=CALLER,
            caller_agent_id=RUN_AGENT,
        )
    assert (grant.token, grant.source, grant.caller_token) == (CALLER, "supplied", "")


async def test_off_mode_passes_the_caller_token_through_as_before():
    grant = await resolve_run_grant(
        tenant_id=TENANT, agent_id=RUN_AGENT, mode=EnforcementMode.OFF, caller_token=CALLER, caller_agent_id=OTHER_AGENT
    )
    assert (grant.mode, grant.token, grant.caller_token) == (EnforcementMode.OFF, CALLER, "")


async def test_a_run_agent_without_a_grant_still_carries_the_caller_token():
    grant = await resolve_run_grant(
        tenant_id=TENANT, agent_id="", mode=EnforcementMode.DENY, caller_token=CALLER, caller_agent_id=OTHER_AGENT
    )
    assert (grant.token, grant.missing_sub_reason, grant.caller_token) == ("", "no_agent", CALLER)


# ── Both tokens must allow ───────────────────────────────────────────────


def _bound(mode: EnforcementMode, run_token: str = RUN) -> RunGrant:
    return RunGrant(mode=mode, token=run_token, source="minted", caller_token=CALLER, caller_agent_id=OTHER_AGENT)


@pytest.mark.parametrize(
    ("mode", "allowed_tokens", "dispatch", "denied_source"),
    [
        (EnforcementMode.DENY, {CALLER, RUN}, True, None),
        (EnforcementMode.DENY, {CALLER}, False, "minted"),
        (EnforcementMode.DENY, {RUN}, False, "caller"),
        (EnforcementMode.DENY, set(), False, "caller"),
        # warn: the caller token stays strict; the run agent's grant only records.
        (EnforcementMode.WARN, {RUN}, False, "caller"),
        (EnforcementMode.WARN, {CALLER}, True, "minted"),
    ],
)
async def test_every_call_needs_both_the_caller_token_and_the_run_agents_grant(
    mode, allowed_tokens, dispatch, denied_source
):
    client = _enforcer(allowed_tokens)
    with capture_logs() as logs:
        check = await check_run_grant(
            _bound(mode),
            connector="hubspot",
            tool="create_contact",
            context=GrantCallContext(tenant_id=TENANT, agent_id=RUN_AGENT, runtime="test", grant_source="minted"),
            client_factory=lambda: client,
        )
    assert check.dispatch_allowed is dispatch
    events = [e for e in logs if e["event"] in ("grant_enforcement_denied", "grant_enforcement_would_deny")]
    assert [e["grant_source"] for e in events][:1] == ([denied_source] if denied_source else [])


async def test_a_caller_for_another_agent_cannot_run_the_run_agent_without_its_grant():
    client = _enforcer({CALLER})
    check = await check_run_grant(
        _bound(EnforcementMode.DENY, run_token=""),
        connector="hubspot",
        tool="create_contact",
        context=CTX,
        client_factory=lambda: client,
    )
    assert check.dispatch_allowed is False
    assert check.denial is not None and check.denial.reason.value == "grant_missing"


# ── Routes: chat, A2A, MCP ───────────────────────────────────────────────


async def test_a2a_and_mcp_bind_a_caller_token_for_another_agent_to_the_type_agents_grant():
    from api.v1 import agents

    row = SimpleNamespace(id=uuid.UUID(RUN_AGENT), config={"grantex": REGISTERED})
    with (
        patch.object(agents, "resolve_enforcement_mode", AsyncMock(return_value=EnforcementMode.DENY)),
        patch.object(agents, "_select_agent_for_type", AsyncMock(return_value=row)),
        patch("auth.token_pool.token_pool.get_run_grant_token", _minted()),
    ):
        grant = await agents._resolve_run_grant_for_type(
            tenant_id=TENANT,
            agent_type="ap_processor",
            company_id=None,
            caller_token=CALLER,
            caller_agent_id=OTHER_AGENT,
            runtime="a2a",
        )
    assert (grant.token, grant.caller_token) == (RUN, CALLER)

    client = _enforcer({CALLER})  # the caller's own scopes would allow it ...
    check = await check_run_grant(
        grant, connector="hubspot", tool="create_contact", context=CTX, client_factory=lambda: client
    )
    assert check.dispatch_allowed is False  # ... but the type agent's grant does not


async def test_a2a_and_mcp_use_a_caller_token_issued_to_the_type_agent_as_its_grant():
    from api.v1 import agents

    row = SimpleNamespace(id=uuid.UUID(RUN_AGENT), config={"grantex": REGISTERED})
    with (
        patch.object(agents, "resolve_enforcement_mode", AsyncMock(return_value=EnforcementMode.DENY)),
        patch.object(agents, "_select_agent_for_type", AsyncMock(return_value=row)),
        patch("auth.token_pool.token_pool.get_run_grant_token", AsyncMock(side_effect=AssertionError("no mint"))),
    ):
        grant = await agents._resolve_run_grant_for_type(
            tenant_id=TENANT,
            agent_type="ap_processor",
            company_id=None,
            caller_token=CALLER,
            caller_agent_id=RUN_AGENT,
            runtime="mcp",
        )
    assert (grant.token, grant.source, grant.caller_token) == (CALLER, "supplied", "")


async def test_a2a_and_mcp_with_no_type_agent_keep_the_caller_token_and_have_no_run_grant():
    from api.v1 import agents

    with (
        patch.object(agents, "resolve_enforcement_mode", AsyncMock(return_value=EnforcementMode.DENY)),
        patch.object(agents, "_select_agent_for_type", AsyncMock(return_value=None)),
    ):
        grant = await agents._resolve_run_grant_for_type(
            tenant_id=TENANT,
            agent_type="ap_processor",
            company_id=None,
            caller_token=CALLER,
            caller_agent_id=OTHER_AGENT,
            runtime="a2a",
        )
    assert (grant.token, grant.missing_sub_reason, grant.caller_token) == ("", "no_agent", CALLER)


@pytest.mark.parametrize(("module", "function"), [("api.v1.a2a", "create_task"), ("api.v1.mcp", "call_tool")])
def test_a2a_and_mcp_pass_the_callers_agent_identity(module, function):
    import importlib

    src = inspect.getsource(getattr(importlib.import_module(module), function))
    call = src[src.index("_resolve_run_grant_for_type(") :]
    call = call[: call.index("runtime=")]
    assert "caller_token=grant_token" in call
    assert 'caller_agent_id=str(getattr(request.state, "agent_id", "") or "")' in call
    assert "supplied_token" not in call


def test_chat_binds_the_caller_token_to_its_agent():
    from api.v1 import chat

    src = inspect.getsource(chat.chat_query)
    call = src[src.index("run_grant = await resolve_run_grant(") :]
    call = call[: call.index("\n    )")]
    assert 'caller_token=getattr(request.state, "grant_token", None)' in call
    assert 'caller_agent_id=str(getattr(request.state, "agent_id", "") or "")' in call
    assert "supplied_token" not in call


async def test_chat_resolution_for_a_routed_agent_other_than_the_caller_requires_both():
    # The call chat makes, with a caller token issued to a different agent.
    with patch("auth.token_pool.token_pool.get_run_grant_token", _minted()):
        grant = await resolve_run_grant(
            tenant_id=TENANT,
            agent_id=RUN_AGENT,
            grantex_config=REGISTERED,
            mode=EnforcementMode.DENY,
            caller_token=CALLER,
            caller_agent_id=OTHER_AGENT,
            runtime="chat",
        )
    client = _enforcer({CALLER})
    check = await check_run_grant(
        grant, connector="hubspot", tool="get_contact", context=CTX, client_factory=lambda: client
    )
    assert check.dispatch_allowed is False


# ── Tool gateway: grant check plus every legacy check ────────────────────


class _Connector:
    def __init__(self) -> None:
        self.calls = 0

    async def execute_tool(self, tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        return {"id": "c-1"}


async def _gateway(run_grant: RunGrant, *, agent_scopes: list[str], grant_token: str | None, client: MagicMock):
    from core.tool_gateway.gateway import ToolGateway

    gateway = ToolGateway()
    connector = _Connector()
    gateway.register_connector("hubspot", connector, tenant_id=TENANT)
    with patch("core.langgraph.grantex_auth.get_grantex_client", return_value=client), capture_logs() as logs:
        result = await gateway.execute(
            tenant_id=TENANT,
            agent_id=RUN_AGENT,
            agent_scopes=agent_scopes,
            connector_name="hubspot",
            tool_name="get_contact",
            params={},
            grant_token=grant_token,
            run_grant=run_grant,
            agent_type="analyst",
        )
    return result, connector, logs


async def test_gateway_still_runs_legacy_scope_checks_when_the_grant_covers_the_call():
    grant = RunGrant(mode=EnforcementMode.DENY, token=RUN, source="minted")
    result, connector, _ = await _gateway(grant, agent_scopes=[], grant_token=None, client=_enforcer({RUN}))
    assert result["error"]["message"] == "scope_denied: missing_grant_and_legacy_scopes"
    assert connector.calls == 0


async def test_gateway_enforces_a_passed_token_strictly_in_warn_mode():
    grant = RunGrant(mode=EnforcementMode.WARN, source="none", missing_sub_reason="minting_unconfigured")
    result, connector, logs = await _gateway(
        grant, agent_scopes=["tool:hubspot:read:contact"], grant_token=CALLER, client=_enforcer(set())
    )
    assert result["error"]["message"].startswith("scope_denied")
    assert connector.calls == 0
    assert [e["agent_type"] for e in logs if e["event"] == "grant_enforcement_would_deny"] == ["analyst"]


async def test_gateway_runs_a_call_both_the_grant_and_legacy_checks_allow():
    grant = RunGrant(mode=EnforcementMode.DENY, token=RUN, source="minted")
    result, connector, _ = await _gateway(grant, agent_scopes=[], grant_token=RUN, client=_enforcer({RUN}))
    assert result == {"id": "c-1"} and connector.calls == 1


# ── BaseAgent ─────────────────────────────────────────────────────────────


async def test_base_agent_refreshes_its_grant_before_later_calls_and_logs_its_type():
    from core.agents.base import BaseAgent

    agent = BaseAgent(agent_id=RUN_AGENT, tenant_id=TENANT, authorized_tools=["hubspot:get_contact"])
    agent.agent_type = "analyst"
    first = RunGrant(mode=EnforcementMode.WARN, token=RUN, source="minted")
    second = RunGrant(mode=EnforcementMode.WARN, token="placeholder-fresh", source="minted")
    executed = AsyncMock(return_value={"id": "c-1"})
    with (
        patch("core.agents.base.resolve_run_grant", AsyncMock(return_value=first)) as resolve,
        patch("core.agents.base.refresh_run_grant", AsyncMock(return_value=second)) as refresh,
        patch("core.langgraph.tool_adapter.execute_agent_tool", executed),
    ):
        await agent._call_tool("hubspot", "get_contact", {})
        await agent._call_tool("hubspot", "get_contact", {})
    resolve.assert_awaited_once()
    refresh.assert_awaited_once_with(first)
    assert [c.kwargs["run_grant"] for c in executed.await_args_list] == [first, second]
    assert {c.kwargs["agent_type"] for c in executed.await_args_list} == {"analyst"}
