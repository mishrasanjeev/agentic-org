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
async def test_workflow_agent_step_uses_stored_agent_config(
    monkeypatch: pytest.MonkeyPatch,
    workflow_company_scope: dict[str, str],
) -> None:
    from core.agents.registry import AgentRegistry
    from core.schemas.messages import TaskResult
    from workflows import step_types

    captured: dict[str, object] = {}

    async def fake_load_agent_config(agent_id: str, tenant_id: str) -> dict:
        captured["loaded"] = {"agent_id": agent_id, "tenant_id": tenant_id}
        return {
            "id": agent_id,
            "tenant_id": tenant_id,
            "company_id": workflow_company_scope["company_id"],
            "domain": "operations",
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
            **workflow_company_scope,
            "id": "wfr_test",
            "context": {},
        },
    )

    assert result["status"] == "completed"
    assert captured["loaded"] == {
        "agent_id": "11111111-1111-1111-1111-111111111111",
        "tenant_id": workflow_company_scope["tenant_id"],
    }
    assert captured["config"] == {
        "id": "11111111-1111-1111-1111-111111111111",
        "tenant_id": workflow_company_scope["tenant_id"],
        "company_id": workflow_company_scope["company_id"],
        "domain": "operations",
        "agent_type": "support_triage",
        "authorized_tools": ["freshdesk:update_ticket"],
        "prompt_variables": {"tone": "concise"},
        "hitl_condition": "confidence < 0.5",
        "output_schema": "support_triage_result",
        "system_prompt_text": "Use the stored QA support prompt.",
        "llm_model": "gemini-2.5-flash",
        "cost_controls": {"daily_token_budget": 1000},
    }


def test_cloud_run_deploy_does_not_force_enable_commerce_public_discovery() -> None:
    src = (ROOT / "scripts" / "deploy_cloud_run.sh").read_text(encoding="utf-8")

    assert "API_UPDATE_ENV_VARS=" in src
    assert "AGENTICORG_COMMERCE_PUBLIC_DISCOVERY_ENABLED=true" not in src
    assert 'COMMERCE_PUBLIC_DISCOVERY_VALUE="${AGENTICORG_COMMERCE_PUBLIC_DISCOVERY_ENABLED:-false}"' in src
    assert 'update_service_no_traffic API_NEW_REVISION "$API_SERVICE" "$API_IMAGE" "$API_UPDATE_ENV_VARS"' in src


def test_a2a_tasks_repair_migration_is_idempotent_and_tenant_scoped() -> None:
    migration = (ROOT / "migrations" / "versions" / "v6_y4_repair_a2a_tasks.py").read_text(
        encoding="utf-8"
    )

    assert 'revision = "v6y4_repair_a2a_tasks"' in migration
    assert 'down_revision = "v6y3_industry_pack_uuid_default"' in migration
    assert "CREATE TABLE IF NOT EXISTS a2a_tasks" in migration
    assert "tenant_id UUID NOT NULL REFERENCES tenants(id)" in migration
    assert "input_data JSONB DEFAULT '{}'::jsonb" in migration
    assert "CREATE INDEX IF NOT EXISTS ix_a2a_tasks_tenant" in migration
    assert "CREATE INDEX IF NOT EXISTS ix_a2a_tasks_task_id" in migration
    assert "ALTER TABLE a2a_tasks FORCE ROW LEVEL SECURITY" in migration
    assert "CREATE POLICY a2a_tasks_tenant_isolation" in migration


def test_migration_wrapper_checks_required_runtime_tables_after_upgrade() -> None:
    src = (ROOT / "scripts" / "alembic_migrate.py").read_text(encoding="utf-8")

    assert '"a2a_tasks"' in src
    assert "REQUIRED_RUNTIME_TABLES" in src
    assert "_assert_required_runtime_tables(engine)" in src
    assert "Alembic reported success but required runtime tables are missing" in src
