from __future__ import annotations

import pytest


def _contract(
    *,
    idempotency_supported: bool = True,
    remaining_attempts: int = 2,
    next_retry_at: str = "2026-05-23T12:05:00+00:00",
    confirmations: list[dict] | None = None,
) -> dict:
    return {
        "connector_key": "google_ads",
        "idempotency_key_supported": idempotency_supported,
        "retry_budget": {
            "max_attempts": 3,
            "attempts_used": 1,
            "remaining_attempts": remaining_attempts,
            "next_retry_at": next_retry_at,
            "idempotency_key": "ads-launch-1",
        },
        "external_write_confirmations": confirmations or [],
    }


def _active_state(**overrides: object) -> dict:
    state = {
        "id": "wfr_cmo_write",
        "tenant_id": "tenant-1",
        "workflow_id": "campaign_launch",
        "workflow_mode": "active",
        "domain": "marketing",
        "connector_contracts": [_contract()],
        "marketing_policy_approval_satisfied": True,
    }
    state.update(overrides)
    return state


def _write_step(**overrides: object) -> dict:
    step = {
        "id": "launch_ads",
        "type": "agent",
        "agent_type": "campaign_pilot",
        "action": "launch_campaign",
        "connector_key": "google_ads",
        "external_write_required": True,
        "idempotency_key": "ads-launch-1",
        "inputs": {"campaign_name": "Spring launch", "budget": 5000},
    }
    step.update(overrides)
    return step


def _patch_workflow_agent(monkeypatch: pytest.MonkeyPatch, output: dict) -> None:
    from core.agents.registry import AgentRegistry
    from core.schemas.messages import TaskResult
    from workflows import step_types

    monkeypatch.setattr(step_types.settings, "env", "production")
    monkeypatch.setattr(step_types.external_keys, "google_gemini_api_key", "test-key")
    monkeypatch.setattr(step_types, "_llm_available_for_workflow", lambda: True)

    class FakeAgent:
        async def execute(self, task):
            return TaskResult(
                message_id="msg-test",
                correlation_id=task.correlation_id,
                workflow_run_id=task.workflow_run_id,
                step_id=task.step_id,
                agent_id=task.target_agent.agent_id,
                status="completed",
                output=output,
                confidence=0.95,
            )

    monkeypatch.setattr(
        AgentRegistry,
        "create_from_config",
        staticmethod(lambda config: FakeAgent()),
    )


async def _run_marketing_write(
    monkeypatch: pytest.MonkeyPatch,
    output: dict,
    *,
    step: dict | None = None,
    state: dict | None = None,
) -> dict:
    from workflows.step_types import execute_step

    _patch_workflow_agent(monkeypatch, output)
    return await execute_step(step or _write_step(), state or _active_state())


def _event_types(result: dict) -> list[str]:
    return [
        event["event_type"]
        for event in result["output"].get("external_write_audit", [])
    ]


@pytest.mark.asyncio
async def test_accepted_connector_write_with_external_object_id_becomes_confirmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = await _run_marketing_write(
        monkeypatch,
        {
            "external_write_state": "accepted",
            "external_object_id": "customers/123/campaigns/456",
            "source_url": "https://ads.google.com/campaigns/456",
            "idempotency_key": "ads-launch-1",
            "request_fingerprint": "fp-launch-1",
        },
    )

    assert result["status"] == "completed"
    assert result["output"]["external_write_state"] == "write_confirmed"
    confirmation = result["output"]["external_write_confirmation"]
    assert confirmation["connector_key"] == "google_ads"
    assert confirmation["external_object_id"] == "customers/123/campaigns/456"
    assert confirmation["source_url"] == "https://ads.google.com/campaigns/456"
    assert confirmation["idempotency_key"] == "ads-launch-1"
    assert confirmation["request_fingerprint"] == "fp-launch-1"
    assert confirmation["workflow_run_id"] == "wfr_cmo_write"
    assert confirmation["audit_reference"].startswith("mkt_write_")
    assert _event_types(result) == [
        "marketing_external_write_attempted",
        "marketing_external_write_write_confirmed",
    ]


@pytest.mark.asyncio
async def test_active_external_write_requires_marketing_policy_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = await _run_marketing_write(
        monkeypatch,
        {
            "external_write_state": "accepted",
            "external_object_id": "customers/123/campaigns/456",
            "idempotency_key": "ads-launch-1",
        },
        state=_active_state(marketing_policy_approval_satisfied=False),
    )

    assert result["status"] == "failed"
    assert result["error"]["code"] == "external_write_marketing_policy_approval_required"
    assert result["output"]["marketing_policy_decision"]["decision"] == "requires_approval"


@pytest.mark.asyncio
async def test_rejected_connector_write_does_not_complete_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = await _run_marketing_write(
        monkeypatch,
        {
            "external_write_state": "rejected",
            "rejection_reason": "Budget cap exceeded",
            "next_action": "request_budget_approval",
            "idempotency_key": "ads-launch-1",
        },
    )

    assert result["status"] == "failed"
    assert result["error"]["code"] == "external_write_rejected"
    assert result["error"]["details"]["final_state"] == "rejected"
    assert result["error"]["details"]["next_action"] == "request_budget_approval"
    assert "Budget cap exceeded" in result["error"]["details"]["reason"]
    assert _event_types(result) == [
        "marketing_external_write_attempted",
        "marketing_external_write_rejected",
    ]


@pytest.mark.asyncio
async def test_timeout_unknown_without_idempotency_does_not_complete_active_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = await _run_marketing_write(
        monkeypatch,
        {
            "external_write_state": "timeout_unknown",
            "request_fingerprint": "fp-timeout-1",
        },
        step=_write_step(idempotency_key=None),
        state=_active_state(connector_contracts=[_contract(idempotency_supported=False)]),
    )

    assert result["status"] == "failed"
    assert result["error"]["code"] == "external_write_timeout_unknown"
    assert result["error"]["details"]["next_action"] == "manual_reconcile_before_retry"
    assert result["output"]["external_write_state"] == "timeout_unknown"
    assert _event_types(result)[-1] == "marketing_external_write_timeout_unknown"


@pytest.mark.asyncio
async def test_retry_without_idempotency_metadata_is_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = await _run_marketing_write(
        monkeypatch,
        {"external_write_state": "retry_scheduled"},
        step=_write_step(idempotency_key=None),
        state=_active_state(connector_contracts=[_contract(idempotency_supported=False)]),
    )

    assert result["status"] == "failed"
    assert result["error"]["code"] == "external_write_retry_blocked"
    assert result["output"]["external_write_next_action"] == "manual_reconcile_before_retry"


@pytest.mark.asyncio
async def test_retry_with_idempotency_metadata_is_scheduled_safely(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = await _run_marketing_write(
        monkeypatch,
        {
            "external_write_state": "timeout_unknown",
            "idempotency_key": "ads-launch-1",
            "request_fingerprint": "fp-timeout-2",
        },
    )

    assert result["status"] == "waiting_delay"
    assert result["resume_at"] == "2026-05-23T12:05:00+00:00"
    assert result["output"]["external_write_state"] == "retry_scheduled"
    assert result["output"]["external_write_retry_plan"]["safe_to_retry"] is True
    assert result["output"]["external_write_retry_plan"]["duplicate_policy"] == "reuse_idempotency_key"
    assert _event_types(result)[-1] == "marketing_external_write_retry_scheduled"


@pytest.mark.asyncio
async def test_duplicate_retry_recovers_existing_confirmed_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = await _run_marketing_write(
        monkeypatch,
        {
            "external_write_state": "timeout_unknown",
            "idempotency_key": "ads-launch-1",
            "request_fingerprint": "fp-timeout-recovered",
        },
        state=_active_state(
            connector_contracts=[
                _contract(
                    confirmations=[
                        {
                            "action": "launch_campaign",
                            "status": "write_confirmed",
                            "idempotency_key": "ads-launch-1",
                            "external_object_id": "customers/123/campaigns/456",
                            "source_url": "https://ads.google.com/campaigns/456",
                            "confirmed_at": "2026-05-23T12:01:00+00:00",
                        }
                    ],
                )
            ]
        ),
    )

    assert result["status"] == "completed"
    assert result["output"]["external_write_state"] == "idempotent_recovered"
    confirmation = result["output"]["external_write_confirmation"]
    assert confirmation["external_object_id"] == "customers/123/campaigns/456"
    assert confirmation["idempotency_key"] == "ads-launch-1"
    assert _event_types(result)[-1] == "marketing_external_write_idempotent_recovered"


@pytest.mark.asyncio
async def test_draft_created_can_complete_only_as_draft_or_internal_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    draft_output = {
        "external_write_state": "draft_created",
        "draft_id": "draft-123",
        "idempotency_key": "ads-launch-1",
    }

    internal = await _run_marketing_write(
        monkeypatch,
        draft_output,
        state=_active_state(workflow_mode="internal"),
    )
    assert internal["status"] == "completed"
    assert internal["output"]["external_write_state"] == "draft_created"

    active = await _run_marketing_write(monkeypatch, draft_output)
    assert active["status"] == "failed"
    assert active["error"]["code"] == "external_write_draft_only"
    assert active["output"]["external_write_next_action"] == "promote_draft_or_mark_step_internal"


@pytest.mark.asyncio
async def test_shadow_mode_rejects_external_write_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = await _run_marketing_write(
        monkeypatch,
        {
            "external_write_state": "write_confirmed",
            "external_object_id": "customers/123/campaigns/456",
            "idempotency_key": "ads-launch-1",
        },
        state=_active_state(workflow_mode="shadow"),
    )

    assert result["status"] == "failed"
    assert result["error"]["code"] == "external_write_shadow_violation"
    assert result["error"]["details"]["next_action"] == "remove_external_write_from_shadow_step"


@pytest.mark.asyncio
async def test_shadow_only_recommendation_can_complete_without_external_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = await _run_marketing_write(
        monkeypatch,
        {"recommendation": "Keep campaign in draft until CMO approves."},
        state=_active_state(workflow_mode="shadow"),
    )

    assert result["status"] == "completed"
    assert result["output"]["external_write_state"] == "shadow_only"
    assert _event_types(result)[-1] == "marketing_external_write_shadow_only"


@pytest.mark.asyncio
async def test_active_launch_step_cannot_complete_with_write_unconfirmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = await _run_marketing_write(
        monkeypatch,
        {
            "external_write_state": "write_unconfirmed",
            "idempotency_key": "ads-launch-1",
        },
    )

    assert result["status"] == "failed"
    assert result["error"]["code"] == "external_write_confirmation_missing"
    assert result["error"]["details"]["final_state"] == "write_unconfirmed"
    assert result["output"]["external_write_audit_reference"].startswith("mkt_write_")
