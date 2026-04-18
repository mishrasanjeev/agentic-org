/**
 * AgenticOrg TypeScript SDK
 *
 * @example
 * ```typescript
 * import { AgenticOrg } from "@agenticorg/sdk";
 *
 * const client = new AgenticOrg({ apiKey: "your-key" });
 * const result = await client.agents.run("ap_processor", { inputs: { invoice_id: "INV-001" } });
 * ```
 */

export interface AgenticOrgConfig {
  apiKey?: string;
  grantexToken?: string;
  baseUrl?: string;
  timeout?: number;
}

export interface RunOptions {
  action?: string;
  inputs?: Record<string, unknown>;
  context?: Record<string, unknown>;
}

/**
 * Canonical response shape for every agent-execution endpoint.
 * Mirrors `docs/api/agent-run-contract.md`. Both `/agents/{id}/run`
 * (canonical after PR-A) and `/a2a/tasks` (legacy-wrapped
 * `{id, result:{output,confidence}}`) normalize into this via
 * {@link toAgentRunResult}.
 */
export interface AgentRunResult {
  run_id: string;
  status: string; // completed | failed | hitl_triggered | budget_exceeded
  output: Record<string, unknown>;
  confidence: number;
  reasoning_trace: string[];
  tool_calls: Array<Record<string, unknown>>;
  runtime: string;
  agent_id: string | null;
  agent_type: string | null;
  correlation_id: string | null;
  performance: Record<string, unknown> | null;
  explanation: Record<string, unknown> | null;
  hitl_trigger: string | null;
  error: string | null;
  /** Raw response body, for power users / legacy-field access. */
  raw: Record<string, unknown>;
}

/** @deprecated — use {@link AgentRunResult}. Kept as an alias during the v4.8 → v5.0 window. */
export type AgentResult = AgentRunResult;

/**
 * Normalize any agent-run response body into the canonical
 * {@link AgentRunResult}. Accepts three input shapes:
 *  1. Canonical: top-level `run_id`, `output`, `confidence`.
 *  2. Legacy `/agents/{id}/run`: `task_id` instead of `run_id`.
 *  3. Legacy `/a2a/tasks`: `id` + nested `result: {output, confidence}`.
 */
export function toAgentRunResult(payload: Record<string, unknown>): AgentRunResult {
  const p = payload ?? {};
  const runId = (p.run_id ?? p.task_id ?? p.id ?? "") as string;

  let output: Record<string, unknown>;
  if ("output" in p) {
    output = (p.output as Record<string, unknown>) ?? {};
  } else {
    const nested = (p.result as Record<string, unknown>) ?? {};
    output = (nested.output as Record<string, unknown>) ?? {};
  }

  let confidence: number;
  if ("confidence" in p) {
    confidence = Number(p.confidence ?? 0);
  } else {
    const nested = (p.result as Record<string, unknown>) ?? {};
    confidence = Number(nested.confidence ?? 0);
  }

  return {
    run_id: String(runId),
    status: String(p.status ?? ""),
    output,
    confidence,
    reasoning_trace: Array.isArray(p.reasoning_trace) ? (p.reasoning_trace as string[]) : [],
    tool_calls: Array.isArray(p.tool_calls)
      ? (p.tool_calls as Array<Record<string, unknown>>)
      : [],
    runtime: String(p.runtime ?? ""),
    agent_id: (p.agent_id as string | null | undefined) ?? null,
    agent_type: (p.agent_type as string | null | undefined) ?? null,
    correlation_id: (p.correlation_id as string | null | undefined) ?? null,
    performance: (p.performance as Record<string, unknown> | null | undefined) ?? null,
    explanation: (p.explanation as Record<string, unknown> | null | undefined) ?? null,
    hitl_trigger: (p.hitl_trigger as string | null | undefined) ?? null,
    error: (p.error as string | null | undefined) ?? null,
    raw: p,
  };
}

export interface AgentSkill {
  id: string;
  name: string;
  description: string;
  domain: string;
  tools: string[];
  inputSchema: Record<string, unknown>;
}

export interface MCPTool {
  name: string;
  description: string;
  inputSchema: Record<string, unknown>;
}

export interface MCPCallResult {
  content: Array<{ type: string; text: string }>;
  isError: boolean;
}

export interface SOPParseResult {
  status: string;
  config: Record<string, unknown>;
  document_length?: number;
}

class HttpClient {
  private baseUrl: string;
  private headers: Record<string, string>;
  private timeout: number;

  constructor(baseUrl: string, headers: Record<string, string>, timeout: number) {
    this.baseUrl = baseUrl;
    this.headers = headers;
    this.timeout = timeout;
  }

  async get(path: string, params?: Record<string, string>): Promise<unknown> {
    const url = new URL(path, this.baseUrl);
    if (params) {
      Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, v));
    }
    const resp = await fetch(url.toString(), {
      headers: this.headers,
      signal: AbortSignal.timeout(this.timeout),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}: ${await resp.text()}`);
    return resp.json();
  }

  async post(path: string, body?: unknown): Promise<unknown> {
    const resp = await fetch(new URL(path, this.baseUrl).toString(), {
      method: "POST",
      headers: this.headers,
      body: body ? JSON.stringify(body) : undefined,
      signal: AbortSignal.timeout(this.timeout),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}: ${await resp.text()}`);
    return resp.json();
  }
}

class AgentsResource {
  constructor(private http: HttpClient) {}

  async list(domain?: string): Promise<Record<string, unknown>[]> {
    const params = domain ? { domain } : undefined;
    const data = (await this.http.get("/api/v1/agents", params)) as any;
    return data.items ?? data;
  }

  async get(agentId: string): Promise<Record<string, unknown>> {
    return (await this.http.get(`/api/v1/agents/${agentId}`)) as Record<string, unknown>;
  }

  async run(agentIdOrType: string, options: RunOptions = {}): Promise<AgentRunResult> {
    const payload = {
      action: options.action ?? "process",
      inputs: options.inputs ?? {},
      context: options.context ?? {},
    };

    let raw: Record<string, unknown>;
    if (agentIdOrType.includes("-") && agentIdOrType.length > 30) {
      raw = (await this.http.post(
        `/api/v1/agents/${agentIdOrType}/run`,
        payload,
      )) as Record<string, unknown>;
    } else {
      raw = (await this.http.post("/api/v1/a2a/tasks", {
        agent_type: agentIdOrType,
        ...payload,
      })) as Record<string, unknown>;
    }
    return toAgentRunResult(raw);
  }

  async create(data: Record<string, unknown>): Promise<Record<string, unknown>> {
    return (await this.http.post("/api/v1/agents", data)) as Record<string, unknown>;
  }
}

class SOPResource {
  constructor(private http: HttpClient) {}

  async parseText(text: string, domainHint?: string): Promise<SOPParseResult> {
    return (await this.http.post("/api/v1/sop/parse-text", {
      text,
      domain_hint: domainHint ?? "",
    })) as SOPParseResult;
  }

  async deploy(config: Record<string, unknown>): Promise<Record<string, unknown>> {
    return (await this.http.post("/api/v1/sop/deploy", { config })) as Record<string, unknown>;
  }
}

class A2AResource {
  constructor(private http: HttpClient) {}

  async agentCard(): Promise<Record<string, unknown>> {
    return (await this.http.get("/api/v1/a2a/agent-card")) as Record<string, unknown>;
  }

  async agents(): Promise<AgentSkill[]> {
    const data = (await this.http.get("/api/v1/a2a/agents")) as any;
    return data.agents ?? [];
  }
}

class MCPResource {
  constructor(private http: HttpClient) {}

  async tools(): Promise<MCPTool[]> {
    const data = (await this.http.get("/api/v1/mcp/tools")) as any;
    return data.tools ?? [];
  }

  async call(toolName: string, args?: Record<string, unknown>): Promise<MCPCallResult> {
    return (await this.http.post("/api/v1/mcp/call", {
      name: toolName,
      arguments: args ?? {},
    })) as MCPCallResult;
  }
}

export class AgenticOrg {
  public agents: AgentsResource;
  public sop: SOPResource;
  public a2a: A2AResource;
  public mcp: MCPResource;

  constructor(config: AgenticOrgConfig = {}) {
    const apiKey = config.apiKey ?? process.env.AGENTICORG_API_KEY ?? "";
    const grantexToken = config.grantexToken ?? process.env.AGENTICORG_GRANTEX_TOKEN ?? "";
    const baseUrl = (config.baseUrl ?? process.env.AGENTICORG_BASE_URL ?? "https://app.agenticorg.ai").replace(/\/$/, "");
    const timeout = config.timeout ?? 60000;

    if (!apiKey && !grantexToken) {
      throw new Error(
        "Provide apiKey or grantexToken, or set AGENTICORG_API_KEY / AGENTICORG_GRANTEX_TOKEN env var."
      );
    }

    const token = grantexToken || apiKey;
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    };

    const http = new HttpClient(baseUrl, headers, timeout);

    this.agents = new AgentsResource(http);
    this.sop = new SOPResource(http);
    this.a2a = new A2AResource(http);
    this.mcp = new MCPResource(http);
  }
}
