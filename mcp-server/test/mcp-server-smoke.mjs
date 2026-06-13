import assert from "node:assert/strict";
import http from "node:http";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

const calls = [];

const api = http.createServer((req, res) => {
  let raw = "";
  req.setEncoding("utf8");
  req.on("data", (chunk) => {
    raw += chunk;
  });
  req.on("end", () => {
    const body = raw ? JSON.parse(raw) : {};
    calls.push({
      method: req.method,
      path: req.url?.split("?")[0],
      body,
      authorization: req.headers.authorization,
    });

    const json = (payload, status = 200) => {
      res.writeHead(status, { "content-type": "application/json" });
      res.end(JSON.stringify(payload));
    };

    if (req.method === "GET" && req.url?.startsWith("/api/v1/agents")) {
      return json({
        items: [
          {
            name: "Commerce Sales Agent",
            agent_type: "commerce_sales_agent",
            domain: "commerce",
            status: "shadow",
            description: "Grantex-grounded buyer/seller commerce runtime",
          },
        ],
      });
    }
    if (req.method === "POST" && req.url === "/api/v1/a2a/tasks") {
      return json({
        run_id: "mcp_a2a_1",
        status: "completed",
        agent_type: body.agent_type,
        output: { commerce_response: { status: "preview_only" } },
        confidence: 0.93,
        runtime: "a2a",
        tool_calls: [{ tool: "grantex_commerce:buyer_discovery_preview" }],
      });
    }
    if (req.method === "POST" && req.url === "/api/v1/sop/parse-text") {
      return json({
        status: "draft",
        config: {
          agent_type: "contract_intelligence",
          domain: "ops",
          required_tools: ["search_content_fulltext", "create_page"],
        },
      });
    }
    if (req.method === "GET" && req.url === "/api/v1/connectors") {
      return json({ items: [{ id: "registry-confluence", category: "ops" }] });
    }
    if (req.method === "GET" && req.url === "/api/v1/mcp/tools") {
      return json({ tools: [{ name: "agenticorg_commerce_sales_agent", inputSchema: { type: "object" } }] });
    }
    if (req.method === "GET" && req.url === "/api/v1/a2a/agents") {
      return json({ agents: [{ id: "commerce_sales_agent", tools: ["grantex_commerce:buyer_discovery_preview"] }] });
    }
    if (req.method === "GET" && req.url === "/api/v1/a2a/agent-card") {
      return json({ name: "AgenticOrg Agent Platform", skills: [{ id: "commerce_sales_agent" }] });
    }
    return json({ error: `unhandled ${req.method} ${req.url}` }, 404);
  });
});

await new Promise((resolve) => api.listen(0, "127.0.0.1", resolve));
const address = api.address();
const baseUrl = `http://127.0.0.1:${address.port}`;

const transport = new StdioClientTransport({
  command: process.execPath,
  args: ["dist/index.js"],
  env: {
    ...process.env,
    AGENTICORG_API_KEY: "mcp-test-key",
    AGENTICORG_BASE_URL: baseUrl,
  },
});
const client = new Client({ name: "agenticorg-mcp-smoke", version: "1.0.0" });

try {
  await client.connect(transport);
  const tools = await client.listTools();
  const names = new Set(tools.tools.map((tool) => tool.name));
  for (const expected of [
    "list_agents",
    "run_agent",
    "create_agent_from_sop",
    "list_connectors",
    "list_mcp_tools",
    "discover_agents_a2a",
    "get_agent_card",
  ]) {
    assert.ok(names.has(expected), `missing MCP tool ${expected}`);
  }

  const run = await client.callTool({
    name: "run_agent",
    arguments: {
      agent_type: "commerce_sales_agent",
      action: "discover",
      inputs: { merchant_id: "mch_C6W3" },
    },
  });
  assert.match(run.content[0].text, /commerce_sales_agent|Commerce Sales Agent|completed/);

  const listed = await client.callTool({ name: "list_mcp_tools", arguments: {} });
  assert.match(listed.content[0].text, /agenticorg_commerce_sales_agent/);
} finally {
  await client.close();
  await new Promise((resolve) => api.close(resolve));
}

assert.ok(calls.some((call) => call.path === "/api/v1/a2a/tasks"));
assert.ok(calls.some((call) => call.path === "/api/v1/mcp/tools"));
assert.equal(new Set(calls.map((call) => call.authorization)).size, 1);
assert.equal(calls[0].authorization, "Bearer mcp-test-key");

console.log(`mcp server smoke passed (${calls.length} backend calls)`);
