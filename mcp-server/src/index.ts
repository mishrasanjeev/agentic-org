#!/usr/bin/env node
/**
 * AgenticOrg MCP Server
 *
 * Repository adapter that exposes configured AgenticOrg API records as MCP
 * tools. Runtime availability depends on authentication, tenant/company
 * context, deployed endpoints, grants, and provider configuration. It can:
 *   - List agent records and submit company-scoped execution requests
 *   - Parse SOPs and submit reviewed shadow candidates
 *   - List agent skills via A2A
 *   - List the native tool catalogue for informational use
 *
 * Connector tools are NOT exposed as direct MCP tools. Agents call
 * connector tools internally via the AgenticOrg runtime - see
 * docs/mcp-product-model.md. Live counts (agents, connectors, tools,
 * version) come from `GET /api/v1/product-facts`.
 *
 * Usage:
 *   AGENTICORG_API_KEY=your-key npx agenticorg-mcp-server
 *
 * Or add to your MCP client config:
 *   {
 *     "mcpServers": {
 *       "agenticorg": {
 *         "command": "npx",
 *         "args": ["agenticorg-mcp-server"],
 *         "env": { "AGENTICORG_API_KEY": "your-key" }
 *       }
 *     }
 *   }
 */

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";

const BASE_URL = (process.env.AGENTICORG_BASE_URL ?? "https://app.agenticorg.ai").replace(/\/$/, "");
const API_KEY = process.env.AGENTICORG_API_KEY ?? "";
const GRANTEX_TOKEN = process.env.AGENTICORG_GRANTEX_TOKEN ?? "";
const COMMERCE_ONLY = /^(1|true|yes)$/i.test(process.env.AGENTICORG_MCP_COMMERCE_ONLY ?? "");

function authHeaders(): Record<string, string> {
  const token = GRANTEX_TOKEN || API_KEY;
  if (!token) {
    throw new Error("Set AGENTICORG_API_KEY or AGENTICORG_GRANTEX_TOKEN env var");
  }
  return {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
  };
}

async function apiGet(path: string): Promise<unknown> {
  const resp = await fetch(`${BASE_URL}${path}`, { headers: authHeaders() });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}: ${await resp.text()}`);
  return resp.json();
}

async function apiPost(path: string, body: unknown): Promise<unknown> {
  const resp = await fetch(`${BASE_URL}${path}`, {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify(body),
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}: ${await resp.text()}`);
  return resp.json();
}

// MCP Server

// Version is read from package.json at runtime so the advertised
// server.version can't drift from the published package identifier.
// The test suite asserts parity on every CI run.
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";

// __dirname is available under CommonJS output (tsconfig module=Node16).
const pkg = JSON.parse(
  readFileSync(resolve(dirname(__filename), "..", "package.json"), "utf-8"),
) as { version: string };

const server = new McpServer({
  name: "agenticorg",
  version: pkg.version,
  description:
    "Repository MCP adapter for company-scoped workflow candidates and conditional API discovery.",
});

// Tool: list_agents

if (!COMMERCE_ONLY) {
server.tool(
  "list_agents",
  "List agent records returned by the configured endpoint. Optionally filter by domain.",
  { domain: z.string().optional().describe("Filter by domain: finance, hr, marketing, ops") },
  async ({ domain }) => {
    const params = domain ? `?domain=${domain}` : "";
    const data = (await apiGet(`/api/v1/agents${params}`)) as any;
    const agents = data.items ?? data;
    const summary = agents.map((a: any) => ({
      name: a.name,
      type: a.agent_type,
      domain: a.domain,
      status: a.status,
      description: a.description,
    }));
    return { content: [{ type: "text" as const, text: JSON.stringify(summary, null, 2) }] };
  },
);

// Tool: run_agent

server.tool(
  "run_agent",
  "Submit a tenant-authenticated, company-scoped request to the configured A2A endpoint.",
  {
    agent_type: z.string().describe("Agent type slug, e.g. 'ap_processor', 'recon_agent', 'support_triage'"),
    company_id: z.string().min(1).describe("Company UUID in the authenticated tenant"),
    action: z.string().optional().default("process").describe("Action to perform (default: 'process')"),
    inputs: z.record(z.string(), z.unknown()).optional().default({}).describe("Input data for the agent (key-value pairs)"),
  },
  async ({ agent_type, company_id, action, inputs }) => {
    const result = await apiPost("/api/v1/a2a/tasks", {
      agent_type,
      company_id,
      action,
      inputs,
      context: { company_id },
    });
    return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
  },
);

// Tool: get_agent_details

server.tool(
  "get_agent_details",
  "Get the agent record returned by the configured endpoint.",
  {
    agent_id: z.string().describe("UUID of the agent"),
  },
  async ({ agent_id }) => {
    const data = await apiGet(`/api/v1/agents/${agent_id}`);
    return { content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }] };
  },
);

// Tool: create_agent_from_sop

server.tool(
  "create_agent_from_sop",
  "Parse SOP text into a draft configuration that requires review.",
  {
    sop_text: z.string().describe("The SOP document text to parse"),
    domain_hint: z.string().optional().default("").describe("Domain hint: finance, hr, marketing, ops"),
  },
  async ({ sop_text, domain_hint }) => {
    const parsed = await apiPost("/api/v1/sop/parse-text", {
      text: sop_text,
      domain_hint,
    });
    return { content: [{ type: "text" as const, text: JSON.stringify(parsed, null, 2) }] };
  },
);

// Tool: deploy_agent

server.tool(
  "deploy_agent",
  "Submit a reviewed configuration as a company-scoped shadow candidate.",
  {
    company_id: z.string().min(1).describe("Company UUID in the authenticated tenant"),
    config: z.record(z.string(), z.unknown()).describe("Agent configuration object (from create_agent_from_sop output)"),
  },
  async ({ company_id, config }) => {
    const result = await apiPost("/api/v1/sop/deploy", {
      config: { ...config, company_id },
    });
    return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
  },
);

// Tool: list_connectors

server.tool(
  "list_connectors",
  "List connector records for discovery. Presence does not establish runtime configuration or execution authority.",
  async () => {
    const data = await apiGet("/api/v1/connectors");
    return { content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }] };
  },
);

// NOTE: `call_connector_tool` was removed in PR-A (Enterprise Readiness P3).
// The backend /api/v1/mcp/call only accepts agent tool names prefixed
// `agenticorg_<agent_type>` - direct connector invocation was never
// supported. See docs/mcp-product-model.md for the product decision.
// External callers should invoke the agent that wraps the connector action
// via `run_agent` instead.

// Tool: list_mcp_tools

server.tool(
  "list_mcp_tools",
  "List agent-tool records returned by the configured MCP endpoint. Results are environment-specific.",
  async () => {
    const data = await apiGet("/api/v1/mcp/tools");
    return { content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }] };
  },
);

// Tool: discover_agents_a2a

server.tool(
  "discover_agents_a2a",
  "Discover available agent skills via A2A (Agent-to-Agent) protocol. Returns agent capabilities, input schemas, and domains.",
  async () => {
    const data = await apiGet("/api/v1/a2a/agents");
    return { content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }] };
  },
);

// Tool: get_agent_card

server.tool(
  "get_agent_card",
  "Get the A2A Agent Card - the public discovery document describing this AgenticOrg instance's capabilities.",
  async () => {
    const data = await apiGet("/api/v1/a2a/agent-card");
    return { content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }] };
  },
);
}

// Start

// C6Z internal seller commerce tools read AgenticOrg cached OACP artifacts only.
// They never create checkout, payment, order, hold, refund, return, shipping, or mandate actions.

const sellerScopeSchema = {
  merchant_id: z.string().describe("Merchant scope id"),
  seller_agent_id: z.string().optional().describe("Seller Commerce Agent scope id"),
};

server.tool(
  "seller.list_products",
  "List cached Seller Commerce Agent product snapshots with source and freshness metadata.",
  sellerScopeSchema,
  async ({ merchant_id, seller_agent_id }) => {
    const params = new URLSearchParams({ merchant_id });
    if (seller_agent_id) params.set("seller_agent_id", seller_agent_id);
    const data = await apiGet(`/api/v1/commerce/runtime/products?${params.toString()}`);
    return { content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }] };
  },
);

server.tool(
  "seller.search_products",
  "Search cached Seller Commerce Agent product snapshots. Results are not transaction authority.",
  {
    ...sellerScopeSchema,
    query: z.string().describe("Product search query"),
  },
  async ({ merchant_id, seller_agent_id, query }) => {
    const params = new URLSearchParams({ merchant_id, q: query });
    if (seller_agent_id) params.set("seller_agent_id", seller_agent_id);
    const data = await apiGet(`/api/v1/commerce/runtime/products?${params.toString()}`);
    return { content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }] };
  },
);

server.tool(
  "seller.get_product_facts",
  "Answer product fact questions from cached Shopify-backed OACP artifacts.",
  {
    ...sellerScopeSchema,
    question: z.string().describe("Product fact question"),
    buyer_agent_id: z.string().optional().describe("Buyer agent scope id"),
  },
  async ({ merchant_id, seller_agent_id, question, buyer_agent_id }) => {
    const data = await apiPost("/api/v1/commerce/runtime/buyer-sessions/ask", {
      merchant_id,
      seller_agent_id,
      buyer_agent_id,
      question,
      action_intent: "non_binding_preview",
      grantex_available: false,
    });
    return { content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }] };
  },
);

server.tool(
  "seller.get_offer_snapshot",
  "Return cached price/offer snapshot labels. It does not create price locks or checkout sessions.",
  {
    ...sellerScopeSchema,
    product_query: z.string().describe("Product or SKU query"),
  },
  async ({ merchant_id, seller_agent_id, product_query }) => {
    const data = await apiPost("/api/v1/commerce/runtime/buyer-sessions/ask", {
      merchant_id,
      seller_agent_id,
      question: `Show price snapshot for ${product_query}`,
      action_intent: "non_binding_preview",
      grantex_available: false,
    });
    return { content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }] };
  },
);

server.tool(
  "seller.get_inventory_snapshot",
  "Return cached inventory snapshot labels. Inventory is a snapshot, not a reservation.",
  {
    ...sellerScopeSchema,
    product_query: z.string().describe("Product or SKU query"),
  },
  async ({ merchant_id, seller_agent_id, product_query }) => {
    const data = await apiPost("/api/v1/commerce/runtime/buyer-sessions/ask", {
      merchant_id,
      seller_agent_id,
      question: `Show inventory snapshot for ${product_query}`,
      action_intent: "non_binding_preview",
      grantex_available: false,
    });
    return { content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }] };
  },
);

server.tool(
  "seller.get_mandate_capability",
  "Read cached mandate/payment capability posture from OACP protocol adapter metadata. It does not create mandates, checkout sessions, orders, or payments.",
  {
    ...sellerScopeSchema,
    buyer_agent_id: z.string().optional().describe("Buyer agent scope id"),
  },
  async ({ merchant_id, seller_agent_id, buyer_agent_id }) => {
    const params = new URLSearchParams({ merchant_id });
    if (seller_agent_id) params.set("seller_agent_id", seller_agent_id);
    if (buyer_agent_id) params.set("buyer_agent_id", buyer_agent_id);
    const data = await apiGet(
      `/api/v1/commerce/runtime/protocol-adapters/ap2_style_mandate_payment_evidence_profile?${params.toString()}`,
    );
    return { content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }] };
  },
);

server.tool(
  "seller.ask_product_question",
  "Ask a buyer-safe product question from cached artifacts. Final commitments are refused.",
  {
    ...sellerScopeSchema,
    question: z.string().describe("Buyer-safe product question"),
    buyer_agent_id: z.string().optional().describe("Buyer agent scope id"),
  },
  async ({ merchant_id, seller_agent_id, question, buyer_agent_id }) => {
    const data = await apiPost("/api/v1/commerce/runtime/buyer-sessions/ask", {
      merchant_id,
      seller_agent_id,
      buyer_agent_id,
      question,
      action_intent: "non_binding_preview",
      grantex_available: false,
    });
    return { content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }] };
  },
);

async function main() {
  const transport = new StdioServerTransport();
  await server.connect(transport);
}

main().catch((err) => {
  console.error("MCP server failed to start:", err);
  process.exit(1);
});
