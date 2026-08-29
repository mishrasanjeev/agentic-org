import assert from "node:assert/strict";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { AgenticOrg, toAgentRunResult } = require("../dist/index.js");

const calls = [];

globalThis.fetch = async (url, init = {}) => {
  const parsed = new URL(url);
  let body;
  if (init.body instanceof FormData) {
    body = Object.fromEntries(init.body.entries());
  } else if (init.body) {
    body = JSON.parse(String(init.body));
  }
  calls.push({
    method: init.method || "GET",
    path: parsed.pathname,
    params: Object.fromEntries(parsed.searchParams.entries()),
    body,
    authorization: init.headers?.Authorization || init.headers?.authorization,
  });

  const json = (payload, status = 200) =>
    new Response(JSON.stringify(payload), {
      status,
      headers: { "content-type": "application/json" },
    });

  if (parsed.pathname === "/api/v1/connectors") {
    return json({ items: [{ id: "registry-confluence", category: "ops" }] });
  }
  if (parsed.pathname === "/api/v1/connectors/connector_ts_1/health") return json({ status: "healthy" });
  if (parsed.pathname === "/api/v1/connectors/connector_ts_1/test") return json({ ok: true });
  if (parsed.pathname === "/api/v1/agents/generate") {
    return json({
      suggestions: [
        {
          agent_type: "contract_intelligence",
          domain: "ops",
          suggested_tools: ["search_content_fulltext", "create_page"],
        },
      ],
      deployed: { agent_id: "agent_shadow_ts", status: "shadow" },
    });
  }
  if (parsed.pathname === "/api/v1/a2a/tasks") {
    return json({
      run_id: "a2a_ts_1",
      status: "completed",
      agent_type: body.agent_type,
      output: { commerce_response: { status: "preview_only" } },
      confidence: 0.92,
      runtime: "a2a",
      tool_calls: [{ tool: "grantex_commerce:buyer_discovery_preview" }],
    });
  }
  if (parsed.pathname === "/api/v1/a2a/agent-card") {
    return json({ name: "AgenticOrg Agent Platform", skills: [{ id: "commerce_sales_agent" }] });
  }
  if (parsed.pathname === "/api/v1/a2a/agents") {
    return json({ agents: [{ id: "commerce_sales_agent" }] });
  }
  if (parsed.pathname === "/api/v1/mcp/tools") {
    return json({ tools: [{ name: "agenticorg_commerce_sales_agent", inputSchema: { type: "object" } }] });
  }
  if (parsed.pathname === "/api/v1/mcp/call") {
    return json({ content: [{ type: "text", text: "Status: completed" }], isError: false });
  }
  if (parsed.pathname === "/api/v1/knowledge/search") {
    return json({
      results: [{ chunk_text: "KB result", score: 0.91, document_name: "contract-kb.md" }],
    });
  }
  if (parsed.pathname === "/api/v1/knowledge/supported-types") {
    return json({ extensions: ["pdf", "png", "docx"], ocr: true });
  }
  if (parsed.pathname === "/api/v1/knowledge/upload") {
    return json({ document_id: "doc_ts_1", filename: body.file.name, status: "processing" }, 201);
  }
  if (parsed.pathname === "/api/v1/knowledge/documents/doc_ts_1" && init.method === "DELETE") {
    return json({ document_id: "doc_ts_1", status: "deleted" });
  }
  if (parsed.pathname === "/api/v1/knowledge/documents") {
    return json({ items: [{ document_id: "doc_ts_1" }], total: 1 });
  }
  if (parsed.pathname === "/api/v1/knowledge/health") return json({ effective_mode: "pgvector" });
  if (parsed.pathname === "/api/v1/knowledge/stats") return json({ total_documents: 1 });
  if (parsed.pathname === "/api/v1/voice/status") return json({ configured: true, runtime_status: "ready" });
  if (parsed.pathname === "/api/v1/voice/config") return json({ runtime_status: "ready", credentials: {} });
  if (parsed.pathname === "/api/v1/voice/test-connection") return json({ ok: true });
  if (parsed.pathname === "/api/v1/voice/runtime/health") return json({ status: "ready" });
  if (parsed.pathname === "/api/v1/voice/calls/outbound") return json({ id: "call_ts_1", status: "queued" });
  if (parsed.pathname === "/api/v1/voice/calls") return json([{ id: "call_ts_1" }]);
  if (parsed.pathname === "/api/v1/rpa/scripts/builtin-gst-portal/run") return json({ id: "rpa_ts_1", status: "completed" });
  if (parsed.pathname === "/api/v1/rpa/scripts") return json([{ id: "builtin-gst-portal" }]);
  if (parsed.pathname === "/api/v1/rpa/history") return json([{ id: "rpa_ts_1" }]);
  if (parsed.pathname === "/api/v1/bridge/register") return json({ bridge_id: "bridge_ts_1", bridge_token: "masked-test" });
  if (parsed.pathname === "/api/v1/bridge/bridge_ts_1/status") return json({ bridge_id: "bridge_ts_1", connected: false });
  if (parsed.pathname === "/api/v1/bridge/bridge_ts_1" && init.method === "DELETE") return json({ bridge_id: "bridge_ts_1", status: "deregistered" });
  if (parsed.pathname === "/api/v1/bridge/route/tally") return json({ status: "completed" });
  if (parsed.pathname === "/api/v1/bridge/list") return json([{ bridge_id: "bridge_ts_1" }]);
  if (parsed.pathname === "/api/v1/commerce/runtime/seller-agents/onboarding-packets") return json({ packet_id: "packet_ts_1" });
  if (parsed.pathname === "/api/v1/commerce/runtime/seller-agents/onboarding-packets/packet_ts_1") return json({ packet_id: "packet_ts_1" });
  if (parsed.pathname === "/api/v1/commerce/runtime/seller-agents/connectors/shopify/credentials") return json({ status: "configured" });
  if (parsed.pathname === "/api/v1/commerce/runtime/seller-agents/connectors/shopify/status") return json({ status: "configured" });
  if (parsed.pathname === "/api/v1/commerce/runtime/seller-agents/shopify/sync") return json({ status: "read_only_sync_queued" });
  if (parsed.pathname === "/api/v1/commerce/runtime/authority/grantex/request") return json({ status: "prepared" });
  if (parsed.pathname === "/api/v1/commerce/runtime/artifacts/cache") return json({ cached: 1 });
  if (parsed.pathname === "/api/v1/commerce/runtime/buyer-sessions/ask") return json({ status: "answered_from_cache" });
  if (parsed.pathname === "/api/v1/commerce/runtime/products") return json({ products: [{ id: "product_ts_1" }] });
  if (parsed.pathname === "/api/v1/commerce/runtime/protocol-adapters") return json({ adapters: ["schema_org"] });
  if (parsed.pathname === "/api/v1/commerce/runtime/protocol-adapters/schema_org") return json({ surface: "schema_org" });
  if (parsed.pathname === "/api/v1/commerce/runtime/bridges/surfaces") return json({ surfaces: ["mcp", "a2a"] });
  if (parsed.pathname === "/api/v1/commerce/runtime/providers/plural-pine/mandate-capability/verify") return json({ capability_verified: true, allowed_to_execute: false });
  if (parsed.pathname === "/api/v1/commerce/runtime/purchase/prepare") return json({ status: "prepared", allowed_to_execute: false });
  if (parsed.pathname === "/api/v1/commerce/runtime/pos/offline/readiness") return json({ status: "ready", allowed_to_execute: false });
  if (parsed.pathname === "/api/v1/commerce/runtime/pos/offline/handoffs") return json({ handoff_id: "handoff_ts_1" });
  if (parsed.pathname === "/api/v1/commerce/runtime/pos/offline/confirmations") return json({ status: "recorded" });
  if (parsed.pathname === "/api/v1/commerce/runtime/pos/offline/simulator/confirm") return json({ status: "simulated" });
  if (parsed.pathname === "/api/v1/workflows/templates") {
    return json({ items: [{ id: "tpl-contract-renewal", domain: "ops" }] });
  }
  if (parsed.pathname === "/api/v1/workflows/generate") {
    return json({
      workflow: {
        name: "Contract Workflow",
        steps: [{ id: "search_kb", type: "agent", agent_type: "contract_intelligence" }],
      },
      deployed: false,
      workflow_id: null,
    });
  }
  if (parsed.pathname === "/api/v1/workflows") {
    return json({ workflow_id: "wf_ts_1", name: body.name, version: body.version });
  }
  if (parsed.pathname === "/api/v1/workflows/wf_ts_1/run") {
    return json({ run_id: "run_ts_1", status: "running" });
  }
  if (parsed.pathname === "/api/v1/workflows/runs/run_ts_1") {
    return json({ run_id: "run_ts_1", status: "completed", steps: [{ step_id: "search_kb" }] });
  }
  if (parsed.pathname === "/api/v1/workflows/runs/run_ts_1/cancel") {
    return json({ run_id: "run_ts_1", status: "cancel_requested" });
  }
  return json({ error: `unhandled ${parsed.pathname}` }, 404);
};

const legacy = toAgentRunResult({
  id: "legacy_a2a",
  status: "completed",
  result: { output: { ok: true }, confidence: 0.8 },
});
assert.equal(legacy.run_id, "legacy_a2a");
assert.deepEqual(legacy.output, { ok: true });
assert.equal(legacy.confidence, 0.8);

const client = new AgenticOrg({ apiKey: "sdk-ts-test-key", baseUrl: "https://agenticorg.test" });

assert.equal((await client.connectors.list("ops"))[0].id, "registry-confluence");
assert.equal((await client.connectors.health("connector_ts_1")).status, "healthy");
assert.equal((await client.connectors.test("connector_ts_1")).ok, true);
assert.equal((await client.a2a.agentCard()).skills[0].id, "commerce_sales_agent");
assert.equal((await client.a2a.agents())[0].id, "commerce_sales_agent");
assert.equal((await client.mcp.tools())[0].name, "agenticorg_commerce_sales_agent");

const generatedAgent = await client.agents.generate("Create a contract intelligence agent.", { deploy: true });
assert.equal(generatedAgent.deployed.status, "shadow");

const commerceRun = await client.agents.run("commerce_sales_agent", {
  action: "discover",
  inputs: { merchant_id: "mch_C6W3" },
});
assert.equal(commerceRun.status, "completed");
assert.equal(commerceRun.agent_type, "commerce_sales_agent");
assert.equal(commerceRun.output.commerce_response.status, "preview_only");
assert.equal(commerceRun.tool_calls[0].tool, "grantex_commerce:buyer_discovery_preview");

assert.equal((await client.mcp.call("agenticorg_commerce_sales_agent", { inputs: {} })).isError, false);
assert.equal((await client.knowledge.search("renewal policy", { topK: 1 }))[0].document_name, "contract-kb.md");
assert.equal((await client.knowledge.supportedTypes()).ocr, true);
const uploaded = await client.knowledge.upload(new Blob(["scanned invoice"]), "invoice.png");
assert.equal(uploaded.document_id, "doc_ts_1");
const uploadCall = calls.find((call) => call.path === "/api/v1/knowledge/upload");
assert.deepEqual(uploadCall.params, { replace: "false", allow_duplicate: "false" });
assert.equal((await client.knowledge.documents()).total, 1);
assert.equal((await client.knowledge.health()).effective_mode, "pgvector");
assert.equal((await client.knowledge.stats()).total_documents, 1);
assert.equal((await client.knowledge.delete("doc_ts_1")).status, "deleted");
assert.equal((await client.voice.status()).runtime_status, "ready");
assert.equal((await client.voice.saveConfig({ provider: "mock" })).runtime_status, "ready");
assert.equal((await client.voice.testConnection("mock", { token: "synthetic" })).ok, true);
assert.equal((await client.voice.runtimeHealth("agent_ts_1")).status, "ready");
assert.equal((await client.voice.calls("agent_ts_1"))[0].id, "call_ts_1");
assert.equal((await client.voice.placeOutboundCall("agent_ts_1", "+919999999999")).status, "queued");
assert.equal((await client.rpa.scripts())[0].id, "builtin-gst-portal");
assert.equal((await client.rpa.history())[0].id, "rpa_ts_1");
assert.equal((await client.rpa.run("builtin-gst-portal")).status, "completed");
const bridge = await client.bridges.register({ tenant_id: "tenant_ts_1", connector_type: "tally" });
assert.equal((await client.bridges.status(bridge.bridge_id)).connected, false);
assert.equal((await client.bridges.list())[0].bridge_id, "bridge_ts_1");
assert.equal((await client.bridges.route("tally", { bridge_id: bridge.bridge_id })).status, "completed");
assert.equal((await client.bridges.deregister(bridge.bridge_id)).status, "deregistered");
const packet = await client.commerce.createOnboardingPacket({ merchant_id: "merchant_ts_1" });
assert.equal((await client.commerce.onboardingPacket(packet.packet_id)).packet_id, "packet_ts_1");
assert.equal((await client.commerce.configureShopify({ merchant_id: "merchant_ts_1" })).status, "configured");
assert.equal((await client.commerce.shopifyStatus("merchant_ts_1")).status, "configured");
assert.equal((await client.commerce.syncShopify({ packet_id: "packet_ts_1" })).status, "read_only_sync_queued");
assert.equal((await client.commerce.requestGrantexAuthority({ packet_id: "packet_ts_1" })).status, "prepared");
assert.equal((await client.commerce.cacheArtifacts([{ artifact_id: "artifact_ts_1" }])).cached, 1);
assert.equal((await client.commerce.products("merchant_ts_1")).products[0].id, "product_ts_1");
assert.equal((await client.commerce.ask({ merchant_id: "merchant_ts_1", question: "What is fresh?" })).status, "answered_from_cache");
assert.equal(
  (await client.commerce.protocolAdapters("merchant_ts_1", "seller_ts_1")).adapters[0],
  "schema_org",
);
assert.equal(
  (await client.commerce.protocolAdapter("schema_org", "merchant_ts_1", "seller_ts_1")).surface,
  "schema_org",
);
assert.equal((await client.commerce.bridgeSurfaces()).surfaces[0], "mcp");
assert.equal((await client.commerce.verifyMandateCapability({ merchant_id: "merchant_ts_1" })).allowed_to_execute, false);
assert.equal((await client.commerce.preparePurchase({ merchant_id: "merchant_ts_1" })).allowed_to_execute, false);
assert.equal((await client.commerce.offlinePosReadiness()).allowed_to_execute, false);
assert.equal((await client.commerce.createOfflinePosHandoff({ merchant_id: "merchant_ts_1" })).handoff_id, "handoff_ts_1");
assert.equal((await client.commerce.confirmOfflinePosHandoff({ handoff_id: "handoff_ts_1" })).status, "recorded");
assert.equal((await client.commerce.simulateOfflinePosConfirmation({ handoff_id: "handoff_ts_1" })).status, "simulated");
assert.equal((await client.workflows.templates("ops"))[0].id, "tpl-contract-renewal");
assert.equal(
  (await client.workflows.generate("Search the KB and open a Jira issue.")).workflow.steps[0].agent_type,
  "contract_intelligence",
);

const workflow = await client.workflows.create({
  name: "Contract Workflow",
  domain: "ops",
  triggerType: "manual",
  definition: {
    steps: [
      {
        id: "search_kb",
        type: "agent",
        agent_type: "contract_intelligence",
        authorized_tools: ["search_content_fulltext"],
        knowledge_sources: ["kb_contracts"],
      },
    ],
  },
});
const run = await client.workflows.run(workflow.workflow_id, { payload: { contract_id: "CTR-1" } });
assert.equal((await client.workflows.getRun(run.run_id)).status, "completed");
assert.equal((await client.workflows.cancelRun(run.run_id)).status, "cancel_requested");

assert.equal(new Set(calls.map((call) => call.authorization)).size, 1);
assert.equal(calls[0].authorization, "Bearer sdk-ts-test-key");
for (const expected of [
  "/api/v1/connectors",
  "/api/v1/agents/generate",
  "/api/v1/a2a/tasks",
  "/api/v1/mcp/call",
  "/api/v1/knowledge/search",
  "/api/v1/workflows/templates",
  "/api/v1/workflows/generate",
  "/api/v1/workflows",
]) {
  assert.ok(calls.some((call) => call.path === expected), `missing call to ${expected}`);
}

console.log(`sdk contract smoke passed (${calls.length} calls)`);
