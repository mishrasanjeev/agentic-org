/**
 * AgenticOrg TypeScript SDK
 *
 * @example
 * ```typescript
 * import { AgenticOrg } from "agenticorg-sdk";
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

export interface GenerateAgentOptions {
  deploy?: boolean;
  companyId?: string;
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

export interface WorkflowCreateOptions {
  name: string;
  definition: Record<string, unknown>;
  version?: string;
  description?: string;
  domain?: string;
  triggerType?: string;
  triggerConfig?: Record<string, unknown>;
  replanOnFailure?: boolean;
  companyId?: string;
}

export interface WorkflowRunOptions {
  payload?: Record<string, unknown>;
}

export interface KnowledgeSearchResult {
  chunk_text: string;
  score: number;
  document_name: string;
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

  async delete(path: string): Promise<unknown> {
    const resp = await fetch(new URL(path, this.baseUrl).toString(), {
      method: "DELETE",
      headers: this.headers,
      signal: AbortSignal.timeout(this.timeout),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}: ${await resp.text()}`);
    return resp.json();
  }

  async upload(path: string, form: FormData, params?: Record<string, string>): Promise<unknown> {
    const headers = { ...this.headers };
    delete headers["Content-Type"];
    const url = new URL(path, this.baseUrl);
    if (params) {
      Object.entries(params).forEach(([key, value]) => url.searchParams.set(key, value));
    }
    const resp = await fetch(url.toString(), {
      method: "POST",
      headers,
      body: form,
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

  async generate(
    description: string,
    options: GenerateAgentOptions = {},
  ): Promise<Record<string, unknown>> {
    const payload: Record<string, unknown> = {
      description,
      deploy: options.deploy ?? false,
    };
    if (options.companyId) payload.company_id = options.companyId;
    return (await this.http.post("/api/v1/agents/generate", payload)) as Record<string, unknown>;
  }
}

class ConnectorsResource {
  constructor(private http: HttpClient) {}

  async list(category?: string): Promise<Record<string, unknown>[]> {
    const params = category ? { category } : undefined;
    const data = (await this.http.get("/api/v1/connectors", params)) as any;
    return data.items ?? data;
  }

  async get(connectorId: string): Promise<Record<string, unknown>> {
    return (await this.http.get(`/api/v1/connectors/${connectorId}`)) as Record<string, unknown>;
  }

  async health(connectorId: string): Promise<Record<string, unknown>> {
    return (await this.http.get(`/api/v1/connectors/${connectorId}/health`)) as Record<string, unknown>;
  }

  async test(connectorId: string): Promise<Record<string, unknown>> {
    return (await this.http.post(`/api/v1/connectors/${connectorId}/test`)) as Record<string, unknown>;
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

class WorkflowsResource {
  constructor(private http: HttpClient) {}

  async templates(domain?: string): Promise<Record<string, unknown>[]> {
    const params = domain ? { domain } : undefined;
    const data = (await this.http.get("/api/v1/workflows/templates", params)) as any;
    return data.items ?? data;
  }

  async list(options: { page?: number; perPage?: number; companyId?: string } = {}): Promise<Record<string, unknown>> {
    const params: Record<string, string> = {
      page: String(options.page ?? 1),
      per_page: String(options.perPage ?? 20),
    };
    if (options.companyId) params.company_id = options.companyId;
    return (await this.http.get("/api/v1/workflows", params)) as Record<string, unknown>;
  }

  async generate(description: string, options: { deploy?: boolean } = {}): Promise<Record<string, unknown>> {
    return (await this.http.post("/api/v1/workflows/generate", {
      description,
      deploy: options.deploy ?? false,
    })) as Record<string, unknown>;
  }

  async create(options: WorkflowCreateOptions): Promise<Record<string, unknown>> {
    const payload: Record<string, unknown> = {
      name: options.name,
      version: options.version ?? "1.0",
      definition: options.definition,
      replan_on_failure: options.replanOnFailure ?? false,
    };
    if (options.description !== undefined) payload.description = options.description;
    if (options.domain !== undefined) payload.domain = options.domain;
    if (options.triggerType !== undefined) payload.trigger_type = options.triggerType;
    if (options.triggerConfig !== undefined) payload.trigger_config = options.triggerConfig;
    if (options.companyId !== undefined) payload.company_id = options.companyId;
    return (await this.http.post("/api/v1/workflows", payload)) as Record<string, unknown>;
  }

  async get(workflowId: string): Promise<Record<string, unknown>> {
    return (await this.http.get(`/api/v1/workflows/${workflowId}`)) as Record<string, unknown>;
  }

  async run(workflowId: string, options: WorkflowRunOptions = {}): Promise<Record<string, unknown>> {
    return (await this.http.post(`/api/v1/workflows/${workflowId}/run`, {
      payload: options.payload ?? {},
    })) as Record<string, unknown>;
  }

  async getRun(runId: string): Promise<Record<string, unknown>> {
    return (await this.http.get(`/api/v1/workflows/runs/${runId}`)) as Record<string, unknown>;
  }

  async cancelRun(runId: string): Promise<Record<string, unknown>> {
    return (await this.http.post(`/api/v1/workflows/runs/${runId}/cancel`)) as Record<string, unknown>;
  }
}

class KnowledgeResource {
  constructor(private http: HttpClient) {}

  async search(query: string, options: { topK?: number } = {}): Promise<KnowledgeSearchResult[]> {
    const data = (await this.http.post("/api/v1/knowledge/search", {
      query,
      top_k: options.topK ?? 5,
    })) as any;
    return data.results ?? data;
  }

  async supportedTypes(): Promise<Record<string, unknown>> {
    return (await this.http.get("/api/v1/knowledge/supported-types")) as Record<string, unknown>;
  }

  async upload(
    file: Blob,
    filename: string,
    options: { duplicatePolicy?: "reject" | "replace" | "allow_duplicate" } = {},
  ): Promise<Record<string, unknown>> {
    const form = new FormData();
    form.append("file", file, filename);
    const policy = options.duplicatePolicy ?? "reject";
    return (await this.http.upload("/api/v1/knowledge/upload", form, {
      replace: String(policy === "replace"),
      allow_duplicate: String(policy === "allow_duplicate"),
    })) as Record<string, unknown>;
  }

  async documents(options: { page?: number; perPage?: number } = {}): Promise<Record<string, unknown>> {
    return (await this.http.get("/api/v1/knowledge/documents", {
      page: String(options.page ?? 1),
      per_page: String(options.perPage ?? 20),
    })) as Record<string, unknown>;
  }

  async delete(documentId: string): Promise<Record<string, unknown>> {
    return (await this.http.delete(`/api/v1/knowledge/documents/${documentId}`)) as Record<string, unknown>;
  }

  async health(): Promise<Record<string, unknown>> {
    return (await this.http.get("/api/v1/knowledge/health")) as Record<string, unknown>;
  }

  async stats(): Promise<Record<string, unknown>> {
    return (await this.http.get("/api/v1/knowledge/stats")) as Record<string, unknown>;
  }
}

class VoiceResource {
  constructor(private http: HttpClient) {}

  async status(agentId?: string): Promise<Record<string, unknown>> {
    return (await this.http.get(
      "/api/v1/voice/status",
      agentId ? { agent_id: agentId } : undefined,
    )) as Record<string, unknown>;
  }

  async saveConfig(config: Record<string, unknown>): Promise<Record<string, unknown>> {
    return (await this.http.post("/api/v1/voice/config", config)) as Record<string, unknown>;
  }

  async testConnection(provider: string, credentials: Record<string, unknown>): Promise<Record<string, unknown>> {
    return (await this.http.post("/api/v1/voice/test-connection", {
      provider,
      credentials,
    })) as Record<string, unknown>;
  }

  async runtimeHealth(agentId: string): Promise<Record<string, unknown>> {
    return (await this.http.get("/api/v1/voice/runtime/health", {
      agent_id: agentId,
    })) as Record<string, unknown>;
  }

  async calls(agentId: string, limit = 25): Promise<Record<string, unknown>[]> {
    return (await this.http.get("/api/v1/voice/calls", {
      agent_id: agentId,
      limit: String(limit),
    })) as Record<string, unknown>[];
  }

  /** Place a real outbound call. This can incur provider charges. */
  async placeOutboundCall(agentId: string, toNumber: string): Promise<Record<string, unknown>> {
    return (await this.http.post("/api/v1/voice/calls/outbound", {
      agent_id: agentId,
      to_number: toNumber,
    })) as Record<string, unknown>;
  }
}

class RPAResource {
  constructor(private http: HttpClient) {}

  async scripts(): Promise<Record<string, unknown>[]> {
    return (await this.http.get("/api/v1/rpa/scripts")) as Record<string, unknown>[];
  }

  async history(limit = 50): Promise<Record<string, unknown>[]> {
    return (await this.http.get("/api/v1/rpa/history", { limit: String(limit) })) as Record<string, unknown>[];
  }

  /** Run an RPA script. The selected script may perform external actions. */
  async run(scriptId: string, params: Record<string, unknown> = {}): Promise<Record<string, unknown>> {
    return (await this.http.post(`/api/v1/rpa/scripts/${scriptId}/run`, { params })) as Record<string, unknown>;
  }
}

class BridgesResource {
  constructor(private http: HttpClient) {}

  async list(): Promise<Record<string, unknown>[]> {
    return (await this.http.get("/api/v1/bridge/list")) as Record<string, unknown>[];
  }

  async register(data: Record<string, unknown>): Promise<Record<string, unknown>> {
    return (await this.http.post("/api/v1/bridge/register", data)) as Record<string, unknown>;
  }

  async status(bridgeId: string): Promise<Record<string, unknown>> {
    return (await this.http.get(`/api/v1/bridge/${bridgeId}/status`)) as Record<string, unknown>;
  }

  async deregister(bridgeId: string): Promise<Record<string, unknown>> {
    return (await this.http.delete(`/api/v1/bridge/${bridgeId}`)) as Record<string, unknown>;
  }

  async route(connectorType: string, payload: Record<string, unknown>): Promise<Record<string, unknown>> {
    return (await this.http.post(`/api/v1/bridge/route/${connectorType}`, payload)) as Record<string, unknown>;
  }
}

class CommerceResource {
  private prefix = "/api/v1/commerce/runtime";

  constructor(private http: HttpClient) {}

  async createOnboardingPacket(data: Record<string, unknown>): Promise<Record<string, unknown>> {
    return (await this.http.post(`${this.prefix}/seller-agents/onboarding-packets`, data)) as Record<string, unknown>;
  }

  async onboardingPacket(packetId: string): Promise<Record<string, unknown>> {
    return (await this.http.get(`${this.prefix}/seller-agents/onboarding-packets/${packetId}`)) as Record<string, unknown>;
  }

  async configureShopify(data: Record<string, unknown>): Promise<Record<string, unknown>> {
    return (await this.http.post(`${this.prefix}/seller-agents/connectors/shopify/credentials`, data)) as Record<string, unknown>;
  }

  async shopifyStatus(merchantId: string): Promise<Record<string, unknown>> {
    return (await this.http.get(`${this.prefix}/seller-agents/connectors/shopify/status`, {
      merchant_id: merchantId,
    })) as Record<string, unknown>;
  }

  async syncShopify(data: Record<string, unknown>): Promise<Record<string, unknown>> {
    return (await this.http.post(`${this.prefix}/seller-agents/shopify/sync`, data)) as Record<string, unknown>;
  }

  async requestGrantexAuthority(data: Record<string, unknown>): Promise<Record<string, unknown>> {
    return (await this.http.post(`${this.prefix}/authority/grantex/request`, data)) as Record<string, unknown>;
  }

  async cacheArtifacts(artifacts: Record<string, unknown>[], buyerAgentId?: string): Promise<Record<string, unknown>> {
    return (await this.http.post(`${this.prefix}/artifacts/cache`, {
      artifacts,
      buyer_agent_id: buyerAgentId,
    })) as Record<string, unknown>;
  }

  async ask(data: Record<string, unknown>): Promise<Record<string, unknown>> {
    return (await this.http.post(`${this.prefix}/buyer-sessions/ask`, data)) as Record<string, unknown>;
  }

  async products(
    merchantId: string,
    options: { sellerAgentId?: string; query?: string } = {},
  ): Promise<Record<string, unknown>> {
    const params: Record<string, string> = { merchant_id: merchantId };
    if (options.sellerAgentId) params.seller_agent_id = options.sellerAgentId;
    if (options.query) params.q = options.query;
    return (await this.http.get(`${this.prefix}/products`, params)) as Record<string, unknown>;
  }

  async protocolAdapters(
    merchantId: string,
    sellerAgentId: string,
    buyerAgentId?: string,
  ): Promise<Record<string, unknown>> {
    const params: Record<string, string> = {
      merchant_id: merchantId,
      seller_agent_id: sellerAgentId,
    };
    if (buyerAgentId) params.buyer_agent_id = buyerAgentId;
    return (await this.http.get(`${this.prefix}/protocol-adapters`, params)) as Record<string, unknown>;
  }

  async protocolAdapter(
    surface: string,
    merchantId: string,
    sellerAgentId: string,
    buyerAgentId?: string,
  ): Promise<Record<string, unknown>> {
    const params: Record<string, string> = {
      merchant_id: merchantId,
      seller_agent_id: sellerAgentId,
    };
    if (buyerAgentId) params.buyer_agent_id = buyerAgentId;
    return (await this.http.get(`${this.prefix}/protocol-adapters/${surface}`, params)) as Record<string, unknown>;
  }

  async bridgeSurfaces(): Promise<Record<string, unknown>> {
    return (await this.http.get(`${this.prefix}/bridges/surfaces`)) as Record<string, unknown>;
  }

  async verifyMandateCapability(data: Record<string, unknown>): Promise<Record<string, unknown>> {
    return (await this.http.post(
      `${this.prefix}/providers/plural-pine/mandate-capability/verify`,
      data,
    )) as Record<string, unknown>;
  }

  /** Prepare a non-executing purchase handoff. */
  async preparePurchase(data: Record<string, unknown>): Promise<Record<string, unknown>> {
    return (await this.http.post(`${this.prefix}/purchase/prepare`, data)) as Record<string, unknown>;
  }

  async offlinePosReadiness(): Promise<Record<string, unknown>> {
    return (await this.http.get(`${this.prefix}/pos/offline/readiness`)) as Record<string, unknown>;
  }

  async createOfflinePosHandoff(data: Record<string, unknown>): Promise<Record<string, unknown>> {
    return (await this.http.post(`${this.prefix}/pos/offline/handoffs`, data)) as Record<string, unknown>;
  }

  async confirmOfflinePosHandoff(data: Record<string, unknown>): Promise<Record<string, unknown>> {
    return (await this.http.post(`${this.prefix}/pos/offline/confirmations`, data)) as Record<string, unknown>;
  }

  async simulateOfflinePosConfirmation(data: Record<string, unknown>): Promise<Record<string, unknown>> {
    return (await this.http.post(`${this.prefix}/pos/offline/simulator/confirm`, data)) as Record<string, unknown>;
  }
}

export class AgenticOrg {
  public agents: AgentsResource;
  public connectors: ConnectorsResource;
  public sop: SOPResource;
  public a2a: A2AResource;
  public mcp: MCPResource;
  public workflows: WorkflowsResource;
  public knowledge: KnowledgeResource;
  public voice: VoiceResource;
  public rpa: RPAResource;
  public bridges: BridgesResource;
  public commerce: CommerceResource;

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
    this.connectors = new ConnectorsResource(http);
    this.sop = new SOPResource(http);
    this.a2a = new A2AResource(http);
    this.mcp = new MCPResource(http);
    this.workflows = new WorkflowsResource(http);
    this.knowledge = new KnowledgeResource(http);
    this.voice = new VoiceResource(http);
    this.rpa = new RPAResource(http);
    this.bridges = new BridgesResource(http);
    this.commerce = new CommerceResource(http);
  }
}
