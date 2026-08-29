"""Contract coverage for the AgenticOrg 0.4 runtime SDK resources."""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from typing import Any

import httpx
import pytest


def _load_sdk() -> Any:
    root = pathlib.Path(__file__).resolve().parents[2]
    path = root / "sdk" / "agenticorg" / "client.py"
    spec = importlib.util.spec_from_file_location("_repo_runtime_sdk", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["_repo_runtime_sdk"] = module
    spec.loader.exec_module(module)
    return module


def test_runtime_sdk_resources_use_canonical_paths_and_preserve_auth(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    sdk = _load_sdk()
    calls: list[tuple[str, str, str, bytes, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(
            (
                request.method,
                request.url.path,
                request.headers.get("authorization", ""),
                request.content,
                request.url.query.decode(),
            )
        )
        path = request.url.path
        if path == "/api/v1/connectors/connector_py_1/health":
            return httpx.Response(200, json={"status": "healthy"})
        if path == "/api/v1/connectors/connector_py_1/test":
            return httpx.Response(200, json={"ok": True})
        if path == "/api/v1/workflows/runs/run_py_1/cancel":
            return httpx.Response(200, json={"status": "cancel_requested"})
        if path == "/api/v1/knowledge/supported-types":
            return httpx.Response(200, json={"ocr": True})
        if path == "/api/v1/knowledge/upload":
            return httpx.Response(201, json={"document_id": "doc_py_1"})
        if path == "/api/v1/knowledge/documents":
            return httpx.Response(200, json={"items": [{"document_id": "doc_py_1"}], "total": 1})
        if path == "/api/v1/knowledge/documents/doc_py_1":
            return httpx.Response(200, json={"status": "deleted"})
        if path == "/api/v1/knowledge/health":
            return httpx.Response(200, json={"effective_mode": "pgvector"})
        if path == "/api/v1/knowledge/stats":
            return httpx.Response(200, json={"total_documents": 1})
        if path == "/api/v1/voice/status":
            return httpx.Response(200, json={"runtime_status": "ready"})
        if path == "/api/v1/voice/config":
            return httpx.Response(200, json={"status": "saved"})
        if path == "/api/v1/voice/test-connection":
            return httpx.Response(200, json={"ok": True})
        if path == "/api/v1/voice/runtime/health":
            return httpx.Response(200, json={"status": "ready"})
        if path == "/api/v1/voice/calls":
            return httpx.Response(200, json=[{"id": "call_py_1"}])
        if path == "/api/v1/voice/calls/outbound":
            return httpx.Response(200, json={"status": "queued"})
        if path == "/api/v1/rpa/scripts":
            return httpx.Response(200, json=[{"id": "builtin-gst-portal"}])
        if path == "/api/v1/rpa/history":
            return httpx.Response(200, json=[{"id": "rpa_py_1"}])
        if path == "/api/v1/rpa/scripts/builtin-gst-portal/run":
            return httpx.Response(200, json={"status": "completed"})
        if path == "/api/v1/bridge/register":
            return httpx.Response(200, json={"bridge_id": "bridge_py_1"})
        if path == "/api/v1/bridge/bridge_py_1/status":
            return httpx.Response(200, json={"connected": False})
        if path == "/api/v1/bridge/bridge_py_1":
            return httpx.Response(200, json={"status": "deregistered"})
        if path == "/api/v1/bridge/list":
            return httpx.Response(200, json=[{"bridge_id": "bridge_py_1"}])
        if path == "/api/v1/bridge/route/tally":
            return httpx.Response(200, json={"status": "completed"})
        if path == "/api/v1/commerce/runtime/seller-agents/onboarding-packets":
            return httpx.Response(200, json={"packet_id": "packet_py_1"})
        if path == "/api/v1/commerce/runtime/seller-agents/onboarding-packets/packet_py_1":
            return httpx.Response(200, json={"packet_id": "packet_py_1"})
        if path == "/api/v1/commerce/runtime/seller-agents/connectors/shopify/credentials":
            return httpx.Response(200, json={"status": "configured"})
        if path == "/api/v1/commerce/runtime/seller-agents/connectors/shopify/status":
            return httpx.Response(200, json={"status": "configured"})
        if path == "/api/v1/commerce/runtime/seller-agents/shopify/sync":
            return httpx.Response(200, json={"status": "read_only_sync_queued"})
        if path == "/api/v1/commerce/runtime/authority/grantex/request":
            return httpx.Response(200, json={"status": "prepared"})
        if path == "/api/v1/commerce/runtime/artifacts/cache":
            return httpx.Response(200, json={"cached": 1})
        if path == "/api/v1/commerce/runtime/products":
            return httpx.Response(200, json={"products": [{"id": "product_py_1"}]})
        if path == "/api/v1/commerce/runtime/buyer-sessions/ask":
            return httpx.Response(200, json={"status": "answered_from_cache"})
        if path == "/api/v1/commerce/runtime/protocol-adapters":
            return httpx.Response(200, json={"adapters": ["schema_org"]})
        if path == "/api/v1/commerce/runtime/protocol-adapters/schema_org":
            return httpx.Response(200, json={"surface": "schema_org"})
        if path == "/api/v1/commerce/runtime/bridges/surfaces":
            return httpx.Response(200, json={"surfaces": ["mcp", "a2a"]})
        if path.endswith("/providers/plural-pine/mandate-capability/verify"):
            return httpx.Response(200, json={"allowed_to_execute": False})
        if path == "/api/v1/commerce/runtime/purchase/prepare":
            return httpx.Response(200, json={"allowed_to_execute": False})
        if path == "/api/v1/commerce/runtime/pos/offline/readiness":
            return httpx.Response(200, json={"allowed_to_execute": False})
        if path == "/api/v1/commerce/runtime/pos/offline/handoffs":
            return httpx.Response(200, json={"handoff_id": "handoff_py_1"})
        if path == "/api/v1/commerce/runtime/pos/offline/confirmations":
            return httpx.Response(200, json={"status": "recorded"})
        if path == "/api/v1/commerce/runtime/pos/offline/simulator/confirm":
            return httpx.Response(200, json={"status": "simulated"})
        return httpx.Response(404, json={"path": path})

    transport = httpx.MockTransport(handler)
    original_client = sdk.httpx.Client

    def client_factory(*args: Any, **kwargs: Any) -> httpx.Client:
        kwargs["transport"] = transport
        return original_client(*args, **kwargs)

    monkeypatch.setattr(sdk.httpx, "Client", client_factory)
    upload = tmp_path / "scan.png"
    upload.write_bytes(b"synthetic scanned document")

    with sdk.AgenticOrg(api_key="sdk-runtime-key", base_url="https://agenticorg.test") as client:
        assert client.connectors.health("connector_py_1")["status"] == "healthy"
        assert client.connectors.test("connector_py_1")["ok"] is True
        assert client.workflows.cancel_run("run_py_1")["status"] == "cancel_requested"
        assert client.knowledge.supported_types()["ocr"] is True
        assert client.knowledge.upload(str(upload))["document_id"] == "doc_py_1"
        assert client.knowledge.documents()["total"] == 1
        assert client.knowledge.health()["effective_mode"] == "pgvector"
        assert client.knowledge.stats()["total_documents"] == 1
        assert client.knowledge.delete("doc_py_1")["status"] == "deleted"
        assert client.voice.status()["runtime_status"] == "ready"
        assert client.voice.save_config({"provider": "mock"})["status"] == "saved"
        assert client.voice.test_connection("mock", {"token": "synthetic"})["ok"] is True
        assert client.voice.runtime_health("agent_py_1")["status"] == "ready"
        assert client.voice.calls("agent_py_1")[0]["id"] == "call_py_1"
        assert (
            client.voice.place_outbound_call(
                agent_id="00000000-0000-0000-0000-000000000001",
                to_number="+919999999999",
            )["status"]
            == "queued"
        )
        assert client.rpa.scripts()[0]["id"] == "builtin-gst-portal"
        assert client.rpa.history()[0]["id"] == "rpa_py_1"
        assert client.rpa.run("builtin-gst-portal")["status"] == "completed"
        bridge = client.bridges.register({"tenant_id": "tenant_py_1", "connector_type": "tally"})
        assert client.bridges.status(bridge["bridge_id"])["connected"] is False
        assert client.bridges.list()[0]["bridge_id"] == "bridge_py_1"
        assert client.bridges.route("tally", {"bridge_id": bridge["bridge_id"]})["status"] == "completed"
        assert client.bridges.deregister(bridge["bridge_id"])["status"] == "deregistered"
        packet = client.commerce.create_onboarding_packet({"merchant_id": "merchant_py_1"})
        assert client.commerce.onboarding_packet(packet["packet_id"])["packet_id"] == "packet_py_1"
        assert client.commerce.configure_shopify({"merchant_id": "merchant_py_1"})["status"] == "configured"
        assert client.commerce.shopify_status("merchant_py_1")["status"] == "configured"
        assert client.commerce.sync_shopify({"packet_id": "packet_py_1"})["status"] == "read_only_sync_queued"
        assert client.commerce.request_grantex_authority({"packet_id": "packet_py_1"})["status"] == "prepared"
        assert client.commerce.cache_artifacts([{"artifact_id": "artifact_py_1"}])["cached"] == 1
        assert client.commerce.products(merchant_id="merchant_py_1")["products"][0]["id"] == "product_py_1"
        assert (
            client.commerce.ask({"merchant_id": "merchant_py_1", "question": "What is fresh?"})["status"]
            == "answered_from_cache"
        )
        assert client.commerce.protocol_adapters(
            merchant_id="merchant_py_1",
            seller_agent_id="seller_py_1",
        )["adapters"] == ["schema_org"]
        assert (
            client.commerce.protocol_adapter(
                "schema_org",
                merchant_id="merchant_py_1",
                seller_agent_id="seller_py_1",
            )["surface"]
            == "schema_org"
        )
        assert client.commerce.bridge_surfaces()["surfaces"] == ["mcp", "a2a"]
        assert (
            client.commerce.verify_mandate_capability({"merchant_id": "merchant_py_1"})["allowed_to_execute"] is False
        )
        assert client.commerce.prepare_purchase({"merchant_id": "merchant_py_1"})["allowed_to_execute"] is False
        assert client.commerce.offline_pos_readiness()["allowed_to_execute"] is False
        assert (
            client.commerce.create_offline_pos_handoff({"merchant_id": "merchant_py_1"})["handoff_id"] == "handoff_py_1"
        )
        assert client.commerce.confirm_offline_pos_handoff({"handoff_id": "handoff_py_1"})["status"] == "recorded"
        assert (
            client.commerce.simulate_offline_pos_confirmation({"handoff_id": "handoff_py_1"})["status"] == "simulated"
        )

    assert calls
    assert {authorization for _, _, authorization, _, _ in calls} == {"Bearer sdk-runtime-key"}
    upload_call = next(call for call in calls if call[1] == "/api/v1/knowledge/upload")
    assert upload_call[4] == "replace=false&allow_duplicate=false"
    assert b'name="file"; filename="scan.png"' in upload_call[3]


def test_openapi_operation_ids_are_unique_for_generated_sdks() -> None:
    from api.main import app

    operation_ids = [
        operation["operationId"]
        for path in app.openapi()["paths"].values()
        for operation in path.values()
        if isinstance(operation, dict) and "operationId" in operation
    ]
    assert len(operation_ids) == len(set(operation_ids))
