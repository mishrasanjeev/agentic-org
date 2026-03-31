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

export interface AgentResult {
  task_id: string;
  agent_id: string;
  status: string;
  output: Record<string, unknown>;
  confidence: number;
  reasoning_trace: string[];
  runtime?: string;
  hitl_trigger?: string;
  error?: string;
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

  async run(agentIdOrType: string, options: RunOptions = {}): Promise<AgentResult> {
    const payload = {
      action: options.action ?? "process",
      inputs: options.inputs ?? {},
      context: options.context ?? {},
    };

    if (agentIdOrType.includes("-") && agentIdOrType.length > 30) {
      return (await this.http.post(`/api/v1/agents/${agentIdOrType}/run`, payload)) as AgentResult;
    }
    return (await this.http.post("/api/v1/a2a/tasks", {
      agent_type: agentIdOrType,
      ...payload,
    })) as AgentResult;
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
