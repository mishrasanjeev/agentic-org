# SPDX-License-Identifier: Apache-2.0
"""PRD F-1 end to end against a real Grantex auth service.

Registers an agent through ``auth.grantex_registration`` (the production scope
mapping), obtains a root grant for an orchestrator agent, mints a delegated
per-run grant through ``auth.token_pool``, refreshes an agent token through
the pool, and runs agent tool calls through the real LangGraph graph in warn
and deny: calls the grant covers run, calls it does not are recorded (warn) or
refused (deny) with the Grantex reason.

Runs only when pointed at a Grantex auth service with a sandbox developer key
(authorization requests are approved without a consent screen)::

    AGENTICORG_GRANTEX_E2E_URL=http://127.0.0.1:58500 \\
    AGENTICORG_GRANTEX_E2E_SANDBOX_KEY=<sandbox developer key> \\
    pytest tests/integration/test_grant_enforcement_grantex_e2e.py

Reason codes come from the Grantex SDK's ``EnforceResult.reason_code``; with an
SDK that predates them every denial is ``unclassified`` and the test says so.
"""

from __future__ import annotations

import os
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import HumanMessage, SystemMessage
from structlog.testing import capture_logs

from auth.grant_enforcement import EnforcementMode
from auth.run_grants import RunGrant, resolve_run_grant
from core.test_doubles.scripted_model import final, tool_call

BASE_URL = os.getenv("AGENTICORG_GRANTEX_E2E_URL", "")
SANDBOX_KEY = os.getenv("AGENTICORG_GRANTEX_E2E_SANDBOX_KEY", "")

pytestmark = [
    pytest.mark.skipif(not (BASE_URL and SANDBOX_KEY), reason="needs AGENTICORG_GRANTEX_E2E_URL and _SANDBOX_KEY"),
    pytest.mark.real_flag_store,
]

TENANT = str(uuid.uuid4())
AGENT = str(uuid.uuid4())
AUTHORIZED_TOOLS = ["hubspot:list_contacts", "salesforce:query"]
CALLED_TOOLS = ["hubspot:list_contacts", "hubspot:create_contact", "salesforce:query"]


def _sdk_has_reason_codes() -> bool:
    from grantex.manifest import EnforceResult

    return "reason_code" in getattr(EnforceResult, "__dataclass_fields__", {})


def _expected(reason: str) -> str:
    return reason if _sdk_has_reason_codes() else "unclassified"


@pytest.fixture(scope="module")
def grantex_env():
    """Point the platform's Grantex client at the service and set up the grants."""
    from grantex import Grantex

    from core import config as config_mod
    from core.langgraph import grantex_auth

    patch = pytest.MonkeyPatch()
    patch.setenv("GRANTEX_API_KEY", SANDBOX_KEY)
    patch.setattr(config_mod, "grantex_base_url_for_env", lambda env=None: BASE_URL)
    patch.setattr(grantex_auth, "grantex_base_url_for_env", lambda env=None: BASE_URL)
    patch.setattr(grantex_auth, "_grantex_client", None)

    from auth.grantex_registration import register_agent

    # The run agent, registered with the production scope mapping.
    registration = register_agent(
        name=f"f1-e2e-{AGENT[:8]}",
        agent_type="crm_intelligence",
        domain="marketing",
        authorized_tools=AUTHORIZED_TOOLS,
        connector_names=["hubspot", "salesforce"],
    )
    assert registration is not None, "registration failed"

    # An orchestrator authorized (sandbox auto-approval) for a superset of scopes.
    sdk = Grantex(api_key=SANDBOX_KEY, base_url=BASE_URL)
    root_scopes = sorted(set(registration["grantex_scopes"]) | {"tool:hubspot:write:create_contact"})
    orchestrator = sdk.agents.register(name=f"f1-e2e-orchestrator-{AGENT[:8]}", scopes=root_scopes, description="e2e")
    auth_request = sdk._http.post(
        "/v1/authorize", {"agentId": orchestrator.id, "principalId": "f1-e2e-operator", "scopes": root_scopes}
    )
    root = sdk._http.post("/v1/token", {"code": auth_request["code"], "agentId": orchestrator.id})["grantToken"]
    patch.setattr(config_mod.external_keys, "grantex_root_grant_token", root)
    yield {"registration": registration}
    patch.undo()


def test_registration_scopes_carry_permission_levels(grantex_env):
    scopes = grantex_env["registration"]["grantex_scopes"]
    assert "tool:hubspot:read:list_contacts" in scopes
    assert "tool:salesforce:read:query" in scopes
    assert not any(":execute:" in scope for scope in scopes)


async def test_the_pool_mints_a_delegated_run_grant_and_refreshes_agent_tokens(grantex_env, monkeypatch):
    from auth.token_pool import TokenPool, token_pool

    monkeypatch.setattr(token_pool, "lazy_redis", False)
    grant = await resolve_run_grant(
        tenant_id=TENANT, agent_id=AGENT, grantex_config=grantex_env["registration"], mode=EnforcementMode.DENY
    )
    assert grant.source == "minted" and grant.token and grant.grant_id.startswith("grnt_")
    assert grant.expires_at is not None

    pool = TokenPool()
    pool.redis = AsyncMock()
    pool._schedule_refresh = MagicMock()
    pool.set_agent_config_resolver(
        AsyncMock(
            return_value={
                "grantex_agent_id": grantex_env["registration"]["grantex_agent_id"],
                "scopes": grantex_env["registration"]["grantex_scopes"],
            }
        )
    )
    await pool._refresh_after(AGENT, 0)
    pool.redis.setex.assert_awaited_once()
    assert '"access_token"' in pool.redis.setex.await_args.args[2]


def _state(token: str) -> dict[str, Any]:
    return {
        "messages": [SystemMessage(content="scripted"), HumanMessage(content="review the pipeline")],
        "agent_id": AGENT,
        "agent_type": "crm_intelligence",
        "domain": "marketing",
        "tenant_id": TENANT,
        "grant_token": token,
        "grant_denial": {},
        "confidence": 0.0,
        "status": "running",
        "output": {},
        "reasoning_trace": [],
        "tool_calls_log": [],
        "hitl_trigger": "",
        "error": "",
    }


async def _run(mode: EnforcementMode, steps: list[Any], scripted_model: Any, registration: dict[str, Any], monkeypatch):
    from auth.token_pool import token_pool
    from core.langgraph.agent_graph import build_agent_graph

    monkeypatch.setattr(token_pool, "lazy_redis", False)
    grant: RunGrant = await resolve_run_grant(tenant_id=TENANT, agent_id=AGENT, grantex_config=registration, mode=mode)
    scripted_model(steps)
    executed = AsyncMock(return_value={"results": []})
    monkeypatch.setattr("core.langgraph.tool_adapter._execute_connector_tool", executed)
    graph = build_agent_graph(
        system_prompt="scripted",
        authorized_tools=CALLED_TOOLS,
        connector_config={},
        connector_names=["hubspot", "salesforce"],
        confidence_floor=0.88,
        run_grant=grant,
    )
    with capture_logs() as logs:
        result = await graph.compile().ainvoke(_state(grant.token))
    called = [call.args[1] for call in executed.await_args_list]
    return result, called, logs


async def test_warn_runs_every_call_and_records_the_ungranted_ones(grantex_env, scripted_model, monkeypatch):
    result, called, logs = await _run(
        EnforcementMode.WARN,
        [
            tool_call("hubspot__list_contacts"),
            tool_call("hubspot__create_contact", email="lead@example.com"),
            tool_call("salesforce__query", soql="SELECT Id FROM Account LIMIT 1"),
            final({"status": "completed", "confidence": 0.95}),
        ],
        scripted_model,
        grantex_env["registration"],
        monkeypatch,
    )
    assert called == ["list_contacts", "create_contact", "query"]
    events = [(e["tool"], e["reason"]) for e in logs if e["event"] == "grant_enforcement_would_deny"]
    # list_contacts and query are granted (read); create_contact needs write.
    assert events == [("create_contact", _expected("permission_insufficient"))]
    assert result["status"] == "completed"


async def test_deny_runs_granted_calls_and_refuses_an_ungranted_one(grantex_env, scripted_model, monkeypatch):
    result, called, logs = await _run(
        EnforcementMode.DENY,
        [
            tool_call("hubspot__list_contacts"),
            tool_call("hubspot__create_contact", email="lead@example.com"),
        ],
        scripted_model,
        grantex_env["registration"],
        monkeypatch,
    )
    assert called == ["list_contacts"]
    assert result["status"] == "failed"
    assert result["hitl_trigger"] == ""
    assert result["grant_denial"]["reason"] == _expected("permission_insufficient")
    assert result["grant_denial"]["tool"] == "create_contact"
    assert [e["reason"] for e in logs if e["event"] == "grant_enforcement_denied"] == [
        _expected("permission_insufficient")
    ]


async def test_deny_refuses_a_connector_the_grant_does_not_cover(grantex_env, scripted_model, monkeypatch):
    registration = dict(grantex_env["registration"])
    registration["grantex_scopes"] = [s for s in registration["grantex_scopes"] if ":salesforce:" not in s]
    result, called, _ = await _run(
        EnforcementMode.DENY,
        [tool_call("salesforce__query", soql="SELECT Id FROM Account LIMIT 1")],
        scripted_model,
        registration,
        monkeypatch,
    )
    assert called == []
    assert result["grant_denial"]["reason"] == _expected("tool_not_granted")


async def test_patch_scope_push_and_pool_delegate_only_registered_scopes(grantex_env, monkeypatch):
    """A PATCH pushes new scopes to the registration; the pool then delegates only registered scopes.

    Also pins the compatibility PATCH: the SDK's ``agents.update`` posts to a
    route the auth service does not serve (FINDINGS A-42).
    """
    from grantex import Grantex

    from api.v1.agents import _push_grantex_scopes
    from auth.token_pool import token_pool

    sdk = Grantex(api_key=SANDBOX_KEY, base_url=BASE_URL)
    registered = list(grantex_env["registration"]["grantex_scopes"])
    extra = sdk.agents.register(name=f"f1-e2e-patch-{AGENT[:8]}", scopes=registered, description="e2e patch")
    narrowed = [s for s in registered if s != "tool:salesforce:read:query"]
    agent = MagicMock(id=uuid.uuid4())
    agent.config = {"grantex": {"grantex_agent_id": extra.id, "grantex_scopes": registered}}

    from grantex import GrantexApiError

    with pytest.raises(GrantexApiError):
        sdk.agents.update(extra.id, scopes=narrowed)
    await _push_grantex_scopes(agent, narrowed, tenant_id=TENANT)
    assert sorted(sdk.agents.get(extra.id).scopes) == sorted(narrowed)
    assert agent.config["grantex"]["grantex_scopes"] == narrowed

    # Stored scopes that ran ahead of the registration are not delegated.
    monkeypatch.setattr(token_pool, "lazy_redis", False)
    with capture_logs() as logs:
        grant = await resolve_run_grant(
            tenant_id=TENANT,
            agent_id=str(uuid.uuid4()),
            grantex_config={"grantex_agent_id": extra.id, "grantex_scopes": registered},
            mode=EnforcementMode.DENY,
        )
    assert grant.source == "minted" and grant.token
    [dropped] = [e for e in logs if e["event"] == "run_grant_scopes_not_registered"]
    assert (dropped["stored_count"], dropped["delegated_count"]) == (len(registered), len(narrowed))
