"""End-to-end SDK launch and workflow composition regressions.

This suite pins the user-facing contract across the SDKs, A2A/MCP discovery,
commerce buyer/seller guardrails, and template-to-workflow generation. It uses
deterministic local doubles for external HTTP/LLM systems, but calls the real
SDK/client/generator/commerce code in this checkout.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

TENANT_ID = "00000000-0000-0000-0000-000000000001"


def _load_repo_sdk_client_module() -> Any:
    root = pathlib.Path(__file__).resolve().parents[2]
    client_path = root / "sdk" / "agenticorg" / "client.py"
    spec = importlib.util.spec_from_file_location("_repo_sdk_e2e_client", client_path)
    assert spec and spec.loader, f"cannot load {client_path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules["_repo_sdk_e2e_client"] = module
    spec.loader.exec_module(module)
    return module


def test_python_sdk_sop_upload_uses_multipart_and_preserves_auth(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    sdk_mod = _load_repo_sdk_client_module()
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization")
        captured["content_type"] = request.headers.get("content-type")
        captured["body"] = request.content
        return httpx.Response(200, json={"status": "parsed"})

    transport = httpx.MockTransport(handler)
    original_client = sdk_mod.httpx.Client

    def client_factory(*args: Any, **kwargs: Any) -> httpx.Client:
        kwargs["transport"] = transport
        return original_client(*args, **kwargs)

    monkeypatch.setattr(sdk_mod.httpx, "Client", client_factory)
    sop_path = tmp_path / "invoice-sop.md"
    sop_path.write_text("# Invoice SOP\n\nValidate the invoice.", encoding="utf-8")

    client = sdk_mod.AgenticOrg(
        api_key="sdk-upload-key",
        base_url="https://agenticorg.test",
    )
    try:
        result = client.sop.upload(str(sop_path), domain_hint="finance")
    finally:
        client.close()

    assert result == {"status": "parsed"}
    assert captured["authorization"] == "Bearer sdk-upload-key"
    assert captured["content_type"].startswith("multipart/form-data; boundary=")
    assert b'name="domain_hint"' in captured["body"]
    assert b'name="file"; filename="invoice-sop.md"' in captured["body"]


def test_python_sdk_can_launch_agents_discover_mcp_use_kb_and_workflows(monkeypatch: pytest.MonkeyPatch) -> None:
    sdk_mod = _load_repo_sdk_client_module()
    calls: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8")) if request.content else {}
        calls.append(
            {
                "method": request.method,
                "path": request.url.path,
                "params": dict(request.url.params),
                "json": body,
                "authorization": request.headers.get("authorization"),
            }
        )
        path = request.url.path
        method = request.method

        if method == "GET" and path == "/api/v1/a2a/agent-card":
            return httpx.Response(
                200,
                json={
                    "name": "AgenticOrg Agent Platform",
                    "skills": [
                        {"id": "commerce_sales_agent", "tools": ["grantex_commerce:buyer_discovery_preview"]},
                        {"id": "contract_intelligence", "tools": ["search_content_fulltext"]},
                    ],
                },
            )
        if method == "GET" and path == "/api/v1/a2a/agents":
            return httpx.Response(200, json={"agents": [{"id": "commerce_sales_agent"}]})
        if method == "GET" and path == "/api/v1/mcp/tools":
            return httpx.Response(
                200,
                json={"tools": [{"name": "agenticorg_commerce_sales_agent", "inputSchema": {"type": "object"}}]},
            )
        if method == "POST" and path == "/api/v1/mcp/call":
            return httpx.Response(
                200,
                json={
                    "content": [{"type": "text", "text": "Agent: Commerce Sales Agent\nStatus: completed"}],
                    "isError": False,
                },
            )
        if method == "GET" and path == "/api/v1/connectors":
            return httpx.Response(200, json={"items": [{"id": "registry-confluence", "category": "ops"}]})
        if method == "POST" and path == "/api/v1/agents/generate":
            return httpx.Response(
                200,
                json={
                    "suggestions": [
                        {
                            "agent_type": "contract_intelligence",
                            "domain": "ops",
                            "suggested_tools": ["search_content_fulltext", "create_page", "search_issues"],
                        }
                    ],
                    "deployed": {"agent_id": "agent_shadow_1", "status": "shadow"},
                },
            )
        if method == "POST" and path == "/api/v1/a2a/tasks":
            return httpx.Response(
                200,
                json={
                    "run_id": "a2a_commerce_1",
                    "agent_type": body["agent_type"],
                    "status": "completed",
                    "output": {
                        "commerce_response": {
                            "status": "preview_only",
                            "merchant_preview": {"display_name": "Grounded Store"},
                        }
                    },
                    "confidence": 0.91,
                    "runtime": "a2a",
                    "reasoning_trace": ["read Grantex buyer preview"],
                    "tool_calls": [{"tool": "grantex_commerce:buyer_discovery_preview"}],
                },
            )
        if method == "POST" and path == "/api/v1/knowledge/search":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "chunk_text": "Refunds require seller-source confirmation.",
                            "score": 0.94,
                            "document_name": "commerce-policy.md",
                        }
                    ]
                },
            )
        if method == "GET" and path == "/api/v1/workflows/templates":
            return httpx.Response(200, json={"items": [{"id": "tpl-contract-renewal", "domain": "ops"}]})
        if method == "POST" and path == "/api/v1/workflows/generate":
            return httpx.Response(
                200,
                json={
                    "workflow": {
                        "name": "Contract Intelligence Workflow",
                        "steps": [{"id": "search_kb", "type": "agent", "agent_type": "contract_intelligence"}],
                    },
                    "deployed": False,
                    "workflow_id": None,
                },
            )
        if method == "POST" and path == "/api/v1/workflows":
            return httpx.Response(201, json={"workflow_id": "wf_contract_1", "name": body["name"], "version": "1.0"})
        if method == "POST" and path == "/api/v1/workflows/wf_contract_1/run":
            return httpx.Response(200, json={"run_id": "run_contract_1", "status": "running"})
        if method == "GET" and path == "/api/v1/workflows/runs/run_contract_1":
            return httpx.Response(
                200,
                json={"run_id": "run_contract_1", "status": "completed", "steps": [{"step_id": "search_kb"}]},
            )
        return httpx.Response(404, json={"error": f"unhandled {method} {path}"})

    transport = httpx.MockTransport(handler)
    original_client = sdk_mod.httpx.Client

    def client_factory(*args: Any, **kwargs: Any) -> httpx.Client:
        kwargs["transport"] = transport
        return original_client(*args, **kwargs)

    monkeypatch.setattr(sdk_mod.httpx, "Client", client_factory)

    client = sdk_mod.AgenticOrg(api_key="sdk-test-key", base_url="https://agenticorg.test")
    try:
        assert client.a2a.agent_card()["skills"][0]["id"] == "commerce_sales_agent"
        assert client.a2a.agents()[0]["id"] == "commerce_sales_agent"
        assert client.mcp.tools()[0]["name"] == "agenticorg_commerce_sales_agent"
        assert client.connectors.list(category="ops")[0]["id"] == "registry-confluence"

        generated_agent = client.agents.generate(
            "Create a contract intelligence agent that uses knowledge search and Jira.",
            deploy=True,
        )
        assert generated_agent["deployed"]["status"] == "shadow"

        commerce_run = client.agents.run(
            "commerce_sales_agent",
            action="discover",
            inputs={"merchant_id": "mch_C6W3", "buyer_agent_id": "buyer_C6W3"},
        )
        assert commerce_run.status == "completed"
        assert commerce_run.agent_type == "commerce_sales_agent"
        assert commerce_run.output["commerce_response"]["status"] == "preview_only"
        assert commerce_run.tool_calls[0]["tool"] == "grantex_commerce:buyer_discovery_preview"

        mcp_call = client.mcp.call("agenticorg_commerce_sales_agent", {"inputs": {"merchant_id": "mch_C6W3"}})
        assert mcp_call["isError"] is False

        kb = client.knowledge.search("refund confirmation policy", top_k=1)
        assert kb[0]["document_name"] == "commerce-policy.md"

        assert client.workflows.templates(domain="ops")[0]["id"] == "tpl-contract-renewal"
        generated_workflow = client.workflows.generate("Search KB then open a Jira-backed renewal workflow.")
        assert generated_workflow["workflow"]["steps"][0]["agent_type"] == "contract_intelligence"

        workflow = client.workflows.create(
            name="Contract Intelligence Workflow",
            domain="ops",
            trigger_type="manual",
            definition={
                "steps": [
                    {
                        "id": "search_kb",
                        "type": "agent",
                        "agent_type": "contract_intelligence",
                        "authorized_tools": ["search_content_fulltext"],
                        "knowledge_sources": ["kb_contracts"],
                    }
                ]
            },
        )
        run = client.workflows.run(workflow["workflow_id"], payload={"contract_id": "CTR-1"})
        assert client.workflows.get_run(run["run_id"])["status"] == "completed"
    finally:
        client.close()

    paths = [call["path"] for call in calls]
    for expected in (
        "/api/v1/a2a/agent-card",
        "/api/v1/a2a/tasks",
        "/api/v1/mcp/tools",
        "/api/v1/mcp/call",
        "/api/v1/agents/generate",
        "/api/v1/knowledge/search",
        "/api/v1/workflows/templates",
        "/api/v1/workflows/generate",
        "/api/v1/workflows",
    ):
        assert expected in paths
    assert {call["authorization"] for call in calls} == {"Bearer sdk-test-key"}


def test_python_cli_launch_generation_knowledge_and_workflow_commands(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = pathlib.Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(root / "sdk"))
    for module_name in ("agenticorg.cli", "agenticorg"):
        sys.modules.pop(module_name, None)

    import agenticorg
    from agenticorg import cli

    class FakeClient:
        instances: list[FakeClient] = []

        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs
            self.calls: list[tuple[str, dict[str, Any]]] = []
            self.agents = SimpleNamespace(
                run=self._agents_run,
                generate=self._agents_generate,
                list=lambda domain=None: [],
                get=lambda agent_id: {"id": agent_id},
            )
            self.connectors = SimpleNamespace(
                list=lambda category=None: [{"id": "registry-jira", "category": category}],
                get=lambda connector_id: {"id": connector_id},
            )
            self.workflows = SimpleNamespace(
                generate=self._workflows_generate,
                run=self._workflows_run,
                get_run=lambda run_id: {"run_id": run_id, "status": "completed"},
                templates=lambda domain=None: [{"id": "tpl-renewal", "domain": domain}],
                list=lambda: {"items": []},
                get=lambda workflow_id: {"id": workflow_id},
                create=lambda **kwargs: {"id": "wf-created", **kwargs},
            )
            self.knowledge = SimpleNamespace(
                search=lambda query, top_k=5: [{"document_name": "policy.md", "query": query, "top_k": top_k}],
            )
            self.sop = SimpleNamespace(
                parse_text=lambda text, domain_hint="": {"text": text, "domain_hint": domain_hint}
            )
            self.a2a = SimpleNamespace(agent_card=lambda: {"skills": []}, agents=lambda: [])
            self.mcp = SimpleNamespace(tools=lambda: [])
            FakeClient.instances.append(self)

        def close(self) -> None:
            self.calls.append(("close", {}))

        def _agents_run(self, agent_type: str, *, action: str, inputs: dict[str, Any]) -> dict[str, Any]:
            call = {"agent_type": agent_type, "action": action, "inputs": inputs}
            self.calls.append(("agents.run", call))
            return call

        def _agents_generate(
            self,
            description: str,
            *,
            deploy: bool = False,
            company_id: str | None = None,
        ) -> dict[str, Any]:
            call = {"description": description, "deploy": deploy, "company_id": company_id}
            self.calls.append(("agents.generate", call))
            return call

        def _workflows_generate(self, description: str, *, deploy: bool = False) -> dict[str, Any]:
            call = {"description": description, "deploy": deploy}
            self.calls.append(("workflows.generate", call))
            return call

        def _workflows_run(self, workflow_id: str, *, payload: dict[str, Any]) -> dict[str, Any]:
            call = {"workflow_id": workflow_id, "payload": payload}
            self.calls.append(("workflows.run", call))
            return call

    monkeypatch.setattr(agenticorg, "AgenticOrg", FakeClient)

    def run_cli(argv: list[str]) -> tuple[dict[str, Any] | list[Any], FakeClient]:
        monkeypatch.setattr(sys, "argv", ["agenticorg", "--api-key", "cli-key", *argv])
        cli.main()
        stdout = capsys.readouterr().out
        return json.loads(stdout), FakeClient.instances[-1]

    output, client = run_cli(
        [
            "agents",
            "run",
            "commerce_sales_agent",
            "--action",
            "buyer_discovery_preview",
            "--input",
            '{"merchant_id":"merchant_demo"}',
        ]
    )
    assert output["agent_type"] == "commerce_sales_agent"
    assert output["action"] == "buyer_discovery_preview"
    assert output["inputs"] == {"merchant_id": "merchant_demo"}
    assert ("agents.run", output) in client.calls

    output, _ = run_cli(["agents", "generate", "Create contract intelligence agent", "--deploy"])
    assert output["deploy"] is True

    output, _ = run_cli(["knowledge", "search", "vendor renewal policy", "--top-k", "2"])
    assert output[0]["document_name"] == "policy.md"
    assert output[0]["top_k"] == 2

    output, _ = run_cli(["workflows", "generate", "Review vendor renewal risk"])
    assert output["description"] == "Review vendor renewal risk"

    output, _ = run_cli(["workflows", "run", "wf-1", "--input", '{"vendor_id":"V-100"}'])
    assert output["workflow_id"] == "wf-1"
    assert output["payload"] == {"vendor_id": "V-100"}


@pytest.mark.asyncio
async def test_runtime_catalogs_do_not_drift_between_generators_a2a_and_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    from api.v1.a2a import list_available_agents
    from api.v1.agents import _AGENT_TYPE_DEFAULT_TOOLS as API_AGENT_TOOLS
    from api.v1.mcp import list_tools
    from core.agent_generator import VALID_AGENT_TYPES
    from core.commerce.discovery_gate import COMMERCE_PUBLIC_DISCOVERY_ENV
    from core.workflow_generator import KNOWN_AGENT_TYPES

    monkeypatch.setenv(COMMERCE_PUBLIC_DISCOVERY_ENV, "true")

    api_agent_types = set(API_AGENT_TOOLS)
    assert "commerce_sales_agent" in api_agent_types
    assert set(KNOWN_AGENT_TYPES) == api_agent_types
    assert VALID_AGENT_TYPES == api_agent_types

    a2a_agent_ids = {agent["id"] for agent in (await list_available_agents())["agents"]}
    mcp_agent_ids = {tool["name"].removeprefix("agenticorg_") for tool in (await list_tools())["tools"]}
    assert a2a_agent_ids == api_agent_types
    assert mcp_agent_ids == api_agent_types


@pytest.mark.asyncio
async def test_buyer_seller_commerce_discovery_and_prepared_handoff_are_non_executing() -> None:
    from core.commerce.buyer_session import start_buyer_discovery_session
    from core.commerce.oacp_artifacts import (
        OACP_C6W3_VALID_ARTIFACT_FIXTURES,
        InMemoryOacpArtifactCacheRepository,
        OacpArtifactCacheRepositoryQuery,
        OacpPersistentArtifactCacheRecord,
        evaluate_agenticorg_c6w5_commitment_boundary,
        prepare_agenticorg_c6w6_commitment_envelope,
    )
    from core.commerce.sales_guardrails import validate_payment_action

    class FakeConnector:
        def __init__(self) -> None:
            self.calls: list[dict[str, str]] = []

        async def buyer_discovery_preview(self, **params: str) -> dict[str, Any]:
            self.calls.append(dict(params))
            return {
                "data": {
                    "merchant_reference": "safe-merchant-ref",
                    "display_name": "Grounded Preview Store",
                    "integration_status": "sandbox_handoff_requested",
                    "generated_at": "2026-06-13T10:00:00Z",
                    "audit_event_id": "audit_sdk_e2e",
                    "merchant": {
                        "display_name": "Grounded Preview Store",
                        "category_preset": "electronics_appliances",
                        "country_code": "IN",
                        "default_currency": "INR",
                        "public_discovery_description_draft": "Preview-only catalog evidence.",
                    },
                    "readiness_summary": {"overall_status": "ready"},
                    "agent_facing_preview_summary": {"preview_status": "ready", "sample_product_count": 1},
                    "rollout_proposal_summary": {"proposal_status": "dry_run_passed"},
                    "evidence_checklist": [{"key": "preview", "label": "Preview ready", "status": "pass"}],
                    "sample_products": [
                        {
                            "title": "Grounded Mixer",
                            "brand": "SafeBrand",
                            "category_preset": "electronics_appliances",
                            "price": "999",
                            "currency": "INR",
                            "source_label": "grantex_preview",
                        }
                    ],
                    "allowed_buyer_agent_capabilities": ["read_only_catalog_discovery_preview"],
                    "blocked_buyer_agent_capabilities": [
                        "checkout_payment_creation",
                        "live_payment",
                        "order_fulfillment",
                    ],
                    "blockers": [],
                    "sandbox_only": True,
                    "buyer_agent_discovery_is_public": False,
                    "agenticorg_public_discovery_enabled": False,
                    "public_discovery_enabled": False,
                    "checkout_payment_enabled": False,
                    "live_provider_enabled": False,
                    "live_mode_status": "not_live",
                    "production_approval_status": "not_approved",
                }
            }

    connector = FakeConnector()
    buyer_response = await start_buyer_discovery_session(
        connector,
        merchant_id="mch_C6W3",
        request_text="Show me this seller catalog preview.",
        channel="chatgpt",
    )
    assert connector.calls == [{"merchant_id": "mch_C6W3"}]
    assert buyer_response["status"] == "preview_only"
    assert buyer_response["merchant_preview"]["display_name"] == "Grounded Preview Store"
    assert buyer_response["evidence_summary"]["grantex_grounded"] is True
    assert "checkout_payment_creation" in buyer_response["blocked_capabilities"]

    checkout = validate_payment_action("checkout_create", {"merchant_id": "mch_C6W3", "provider_key": "mock"})
    assert checkout["allowed"] is False
    assert checkout["error"] == "consent_required"

    repository = InMemoryOacpArtifactCacheRepository()
    base_cache_record = OacpPersistentArtifactCacheRecord(
        cache_record_id="cache_price_sdk_e2e",
        artifact_id="price_C6W3",
        artifact_type="price",
        authority="grantex_canonical_oacp_artifact_authority",
        issuer="grantex",
        scope_kind="buyer_agent",
        tenant_id="cten_C6W3",
        merchant_id="mch_C6W3",
        seller_agent_id="seller_C6W3",
        buyer_agent_id="buyer_C6W3",
        source_refs=("source_ref_price_sdk_e2e",),
        evidence_refs=("evidence_price_sdk_e2e_redacted",),
        generated_at="2026-06-11T00:00:00.000Z",
        cached_at="2026-06-11T00:00:10.000Z",
        expires_at="2026-06-11T00:05:00.000Z",
        freshness_status="fresh",
        revocation_snapshot_status="fresh",
        revocation_snapshot_observed_at="2026-06-11T00:00:30.000Z",
        revocation_snapshot_age_seconds=30,
        ttl_policy_seconds=300,
        risk_tier="low",
        blocked_capabilities=("checkout_payment_creation", "live_provider_call"),
        unsupported_capabilities=("transaction_authority_from_adapter_preview",),
        verifier_result_ref="verifier_result_price_sdk_e2e",
    )
    assert repository.upsert(base_cache_record)["stored"] is True
    assert repository.upsert(
        replace(
            base_cache_record,
            cache_record_id="cache_seller_sdk_e2e",
            artifact_id="seller_agent_capability_C6W3",
            artifact_type="seller_agent_capability",
            scope_kind="seller_agent",
            buyer_agent_id=None,
            ttl_policy_seconds=6 * 60 * 60,
        )
    )["stored"] is True
    assert repository.list_for_scope(
        OacpArtifactCacheRepositoryQuery(
            tenant_id="cten_C6W3",
            merchant_id="mch_C6W3",
            seller_agent_id="seller_C6W3",
        )
    )

    commitment = repository.evaluate(
        cache_record_id="cache_price_sdk_e2e",
        action_intent="final_commitment",
        now_iso="2026-06-11T00:01:00.000Z",
        grantex_available=False,
        expected_scope={
            "tenant_id": "cten_C6W3",
            "merchant_id": "mch_C6W3",
            "seller_agent_id": "seller_C6W3",
            "buyer_agent_id": "buyer_C6W3",
        },
    )
    assert commitment["allowed_to_prepare"] is True
    assert commitment["allowed_to_execute"] is False
    assert commitment["non_authoritative_for_transaction"] is True

    artifacts = [
        OACP_C6W3_VALID_ARTIFACT_FIXTURES[name]
        for name in (
            "merchant_capability",
            "seller_agent_capability",
            "catalog_snapshot",
            "offer",
            "price",
            "inventory",
            "policy",
            "mandate_capability",
            "commitment_evidence",
            "protocol_adapter",
        )
    ]
    preview = {
        "generated": True,
        "status": "preview_only",
        "surface": "mcp_tool_resource_capability",
        "source_artifact_ids": [artifact["envelope"]["artifact_id"] for artifact in artifacts],
        "source_artifact_families": [artifact["envelope"]["artifact_type"] for artifact in artifacts],
        "source_authority": "grantex_canonical_oacp_artifact_authority",
        "generated_at": "2026-06-11T00:00:30.000Z",
        "expires_at": "2026-06-11T00:02:00.000Z",
        "max_ttl_seconds": 90,
        "freshness_tier": "fresh",
        "unsupported_capabilities": ["checkout_create", "payment_authorize", "live_provider_call"],
        "blocked_capabilities": ["checkout_create", "payment_authorize"],
        "non_authoritative_for_transaction": True,
        "no_checkout_payment_enablement": True,
        "no_live_provider_enablement": True,
        "no_public_discovery_enablement": True,
    }
    decision = evaluate_agenticorg_c6w5_commitment_boundary(
        action="price_lock",
        cached_artifacts=artifacts,
        adapter_preview=preview,
        now_iso="2026-06-11T00:01:00.000Z",
        grantex_available=False,
        revocation_snapshot_age_seconds=30,
        currency="INR",
        amount_minor_units=200000,
        total_quantity=1,
        max_quantity_per_sku=1,
    )
    envelope = prepare_agenticorg_c6w6_commitment_envelope(
        envelope_kind="buyer_confirmation_request",
        resolver_decision=decision,
        created_at="2026-06-11T00:01:15.000Z",
        evidence_refs=["price-evidence-ref", "https://private.example/internal"],
        currency="INR",
        amount_minor_units=200000,
        total_quantity=1,
    )
    assert envelope["generated"] is True
    assert envelope["envelope"]["prepared_only"] is True
    assert envelope["envelope"]["allowed_to_execute"] is False
    assert envelope["envelope"]["commerce_facts_invented"] is False
    assert "redacted_private_evidence_ref" in envelope["envelope"]["redacted_evidence_refs"]


@pytest.mark.asyncio
async def test_template_skills_connectors_tools_and_kb_generate_launchable_workflows() -> None:
    from api.v1.agents import _AGENT_TYPE_DEFAULT_TOOLS as API_AGENT_TOOLS
    from core.agent_generator import generate_agent_config
    from core.workflow_generator import generate_workflow

    suggestion = {
        "confidence": 0.94,
        "agent_type": "contract_intelligence",
        "domain": "ops",
        "employee_name": "Ananya Rao",
        "designation": "Contract Intelligence Lead",
        "suggested_tools": ["search_content_fulltext", "create_page", "search_issues"],
        "system_prompt": (
            "You inspect contract renewal requests, search approved knowledge bases, "
            "prepare source-grounded summaries, and open follow-up issues for human review."
        ),
        "confidence_floor": 0.9,
        "hitl_condition": "confidence < 0.9 OR renewal_value > 500000",
        "specialization": "Contract renewal workflows with KB and Jira evidence",
    }

    class MockLLM:
        async def complete(self, **_kwargs: Any) -> SimpleNamespace:
            return SimpleNamespace(
                content=json.dumps({"suggestions": [suggestion]}),
                model="mock-agent-generator",
                tokens_used=123,
            )

    generated_agent = await generate_agent_config(
        "Launch a contract intelligence agent using Confluence KB, Jira issues, and source-grounded tools.",
        llm=MockLLM(),
    )
    top = generated_agent["suggestions"][0]
    assert top["agent_type"] == "contract_intelligence"
    assert set(top["suggested_tools"]) <= set(API_AGENT_TOOLS[top["agent_type"]])
    assert "validation_errors" not in top

    workflow_definition = {
        "name": "Contract Renewal Intelligence",
        "description": "Search KB, inspect vendor issues, and route renewal decisions.",
        "domain": "ops",
        "trigger_type": "manual",
        "trigger_config": {},
        "version": "1.0",
        "steps": [
            {
                "id": "search_contract_kb",
                "type": "agent",
                "title": "Search approved contract KB",
                "agent_type": "contract_intelligence",
                "authorized_tools": ["search_content_fulltext", "create_page"],
                "connectors": ["confluence"],
                "knowledge_sources": ["kb_contracts"],
            },
            {
                "id": "inspect_vendor_issue",
                "type": "agent",
                "title": "Inspect vendor issue history",
                "agent_type": "vendor_manager",
                "authorized_tools": ["search_issues", "create_issue"],
                "connectors": ["jira"],
                "depends_on": ["search_contract_kb"],
            },
            {
                "id": "human_review",
                "type": "human_in_loop",
                "title": "Human renewal approval",
                "assignee_role": "legal_ops",
                "timeout_hours": 4,
                "depends_on": ["inspect_vendor_issue"],
            },
        ],
    }

    async def mock_workflow_llm(_messages: list[dict[str, str]]) -> str:
        return json.dumps(workflow_definition)

    generated_workflow = await generate_workflow(
        "Create a workflow from the contract intelligence AI template using KB search, Confluence, and Jira.",
        TENANT_ID,
        _llm_override=mock_workflow_llm,
    )
    assert generated_workflow["name"] == "Contract Renewal Intelligence"
    for step in generated_workflow["steps"]:
        if step.get("type") == "agent":
            agent_type = step["agent_type"]
            assert agent_type in API_AGENT_TOOLS
            assert set(step.get("authorized_tools", [])) <= set(API_AGENT_TOOLS[agent_type])
    assert generated_workflow["steps"][0]["knowledge_sources"] == ["kb_contracts"]
    assert generated_workflow["steps"][0]["connectors"] == ["confluence"]
