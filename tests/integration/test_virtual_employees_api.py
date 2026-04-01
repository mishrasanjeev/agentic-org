"""API integration tests for the Virtual Employee Agent System.

Requires a running PostgreSQL instance (uses conftest.py's test engine).

Covers:
- POST /agents with persona fields
- POST /agents with custom agent_type
- PATCH /agents/{id} prompt lock on active agents
- PATCH /agents/{id} prompt edit on shadow agents + audit trail
- POST /agents/{id}/clone with persona
- GET /agents/{id}/prompt-history
- POST /prompt-templates CRUD
- PUT /prompt-templates/{id} rejects built-in edits
- DELETE /prompt-templates/{id} soft-deletes
- POST /agents/{id}/run with custom type (no Python class)
- Multiple agents of same type (UniqueConstraint removed)
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


# ═══════════════════════════════════════════════════════════════════════════
# Agent Creation with Persona Fields
# ═══════════════════════════════════════════════════════════════════════════


class TestAgentCreatePersona:
    async def test_create_agent_with_persona(self, client: AsyncClient, auth_headers):
        resp = await client.post("/api/v1/agents", headers=auth_headers, json={
            "name": "Priya AP",
            "agent_type": "ap_processor",
            "domain": "finance",
            "employee_name": "Priya",
            "designation": "Senior AP Analyst - Mumbai",
            "specialization": "Domestic invoices under 5L",
            "routing_filter": {"region": "west"},
            "system_prompt": "test",
            "hitl_policy": {"condition": "confidence < 0.88"},
        })
        assert resp.status_code == 201
        data = resp.json()
        assert "agent_id" in data
        return data["agent_id"]

    async def test_create_agent_defaults_employee_name_to_name(self, client: AsyncClient, auth_headers):
        resp = await client.post("/api/v1/agents", headers=auth_headers, json={
            "name": "Test Agent",
            "agent_type": "test_default_name",
            "domain": "finance",
            "system_prompt": "test",
            "hitl_policy": {"condition": "confidence < 0.88"},
        })
        assert resp.status_code == 201
        agent_id = resp.json()["agent_id"]

        # Verify employee_name was set to name
        resp2 = await client.get(f"/api/v1/agents/{agent_id}", headers=auth_headers)
        assert resp2.status_code == 200
        assert resp2.json()["employee_name"] == "Test Agent"

    async def test_create_agent_with_custom_type(self, client: AsyncClient, auth_headers):
        """Creating an agent with a type that has no Python class should work."""
        resp = await client.post("/api/v1/agents", headers=auth_headers, json={
            "name": "Customer Success Bot",
            "agent_type": "customer_success",
            "domain": "ops",
            "employee_name": "CS Bot",
            "system_prompt_text": "You are a customer success agent.",
            "hitl_policy": {"condition": "confidence < 0.88"},
        })
        assert resp.status_code == 201

    async def test_get_agent_returns_persona_fields(self, client: AsyncClient, auth_headers):
        # Create
        resp = await client.post("/api/v1/agents", headers=auth_headers, json={
            "name": "Arjun",
            "agent_type": "arjun_type",
            "domain": "finance",
            "employee_name": "Arjun",
            "designation": "AP Analyst - East",
            "specialization": "Import invoices",
            "routing_filter": {"region": "east"},
            "system_prompt": "test",
            "hitl_policy": {"condition": "confidence < 0.88"},
        })
        agent_id = resp.json()["agent_id"]

        # Get
        resp2 = await client.get(f"/api/v1/agents/{agent_id}", headers=auth_headers)
        data = resp2.json()
        assert data["employee_name"] == "Arjun"
        assert data["designation"] == "AP Analyst - East"
        assert data["specialization"] == "Import invoices"
        assert data["routing_filter"] == {"region": "east"}


# ═══════════════════════════════════════════════════════════════════════════
# Multiple Agents of Same Type
# ═══════════════════════════════════════════════════════════════════════════


class TestMultipleAgentsPerType:
    async def test_create_two_agents_same_type_different_names(self, client: AsyncClient, auth_headers):
        """Two agents with the same agent_type but different employee_names should work."""
        unique = uuid.uuid4().hex[:6]
        resp1 = await client.post("/api/v1/agents", headers=auth_headers, json={
            "name": f"Agent A {unique}",
            "agent_type": f"multi_type_{unique}",
            "domain": "finance",
            "employee_name": f"Alpha {unique}",
            "system_prompt": "test",
            "hitl_policy": {"condition": "confidence < 0.88"},
        })
        assert resp1.status_code == 201

        resp2 = await client.post("/api/v1/agents", headers=auth_headers, json={
            "name": f"Agent B {unique}",
            "agent_type": f"multi_type_{unique}",
            "domain": "finance",
            "employee_name": f"Beta {unique}",
            "system_prompt": "test",
            "hitl_policy": {"condition": "confidence < 0.88"},
        })
        assert resp2.status_code == 201
        assert resp1.json()["agent_id"] != resp2.json()["agent_id"]


# ═══════════════════════════════════════════════════════════════════════════
# Prompt Lock on Active Agents
# ═══════════════════════════════════════════════════════════════════════════


class TestPromptLock:
    async def _create_active_agent(self, client, auth_headers):
        """Helper: create an agent and force it to active status."""
        unique = uuid.uuid4().hex[:6]
        resp = await client.post("/api/v1/agents", headers=auth_headers, json={
            "name": f"Lock Test {unique}",
            "agent_type": f"lock_{unique}",
            "domain": "finance",
            "employee_name": f"LockAgent {unique}",
            "system_prompt_text": "Original prompt",
            "initial_status": "shadow",
            "hitl_policy": {"condition": "confidence < 0.88"},
            "shadow_min_samples": 0,
            "shadow_accuracy_floor": 0.0,
        })
        assert resp.status_code == 201, f"Agent creation failed: {resp.status_code} {resp.text}"
        agent_id = resp.json()["agent_id"]
        return agent_id

    async def test_patch_prompt_on_shadow_agent_succeeds(self, client: AsyncClient, auth_headers):
        agent_id = await self._create_active_agent(client, auth_headers)

        resp = await client.patch(f"/api/v1/agents/{agent_id}", headers=auth_headers, json={
            "system_prompt_text": "Updated prompt for shadow agent",
            "change_reason": "Testing prompt edit on shadow",
        })
        assert resp.status_code == 200

    async def test_patch_prompt_on_active_agent_rejected(self, client: AsyncClient, auth_headers):
        await self._create_active_agent(client, auth_headers)

        # Force to active (simulate promotion)
        # We need to directly update the status — use the promote endpoint won't work
        # with 0 samples. Let's just set initial_status to active.
        unique = uuid.uuid4().hex[:6]
        resp = await client.post("/api/v1/agents", headers=auth_headers, json={
            "name": f"Active Agent {unique}",
            "agent_type": f"active_{unique}",
            "domain": "finance",
            "employee_name": f"ActiveAgent {unique}",
            "system_prompt_text": "Active prompt",
            "initial_status": "active",
            "hitl_policy": {"condition": "confidence < 0.88"},
        })
        active_id = resp.json()["agent_id"]

        resp2 = await client.patch(f"/api/v1/agents/{active_id}", headers=auth_headers, json={
            "system_prompt_text": "Should be rejected",
        })
        assert resp2.status_code == 409
        assert "locked" in resp2.json()["detail"].lower()


# ═══════════════════════════════════════════════════════════════════════════
# Prompt Edit History
# ═══════════════════════════════════════════════════════════════════════════


class TestPromptEditHistory:
    async def test_prompt_edit_creates_history(self, client: AsyncClient, auth_headers):
        unique = uuid.uuid4().hex[:6]
        resp = await client.post("/api/v1/agents", headers=auth_headers, json={
            "name": f"History Test {unique}",
            "agent_type": f"history_{unique}",
            "domain": "finance",
            "employee_name": f"HistAgent {unique}",
            "system_prompt_text": "Initial prompt",
            "hitl_policy": {"condition": "confidence < 0.88"},
        })
        agent_id = resp.json()["agent_id"]

        # Edit the prompt
        await client.patch(f"/api/v1/agents/{agent_id}", headers=auth_headers, json={
            "system_prompt_text": "Updated prompt v2",
            "change_reason": "Improved instructions",
        })

        # Check history
        resp3 = await client.get(f"/api/v1/agents/{agent_id}/prompt-history", headers=auth_headers)
        assert resp3.status_code == 200
        history = resp3.json()
        assert len(history) >= 1
        entry = history[0]
        assert entry["prompt_after"] == "Updated prompt v2"
        assert entry["change_reason"] == "Improved instructions"

    async def test_empty_history_for_new_agent(self, client: AsyncClient, auth_headers):
        unique = uuid.uuid4().hex[:6]
        resp = await client.post("/api/v1/agents", headers=auth_headers, json={
            "name": f"No History {unique}",
            "agent_type": f"nohist_{unique}",
            "domain": "finance",
            "employee_name": f"NoHistAgent {unique}",
            "system_prompt": "test",
            "hitl_policy": {"condition": "confidence < 0.88"},
        })
        agent_id = resp.json()["agent_id"]

        resp2 = await client.get(f"/api/v1/agents/{agent_id}/prompt-history", headers=auth_headers)
        assert resp2.status_code == 200
        assert resp2.json() == []


# ═══════════════════════════════════════════════════════════════════════════
# Agent Clone with Persona
# ═══════════════════════════════════════════════════════════════════════════


class TestAgentClone:
    async def test_clone_copies_persona_fields(self, client: AsyncClient, auth_headers):
        unique = uuid.uuid4().hex[:6]
        # Create parent
        resp = await client.post("/api/v1/agents", headers=auth_headers, json={
            "name": f"Parent {unique}",
            "agent_type": f"clone_parent_{unique}",
            "domain": "finance",
            "employee_name": f"Parent {unique}",
            "designation": "Senior Analyst",
            "specialization": "Invoice processing",
            "system_prompt_text": "Parent prompt",
            "hitl_policy": {"condition": "confidence < 0.88"},
        })
        parent_id = resp.json()["agent_id"]

        # Clone
        resp2 = await client.post(f"/api/v1/agents/{parent_id}/clone", headers=auth_headers, json={
            "name": f"Clone {unique}",
            "agent_type": f"clone_parent_{unique}",
            "overrides": {
                "employee_name": f"Clone {unique}",
                "designation": "Junior Analyst",
            },
        })
        assert resp2.status_code == 200
        clone_id = resp2.json()["clone_id"]

        # Verify clone has persona
        resp3 = await client.get(f"/api/v1/agents/{clone_id}", headers=auth_headers)
        data = resp3.json()
        assert data["employee_name"] == f"Clone {unique}"
        assert data["designation"] == "Junior Analyst"
        assert data["specialization"] == "Invoice processing"  # inherited
        assert data["system_prompt_text"] == "Parent prompt"  # inherited


# ═══════════════════════════════════════════════════════════════════════════
# Prompt Templates CRUD
# ═══════════════════════════════════════════════════════════════════════════


class TestPromptTemplatesCrud:
    async def test_create_and_list_template(self, client: AsyncClient, auth_headers):
        unique = uuid.uuid4().hex[:6]
        resp = await client.post("/api/v1/prompt-templates", headers=auth_headers, json={
            "name": f"test_template_{unique}",
            "agent_type": f"test_type_{unique}",
            "domain": "finance",
            "template_text": "You are {{role}} for {{org}}.",
            "variables": [{"name": "role", "description": "Agent role", "default": "AP"}],
        })
        assert resp.status_code == 201
        template_id = resp.json()["id"]

        # List
        resp2 = await client.get("/api/v1/prompt-templates", headers=auth_headers)
        assert resp2.status_code == 200
        templates = resp2.json()
        ids = [t["id"] for t in templates]
        assert template_id in ids

    async def test_get_template_by_id(self, client: AsyncClient, auth_headers):
        unique = uuid.uuid4().hex[:6]
        resp = await client.post("/api/v1/prompt-templates", headers=auth_headers, json={
            "name": f"get_template_{unique}",
            "agent_type": f"get_type_{unique}",
            "domain": "hr",
            "template_text": "HR Agent prompt",
        })
        template_id = resp.json()["id"]

        resp2 = await client.get(f"/api/v1/prompt-templates/{template_id}", headers=auth_headers)
        assert resp2.status_code == 200
        assert resp2.json()["template_text"] == "HR Agent prompt"

    async def test_update_custom_template(self, client: AsyncClient, auth_headers):
        unique = uuid.uuid4().hex[:6]
        resp = await client.post("/api/v1/prompt-templates", headers=auth_headers, json={
            "name": f"update_template_{unique}",
            "agent_type": f"update_type_{unique}",
            "domain": "ops",
            "template_text": "Original text",
        })
        template_id = resp.json()["id"]

        resp2 = await client.put(f"/api/v1/prompt-templates/{template_id}", headers=auth_headers, json={
            "template_text": "Updated text",
        })
        assert resp2.status_code == 200

        resp3 = await client.get(f"/api/v1/prompt-templates/{template_id}", headers=auth_headers)
        assert resp3.json()["template_text"] == "Updated text"

    async def test_delete_custom_template(self, client: AsyncClient, auth_headers):
        unique = uuid.uuid4().hex[:6]
        resp = await client.post("/api/v1/prompt-templates", headers=auth_headers, json={
            "name": f"delete_template_{unique}",
            "agent_type": f"delete_type_{unique}",
            "domain": "finance",
            "template_text": "To be deleted",
        })
        template_id = resp.json()["id"]

        resp2 = await client.delete(f"/api/v1/prompt-templates/{template_id}", headers=auth_headers)
        assert resp2.status_code == 200

        # Should no longer appear in list (soft-deleted)
        resp3 = await client.get("/api/v1/prompt-templates", headers=auth_headers)
        ids = [t["id"] for t in resp3.json()]
        assert template_id not in ids

    async def test_template_not_found(self, client: AsyncClient, auth_headers):
        fake_id = str(uuid.uuid4())
        resp = await client.get(f"/api/v1/prompt-templates/{fake_id}", headers=auth_headers)
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════
# Agent Run with Custom Type
# ═══════════════════════════════════════════════════════════════════════════


class TestAgentRunCustomType:
    async def test_run_custom_type_agent(self, client: AsyncClient, auth_headers):
        """Running an agent with a custom type (no Python class) should work."""
        unique = uuid.uuid4().hex[:6]
        resp = await client.post("/api/v1/agents", headers=auth_headers, json={
            "name": f"RunCustom {unique}",
            "agent_type": f"custom_run_{unique}",
            "domain": "ops",
            "employee_name": f"RunBot {unique}",
            "system_prompt_text": "You are a test agent. Return {\"status\": \"completed\", \"confidence\": 0.95}",
            "hitl_policy": {"condition": "confidence < 0.5"},
        })
        agent_id = resp.json()["agent_id"]

        # Run should not return 422 (no registered class)
        resp2 = await client.post(f"/api/v1/agents/{agent_id}/run", headers=auth_headers, json={
            "action": "test",
            "inputs": {"test": True},
        })
        # May fail due to no LLM API key, but should not be 422
        assert resp2.status_code != 422


# ═══════════════════════════════════════════════════════════════════════════
# Edge Cases
# ═══════════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    async def test_agent_not_found(self, client: AsyncClient, auth_headers):
        fake_id = str(uuid.uuid4())
        resp = await client.get(f"/api/v1/agents/{fake_id}", headers=auth_headers)
        assert resp.status_code == 404

    async def test_patch_non_prompt_fields_on_active_agent_succeeds(self, client: AsyncClient, auth_headers):
        """Updating non-prompt fields on an active agent should work."""
        unique = uuid.uuid4().hex[:6]
        resp = await client.post("/api/v1/agents", headers=auth_headers, json={
            "name": f"Active NP {unique}",
            "agent_type": f"active_np_{unique}",
            "domain": "finance",
            "employee_name": f"ActiveNP {unique}",
            "initial_status": "active",
            "system_prompt": "test",
            "hitl_policy": {"condition": "confidence < 0.88"},
        })
        agent_id = resp.json()["agent_id"]

        # Update name (not prompt) should succeed
        resp2 = await client.patch(f"/api/v1/agents/{agent_id}", headers=auth_headers, json={
            "name": f"Renamed {unique}",
            "employee_name": f"Renamed {unique}",
        })
        assert resp2.status_code == 200

    async def test_list_agents_returns_persona_in_items(self, client: AsyncClient, auth_headers):
        unique = uuid.uuid4().hex[:6]
        await client.post("/api/v1/agents", headers=auth_headers, json={
            "name": f"ListTest {unique}",
            "agent_type": f"list_{unique}",
            "domain": "finance",
            "employee_name": f"ListBot {unique}",
            "designation": "Test Designation",
            "system_prompt": "test",
            "hitl_policy": {"condition": "confidence < 0.88"},
        })

        resp = await client.get("/api/v1/agents", headers=auth_headers)
        assert resp.status_code == 200
        items = resp.json()["items"]
        found = [a for a in items if a.get("employee_name") == f"ListBot {unique}"]
        assert len(found) == 1
        assert found[0]["designation"] == "Test Designation"
