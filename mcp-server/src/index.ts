#!/usr/bin/env node
/**
 * AgenticOrg MCP Server
 *
 * Exposes AgenticOrg AI agents and tools via the Model Context Protocol (MCP).
 * Any MCP-compatible client (ChatGPT, Claude Desktop, Cursor, etc.) can:
 *   - Discover and run AI agents (AP Processor, Recon, Payroll, etc.)
 *   - Parse SOPs and deploy new agents
 *   - List available agent skills via A2A
 *   - Call any of the 269 connector tools directly
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

// ── MCP Server ──────────────────────────────────────────────────────────

const server = new McpServer({
  name: "agenticorg",
  version: "0.1.0",
  description: "AgenticOrg — run enterprise AI agents, 25+ agents, 42 connectors, 269 tools",
});

// ── Tool: list_agents ───────────────────────────────────────────────────

server.tool(
  "list_agents",
  "List all available AI agents. Optionally filter by domain (finance, hr, marketing, ops).",
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

// ── Tool: run_agent ─────────────────────────────────────────────────────

server.tool(
  "run_agent",
  "Run an AI agent by type (e.g. 'ap_processor', 'recon_agent', 'payroll_agent'). Returns the agent's output including confidence score and reasoning trace.",
  {
    agent_type: z.string().describe("Agent type slug, e.g. 'ap_processor', 'recon_agent', 'support_triage'"),
    action: z.string().optional().default("process").describe("Action to perform (default: 'process')"),
    inputs: z.record(z.string(), z.unknown()).optional().default({}).describe("Input data for the agent (key-value pairs)"),
  },
  async ({ agent_type, action, inputs }) => {
    const result = await apiPost("/api/v1/a2a/tasks", {
      agent_type,
      action,
      inputs,
      context: {},
    });
    return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
  },
);

// ── Tool: get_agent_details ─────────────────────────────────────────────

server.tool(
  "get_agent_details",
  "Get full details of a specific agent by ID, including config, tools, confidence thresholds.",
  {
    agent_id: z.string().describe("UUID of the agent"),
  },
  async ({ agent_id }) => {
    const data = await apiGet(`/api/v1/agents/${agent_id}`);
    return { content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }] };
  },
);

// ── Tool: create_agent_from_sop ─────────────────────────────────────────

server.tool(
  "create_agent_from_sop",
  "Parse a Standard Operating Procedure (SOP) text and create a new AI agent from it. The SOP is analyzed to extract steps, tools, and decision logic.",
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

// ── Tool: deploy_agent ──────────────────────────────────────────────────

server.tool(
  "deploy_agent",
  "Deploy an agent configuration (from SOP parsing or manual config) to make it live.",
  {
    config: z.record(z.string(), z.unknown()).describe("Agent configuration object (from create_agent_from_sop output)"),
  },
  async ({ config }) => {
    const result = await apiPost("/api/v1/sop/deploy", { config });
    return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
  },
);

// ── Tool: list_connectors ───────────────────────────────────────────────

server.tool(
  "list_connectors",
  "List all 42 available connectors (SAP, Oracle, Jira, HubSpot, GSTN, Darwinbox, etc.) and their status.",
  async () => {
    const data = await apiGet("/api/v1/connectors");
    return { content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }] };
  },
);

// ── Tool: call_connector_tool ───────────────────────────────────────────

server.tool(
  "call_connector_tool",
  "Call a specific connector tool (e.g. 'jira_create_issue', 'hubspot_get_contacts', 'slack_send_message'). Use list_mcp_tools to discover available tools.",
  {
    tool_name: z.string().describe("Tool name, e.g. 'jira_create_issue', 'slack_send_message'"),
    arguments: z.record(z.string(), z.unknown()).optional().default({}).describe("Tool arguments"),
  },
  async ({ tool_name, arguments: args }) => {
    const result = await apiPost("/api/v1/mcp/call", {
      name: tool_name,
      arguments: args,
    });
    return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
  },
);

// ── Tool: list_mcp_tools ────────────────────────────────────────────────

server.tool(
  "list_mcp_tools",
  "List all 269 available MCP tools across 42 connectors. Each tool has a name, description, and input schema.",
  async () => {
    const data = await apiGet("/api/v1/mcp/tools");
    return { content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }] };
  },
);

// ── Tool: discover_agents_a2a ───────────────────────────────────────────

server.tool(
  "discover_agents_a2a",
  "Discover available agent skills via A2A (Agent-to-Agent) protocol. Returns agent capabilities, input schemas, and domains.",
  async () => {
    const data = await apiGet("/api/v1/a2a/agents");
    return { content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }] };
  },
);

// ── Tool: get_agent_card ────────────────────────────────────────────────

server.tool(
  "get_agent_card",
  "Get the A2A Agent Card — the public discovery document describing this AgenticOrg instance's capabilities.",
  async () => {
    const data = await apiGet("/api/v1/a2a/agent-card");
    return { content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }] };
  },
);

// ── Start ───────────────────────────────────────────────────────────────

async function main() {
  const transport = new StdioServerTransport();
  await server.connect(transport);
}

main().catch((err) => {
  console.error("MCP server failed to start:", err);
  process.exit(1);
});
