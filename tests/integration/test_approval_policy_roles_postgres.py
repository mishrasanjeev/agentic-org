"""Approval policy role checks through the API and persisted Postgres rows."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from httpx import AsyncClient, Response
from sqlalchemy import select, update

from api.v1.approval_policies import delete_policy
from core.approvals.policy_engine import resolve_policy
from core.database import get_tenant_session
from core.models.approval_policy import ApprovalPolicy, ApprovalStep
from core.models.hitl import HITLQueue

pytestmark = pytest.mark.asyncio


async def _agent(client: AsyncClient, headers: dict[str, str]) -> uuid.UUID:
    response = await client.post(
        "/api/v1/agents",
        headers=headers,
        json={
            "name": f"approval-role-{uuid.uuid4().hex[:8]}",
            "agent_type": "ap_processor",
            "domain": "finance",
            "system_prompt_text": "Review the action.",
            "authorized_tools": [],
        },
    )
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["agent_id"])


def _policy_payload(agent_id: uuid.UUID, name: str, roles: list[str]) -> dict:
    return {
        "name": name,
        "agent_id": str(agent_id),
        "steps": [
            {"sequence": sequence, "approver_role": role}
            for sequence, role in enumerate(roles, start=1)
        ],
    }


async def test_policy_write_rejects_unknown_role_and_policy_is_tenant_scoped(
    client: AsyncClient,
    auth_headers: dict[str, str],
    tenant_id: str,
) -> None:
    tid = uuid.UUID(tenant_id)
    agent_id = await _agent(client, auth_headers)
    name = f"role-policy-{uuid.uuid4().hex[:8]}"
    invalid = await client.post(
        "/api/v1/approval-policies",
        headers=auth_headers,
        json=_policy_payload(agent_id, name, ["unmapped_reviewer"]),
    )
    assert invalid.status_code == 400, invalid.text

    async with get_tenant_session(tid) as session:
        stored = (
            await session.execute(
                select(ApprovalPolicy).where(ApprovalPolicy.tenant_id == tid, ApprovalPolicy.name == name)
            )
        ).scalar_one_or_none()
    assert stored is None

    created = await client.post(
        "/api/v1/approval-policies",
        headers=auth_headers,
        json=_policy_payload(agent_id, name, ["cfo"]),
    )
    assert created.status_code == 201, created.text
    policy_id = uuid.UUID(created.json()["id"])
    assert created.json()["steps"][0]["approver_role"] == "cfo"

    other_tenant = uuid.uuid4()
    assert await resolve_policy(other_tenant, agent_id=agent_id) is None
    with pytest.raises(HTTPException) as exc:
        await delete_policy(policy_id, tenant_id=str(other_tenant))
    assert exc.value.status_code == 404

    async with get_tenant_session(tid) as session:
        stored = (
            await session.execute(
                select(ApprovalPolicy).where(ApprovalPolicy.tenant_id == tid, ApprovalPolicy.id == policy_id)
            )
        ).scalar_one_or_none()
    assert stored is not None


async def test_persisted_invalid_roles_and_requester_vote_leave_item_pending(
    client: AsyncClient,
    auth_headers: dict[str, str],
    tenant_id: str,
    user_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.deps import get_user_role
    from api.main import app

    monkeypatch.setitem(app.dependency_overrides, get_user_role, lambda: "admin")
    tid = uuid.UUID(tenant_id)
    agent_id = await _agent(client, auth_headers)
    created = await client.post(
        "/api/v1/approval-policies",
        headers=auth_headers,
        json=_policy_payload(agent_id, f"role-policy-{uuid.uuid4().hex[:8]}", ["cfo", "cfo"]),
    )
    assert created.status_code == 201, created.text
    policy_id = uuid.UUID(created.json()["id"])
    item_id = uuid.uuid4()
    async with get_tenant_session(tid) as session:
        session.add(
            HITLQueue(
                id=item_id,
                tenant_id=tid,
                agent_id=agent_id,
                title="Approve action",
                trigger_type="policy_condition",
                priority="high",
                status="pending",
                assignee_role="cfo",
                decision_options={"options": ["approve", "reject"]},
                context={},
                expires_at=datetime.now(UTC) + timedelta(hours=4),
            )
        )

    async def set_role(sequence: int, role: str) -> None:
        async with get_tenant_session(tid) as session:
            await session.execute(
                update(ApprovalStep)
                .where(ApprovalStep.policy_id == policy_id, ApprovalStep.sequence == sequence)
                .values(approver_role=role)
            )

    async def decide() -> Response:
        return await client.post(
            f"/api/v1/approvals/{item_id}/decide",
            headers=auth_headers,
            json={"decision": "approve"},
        )

    async def item() -> HITLQueue:
        async with get_tenant_session(tid) as session:
            return (await session.execute(select(HITLQueue).where(HITLQueue.id == item_id))).scalar_one()

    await set_role(2, "unmapped_reviewer")
    next_invalid = await decide()
    assert next_invalid.status_code == 409, next_invalid.text
    pending = await item()
    assert pending.status == "pending"
    assert not (pending.context or {}).get("policy_state")
    assert pending.decision_by is None

    await set_role(2, "cfo")
    await set_role(1, "unmapped_reviewer")
    current_invalid = await decide()
    assert current_invalid.status_code == 409, current_invalid.text
    assert not ((await item()).context or {}).get("policy_state")

    await set_role(1, "cfo")
    async with get_tenant_session(tid) as session:
        await session.execute(
            update(HITLQueue).where(HITLQueue.id == item_id).values(requested_by_user_id=uuid.UUID(user_id))
        )
    self_vote = await decide()
    assert self_vote.status_code == 403, self_vote.text
    assert not ((await item()).context or {}).get("policy_state")

    async with get_tenant_session(tid) as session:
        await session.execute(
            update(HITLQueue).where(HITLQueue.id == item_id).values(requested_by_user_id=None)
        )
    counted = await decide()
    assert counted.status_code == 200, counted.text
    assert counted.json()["policy_action"] == "collect"
    assert (await item()).context["policy_state"]["current_sequence"] == 2

    repeated = await decide()
    assert repeated.status_code == 409, repeated.text
    state = (await item()).context["policy_state"]
    assert state["current_sequence"] == 2
    assert len(state["approvals"]) == 1
