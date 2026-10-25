// SPDX-License-Identifier: Apache-2.0
/**
 * Network mode: every shop on the AgenticOrg shopping network from one MCP endpoint.
 *
 * The shops come from the public network directory (`GET /api/v1/network/merchants`), which
 * lists only shops whose UCP profile is published. Catalog search and lookup go to each shop's
 * public `/ucp/catalog/*` routes with no credential. Checkouts go through the network checkout
 * gateway (`/api/v1/network/shops/{slug}/checkout-sessions`) with the network service
 * credential, which the caller never sees and cannot change; there is no complete, cancel or
 * payment tool, so nothing here can take a payment.
 *
 * Every tool result names its `shop` (the network slug), and every checkout tool needs one, so
 * a checkout is always routed to the shop that created it.
 */

import { randomUUID } from "node:crypto";
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import {
  allowedUrl,
  discoverUcpBinding,
  errorResult,
  isObject,
  okResult,
  ucpError,
  type UcpBinding,
  type UcpError,
} from "./ucp.js";

export const NETWORK_TOOLS: ReadonlySet<string> = new Set([
  "list_shops",
  "search_catalog",
  "lookup_catalog",
  "create_checkout",
  "get_checkout",
  "update_checkout",
]);

const DIRECTORY_TTL_MS = 60 * 1000;
const BINDING_TTL_MS = 5 * 60 * 1000;
const DIRECTORY_TIMEOUT_MS = 5_000;
const SEARCH_TIMEOUT_MS = 5_000;
const CALL_TIMEOUT_MS = 30_000;
const FAN_OUT_CONCURRENCY = 5;
const MAX_SHOPS = 50;
const SLUG = /^[a-z0-9](?:[a-z0-9-]{1,38})[a-z0-9]$/;
const CATALOG_SEARCH = "dev.ucp.shopping.catalog.search";
const CATALOG_LOOKUP = "dev.ucp.shopping.catalog.lookup";

type Json = Record<string, unknown>;

export interface NetworkShop {
  slug: string;
  name: string;
  categories: string[];
  profileUrl: string;
}

export interface NetworkOptions {
  /** The public directory, `https://<api>/api/v1/network/merchants`. */
  directoryUrl: string;
  /** The network service credential the checkout gateway accepts. */
  serviceToken: string;
  /** The UCP platform profile sent as UCP-Agent on catalog calls. */
  agentProfile: string;
  now?: () => number;
  log?: (entry: Record<string, unknown>) => void;
}

export interface Network {
  shops(): Promise<NetworkShop[]>;
  shop(slug: string): Promise<NetworkShop | undefined>;
  binding(shop: NetworkShop): Promise<UcpBinding>;
  gateway(slug: string, id?: string): string;
  serviceToken: string;
  agentProfile: string;
}

function parseShops(body: unknown): NetworkShop[] {
  const list = isObject(body) && Array.isArray(body.shops) ? body.shops : [];
  const shops: NetworkShop[] = [];
  for (const entry of list.slice(0, MAX_SHOPS)) {
    if (!isObject(entry) || typeof entry.slug !== "string" || !SLUG.test(entry.slug)) continue;
    const profile = typeof entry.profile_url === "string" ? allowedUrl(entry.profile_url) : null;
    if (!profile || !profile.pathname.endsWith("/.well-known/ucp")) continue;
    shops.push({
      slug: entry.slug,
      name: typeof entry.name === "string" ? entry.name.slice(0, 200) : entry.slug,
      categories: Array.isArray(entry.categories) ? entry.categories.filter((c): c is string => typeof c === "string").slice(0, 10) : [],
      profileUrl: profile.href,
    });
  }
  return shops;
}

/** The directory and per-shop bindings, cached with a stale fallback (one instance per process). */
export function createNetwork(options: NetworkOptions): Network {
  const directory = allowedUrl(options.directoryUrl);
  if (!directory || !directory.pathname.endsWith("/network/merchants")) {
    throw new Error("MCP_NETWORK_DIRECTORY_URL must be an https URL ending in /network/merchants");
  }
  if (!options.serviceToken || options.serviceToken.length < 32) {
    throw new Error("AGENTICORG_NETWORK_SERVICE_TOKEN must be at least 32 characters");
  }
  const now = options.now ?? Date.now;
  const log = options.log ?? (() => {});
  const gatewayBase = directory.href.replace(/\/merchants$/, "/shops");
  let cached: { shops: NetworkShop[]; at: number } | null = null;
  const bindings = new Map<string, { binding: UcpBinding; at: number; profileUrl: string }>();

  async function shops(): Promise<NetworkShop[]> {
    if (cached && now() - cached.at < DIRECTORY_TTL_MS) return cached.shops;
    try {
      const resp = await fetch(directory as URL, {
        headers: { Accept: "application/json" },
        redirect: "error",
        signal: AbortSignal.timeout(DIRECTORY_TIMEOUT_MS),
      });
      if (!resp.ok) throw new Error(`directory answered HTTP ${resp.status}`);
      cached = { shops: parseShops(await resp.json()), at: now() };
    } catch (exc) {
      if (!cached) throw exc;
      log({ event: "mcp_directory_refresh_failed", error: (exc as Error).message });
    }
    return cached.shops;
  }

  async function binding(shop: NetworkShop): Promise<UcpBinding> {
    const hit = bindings.get(shop.slug);
    if (hit && hit.profileUrl === shop.profileUrl && now() - hit.at < BINDING_TTL_MS) return hit.binding;
    try {
      const fresh = await discoverUcpBinding(shop.profileUrl);
      bindings.set(shop.slug, { binding: fresh, at: now(), profileUrl: shop.profileUrl });
      return fresh;
    } catch (exc) {
      if (hit && hit.profileUrl === shop.profileUrl) {
        log({ event: "mcp_profile_refresh_failed", shop: shop.slug, error: (exc as Error).message });
        return hit.binding;
      }
      throw exc;
    }
  }

  return {
    shops,
    shop: async (slug) => (await shops()).find((s) => s.slug === slug),
    binding,
    gateway: (slug, id) => `${gatewayBase}/${encodeURIComponent(slug)}/checkout-sessions${id ? `/${encodeURIComponent(id)}` : ""}`,
    serviceToken: options.serviceToken,
    agentProfile: options.agentProfile,
  };
}

// --- calls -----------------------------------------------------------------------------------

async function readJson(resp: Response): Promise<unknown> {
  try {
    return await resp.json();
  } catch {
    return null;
  }
}

/** FastAPI wraps refusals in `detail`; the shop's own UCP errors are top-level. */
function refusal(status: number, body: unknown): UcpError {
  return ucpError(status, isObject(body) && isObject(body.detail) ? body.detail : body);
}

async function publicCatalogCall(
  net: Network,
  shop: NetworkShop,
  path: "/catalog/search" | "/catalog/lookup",
  capability: string,
  request: Json,
  timeoutMs: number,
): Promise<{ ok: true; body: Json } | { ok: false; error: UcpError }> {
  let current: UcpBinding;
  try {
    current = await net.binding(shop);
  } catch {
    return { ok: false, error: { status: 503, code: "unavailable", content: "The shop's profile could not be read." } };
  }
  if (!current.capabilities.has(capability)) {
    return { ok: false, error: { status: 404, code: "not_found", content: "The shop does not serve this catalog operation." } };
  }
  let resp: Response;
  try {
    resp = await fetch(`${current.endpoint}${path}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
        "Request-Id": randomUUID(),
        "UCP-Agent": `profile="${net.agentProfile}"`,
      },
      body: JSON.stringify(request),
      redirect: "error",
      signal: AbortSignal.timeout(timeoutMs),
    });
  } catch {
    return { ok: false, error: { status: 503, code: "unavailable", content: "The shop did not answer in time." } };
  }
  const body = await readJson(resp);
  if (resp.status === 200 && isObject(body)) return { ok: true, body };
  return { ok: false, error: refusal(resp.status, body) };
}

async function gatewayCall(
  net: Network,
  method: "GET" | "POST" | "PUT",
  url: string,
  body?: Json,
): Promise<{ ok: true; body: Json } | { ok: false; error: UcpError }> {
  const headers: Record<string, string> = {
    Accept: "application/json",
    Authorization: `Bearer ${net.serviceToken}`,
    "Request-Id": randomUUID(),
  };
  if (method !== "GET") {
    headers["Content-Type"] = "application/json";
    headers["Idempotency-Key"] = randomUUID();
  }
  let resp: Response;
  try {
    resp = await fetch(url, {
      method,
      headers,
      body: body ? JSON.stringify(body) : undefined,
      redirect: "error",
      signal: AbortSignal.timeout(CALL_TIMEOUT_MS),
    });
  } catch {
    return { ok: false, error: { status: 503, code: "unavailable", content: "The network gateway did not answer; retry later." } };
  }
  const parsed = await readJson(resp);
  if ((resp.status === 200 || resp.status === 201) && isObject(parsed)) return { ok: true, body: parsed };
  return { ok: false, error: refusal(resp.status, parsed) };
}

function withShop(slug: string, body: Json): Json {
  const out: Json = { shop: slug, ...body };
  if (Array.isArray(body.products)) {
    out.products = body.products.map((p) => (isObject(p) ? { ...p, shop: slug } : p));
  }
  return out;
}

async function inBatches<T, R>(items: T[], size: number, fn: (item: T) => Promise<R>): Promise<R[]> {
  const out: R[] = [];
  for (let i = 0; i < items.length; i += size) out.push(...(await Promise.all(items.slice(i, i + size).map(fn))));
  return out;
}

// --- tools -----------------------------------------------------------------------------------

const READ = { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: true };
const CREATE = { readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: true };
const REPLACE = { readOnlyHint: false, destructiveHint: true, idempotentHint: true, openWorldHint: true };

const shopSchema = z.string().regex(SLUG, "a shop slug from list_shops").describe("The shop's network slug, from list_shops or a search result");
const idSchema = z
  .string()
  .min(1)
  .max(256)
  .refine((v) => v !== "." && v !== "..", "id must not be a dot segment");
const objectSchema = z.record(z.string(), z.unknown());

function unknownShop(slug: string) {
  return errorResult({ status: 404, code: "unknown_shop", content: `No shop "${slug}" in the network; call list_shops.` });
}

export function registerNetworkTools(server: McpServer, net: Network): string[] {
  server.registerTool(
    "list_shops",
    {
      title: "List network shops",
      description: "The shops on the AgenticOrg network that can be searched and shopped right now.",
      inputSchema: {},
      annotations: READ,
    },
    async () => {
      try {
        const shops = await net.shops();
        return okResult({ shops: shops.map(({ slug, name, categories }) => ({ shop: slug, name, categories })) });
      } catch {
        return errorResult({ status: 503, code: "unavailable", content: "The network directory is unavailable; retry later." });
      }
    },
  );

  server.registerTool(
    "search_catalog",
    {
      title: "Search network catalogs",
      description:
        "Search products by free text across every network shop, or in one shop when `shop` is given. " +
        "Answers come only from each shop's verified catalog; prices are in minor units (44900 INR is 449.00). " +
        "Every product names its `shop`, which the checkout tools need.",
      inputSchema: {
        request: objectSchema.describe('UCP catalog search request, e.g. {"query":"chai","context":{"currency":"INR"}}'),
        shop: shopSchema.optional(),
      },
      annotations: READ,
    },
    async ({ request, shop }) => {
      let shops: NetworkShop[];
      try {
        shops = await net.shops();
      } catch {
        return errorResult({ status: 503, code: "unavailable", content: "The network directory is unavailable; retry later." });
      }
      if (shop) {
        const one = shops.find((s) => s.slug === shop);
        if (!one) return unknownShop(shop);
        const result = await publicCatalogCall(net, one, "/catalog/search", CATALOG_SEARCH, request, SEARCH_TIMEOUT_MS * 6);
        return result.ok ? okResult(withShop(one.slug, result.body)) : errorResult(result.error);
      }
      const answers = await inBatches(shops, FAN_OUT_CONCURRENCY, async (s) => ({
        shop: s,
        result: await publicCatalogCall(net, s, "/catalog/search", CATALOG_SEARCH, request, SEARCH_TIMEOUT_MS),
      }));
      const results: Json[] = [];
      const unavailable: Json[] = [];
      for (const { shop: s, result } of answers) {
        if (result.ok) results.push({ name: s.name, ...withShop(s.slug, result.body) });
        else unavailable.push({ shop: s.slug, code: result.error.code });
      }
      return okResult({ results, unavailable, shops_searched: shops.length });
    },
  );

  server.registerTool(
    "lookup_catalog",
    {
      title: "Look up products in a shop",
      description: 'Look products up by id in one shop, e.g. request {"ids":["NTH-MASALA-250"]}.',
      inputSchema: { shop: shopSchema, request: objectSchema.describe('UCP catalog lookup request, e.g. {"ids":["..."]}') },
      annotations: READ,
    },
    async ({ shop, request }) => {
      const one = await net.shop(shop).catch(() => undefined);
      if (!one) return unknownShop(shop);
      const result = await publicCatalogCall(net, one, "/catalog/lookup", CATALOG_LOOKUP, request, CALL_TIMEOUT_MS);
      return result.ok ? okResult(withShop(one.slug, result.body)) : errorResult(result.error);
    },
  );

  const checkoutTool = async (
    shop: string,
    method: "GET" | "POST" | "PUT",
    id?: string,
    body?: Json,
  ) => {
    const one = await net.shop(shop).catch(() => undefined);
    if (!one) return unknownShop(shop);
    const result = await gatewayCall(net, method, net.gateway(one.slug, id), body);
    return result.ok ? okResult(withShop(one.slug, result.body)) : errorResult(result.error);
  };

  server.registerTool(
    "create_checkout",
    {
      title: "Open a checkout",
      description:
        'Open a checkout at one shop, e.g. checkout {"line_items":[{"item":{"id":"NTH-MASALA-250"},"quantity":1}]}. ' +
        "It stops before payment: the buyer completes and pays with the shop.",
      inputSchema: { shop: shopSchema, checkout: objectSchema.describe("UCP checkout create request") },
      annotations: CREATE,
    },
    async ({ shop, checkout }) => checkoutTool(shop, "POST", undefined, checkout),
  );

  server.registerTool(
    "get_checkout",
    {
      title: "Read a checkout",
      description: "Read a checkout this network opened at a shop.",
      inputSchema: { shop: shopSchema, id: idSchema.describe("The checkout id") },
      annotations: READ,
    },
    async ({ shop, id }) => checkoutTool(shop, "GET", id),
  );

  server.registerTool(
    "update_checkout",
    {
      title: "Update a checkout",
      description:
        "Replace a checkout's line items, buyer or fulfilment (for example choose store pickup). Send the full checkout; " +
        "payment cannot be added here.",
      inputSchema: { shop: shopSchema, id: idSchema.describe("The checkout id"), checkout: objectSchema.describe("The full UCP checkout") },
      annotations: REPLACE,
    },
    async ({ shop, id, checkout }) => checkoutTool(shop, "PUT", id, checkout),
  );

  return [...NETWORK_TOOLS];
}
