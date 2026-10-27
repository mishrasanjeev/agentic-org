# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""HTTP lifecycle replay with synthetic token/session/DB and activation hooks.

The router, caller derivation, ownership rules and floor predicates are real.
The SQL-aware session double is not PostgreSQL, RLS or locking proof; hook
doubles establish ordering/refusal, not provider or governance integration.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from sqlalchemy import event

from api.v1 import agents
from auth import grantex_middleware as auth
from core.auth_state import UserSessionState
from core.models.agent import Agent, AgentLifecycleEvent, AgentVersion

TENANT = UUID("00000000-0000-0000-0000-000000000001")
OTHER_TENANT = UUID("00000000-0000-0000-0000-000000000002")
OWNER = UUID("00000000-0000-0000-0000-000000000003")
OTHER_USER = UUID("00000000-0000-0000-0000-000000000004")
AGENT_ID = UUID("00000000-0000-0000-0000-000000000005")
COMPANY = UUID("00000000-0000-0000-0000-000000000006")


@pytest.fixture
def case(monkeypatch):
    agent = Agent(
        id=AGENT_ID, tenant_id=TENANT, company_id=COMPANY,
        name="Resume floor replay", agent_type="test_agent", domain="finance",
        status="paused", version="1.0.0", visibility="personal", owner_user_id=OWNER,
        config={}, connector_ids=[], authorized_tools=[],
        shadow_accuracy_floor=Decimal("0.800"), shadow_accuracy_current=Decimal("0.990"),
        shadow_min_samples=10, shadow_sample_count=16, shadow_scored_sample_count=16,
        shadow_feedback_count=16, shadow_human_confidence_current=Decimal("0.990"),
    )
    state = SimpleNamespace(
        agent=agent, pause_origin="active", added=[], queries=[], scopes=[], status_writes=[],
        claims={
            "sub": "owner@example.test", "agenticorg:tenant_id": str(TENANT),
            "agenticorg:user_id": str(OWNER), "role": "developer", "iat": 100,
            "agenticorg:domains": ["finance"], "grantex:scopes": ["agents.lifecycle", "agents.execute"],
        },
    )

    async def execute(statement):
        state.queries.append(statement)
        entity = statement.column_descriptions[0]["entity"]
        if entity is Agent:
            # Explicit route filters, not a simulation of database RLS.
            assert "agents.tenant_id =" in str(statement)
            params = statement.compile().params
            row = agent if agent.id in params.values() and agent.tenant_id in params.values() else None
        elif entity is AgentLifecycleEvent:
            assert "agent_lifecycle_events.agent_id =" in str(statement)
            row = SimpleNamespace(from_status=state.pause_origin) if state.pause_origin is not None else None
        elif entity is AgentVersion:
            row = None
        else:
            raise AssertionError(f"Unexpected query: {statement}")
        return SimpleNamespace(scalar_one_or_none=lambda: row)

    state.session = SimpleNamespace(execute=AsyncMock(side_effect=execute), add=state.added.append)

    @asynccontextmanager
    async def session(tenant_id, company_id=None):
        state.scopes.append((tenant_id, company_id))
        assert str(tenant_id) == state.claims["agenticorg:tenant_id"]
        yield state.session

    def status_set(target, value, _oldvalue, _initiator):
        if target is agent:
            state.status_writes.append(value)

    event.listen(Agent.status, "set", status_set)
    monkeypatch.setattr(agents, "get_tenant_session", session)
    state.token = AsyncMock(side_effect=lambda _token: dict(state.claims))
    state.user_session = AsyncMock(return_value=UserSessionState(found=True, status="active"))
    monkeypatch.setattr(auth, "validate_token", state.token)
    monkeypatch.setattr(auth, "get_user_session_state", state.user_session)
    for name in ("clear_auth_failures", "record_auth_failure", "is_ip_blocked"):
        monkeypatch.setattr(auth, name, AsyncMock(return_value=False))

    state.hooks = {}
    for name, module, attribute in (
        ("prompt", agents.prompt_activation, "check_activation"),
        ("evaluation", agents.eval_gates, "check_promotion"),
        ("registry", agents.registry_approval, "check_promotion"),
        ("risk", agents.risk_tiers, "check_promotion"),
        ("connector", agents, "_assert_connectors_ready_for_activation"),
        ("follow", agents.registry_approval, "follow_promotion"),
    ):
        async def hook(*_args, _name=name, **_kwargs):
            expected = "active" if _name == "follow" else state.before_status
            assert agent.status == expected
            return OWNER if _name == "prompt" else None

        state.hooks[name] = AsyncMock(side_effect=hook)
        monkeypatch.setattr(module, attribute, state.hooks[name])

    app = FastAPI()
    app.include_router(agents.router, prefix="/api/v1")
    app.add_middleware(auth.GrantexAuthMiddleware)

    async def post(action="resume", *, authenticated=True):
        state.before_status = agent.status
        state.status_writes.clear()
        headers = {"Authorization": "Bearer synthetic-session"} if authenticated else {}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://example.test",
        ) as client:
            payload = {"inputs": {"task": "Summarise synthetic invoices"}} if action == "run" else {}
            return await client.post(f"/api/v1/agents/{AGENT_ID}/{action}", headers=headers, json=payload)

    state.post = post
    try:
        yield state
    finally:
        event.remove(Agent.status, "set", status_set)


def _unchanged(case, status="paused", *, hooks_untouched=True):
    assert case.agent.status == status
    assert case.status_writes == []
    assert case.added == []
    if hooks_untouched:
        for hook in case.hooks.values():
            hook.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", ["active", None], ids=["active-history", "missing-history"])
@pytest.mark.parametrize("floor", [Decimal("0.200"), Decimal("0.800"), Decimal("0.950"), None])
@pytest.mark.parametrize("samples", [0, 16])
async def test_low_accuracy_active_resume_is_refused_before_side_effects(case, origin, floor, samples):
    case.pause_origin = origin
    case.agent.shadow_accuracy_floor = floor
    case.agent.shadow_sample_count = samples
    case.agent.shadow_scored_sample_count = samples
    case.agent.shadow_min_samples = 0 if samples == 0 else 10
    effective_floor = max(floor or Decimal("0"), agents._MIN_PRODUCTION_SHADOW_ACCURACY)
    case.agent.shadow_accuracy_current = effective_floor - Decimal("0.001")

    response = await case.post()

    assert response.status_code == 409, response.text
    assert response.json() == {"detail": (
        "Cannot resume to active: shadow accuracy "
        f"{case.agent.shadow_accuracy_current} is below production floor {effective_floor}"
    )}
    _unchanged(case)
    assert len(case.queries) == 2
    assert case.queries[0]._for_update_arg is not None
    assert case.scopes == [(TENANT, None)]


@pytest.mark.asyncio
@pytest.mark.parametrize("floor", [Decimal("0.200"), Decimal("0.800"), Decimal("0.950"), None])
@pytest.mark.parametrize("margin", [Decimal("0"), Decimal("0.001"), None], ids=["equal", "above", "no-accuracy"])
@pytest.mark.parametrize("samples", [0, 16])
async def test_active_resume_preserves_numeric_boundaries_and_none(case, floor, margin, samples):
    case.agent.shadow_accuracy_floor = floor
    case.agent.shadow_sample_count = samples
    case.agent.shadow_min_samples = 0 if samples == 0 else 10
    effective_floor = max(floor or Decimal("0"), agents._MIN_PRODUCTION_SHADOW_ACCURACY)
    case.agent.shadow_accuracy_current = None if margin is None else effective_floor + margin

    response = await case.post()

    assert response.status_code == 200, response.text
    assert response.json() == {"id": str(AGENT_ID), "status": "active"}
    assert case.status_writes == ["active"]
    assert case.agent.shadow_sample_count == samples
    assert case.agent.shadow_accuracy_current == (None if margin is None else effective_floor + margin)
    assert len(case.added) == 1
    assert isinstance(case.added[0], AgentLifecycleEvent)
    assert (case.added[0].from_status, case.added[0].to_status) == ("paused", "active")
    for hook in case.hooks.values():
        hook.assert_awaited_once()
    assert case.scopes == [(TENANT, None), (TENANT, COMPANY)]


@pytest.mark.asyncio
@pytest.mark.parametrize("minimum", [0, 10])
@pytest.mark.parametrize("accuracy", [None, Decimal("0.500"), Decimal("0.700")])
async def test_shadow_resume_keeps_configured_floor_and_sample_opt_out(case, minimum, accuracy):
    case.pause_origin = "shadow"
    case.agent.shadow_accuracy_floor = Decimal("0.600")
    case.agent.shadow_accuracy_current = accuracy
    case.agent.shadow_min_samples = minimum
    response = await case.post()
    if minimum and accuracy is not None and accuracy < Decimal("0.600"):
        assert response.status_code == 409, response.text
        assert "use /agents/" in response.json()["detail"]
        _unchanged(case)
    else:
        assert response.status_code == 200, response.text
        assert response.json() == {"id": str(AGENT_ID), "status": "shadow"}
        assert case.status_writes == ["shadow"]
        assert len(case.added) == 1
        for hook in case.hooks.values():
            hook.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("control,expected", [
    ("missing-auth", 401), ("invalid-token", 401), ("revoked", 401),
    ("inactive", 401), ("missing-user", 401), ("session-unavailable", 503),
    ("wrong-owner", 404), ("other-tenant", 404), ("shared-non-admin", 403),
])
async def test_resume_auth_owner_and_tenant_refusals(case, control, expected):
    if control == "invalid-token":
        case.token.side_effect = ValueError("Synthetic invalid token")
    elif control == "revoked":
        case.user_session.return_value = UserSessionState(True, "active", sessions_invalid_before=101)
    elif control == "inactive":
        case.user_session.return_value = UserSessionState(True, "inactive")
    elif control == "missing-user":
        case.user_session.return_value = UserSessionState(False)
    elif control == "session-unavailable":
        case.user_session.side_effect = RuntimeError("Synthetic session outage")
    elif control == "wrong-owner":
        case.claims["agenticorg:user_id"] = str(OTHER_USER)
    elif control == "other-tenant":
        case.claims["agenticorg:tenant_id"] = str(OTHER_TENANT)
    elif control == "shared-non-admin":
        case.agent.visibility = "tenant"
    response = await case.post(authenticated=control != "missing-auth")
    assert response.status_code == expected, response.text
    _unchanged(case)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["draft", "active", "shadow", "retired"])
async def test_resume_still_requires_paused_status(case, status):
    case.agent.status = status
    response = await case.post()
    assert response.status_code == 409, response.text
    assert "must be paused" in response.json()["detail"]
    _unchanged(case, status)


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["resume", "promote"])
@pytest.mark.parametrize("hook_name", ["prompt", "evaluation", "registry", "risk", "connector"])
async def test_activation_hook_refusals_still_precede_mutation(case, action, hook_name):
    if action == "promote":
        case.agent.status = "shadow"
        case.agent.shadow_min_samples = 0
    errors = {
        "prompt": agents.prompt_activation.ActivationError(403, "Synthetic prompt refusal"),
        "evaluation": agents.eval_gates.GateError("synthetic", "Synthetic evaluation refusal"),
        "registry": agents.registry_approval.ApprovalError("synthetic", "Synthetic registry refusal", "draft"),
        "risk": agents.risk_tiers.TierError("synthetic", "Synthetic risk refusal", tier="high"),
        "connector": HTTPException(409, "Synthetic connector refusal"),
    }
    case.hooks[hook_name].side_effect = errors[hook_name]
    response = await case.post(action)
    assert response.status_code == (403 if hook_name == "prompt" else 409), response.text
    assert "Synthetic" in response.text
    _unchanged(case, case.before_status, hooks_untouched=False)
    case.hooks[hook_name].assert_awaited_once()
    case.hooks["follow"].assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("floor", [Decimal("0.200"), Decimal("0.800"), Decimal("0.950")])
async def test_shadow_promotion_still_enforces_effective_floor(case, floor):
    case.agent.status = "shadow"
    case.agent.shadow_accuracy_floor = floor
    effective_floor = max(floor, agents._MIN_PRODUCTION_SHADOW_ACCURACY)
    case.agent.shadow_accuracy_current = effective_floor - Decimal("0.001")
    response = await case.post("promote")
    assert response.status_code == 409, response.text
    assert f"below floor {effective_floor}" in response.json()["detail"]
    _unchanged(case, "shadow")


@pytest.mark.asyncio
@pytest.mark.parametrize("floor", [Decimal("0.200"), Decimal("0.800"), Decimal("0.950")])
@pytest.mark.parametrize("accuracy_state", ["below", "equal", "none"])
async def test_run_keeps_active_status_wrapper_and_numeric_semantics(case, monkeypatch, floor, accuracy_state):
    case.agent.status = "active"
    case.agent.shadow_accuracy_floor = floor
    effective_floor = max(floor, agents._MIN_PRODUCTION_SHADOW_ACCURACY)
    case.agent.shadow_accuracy_current = {
        "below": effective_floor - Decimal("0.001"), "equal": effective_floor, "none": None,
    }[accuracy_state]
    next_guard = AsyncMock(side_effect=HTTPException(423, "Synthetic next run guard"))
    monkeypatch.setattr(agents, "check_operator_override", next_guard)
    response = await case.post("run")
    if accuracy_state == "below":
        assert response.status_code == 409, response.text
        assert "below the production shadow-accuracy" in response.json()["detail"]
        next_guard.assert_not_awaited()
    else:
        assert response.status_code == 423, response.text
        assert response.json() == {"detail": "Synthetic next run guard"}
        next_guard.assert_awaited_once()
    _unchanged(case, "active")


@pytest.mark.parametrize("status", ["paused", "shadow", "draft", "active"])
def test_existing_floor_wrapper_remains_active_only(case, status):
    case.agent.status = status
    case.agent.shadow_accuracy_floor = Decimal("0.800")
    case.agent.shadow_accuracy_current = Decimal("0.799")
    assert agents._active_agent_below_production_floor(case.agent) is (status == "active")
