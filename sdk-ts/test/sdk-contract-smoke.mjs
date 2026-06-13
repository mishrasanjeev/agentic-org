import assert from "node:assert/strict";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { AgenticOrg, toAgentRunResult } = require("../dist/index.js");

const calls = [];

globalThis.fetch = async (url, init = {}) => {
  const parsed = new URL(url);
  const body = init.body ? JSON.parse(String(init.body)) : undefined;
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
