// SPDX-License-Identifier: Apache-2.0
/**
 * UCP checkout, cart and order tools (UCP 2026-04-08 MCP binding,
 * services/shopping/mcp.openrpc.json).
 *
 * The tools are a stdio adapter over the merchant's UCP REST binding. They
 * are registered only when the configured merchant profile
 * (`/.well-known/ucp`) advertises the capability each tool needs; the API
 * lists checkout, cart and order there only while
 * AGENTICORG_UCP_CHECKOUT_ENABLED is on, and its `/ucp` routes answer 404
 * otherwise. Tool names follow the binding (`create_checkout`, ...), not a
 * `checkout.*` prefix.
 *
 * `complete_checkout` never sends a Complete that could execute without a
 * sealed allow decision: a call must either ask for a decision only
 * (`decide_only: true`, nothing executes) or present the `decision_id` that
 * call returned. The API consumes the decision once, bound to the session
 * payload hash, through its executor path.
 */

import { randomUUID } from "node:crypto";
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";

export const UCP_VERSION = "2026-04-08";
export const VENDOR_MEMBER = "ai.agenticorg.commerce";

const SHOPPING_SERVICE = "dev.ucp.shopping";
const CHECKOUT = "dev.ucp.shopping.checkout";
const CART = "dev.ucp.shopping.cart";
const ORDER = "dev.ucp.shopping.order";

const PROFILE_TIMEOUT_MS = 5_000;
const CALL_TIMEOUT_MS = 30_000;
const MAX_ERROR_CONTENT = 500;
const DECISION_FIELDS = ["decision_id", "action", "outcome", "failing_rule", "payload_hash", "expires_at"];

export interface UcpBinding {
  /** The merchant's UCP REST base, same origin as its profile, no trailing slash. */
  endpoint: string;
  capabilities: Set<string>;
}

export interface UcpToolOptions {
  /** The merchant business profile URL (`https://<serving host>/.well-known/ucp`). */
  profileUrl: string;
  /** Default platform profile for `meta["ucp-agent"].profile` when a call omits it. */
  agentProfile?: string;
  headers: () => Record<string, string>;
  log?: (message: string) => void;
}

type Json = Record<string, unknown>;

function isObject(value: unknown): value is Json {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isLoopback(hostname: string): boolean {
  return hostname === "localhost" || hostname === "127.0.0.1" || hostname === "[::1]";
}

/** https only, except a loopback host (local development and the test stub). */
function allowedUrl(raw: string): URL | null {
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    return null;
  }
  if (url.username || url.password) return null;
  if (url.protocol === "https:") return url;
  if (url.protocol === "http:" && isLoopback(url.hostname)) return url;
  return null;
}

async function fetchProfile(url: URL): Promise<Json> {
  const resp = await fetch(url, {
    headers: { Accept: "application/json" },
    redirect: "error",
    signal: AbortSignal.timeout(PROFILE_TIMEOUT_MS),
  });
  if (!resp.ok) throw new Error(`profile answered HTTP ${resp.status}`);
  const body = (await resp.json()) as unknown;
  if (!isObject(body) || !isObject(body.ucp)) throw new Error("not a UCP profile");
  return body;
}

/**
 * Read the merchant profile and return the 2026-04-08 REST binding, or null
 * when the profile cannot be read or does not serve checkout, cart or order.
 * With both releases on, the root is 2026-08-25 and 2026-04-08 is the
 * `supported_versions` leaf, which is followed on the same origin.
 */
export async function discoverUcpBinding(profileUrl: string): Promise<UcpBinding> {
  const root = allowedUrl(profileUrl);
  if (!root || !root.pathname.endsWith("/.well-known/ucp")) {
    throw new Error("AGENTICORG_UCP_PROFILE_URL must be an https URL ending in /.well-known/ucp");
  }
  let profile = await fetchProfile(root);
  let ucp = profile.ucp as Json;
  if (ucp.version !== UCP_VERSION) {
    const leaf = isObject(ucp.supported_versions) ? ucp.supported_versions[UCP_VERSION] : undefined;
    const leafUrl = typeof leaf === "string" ? allowedUrl(leaf) : null;
    if (!leafUrl || leafUrl.origin !== root.origin) {
      throw new Error(`the profile does not serve UCP ${UCP_VERSION}`);
    }
    profile = await fetchProfile(leafUrl);
    ucp = profile.ucp as Json;
    if (ucp.version !== UCP_VERSION) throw new Error(`the profile does not serve UCP ${UCP_VERSION}`);
  }
  const capabilities = new Set<string>();
  if (isObject(ucp.capabilities)) {
    for (const [name, entries] of Object.entries(ucp.capabilities)) {
      if (Array.isArray(entries) && entries.some((e) => isObject(e) && e.version === UCP_VERSION)) {
        capabilities.add(name);
      }
    }
  }
  const services = isObject(ucp.services) ? ucp.services[SHOPPING_SERVICE] : undefined;
  const rest = Array.isArray(services)
    ? services.find((s) => isObject(s) && s.transport === "rest" && typeof s.endpoint === "string")
    : undefined;
  const endpoint = isObject(rest) ? allowedUrl(String(rest.endpoint)) : null;
  if (!endpoint || endpoint.origin !== root.origin) {
    // The API key is only ever sent to the origin the operator configured.
    throw new Error("the profile advertises no same-origin dev.ucp.shopping REST endpoint");
  }
  return { endpoint: endpoint.href.replace(/\/+$/, ""), capabilities };
}

// --- results ---------------------------------------------------------------------------------

const FALLBACK: Record<number, [string, string]> = {
  400: ["invalid_request", "The merchant refused the request as malformed."],
  401: ["unauthorized", "The credential was not accepted."],
  403: ["forbidden", "The credential lacks the UCP scope for this operation."],
  404: ["not_found", "Not found, or UCP checkout is not enabled for this merchant."],
  409: ["conflict", "The resource is in a state that does not allow this operation."],
  422: ["unprocessable", "The merchant could not process the request."],
  424: ["profile_unreachable", "The platform profile could not be fetched."],
  429: ["rate_limited", "Too many requests; retry later."],
  503: ["unavailable", "The merchant is temporarily unavailable; retry later."],
};

export interface UcpError {
  status: number;
  code: string;
  content: string;
}

/**
 * The UCP error code and text only: `{code, content}` (discovery errors) or the
 * first error message of an `error_response`. Anything else in the body (a
 * framework `detail`, internal refusal details) is dropped.
 */
export function ucpError(status: number, body: unknown): UcpError {
  const [fallbackCode, fallbackContent] = FALLBACK[status] ?? ["ucp_error", "The merchant returned an error."];
  let code: unknown;
  let content: unknown;
  if (isObject(body)) {
    if (typeof body.code === "string") {
      code = body.code;
      content = body.content;
    } else if (Array.isArray(body.messages)) {
      const first = body.messages.find((m) => isObject(m) && m.type === "error");
      if (isObject(first)) {
        code = first.code;
        content = first.content;
      }
    }
  }
  if (typeof code !== "string" || !/^[a-z0-9_.]{1,64}$/.test(code)) {
    return { status, code: fallbackCode, content: fallbackContent };
  }
  const text = typeof content === "string" && content.trim() ? content.slice(0, MAX_ERROR_CONTENT) : fallbackContent;
  return { status, code, content: text };
}

function errorResult(error: UcpError) {
  return {
    isError: true,
    content: [{ type: "text" as const, text: JSON.stringify({ error }) }],
    structuredContent: { error } as Json,
  };
}

/** The vendor member keeps only the decision's identity and outcome. */
function publicBody(body: Json): Json {
  const vendor = body[VENDOR_MEMBER];
  if (!isObject(vendor)) return body;
  const out: Json = { ...body };
  const decision = vendor.decision;
  if (isObject(decision)) {
    const kept: Json = {};
    for (const key of DECISION_FIELDS) if (key in decision) kept[key] = decision[key];
    out[VENDOR_MEMBER] = { decision: kept };
  } else {
    delete out[VENDOR_MEMBER];
  }
  return out;
}

function okResult(body: Json) {
  const shown = publicBody(body);
  return {
    content: [{ type: "text" as const, text: JSON.stringify(shown, null, 2) }],
    structuredContent: shown,
  };
}

// --- tools -----------------------------------------------------------------------------------

const agentSchema = z
  .object({
    profile: z
      .string()
      .url()
      .refine((v) => !/["\\]/.test(v), "profile must not contain quotes or backslashes")
      .describe("URL of the platform's UCP profile document"),
  })
  .describe("Platform agent identification (maps to the UCP-Agent header)");

const metaSchema = z
  .object({
    "ucp-agent": agentSchema.optional(),
    "idempotency-key": z.string().uuid().optional().describe("Retry key (maps to Idempotency-Key)"),
  })
  .passthrough()
  .describe("UCP request metadata; ucp-agent defaults to AGENTICORG_UCP_AGENT_PROFILE");

const metaWithKeySchema = z
  .object({
    "ucp-agent": agentSchema.optional(),
    "idempotency-key": z.string().uuid().describe("Retry key (maps to Idempotency-Key); required"),
  })
  .passthrough()
  .describe("UCP request metadata with a required idempotency-key");

const idSchema = z
  .string()
  .min(1)
  .max(256)
  .refine((v) => v !== "." && v !== "..", "id must not be a dot segment");
const objectSchema = z.record(z.string(), z.unknown());

type Meta = z.infer<typeof metaSchema>;

interface Operation {
  name: string;
  capability: string;
  title: string;
  description: string;
  method: "GET" | "POST" | "PUT";
  path: (id?: string) => string;
  shape: Record<string, z.ZodTypeAny>;
  body?: string;
  annotations: { readOnlyHint: boolean; destructiveHint: boolean; idempotentHint: boolean };
}

const READ = { readOnlyHint: true, destructiveHint: false, idempotentHint: true };
const CREATE = { readOnlyHint: false, destructiveHint: false, idempotentHint: false };
const REPLACE = { readOnlyHint: false, destructiveHint: true, idempotentHint: true };
const CANCEL = { readOnlyHint: false, destructiveHint: true, idempotentHint: true };
const COMPLETE = { readOnlyHint: false, destructiveHint: true, idempotentHint: false };

const seg = (id?: string) => encodeURIComponent(id ?? "");

export const OPERATIONS: Operation[] = [
  {
    name: "create_checkout",
    capability: CHECKOUT,
    title: "Create UCP checkout",
    description: "Create a UCP checkout session at the merchant. Nothing is charged.",
    method: "POST",
    path: () => "/checkout-sessions",
    shape: { meta: metaSchema.optional(), checkout: objectSchema.describe("UCP checkout create request") },
    body: "checkout",
    annotations: CREATE,
  },
  {
    name: "get_checkout",
    capability: CHECKOUT,
    title: "Get UCP checkout",
    description: "Read a UCP checkout session.",
    method: "GET",
    path: (id) => `/checkout-sessions/${seg(id)}`,
    shape: { meta: metaSchema.optional(), id: idSchema },
    annotations: READ,
  },
  {
    name: "update_checkout",
    capability: CHECKOUT,
    title: "Update UCP checkout",
    description: "Replace a UCP checkout session's contents (full replacement). Nothing is charged.",
    method: "PUT",
    path: (id) => `/checkout-sessions/${seg(id)}`,
    shape: { meta: metaSchema.optional(), id: idSchema, checkout: objectSchema.describe("UCP checkout update request") },
    body: "checkout",
    annotations: REPLACE,
  },
  {
    name: "complete_checkout",
    capability: CHECKOUT,
    title: "Complete UCP checkout",
    description:
      "Two steps. First call with decide_only=true: the merchant's commerce arbiter seals a decision for " +
      "the exact session and nothing executes. Then call with that decision_id to place the order; the " +
      "decision is single use and bound to the session payload. A call with neither is refused here.",
    method: "POST",
    path: (id) => `/checkout-sessions/${seg(id)}/complete`,
    shape: {
      meta: metaWithKeySchema,
      id: idSchema,
      checkout: objectSchema.describe("UCP checkout complete request (payment, ap2, signals)"),
      decide_only: z.boolean().optional().describe("Ask for a decision only; nothing executes"),
      decision_id: z.string().min(1).max(128).optional().describe("The allow decision to consume"),
    },
    body: "checkout",
    annotations: COMPLETE,
  },
  {
    name: "cancel_checkout",
    capability: CHECKOUT,
    title: "Cancel UCP checkout",
    description: "Cancel a UCP checkout session. A canceled session is terminal.",
    method: "POST",
    path: (id) => `/checkout-sessions/${seg(id)}/cancel`,
    shape: { meta: metaWithKeySchema, id: idSchema },
    annotations: CANCEL,
  },
  {
    name: "create_cart",
    capability: CART,
    title: "Create UCP cart",
    description: "Create a UCP cart at the merchant.",
    method: "POST",
    path: () => "/carts",
    shape: { meta: metaSchema.optional(), cart: objectSchema.describe("UCP cart create request") },
    body: "cart",
    annotations: CREATE,
  },
  {
    name: "get_cart",
    capability: CART,
    title: "Get UCP cart",
    description: "Read a UCP cart.",
    method: "GET",
    path: (id) => `/carts/${seg(id)}`,
    shape: { meta: metaSchema.optional(), id: idSchema },
    annotations: READ,
  },
  {
    name: "update_cart",
    capability: CART,
    title: "Update UCP cart",
    description: "Replace a UCP cart's contents (full replacement).",
    method: "PUT",
    path: (id) => `/carts/${seg(id)}`,
    shape: { meta: metaSchema.optional(), id: idSchema, cart: objectSchema.describe("UCP cart update request") },
    body: "cart",
    annotations: REPLACE,
  },
  {
    name: "cancel_cart",
    capability: CART,
    title: "Cancel UCP cart",
    description: "Cancel a UCP cart. A canceled cart is terminal.",
    method: "POST",
    path: (id) => `/carts/${seg(id)}/cancel`,
    shape: { meta: metaWithKeySchema, id: idSchema },
    annotations: CANCEL,
  },
  {
    name: "get_order",
    capability: ORDER,
    title: "Get UCP order",
    description: "Read a UCP order created by a completed checkout.",
    method: "GET",
    path: (id) => `/orders/${seg(id)}`,
    shape: { meta: metaSchema.optional(), id: idSchema },
    annotations: READ,
  },
];

/**
 * The Complete body with exactly one of `decide_only: true` or `decision_id`
 * in the vendor member, or a refusal before anything is sent.
 */
export function completionBody(
  checkout: Json,
  decideOnly: boolean | undefined,
  decisionId: string | undefined,
): { ok: true; body: Json } | { ok: false; error: UcpError } {
  const refuse = (code: string, content: string) => ({ ok: false as const, error: { status: 400, code, content } });
  const rawVendor = checkout[VENDOR_MEMBER];
  if (rawVendor !== undefined && !isObject(rawVendor)) {
    return refuse("invalid_request", `${VENDOR_MEMBER} must be an object.`);
  }
  const vendor: Json = { ...(rawVendor ?? {}) };
  const nestedOnly = vendor.decide_only;
  const nestedId = vendor.decision_id;
  delete vendor.decide_only;
  delete vendor.decision_id;
  if (nestedOnly !== undefined && typeof nestedOnly !== "boolean") {
    return refuse("invalid_request", "decide_only must be a boolean.");
  }
  if (nestedId !== undefined && (typeof nestedId !== "string" || !nestedId)) {
    return refuse("invalid_request", "decision_id must be a non-empty string.");
  }
  if (decideOnly !== undefined && nestedOnly !== undefined && decideOnly !== nestedOnly) {
    return refuse("invalid_request", "decide_only differs between the arguments and the checkout.");
  }
  if (decisionId !== undefined && nestedId !== undefined && decisionId !== nestedId) {
    return refuse("invalid_request", "decision_id differs between the arguments and the checkout.");
  }
  const only = decideOnly ?? (nestedOnly as boolean | undefined) ?? false;
  const id = decisionId ?? (nestedId as string | undefined);
  if (only && id) {
    return refuse("invalid_request", "Send decide_only or decision_id, not both.");
  }
  if (!only && !id) {
    return refuse(
      "decision_required",
      "Completing needs an allow decision: call with decide_only=true, then with the returned decision_id.",
    );
  }
  return {
    ok: true,
    body: { ...checkout, [VENDOR_MEMBER]: only ? { ...vendor, decide_only: true } : { ...vendor, decision_id: id } },
  };
}

async function callUcp(
  binding: UcpBinding,
  options: UcpToolOptions,
  op: Operation,
  meta: Meta | undefined,
  id: string | undefined,
  body: Json | undefined,
) {
  const profile = meta?.["ucp-agent"]?.profile ?? options.agentProfile;
  if (!profile) {
    return errorResult({
      status: 400,
      code: "invalid_profile_url",
      content: "Set meta.ucp-agent.profile or AGENTICORG_UCP_AGENT_PROFILE.",
    });
  }
  let headers: Record<string, string>;
  try {
    headers = options.headers();
  } catch (exc) {
    return errorResult({ status: 401, code: "unauthorized", content: (exc as Error).message });
  }
  headers = {
    ...headers,
    Accept: "application/json",
    "UCP-Agent": `profile="${profile}"`,
    "Request-Id": randomUUID(),
  };
  if (op.method !== "GET") headers["Idempotency-Key"] = meta?.["idempotency-key"] ?? randomUUID();
  let resp: Response;
  try {
    resp = await fetch(`${binding.endpoint}${op.path(id)}`, {
      method: op.method,
      headers,
      body: op.method === "GET" ? undefined : JSON.stringify(body ?? {}),
      redirect: "error",
      signal: AbortSignal.timeout(CALL_TIMEOUT_MS),
    });
  } catch {
    return errorResult({ status: 503, code: "unavailable", content: "The merchant UCP endpoint could not be reached." });
  }
  let parsed: unknown;
  try {
    parsed = await resp.json();
  } catch {
    parsed = undefined;
  }
  if ((resp.status === 200 || resp.status === 201) && isObject(parsed)) {
    // Business outcomes, including `capabilities_incompatible`, are results (UCP MCP binding).
    return okResult(parsed);
  }
  if (resp.ok) {
    return errorResult({ status: 502, code: "invalid_response", content: "The merchant returned an unreadable answer." });
  }
  return errorResult(ucpError(resp.status, parsed));
}

/**
 * Register the tools whose capability the merchant profile advertises.
 * Returns the registered tool names; none when the profile is not configured,
 * unreadable, or does not list the capabilities (checkout disabled).
 */
export async function registerUcpTools(server: McpServer, options: UcpToolOptions): Promise<string[]> {
  const log = options.log ?? ((m: string) => console.error(m));
  let binding: UcpBinding;
  try {
    binding = await discoverUcpBinding(options.profileUrl);
  } catch (exc) {
    log(`UCP checkout tools not registered: ${(exc as Error).message}`);
    return [];
  }
  const registered: string[] = [];
  for (const op of OPERATIONS) {
    if (!binding.capabilities.has(op.capability)) continue;
    server.registerTool(
      op.name,
      {
        title: op.title,
        description: op.description,
        inputSchema: op.shape,
        annotations: { ...op.annotations, openWorldHint: true },
      },
      async (args: Record<string, unknown>) => {
        const meta = args.meta as Meta | undefined;
        const id = args.id as string | undefined;
        let body = op.body ? (args[op.body] as Json) : undefined;
        if (op.name === "complete_checkout") {
          const prepared = completionBody(
            body ?? {},
            args.decide_only as boolean | undefined,
            args.decision_id as string | undefined,
          );
          if (!prepared.ok) return errorResult(prepared.error);
          body = prepared.body;
        }
        return callUcp(binding, options, op, meta, id, body);
      },
    );
    registered.push(op.name);
  }
  if (registered.length === 0) {
    log("UCP checkout tools not registered: the merchant profile lists no checkout, cart or order capability");
  }
  return registered;
}
