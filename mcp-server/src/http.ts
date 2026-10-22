#!/usr/bin/env node
// SPDX-License-Identifier: Apache-2.0
/**
 * Hosted MCP endpoint for one public UCP shop, over Streamable HTTP.
 *
 * Built for the synthetic demo shop Nilgiri Tea House so that hosted assistants
 * (Gemini custom apps, ChatGPT connectors) can search it and open a checkout.
 * It exposes only the shop tools in HOSTED_TOOLS: no completion, cancel, cart,
 * order, agent or seller tool, so nothing it does can take a payment.
 *
 * - Stateless: every POST /mcp gets a fresh server and transport.
 * - The shop profile, the buyer platform profile and the platform key are fixed
 *   by the operator; a caller cannot point the server at another shop or
 *   change the UCP-Agent profile.
 * - Host allowlist (DNS-rebinding protection), a 64 KiB body limit, and fixed
 *   per-minute limits per client and per instance.
 *
 *   PORT=8080 AGENTICORG_UCP_PROFILE_URL=https://nilgiritea.agenticorg.ai/.well-known/ucp \
 *   AGENTICORG_UCP_AGENT_PROFILE=https://agenticorg.ai/ucp/platforms/agenticorg-demo-buyer/profile.json \
 *   AGENTICORG_API_KEY=<platform key> MCP_ALLOWED_HOSTS=mcp.agenticorg.ai node dist/http.js
 */

import http from "node:http";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { discoverUcpBinding, registerUcpTools, type UcpBinding } from "./ucp.js";

export const HOSTED_TOOLS: ReadonlySet<string> = new Set([
  "search_catalog",
  "lookup_catalog",
  "create_checkout",
  "get_checkout",
  "update_checkout",
]);

const MAX_BODY_BYTES = 64 * 1024;
const BINDING_TTL_MS = 5 * 60 * 1000;
const WINDOW_MS = 60 * 1000;

export interface HostedConfig {
  profileUrl: string;
  agentProfile: string;
  apiKey: string;
  allowedHosts: ReadonlySet<string>;
  perClientPerMinute: number;
  globalPerMinute: number;
  version: string;
  log?: (entry: Record<string, unknown>) => void;
  now?: () => number;
}

/** Fixed one-minute windows per client and per instance; returns seconds to wait, or 0. */
export function createRateLimiter(perClient: number, global: number, now: () => number = Date.now) {
  let windowStart = now();
  let total = 0;
  const counts = new Map<string, number>();
  return (client: string): number => {
    const t = now();
    if (t - windowStart >= WINDOW_MS) {
      windowStart = t;
      total = 0;
      counts.clear();
    }
    const used = counts.get(client) ?? 0;
    if (used >= perClient || total >= global) return Math.max(1, Math.ceil((windowStart + WINDOW_MS - t) / 1000));
    counts.set(client, used + 1);
    total += 1;
    return 0;
  };
}

function hostName(header: string | undefined): string {
  const host = (header ?? "").trim().toLowerCase();
  if (host.startsWith("[")) return host.slice(0, host.indexOf("]") + 1);
  return host.split(":")[0];
}

function clientOf(req: http.IncomingMessage): string {
  // Cloud Run's front end appends the caller to X-Forwarded-For; the global cap bounds spoofing.
  const forwarded = String(req.headers["x-forwarded-for"] ?? "").split(",")[0].trim();
  return forwarded || req.socket.remoteAddress || "unknown";
}

function send(res: http.ServerResponse, status: number, body: unknown, headers: Record<string, string> = {}) {
  res.writeHead(status, { "Content-Type": "application/json", "Cache-Control": "no-store", ...headers });
  res.end(JSON.stringify(body));
}

function rpcError(res: http.ServerResponse, status: number, code: number, message: string, headers: Record<string, string> = {}) {
  send(res, status, { jsonrpc: "2.0", error: { code, message }, id: null }, headers);
}

async function readBody(req: http.IncomingMessage): Promise<string | null> {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of req) {
    size += (chunk as Buffer).length;
    if (size > MAX_BODY_BYTES) return null;
    chunks.push(chunk as Buffer);
  }
  return Buffer.concat(chunks).toString("utf-8");
}

export function createHostedHandler(config: HostedConfig) {
  const log = config.log ?? ((entry) => console.log(JSON.stringify(entry)));
  const now = config.now ?? Date.now;
  const limit = createRateLimiter(config.perClientPerMinute, config.globalPerMinute, now);
  let cached: { binding: UcpBinding; at: number } | null = null;

  async function binding(): Promise<UcpBinding> {
    if (cached && now() - cached.at < BINDING_TTL_MS) return cached.binding;
    try {
      cached = { binding: await discoverUcpBinding(config.profileUrl), at: now() };
    } catch (exc) {
      if (!cached) throw exc;
      log({ event: "mcp_profile_refresh_failed", error: (exc as Error).message });
    }
    return cached.binding;
  }

  return async (req: http.IncomingMessage, res: http.ServerResponse): Promise<void> => {
    const started = now();
    const path = (req.url ?? "/").split("?")[0];
    if (path === "/healthz" && req.method === "GET") return send(res, 200, { status: "ok" });
    if (!config.allowedHosts.has(hostName(req.headers.host))) {
      return rpcError(res, 403, -32000, "Host not allowed.");
    }
    if (path !== "/mcp") return send(res, 404, { error: "not_found" });
    if (req.method !== "POST") {
      return rpcError(res, 405, -32000, "Method not allowed: this stateless server takes POST /mcp only.", { Allow: "POST" });
    }
    const wait = limit(clientOf(req));
    if (wait > 0) return rpcError(res, 429, -32000, "Too many requests; retry later.", { "Retry-After": String(wait) });
    const raw = await readBody(req);
    if (raw === null) return rpcError(res, 413, -32000, "Request too large.");
    let body: unknown;
    try {
      body = JSON.parse(raw);
    } catch {
      return rpcError(res, 400, -32700, "Parse error.");
    }
    let current: UcpBinding;
    try {
      current = await binding();
    } catch {
      return rpcError(res, 503, -32000, "The shop is unavailable; retry later.");
    }
    const server = new McpServer({
      name: "agenticorg-nilgiri-demo",
      version: config.version,
      description:
        "Nilgiri Tea House, a synthetic demo shop on AgenticOrg: search its catalog and open a checkout. " +
        "It stops before payment; no tool here can pay.",
    });
    await registerUcpTools(server, {
      profileUrl: config.profileUrl,
      agentProfile: config.agentProfile,
      lockAgentProfile: true,
      allowTools: HOSTED_TOOLS,
      binding: current,
      headers: () => ({ "Content-Type": "application/json", Authorization: `Bearer ${config.apiKey}` }),
      log: (message) => log({ event: "mcp_tools", message }),
    });
    const transport = new StreamableHTTPServerTransport({ sessionIdGenerator: undefined, enableJsonResponse: true });
    res.on("close", () => {
      void transport.close();
      void server.close();
      const method = typeof body === "object" && body !== null ? (body as { method?: unknown }).method : undefined;
      log({ event: "mcp_request", method: typeof method === "string" ? method : "batch", status: res.statusCode, ms: now() - started });
    });
    try {
      await server.connect(transport);
      await transport.handleRequest(req, res, body);
    } catch {
      if (!res.headersSent) rpcError(res, 500, -32603, "Internal error.");
    }
  };
}

function required(name: string): string {
  const value = (process.env[name] ?? "").trim();
  if (!value) throw new Error(`${name} is required`);
  return value;
}

export function configFromEnv(): HostedConfig {
  const pkg = JSON.parse(readFileSync(resolve(dirname(__filename), "..", "package.json"), "utf-8")) as { version: string };
  const hosts = required("MCP_ALLOWED_HOSTS")
    .split(",")
    .map((h) => h.trim().toLowerCase())
    .filter(Boolean);
  return {
    profileUrl: required("AGENTICORG_UCP_PROFILE_URL"),
    agentProfile: required("AGENTICORG_UCP_AGENT_PROFILE"),
    apiKey: required("AGENTICORG_API_KEY"),
    allowedHosts: new Set(hosts),
    perClientPerMinute: Number(process.env.MCP_RATE_PER_CLIENT_PER_MINUTE ?? "30"),
    globalPerMinute: Number(process.env.MCP_RATE_GLOBAL_PER_MINUTE ?? "300"),
    version: pkg.version,
  };
}

if (require.main === module) {
  const config = configFromEnv();
  const port = Number(process.env.PORT ?? "8080");
  const handler = createHostedHandler(config);
  http
    .createServer((req, res) => {
      handler(req, res).catch(() => {
        if (!res.headersSent) rpcError(res, 500, -32603, "Internal error.");
      });
    })
    .listen(port, () => console.log(JSON.stringify({ event: "mcp_listening", port, tools: [...HOSTED_TOOLS] })));
}
