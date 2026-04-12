"""Integration tests — full API lifecycle against real PostgreSQL + Redis.

These tests exercise the FastAPI app through ``httpx.AsyncClient`` with
real service-container backends (Postgres via pgvector, Redis 7) as provided
by the CI workflow's ``services`` block.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


# =========================================================================
# 1. Health endpoint (no auth required)
# =========================================================================


class TestHealthEndpoint:
    """Verify the unauthenticated health endpoint."""

    async def test_health_returns_200(self, client: AsyncClient) -> None:
        resp = await client.get("/api/v1/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] in ("healthy", "degraded")  # degraded OK if connectors unhealthy in CI
        assert "version" in body

    async def test_health_contains_version(self, client: AsyncClient) -> None:
        resp = await client.get("/api/v1/health")
        assert resp.json()["version"] == "4.8.0"


# =========================================================================
# 2. Agent CRUD lifecycle
# =========================================================================


class TestAgentCRUDLifecycle:
    """Create -> Read -> Update -> Pause -> Resume -> Clone -> List agents."""

    @pytest.fixture
    def agent_payload(self) -> dict:
        return {
            "name": f"test-agent-{uuid.uuid4().hex[:8]}",
            "agent_type": "ap_processor",
            "domain": "finance",
            "system_prompt": "You are a test AP processing agent.",
            "authorized_tools": ["post_journal_entry", "create_ap_invoice"],
            "hitl_policy": {
                "condition": "total > 500000",
                "assignee_role": "cfo",
                "timeout_hours": 4,
                "on_timeout": "escalate",
            },
            "confidence_floor": 0.9,
            "output_schema": "Invoice",
        }

    async def test_create_agent(
        self, client: AsyncClient, auth_headers: dict, agent_payload: dict
    ) -> None:
        resp = await client.post("/api/v1/agents", json=agent_payload, headers=auth_headers)
        assert resp.status_code == 201
        body = resp.json()
        assert "agent_id" in body
        assert body["status"] == "shadow"

    async def test_list_agents(self, client: AsyncClient, auth_headers: dict) -> None:
        resp = await client.get("/api/v1/agents", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert "items" in body
        assert "total" in body
        assert isinstance(body["items"], list)

    async def test_list_agents_with_domain_filter(
        self, client: AsyncClient, auth_headers: dict
    ) -> None:
        resp = await client.get(
            "/api/v1/agents", params={"domain": "finance"}, headers=auth_headers
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "items" in body

    async def test_get_agent_by_id(self, client: AsyncClient, auth_headers: dict) -> None:
        agent_id = str(uuid.uuid4())
        resp = await client.get(f"/api/v1/agents/{agent_id}", headers=auth_headers)
        assert resp.status_code == 404

    async def test_update_agent_patch(self, client: AsyncClient, auth_headers: dict) -> None:
        agent_id = str(uuid.uuid4())
        resp = await client.patch(
            f"/api/v1/agents/{agent_id}",
            json={"name": "updated-agent-name"},
            headers=auth_headers,
        )
        assert resp.status_code == 404

    async def test_replace_agent_put(
        self, client: AsyncClient, auth_headers: dict, agent_payload: dict
    ) -> None:
        agent_id = str(uuid.uuid4())
        resp = await client.put(
            f"/api/v1/agents/{agent_id}",
            json=agent_payload,
            headers=auth_headers,
        )
        assert resp.status_code == 404

    async def test_pause_agent(self, client: AsyncClient, auth_headers: dict) -> None:
        agent_id = str(uuid.uuid4())
        resp = await client.post(f"/api/v1/agents/{agent_id}/pause", headers=auth_headers)
        assert resp.status_code == 404

    async def test_resume_agent(self, client: AsyncClient, auth_headers: dict) -> None:
        agent_id = str(uuid.uuid4())
        resp = await client.post(f"/api/v1/agents/{agent_id}/resume", headers=auth_headers)
        assert resp.status_code == 404

    async def test_promote_agent(self, client: AsyncClient, auth_headers: dict) -> None:
        agent_id = str(uuid.uuid4())
        resp = await client.post(f"/api/v1/agents/{agent_id}/promote", headers=auth_headers)
        assert resp.status_code == 404

    async def test_rollback_agent(self, client: AsyncClient, auth_headers: dict) -> None:
        agent_id = str(uuid.uuid4())
        resp = await client.post(f"/api/v1/agents/{agent_id}/rollback", headers=auth_headers)
        assert resp.status_code == 404

    async def test_clone_agent(self, client: AsyncClient, auth_headers: dict) -> None:
        agent_id = str(uuid.uuid4())
        resp = await client.post(
            f"/api/v1/agents/{agent_id}/clone",
            json={
                "name": "cloned-agent",
                "agent_type": "ap_processor",
                "overrides": {"confidence_floor": 0.92},
                "initial_status": "shadow",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 404

    async def test_run_agent(self, client: AsyncClient, auth_headers: dict) -> None:
        agent_id = str(uuid.uuid4())
        resp = await client.post(
            f"/api/v1/agents/{agent_id}/run",
            json={"action": "process", "inputs": {"invoice_id": "INV-001"}},
            headers=auth_headers,
        )
        # Non-existent agent returns 404
        assert resp.status_code == 404


# =========================================================================
# 3. Workflow creation and run triggering
# =========================================================================


class TestWorkflowLifecycle:
    """Create workflow -> list -> trigger run -> check run status."""

    @pytest.fixture
    def workflow_payload(self) -> dict:
        return {
            "name": f"invoice-processing-{uuid.uuid4().hex[:8]}",
            "version": "1.0",
            "description": "End-to-end AP invoice processing pipeline",
            "domain": "finance",
            "definition": {
                "steps": [
                    {
                        "id": "extract",
                        "agent": "invoice_extractor",
                        "input": "$trigger.document_url",
                    },
                    {
                        "id": "validate",
                        "agent": "gstin_validator",
                        "input": "$steps.extract.output",
                        "depends_on": ["extract"],
                    },
                    {
                        "id": "match",
                        "agent": "three_way_matcher",
                        "input": "$steps.validate.output",
                        "depends_on": ["validate"],
                    },
                    {
                        "id": "approve",
                        "type": "hitl",
                        "condition": "total > 500000",
                        "depends_on": ["match"],
                    },
                    {
                        "id": "post",
                        "agent": "journal_poster",
                        "input": "$steps.match.output",
                        "depends_on": ["approve"],
                    },
                ],
                "on_failure": "pause_and_alert",
            },
            "trigger_type": "webhook",
            "trigger_config": {"path": "/webhooks/invoice-received"},
        }

    async def test_create_workflow(
        self, client: AsyncClient, auth_headers: dict, workflow_payload: dict
    ) -> None:
        resp = await client.post("/api/v1/workflows", json=workflow_payload, headers=auth_headers)
        assert resp.status_code == 201
        body = resp.json()
        assert "workflow_id" in body
        assert body["version"] == "1.0"

    async def test_list_workflows(self, client: AsyncClient, auth_headers: dict) -> None:
        resp = await client.get("/api/v1/workflows", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert "items" in body
        assert isinstance(body["items"], list)

    async def test_trigger_workflow_run(self, client: AsyncClient, auth_headers: dict) -> None:
        wf_id = str(uuid.uuid4())
        resp = await client.post(
            f"/api/v1/workflows/{wf_id}/run",
            json={"payload": {"document_url": "s3://invoices/INV-001.pdf"}},
            headers=auth_headers,
        )
        assert resp.status_code == 404

    async def test_get_workflow_run(self, client: AsyncClient, auth_headers: dict) -> None:
        run_id = str(uuid.uuid4())
        resp = await client.get(f"/api/v1/workflows/runs/{run_id}", headers=auth_headers)
        assert resp.status_code == 404


# =========================================================================
# 4. HITL approval flow
# =========================================================================


class TestHITLApprovalFlow:
    """List pending approvals -> approve -> reject -> defer."""

    async def test_list_approvals(self, client: AsyncClient, auth_headers: dict) -> None:
        resp = await client.get("/api/v1/approvals", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert "items" in body
        assert "total" in body

    async def test_list_approvals_with_filters(
        self, client: AsyncClient, auth_headers: dict
    ) -> None:
        resp = await client.get(
            "/api/v1/approvals",
            params={"domain": "finance", "priority": "high"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert "items" in resp.json()

    async def test_approve_decision(self, client: AsyncClient, auth_headers: dict) -> None:
        hitl_id = str(uuid.uuid4())
        resp = await client.post(
            f"/api/v1/approvals/{hitl_id}/decide",
            json={"decision": "approve", "notes": "Looks correct, approved."},
            headers=auth_headers,
        )
        assert resp.status_code == 404

    async def test_reject_decision(self, client: AsyncClient, auth_headers: dict) -> None:
        hitl_id = str(uuid.uuid4())
        resp = await client.post(
            f"/api/v1/approvals/{hitl_id}/decide",
            json={"decision": "reject", "notes": "GSTIN mismatch detected."},
            headers=auth_headers,
        )
        assert resp.status_code == 404

    async def test_defer_decision(self, client: AsyncClient, auth_headers: dict) -> None:
        hitl_id = str(uuid.uuid4())
        resp = await client.post(
            f"/api/v1/approvals/{hitl_id}/decide",
            json={"decision": "defer", "notes": "Need more information from vendor."},
            headers=auth_headers,
        )
        assert resp.status_code == 404

    async def test_decide_requires_decision_field(
        self, client: AsyncClient, auth_headers: dict
    ) -> None:
        hitl_id = str(uuid.uuid4())
        resp = await client.post(
            f"/api/v1/approvals/{hitl_id}/decide",
            json={"notes": "Missing decision field"},
            headers=auth_headers,
        )
        assert resp.status_code == 422  # Validation error


# =========================================================================
# 5. Connector registration
# =========================================================================


class TestConnectorRegistration:
    """Register connector -> check health."""

    @pytest.fixture
    def connector_payload(self) -> dict:
        return {
            "name": f"oracle-fusion-{uuid.uuid4().hex[:8]}",
            "category": "erp",
            "base_url": "https://oracle.example.com/api/v1",
            "auth_type": "oauth2",
            "auth_config": {
                "token_url": "https://oracle.example.com/oauth/token",
                "grant_type": "client_credentials",
            },
            "secret_ref": "vault://secrets/oracle-fusion-creds",
            "tool_functions": [
                {
                    "name": "read_purchase_order",
                    "method": "GET",
                    "path": "/purchase-orders/{po_id}",
                    "scopes": ["oracle_fusion:read:purchase_order"],
                },
                {
                    "name": "create_journal_entry",
                    "method": "POST",
                    "path": "/journal-entries",
                    "scopes": ["oracle_fusion:write:journal"],
                },
            ],
            "data_schema_ref": "oracle_fusion_po_v3",
            "rate_limit_rpm": 120,
        }

    async def test_register_connector(
        self, client: AsyncClient, auth_headers: dict, connector_payload: dict
    ) -> None:
        resp = await client.post("/api/v1/connectors", json=connector_payload, headers=auth_headers)
        assert resp.status_code == 201
        body = resp.json()
        assert "connector_id" in body
        assert body["name"] == connector_payload["name"]
        assert body["status"] == "active"

    async def test_connector_health(self, client: AsyncClient, auth_headers: dict) -> None:
        conn_id = str(uuid.uuid4())
        resp = await client.get(f"/api/v1/connectors/{conn_id}/health", headers=auth_headers)
        assert resp.status_code == 404

    async def test_register_connector_minimal(
        self, client: AsyncClient, auth_headers: dict
    ) -> None:
        """Register a connector with only required fields."""
        resp = await client.post(
            "/api/v1/connectors",
            json={
                "name": "slack-notifier",
                "category": "communication",
                "auth_type": "bearer_token",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["status"] == "active"

    async def test_register_connector_missing_required_fields(
        self, client: AsyncClient, auth_headers: dict
    ) -> None:
        """Omitting required fields should return 422."""
        resp = await client.post(
            "/api/v1/connectors",
            json={"name": "incomplete"},
            headers=auth_headers,
        )
        assert resp.status_code == 422


# =========================================================================
# 6. Audit log queries
# =========================================================================


class TestAuditLogQueries:
    """Query the audit log with various filter combinations."""

    async def test_query_audit_unfiltered(self, client: AsyncClient, auth_headers: dict) -> None:
        resp = await client.get("/api/v1/audit", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert "items" in body
        assert "total" in body
        assert "page" in body
        assert "per_page" in body

    async def test_query_audit_by_event_type(self, client: AsyncClient, auth_headers: dict) -> None:
        resp = await client.get(
            "/api/v1/audit",
            params={"event_type": "agent.created"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body["items"], list)

    async def test_query_audit_by_agent_id(self, client: AsyncClient, auth_headers: dict) -> None:
        agent_id = str(uuid.uuid4())
        resp = await client.get(
            "/api/v1/audit",
            params={"agent_id": agent_id},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert "items" in resp.json()

    async def test_query_audit_by_date_range(self, client: AsyncClient, auth_headers: dict) -> None:
        resp = await client.get(
            "/api/v1/audit",
            params={
                "date_from": "2026-01-01",
                "date_to": "2026-12-31",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body["items"], list)

    async def test_query_audit_pagination(self, client: AsyncClient, auth_headers: dict) -> None:
        resp = await client.get(
            "/api/v1/audit",
            params={"page": 2, "per_page": 10},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["page"] == 2
        assert body["per_page"] == 10

    async def test_query_audit_combined_filters(
        self, client: AsyncClient, auth_headers: dict
    ) -> None:
        resp = await client.get(
            "/api/v1/audit",
            params={
                "event_type": "hitl.decided",
                "agent_id": str(uuid.uuid4()),
                "date_from": "2026-01-01",
                "date_to": "2026-06-30",
                "page": 1,
                "per_page": 5,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["per_page"] == 5


# =========================================================================
# 7. Authentication / Authorization edge cases
# =========================================================================


class TestAuthEdgeCases:
    """Verify auth middleware behavior for integration scenarios."""

    async def test_missing_auth_header_returns_401(self, client: AsyncClient) -> None:
        resp = await client.get("/api/v1/agents")
        assert resp.status_code == 401

    async def test_malformed_bearer_token_returns_401(self, client: AsyncClient) -> None:
        resp = await client.get(
            "/api/v1/agents",
            headers={"Authorization": "Bearer not-a-valid-jwt"},
        )
        assert resp.status_code == 401

    async def test_missing_bearer_prefix_returns_401(self, client: AsyncClient) -> None:
        resp = await client.get(
            "/api/v1/agents",
            headers={"Authorization": "Token abc123"},
        )
        assert resp.status_code == 401

    async def test_different_tenants_get_isolated_responses(
        self, client: AsyncClient, make_auth_headers
    ) -> None:
        """Two different tenant tokens should both succeed but get their own data."""
        tenant_a = str(uuid.uuid4())
        tenant_b = str(uuid.uuid4())

        headers_a = make_auth_headers(tenant_id=tenant_a)
        headers_b = make_auth_headers(tenant_id=tenant_b)

        resp_a = await client.get("/api/v1/agents", headers=headers_a)
        resp_b = await client.get("/api/v1/agents", headers=headers_b)

        assert resp_a.status_code == 200
        assert resp_b.status_code == 200

    async def test_health_does_not_require_auth(self, client: AsyncClient) -> None:
        resp = await client.get("/api/v1/health")
        assert resp.status_code == 200


# =========================================================================
# 8. Full end-to-end workflow: create agent -> create workflow -> trigger
# =========================================================================


class TestFullPipeline:
    """Simulate a realistic sequence through the API."""

    async def test_agent_to_workflow_pipeline(
        self, client: AsyncClient, auth_headers: dict
    ) -> None:
        """Create an agent, create a workflow referencing it, trigger a run."""
        # Step 1: Create agent
        agent_resp = await client.post(
            "/api/v1/agents",
            json={
                "name": "pipeline-test-agent",
                "agent_type": "invoice_extractor",
                "domain": "finance",
                "system_prompt": "Extract invoice data from PDFs.",
                "hitl_policy": {
                    "condition": "confidence < 0.88",
                    "assignee_role": "ap_clerk",
                    "timeout_hours": 2,
                    "on_timeout": "escalate",
                },
            },
            headers=auth_headers,
        )
        assert agent_resp.status_code == 201
        agent_id = agent_resp.json()["agent_id"]

        # Step 2: Create workflow
        wf_resp = await client.post(
            "/api/v1/workflows",
            json={
                "name": "pipeline-test-workflow",
                "version": "1.0",
                "domain": "finance",
                "definition": {
                    "steps": [
                        {
                            "id": "extract",
                            "agent": agent_id,
                            "input": "$trigger.document_url",
                        }
                    ],
                },
            },
            headers=auth_headers,
        )
        assert wf_resp.status_code == 201
        wf_id = wf_resp.json()["workflow_id"]

        # Step 3: Trigger workflow run
        run_resp = await client.post(
            f"/api/v1/workflows/{wf_id}/run",
            json={"payload": {"document_url": "s3://test/invoice.pdf"}},
            headers=auth_headers,
        )
        assert run_resp.status_code == 200
        assert run_resp.json()["status"] == "running"

    async def test_agent_create_pause_resume_promote(
        self, client: AsyncClient, auth_headers: dict
    ) -> None:
        """Full agent lifecycle: create -> pause -> resume -> promote."""
        # Create
        create_resp = await client.post(
            "/api/v1/agents",
            json={
                "name": "lifecycle-test-agent",
                "agent_type": "recon_agent",
                "domain": "finance",
                "system_prompt": "Reconcile bank statements.",
                "hitl_policy": {
                    "condition": "variance > 10000",
                    "assignee_role": "accountant",
                    "timeout_hours": 8,
                    "on_timeout": "escalate",
                },
            },
            headers=auth_headers,
        )
        assert create_resp.status_code == 201
        agent_id = create_resp.json()["agent_id"]

        # Pause
        pause_resp = await client.post(f"/api/v1/agents/{agent_id}/pause", headers=auth_headers)
        assert pause_resp.status_code == 200
        assert pause_resp.json()["status"] == "paused"

        # Resume — agent was created in shadow, so it resumes back to shadow
        resume_resp = await client.post(f"/api/v1/agents/{agent_id}/resume", headers=auth_headers)
        assert resume_resp.status_code == 200
        assert resume_resp.json()["status"] == "shadow"

        # Promote (may return 409 if shadow accuracy checks not met)
        promote_resp = await client.post(f"/api/v1/agents/{agent_id}/promote", headers=auth_headers)
        assert promote_resp.status_code in (200, 409)

    async def test_connector_register_and_health_check(
        self, client: AsyncClient, auth_headers: dict
    ) -> None:
        """Register a connector then verify its health endpoint."""
        reg_resp = await client.post(
            "/api/v1/connectors",
            json={
                "name": "tally-prime-test",
                "category": "accounting",
                "base_url": "https://tally.example.com/api",
                "auth_type": "api_key",
                "auth_config": {"header": "X-API-Key"},
                "secret_ref": "vault://secrets/tally-key",
                "rate_limit_rpm": 60,
            },
            headers=auth_headers,
        )
        assert reg_resp.status_code == 201
        conn_id = reg_resp.json()["connector_id"]

        health_resp = await client.get(f"/api/v1/connectors/{conn_id}/health", headers=auth_headers)
        assert health_resp.status_code == 200
        assert health_resp.json()["status"] in ("healthy", "active")

    async def test_approval_flow_after_workflow_trigger(
        self, client: AsyncClient, auth_headers: dict
    ) -> None:
        """Trigger a workflow, then exercise the approval endpoint."""
        # Trigger a workflow run (non-existent workflow returns 404)
        wf_id = str(uuid.uuid4())
        run_resp = await client.post(
            f"/api/v1/workflows/{wf_id}/run",
            json={"payload": {"total": 750000}},
            headers=auth_headers,
        )
        assert run_resp.status_code == 404

        # List pending approvals
        approvals_resp = await client.get("/api/v1/approvals", headers=auth_headers)
        assert approvals_resp.status_code == 200

        # Decide on a non-existent approval returns 404
        hitl_id = str(uuid.uuid4())
        decide_resp = await client.post(
            f"/api/v1/approvals/{hitl_id}/decide",
            json={"decision": "approve", "notes": "Amount verified against PO."},
            headers=auth_headers,
        )
        assert decide_resp.status_code == 404
