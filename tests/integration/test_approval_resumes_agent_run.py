# SPDX-License-Identifier: Apache-2.0
"""Approving a standalone run through the API resumes it from Postgres (PRD F-2).

Real Postgres, the real run and approval endpoints, the scripted model, and
the ``approvals.resume_agent_runs`` flag enabled per tenant:

* tenant A's run pauses for approval; the approval API exposes neither the
  thread nor the resume parameters;
* tenant B cannot decide A's approval, and a B approval row carrying A's
  random run suffix under B's own prefix finds no checkpoint, leaving A's
  run paused;
* A's approval resumes the run to completion, records the outcome on the
  approval and in the audit log, and deletes the finished checkpoints.

Requires ``AGENTICORG_DB_URL``.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from core.approvals import agent_run_resume as ar
from core.langgraph import checkpointer as cp
from core.test_doubles.scripted_model import final
from tests.integration.test_hitl_checkpoint_restart_resume import (  # noqa: F401 - store_env is a fixture
    DB_URL,
    _run,
    _seed,
    _sync_conn,
    store_env,
)

pytestmark = pytest.mark.skipif(not DB_URL, reason="integration tests require AGENTICORG_DB_URL")


def _seed_agent(tenant_id: uuid.UUID) -> uuid.UUID:
    agent_id = uuid.uuid4()
    _seed(tenant_id)
    with _sync_conn() as conn:
        conn.execute(
            "INSERT INTO agents (id, tenant_id, name, agent_type, domain, system_prompt_ref, system_prompt_text, "
            "prompt_variables, llm_model, llm_config, confidence_floor, hitl_condition, max_retries, retry_backoff, "
            "authorized_tools, connector_ids, visibility, status, version, shadow_min_samples, "
            "shadow_accuracy_floor, shadow_sample_count, shadow_scored_sample_count, shadow_feedback_count, "
            "cost_controls, scaling, tags, config, routing_filter, is_builtin, org_level) "
            "VALUES (%s, %s, 'Resume probe', 'resume_probe', 'finance', '', 'Settle the invoice.', '{}'::jsonb, "
            "'scripted', '{}'::jsonb, 0.5, 'total > 500000', 3, 'exponential', '[]'::jsonb, '[]'::jsonb, "
            "'tenant', 'shadow', '1.0.0', 10, 0.8, 0, 0, 0, '{}'::jsonb, '{}'::jsonb, '{}', '{}'::jsonb, "
            "'{}'::jsonb, false, 0)",
            (agent_id, tenant_id),
        )
        conn.execute(
            "INSERT INTO feature_flags (id, tenant_id, flag_key, enabled, rollout_percentage) "
            "VALUES (%s, %s, %s, true, 100)",
            (uuid.uuid4(), tenant_id, ar.RESUME_FLAG),
        )
    return agent_id


def _cleanup(tenants: list[uuid.UUID]) -> None:
    with _sync_conn() as conn:
        for tid in tenants:
            threads = [
                row[0]
                for row in conn.execute(
                    "SELECT checkpoint_thread_id FROM hitl_queue "
                    "WHERE tenant_id = %s AND checkpoint_thread_id IS NOT NULL",
                    (tid,),
                )
            ]
            for thread in threads:
                for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                    conn.execute(f"DELETE FROM {table} WHERE thread_id = %s", (thread,))  # noqa: S608
        # Rows referencing users go before any tenant's users (approvals cross-reference the token user).
        # audit_log is append-only and has no foreign keys, so its rows stay.
        for table in ("agent_feedback", "hitl_queue", "feature_flags", "agents", "users"):
            for tid in tenants:
                conn.execute(f"DELETE FROM {table} WHERE tenant_id = %s", (tid,))  # noqa: S608
        for tid in tenants:
            conn.execute("DELETE FROM tenants WHERE id = %s", (tid,))


def _ensure_token_user(headers: dict[str, str], tenant_id: uuid.UUID) -> None:
    """The test tokens carry one fixed ``agenticorg:user_id``; approvals reference it as a user FK."""
    import jwt

    claims = jwt.decode(headers["Authorization"].removeprefix("Bearer "), options={"verify_signature": False})
    with _sync_conn() as conn:
        conn.execute(
            "INSERT INTO users (id, tenant_id, email, name, role, status, mfa_enabled) "
            "VALUES (%s, %s, %s, 'Resume approver', 'admin', 'active', false) ON CONFLICT (id) DO NOTHING",
            (claims["agenticorg:user_id"], tenant_id, f"resume-approver-{tenant_id.hex[:8]}@example.com"),
        )


def _checkpoint_count(thread_id: str) -> int:
    with _sync_conn() as conn:
        row = conn.execute("SELECT count(*) FROM checkpoints WHERE thread_id = %s", (thread_id,)).fetchone()
    return int(row[0]) if row else 0


def _approval_row(hitl_id: str) -> tuple[str, dict[str, Any], str | None]:
    with _sync_conn() as conn:
        row = conn.execute(
            "SELECT status, context, checkpoint_thread_id FROM hitl_queue WHERE id = %s", (hitl_id,)
        ).fetchone()
    assert row is not None
    return row[0], row[1], row[2]


def test_approving_a_paused_run_resumes_it_and_tenant_b_cannot(
    store_env: dict[str, str],  # noqa: F811
    scripted_model: Any,
    make_auth_headers: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api.deps import get_user_role
    from api.main import app

    monkeypatch.setitem(app.dependency_overrides, get_user_role, lambda: "admin")
    monkeypatch.setenv("AGENTICORG_BILLING_METERING", "0")
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    agent_a, agent_b = _seed_agent(tenant_a), _seed_agent(tenant_b)
    headers_a = make_auth_headers(tenant_id=str(tenant_a))
    headers_b = make_auth_headers(tenant_id=str(tenant_b))
    _ensure_token_user(headers_a, tenant_a)
    model = scripted_model([final({"status": "completed", "confidence": 0.95, "total": 750000})])

    async def _scenario() -> dict[str, Any]:
        from httpx import ASGITransport, AsyncClient

        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
                run = await client.post(
                    f"/api/v1/agents/{agent_a}/run",
                    headers=headers_a,
                    json={"action": "settle", "inputs": {"invoice": "INV-0001"}},
                )
                assert run.status_code == 200, run.text
                assert run.json()["status"] == "hitl_triggered", run.text

                listed = await client.get("/api/v1/approvals", headers=headers_a, params={"status": "pending"})
                assert listed.status_code == 200, listed.text
                (item,) = [i for i in listed.json()["items"] if i["agent_id"] == str(agent_a)]
                _, stored_context, thread_a = _approval_row(item["id"])
                assert thread_a and thread_a.startswith(f"tenant:{tenant_a}:run:")
                assert ar.RESUME_SPEC_KEY in stored_context
                for body in (run.text, listed.text):
                    assert thread_a not in body
                    assert ar.RESUME_SPEC_KEY not in body
                paused_checkpoints = _checkpoint_count(thread_a)
                assert paused_checkpoints > 0

                # Tenant B deciding A's approval by id: not found, nothing resumed.
                denied = await client.post(
                    f"/api/v1/approvals/{item['id']}/decide", headers=headers_b, json={"decision": "approve"}
                )
                assert denied.status_code == 404, denied.text

                # Tenant B's own approval row carrying A's random suffix under B's prefix.
                guessed = thread_a.replace(str(tenant_a), str(tenant_b))
                copied_spec = {ar.RESUME_SPEC_KEY: stored_context[ar.RESUME_SPEC_KEY]}
                forged_id = uuid.uuid4()
                with _sync_conn() as conn:
                    conn.execute(
                        "INSERT INTO hitl_queue (id, tenant_id, agent_id, title, trigger_type, priority, status, "
                        "assignee_role, decision_options, context, expires_at, checkpoint_thread_id) "
                        "VALUES (%s, %s, %s, 'guess', 'policy_condition', 'normal', 'pending', 'finance', "
                        "'{}'::jsonb, %s::jsonb, now() + interval '1 hour', %s)",
                        (forged_id, tenant_b, agent_b, json.dumps(copied_spec), guessed),
                    )
                forged = await client.post(
                    f"/api/v1/approvals/{forged_id}/decide", headers=headers_b, json={"decision": "approve"}
                )
                assert forged.status_code == 200, forged.text
                _, forged_context, _ = _approval_row(str(forged_id))
                assert forged_context[ar.RESUME_STATE_KEY]["state"] == "refused"
                assert forged_context[ar.RESUME_STATE_KEY]["reason"] == "checkpoint_not_found"
                assert _checkpoint_count(thread_a) == paused_checkpoints
                assert _checkpoint_count(guessed) == 0

                approved = await client.post(
                    f"/api/v1/approvals/{item['id']}/decide",
                    headers=headers_a,
                    json={"decision": "approve", "notes": "invoice verified"},
                )
                assert approved.status_code == 200, approved.text
                assert thread_a not in approved.text
                return {"hitl_id": item["id"], "thread": thread_a}
        finally:
            await cp.close_checkpointer()

    try:
        outcome = _run(_scenario())
        assert model.remaining == 0

        status, context, _ = _approval_row(outcome["hitl_id"])
        assert status == "decided"
        state = context[ar.RESUME_STATE_KEY]
        assert state["state"] == "completed", state
        assert state["run_status"] == "completed"
        assert state["checkpoint_deleted"] is True
        assert _checkpoint_count(outcome["thread"]) == 0
        with _sync_conn() as conn:
            audits = conn.execute(
                "SELECT tenant_id, outcome, details FROM audit_log WHERE event_type = %s AND resource_id IN (%s, %s)",
                (ar.AUDIT_EVENT, outcome["hitl_id"], outcome["hitl_id"]),
            ).fetchall()
        assert [(a[0], a[1]) for a in audits] == [(tenant_a, "completed")]
    finally:
        _cleanup([tenant_a, tenant_b])
