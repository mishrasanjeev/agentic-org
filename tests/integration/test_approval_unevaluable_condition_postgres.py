# SPDX-License-Identifier: Apache-2.0
"""``approvals.unevaluable_condition`` through the API on real Postgres.

The policy is created with ``POST /api/v1/approval-policies`` and the vote cast
with ``POST /api/v1/approvals/{id}/decide``; the flag row, the approval item
and the audit log are real tables read back after the request. In ``deny``
mode the refusal of an approval must survive the ``409`` (the tenant session
rolls back on an exception), the refused vote must not count against the
reviewer once the flag is off again, and a rejection is still taken and
clears the refusal from the item. Requires ``AGENTICORG_DB_URL``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, select

from core import feature_flags
from core.database import get_tenant_session
from core.models.audit import AuditLog
from core.models.feature_flag import FeatureFlag
from core.models.hitl import HITLQueue

pytestmark = [pytest.mark.asyncio, pytest.mark.real_flag_store]

DENY_FLAG = "approvals.unevaluable_condition.deny"
REASON = "approval_condition_unevaluable"


async def _agent(client: AsyncClient, headers: dict[str, str]) -> uuid.UUID:
    response = await client.post(
        "/api/v1/agents",
        headers=headers,
        json={
            "name": f"unevaluable-condition-{uuid.uuid4().hex[:8]}",
            "agent_type": "ap_processor",
            "domain": "finance",
            "system_prompt_text": "Settle the invoice.",
            "authorized_tools": [],
        },
    )
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["agent_id"])


async def _policy(client: AsyncClient, headers: dict[str, str], agent_id: uuid.UUID) -> str:
    # The item carries ``amount``; the step names ``output.amount``.
    response = await client.post(
        "/api/v1/approval-policies",
        headers=headers,
        json={
            "name": f"board-{uuid.uuid4().hex[:8]}",
            "agent_id": str(agent_id),
            "steps": [
                {
                    "sequence": 1,
                    "approver_role": "cfo",
                    "quorum_required": 2,
                    "quorum_total": 2,
                    "condition": "output.amount > 1000000",
                }
            ],
        },
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


async def _item(tid: uuid.UUID, agent_id: uuid.UUID) -> uuid.UUID:
    item_id = uuid.uuid4()
    async with get_tenant_session(tid) as session:
        session.add(
            HITLQueue(
                id=item_id,
                tenant_id=tid,
                agent_id=agent_id,
                title="Approve payment",
                trigger_type="policy_condition",
                priority="high",
                status="pending",
                assignee_role="cfo",
                decision_options={"options": ["approve", "reject"]},
                context={"amount": 5_000_000},
                expires_at=datetime.now(UTC) + timedelta(hours=4),
            )
        )
    return item_id


async def _set_deny(tid: uuid.UUID, enabled: bool) -> None:
    async with get_tenant_session(tid) as session:
        await session.execute(
            delete(FeatureFlag).where(FeatureFlag.tenant_id == tid, FeatureFlag.flag_key == DENY_FLAG)
        )
        if enabled:
            session.add(FeatureFlag(tenant_id=tid, flag_key=DENY_FLAG, enabled=True, rollout_percentage=100))
    feature_flags.clear_cache()


async def _read(tid: uuid.UUID, item_id: uuid.UUID) -> tuple[HITLQueue, list[AuditLog]]:
    async with get_tenant_session(tid) as session:
        item = (await session.execute(select(HITLQueue).where(HITLQueue.id == item_id))).scalar_one()
        refusals = (
            (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.tenant_id == tid,
                        AuditLog.resource_id == str(item_id),
                        AuditLog.event_type == "hitl.decision_refused",
                    )
                )
            )
            .scalars()
            .all()
        )
    return item, list(refusals)


async def _cleanup(client: AsyncClient, headers: dict[str, str], tid: uuid.UUID, policy_id: str, item_id: Any) -> None:
    await _set_deny(tid, enabled=False)
    await client.delete(f"/api/v1/approval-policies/{policy_id}", headers=headers)
    if item_id is not None:
        async with get_tenant_session(tid) as session:
            await session.execute(delete(HITLQueue).where(HITLQueue.id == item_id))


async def test_deny_mode_refuses_and_records_the_vote_then_off_counts_it(
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
    policy_id = await _policy(client, auth_headers, agent_id)
    item_id = None
    try:
        item_id = await _item(tid, agent_id)
        await _set_deny(tid, enabled=True)

        refused = await client.post(
            f"/api/v1/approvals/{item_id}/decide", headers=auth_headers, json={"decision": "approve"}
        )
        assert refused.status_code == 409, refused.text
        detail = refused.json()["detail"]
        assert detail["reason_code"] == REASON
        assert detail["unevaluable_steps"] == [1]

        item, refusals = await _read(tid, item_id)
        assert item.status == "pending"
        assert item.decision_by is None
        state = item.context["policy_state"]
        assert state["last_action"] == "refused"
        assert state["last_reason"] == REASON
        assert state["unevaluable_steps"] == [1]
        assert not state.get("approvals")
        [audit] = refusals
        assert audit.outcome == "denied"
        assert audit.actor_id == user_id
        assert audit.details["reason_code"] == REASON
        assert audit.details["policy_id"] == policy_id

        # Flag off again: the step applies, and the same reviewer's vote is
        # the first one counted, not a second vote.
        await _set_deny(tid, enabled=False)
        counted = await client.post(
            f"/api/v1/approvals/{item_id}/decide", headers=auth_headers, json={"decision": "approve"}
        )
        assert counted.status_code == 200, counted.text
        assert counted.json()["policy_action"] == "collect"
        item, refusals = await _read(tid, item_id)
        assert item.status == "pending"
        state = item.context["policy_state"]
        assert state["approvals_collected"] == 1
        assert state["policy_id"] == policy_id
        assert "unevaluable_steps" not in state
        assert len(refusals) == 1
    finally:
        await _cleanup(client, auth_headers, tid, policy_id, item_id)


async def test_deny_mode_takes_a_rejection_and_clears_the_refusal(
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
    policy_id = await _policy(client, auth_headers, agent_id)
    item_id = None
    try:
        item_id = await _item(tid, agent_id)
        await _set_deny(tid, enabled=True)

        refused = await client.post(
            f"/api/v1/approvals/{item_id}/decide", headers=auth_headers, json={"decision": "approve"}
        )
        assert refused.status_code == 409, refused.text
        assert refused.json()["detail"]["reason_code"] == REASON

        # Still deny mode: rejecting closes the item and approves nothing.
        rejected = await client.post(
            f"/api/v1/approvals/{item_id}/decide", headers=auth_headers, json={"decision": "reject"}
        )
        assert rejected.status_code == 200, rejected.text
        assert rejected.json()["policy_action"] == "reject"
        assert rejected.json()["status"] == "rejected"

        item, refusals = await _read(tid, item_id)
        assert item.status == "rejected"
        assert item.decision == "reject"
        assert str(item.decision_by) == user_id
        state = item.context["policy_state"]
        assert state["policy_id"] == policy_id
        assert state["last_action"] == "reject"
        assert "unevaluable_steps" not in state
        assert [vote["decision"] for vote in state["approvals"]] == ["reject"]
        assert len(refusals) == 1  # the refused approval stays on record
    finally:
        await _cleanup(client, auth_headers, tid, policy_id, item_id)


async def test_flag_off_counts_the_vote_against_the_step(
    client: AsyncClient,
    auth_headers: dict[str, str],
    tenant_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.deps import get_user_role
    from api.main import app

    monkeypatch.setitem(app.dependency_overrides, get_user_role, lambda: "admin")
    tid = uuid.UUID(tenant_id)
    agent_id = await _agent(client, auth_headers)
    policy_id = await _policy(client, auth_headers, agent_id)
    item_id = None
    try:
        item_id = await _item(tid, agent_id)
        await _set_deny(tid, enabled=False)

        counted = await client.post(
            f"/api/v1/approvals/{item_id}/decide", headers=auth_headers, json={"decision": "approve"}
        )
        assert counted.status_code == 200, counted.text
        item, refusals = await _read(tid, item_id)
        assert item.status == "pending"
        assert item.context["policy_state"]["approvals_collected"] == 1
        assert refusals == []
    finally:
        await _cleanup(client, auth_headers, tid, policy_id, item_id)
