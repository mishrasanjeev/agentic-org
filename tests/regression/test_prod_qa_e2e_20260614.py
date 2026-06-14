from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_support_triage_accepts_raw_ticket_text() -> None:
    from core.agents.ops.support_triage import _ticket_from_inputs

    assert _ticket_from_inputs("Dashboard is blank after login") == {
        "description": "Dashboard is blank after login"
    }
    assert _ticket_from_inputs({"ticket": "Cannot access dashboard after login"}) == {
        "description": "Cannot access dashboard after login"
    }
    assert _ticket_from_inputs({"ticket": {"subject": "Login fails"}}) == {
        "subject": "Login fails"
    }


@pytest.mark.asyncio
async def test_workflow_agent_step_uses_stored_agent_config(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.agents.registry import AgentRegistry
    from core.schemas.messages import TaskResult
    from workflows import step_types

    captured: dict[str, object] = {}

    async def fake_load_agent_config(agent_id: str, tenant_id: str) -> dict:
        captured["loaded"] = {"agent_id": agent_id, "tenant_id": tenant_id}
        return {
            "id": agent_id,
            "tenant_id": tenant_id,
            "agent_type": "support_triage",
            "authorized_tools": ["freshdesk:update_ticket"],
            "prompt_variables": {"tone": "concise"},
            "hitl_condition": "confidence < 0.5",
            "output_schema": "support_triage_result",
            "llm_model": "gemini-2.5-flash",
            "cost_controls": {"daily_token_budget": 1000},
            "system_prompt_text": "Use the stored QA support prompt.",
        }

    class CapturingAgent:
        async def execute(self, task):
            captured["task"] = task
            return TaskResult(
                message_id="msg_test",
                correlation_id=task.correlation_id,
                workflow_run_id=task.workflow_run_id,
                step_id=task.step_id,
                agent_id=task.target_agent.agent_id,
                status="completed",
                output={"ok": True},
                confidence=0.91,
            )

    def fake_create_from_config(config: dict):
        captured["config"] = dict(config)
        return CapturingAgent()

    monkeypatch.setattr(step_types, "_llm_available_for_workflow", lambda: True)
    monkeypatch.setattr(step_types, "_fake_llm_allowed", lambda: False)
    monkeypatch.setattr(step_types, "_load_workflow_agent_config", fake_load_agent_config)
    monkeypatch.setattr(
        AgentRegistry,
        "create_from_config",
        staticmethod(fake_create_from_config),
    )

    result = await step_types.execute_step(
        {
            "id": "triage_ticket",
            "type": "agent",
            "agent_id": "11111111-1111-1111-1111-111111111111",
            "action": "triage",
            "inputs": {"ticket": "QA smoke ticket: dashboard is blank after login"},
        },
        {
            "id": "wfr_test",
            "tenant_id": "22222222-2222-2222-2222-222222222222",
            "context": {},
        },
    )

    assert result["status"] == "completed"
    assert captured["loaded"] == {
        "agent_id": "11111111-1111-1111-1111-111111111111",
        "tenant_id": "22222222-2222-2222-2222-222222222222",
    }
    assert captured["config"] == {
        "id": "11111111-1111-1111-1111-111111111111",
        "tenant_id": "22222222-2222-2222-2222-222222222222",
        "agent_type": "support_triage",
        "authorized_tools": ["freshdesk:update_ticket"],
        "prompt_variables": {"tone": "concise"},
        "hitl_condition": "confidence < 0.5",
        "output_schema": "support_triage_result",
        "system_prompt_text": "Use the stored QA support prompt.",
        "llm_model": "gemini-2.5-flash",
        "cost_controls": {"daily_token_budget": 1000},
    }


def test_cloud_run_deploy_enables_commerce_public_discovery() -> None:
    src = (ROOT / "scripts" / "deploy_cloud_run.sh").read_text(encoding="utf-8")

    assert "API_UPDATE_ENV_VARS=" in src
    assert "AGENTICORG_COMMERCE_PUBLIC_DISCOVERY_ENABLED=true" in src
    assert 'update_service_no_traffic API_NEW_REVISION "$API_SERVICE" "$API_IMAGE" "$API_UPDATE_ENV_VARS"' in src
