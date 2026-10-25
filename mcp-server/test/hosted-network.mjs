// SPDX-License-Identifier: Apache-2.0
// Hosted endpoint in network mode (src/network.ts): shops from a stub directory, catalog calls to each
// shop's public routes, checkouts through a stub network gateway with the service credential.
// Two stub UCP shops and one stub AgenticOrg API on loopback; synthetic data only.
import assert from "node:assert/strict";
import http from "node:http";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import { createHostedHandler } from "../dist/http.js";
import { NETWORK_TOOLS } from "../dist/network.js";

const SERVICE = "synthetic-network-service-token-0123456789";
const AGENT = "https://platform.example/profiles/network.json";
const calls = [];

function listen(server) {
  return new Promise((r) => server.listen(0, "127.0.0.1", () => r(`http://127.0.0.1:${server.address().port}`)));
}

function collect(req, fn) {
  let raw = "";
  req.setEncoding("utf8");
  req.on("data", (c) => (raw += c));
  req.on("end", () => fn(raw ? JSON.parse(raw) : undefined));
}

function json(res, body, status = 200) {
  res.writeHead(status, { "content-type": "application/json" });
  res.end(JSON.stringify(body));
}

function shopServer(name, product, { down = false } = {}) {
  let base = "";
  const server = http.createServer((req, res) =>
    collect(req, (body) => {
      const path = req.url.split("?")[0];
      if (req.method === "GET" && path === "/.well-known/ucp") {
        const cap = { version: "2026-04-08" };
        return json(res, {
          ucp: {
            version: "2026-04-08",
            services: { "dev.ucp.shopping": [{ version: "2026-04-08", transport: "rest", endpoint: `${base}/ucp` }] },
            capabilities: Object.fromEntries(
              ["dev.ucp.shopping.catalog.search", "dev.ucp.shopping.catalog.lookup"].map((n) => [n, [cap]]),
            ),
          },
        });
      }
      calls.push({ shop: name, method: req.method, path, body, headers: req.headers });
      if (down) return json(res, { code: "unavailable", content: "Down." }, 503);
      if (path === "/ucp/catalog/search") return json(res, { products: [{ id: product.id, title: product.title }] });
      if (path === "/ucp/catalog/lookup") return json(res, { products: [{ id: product.id, title: product.title }] });
      return json(res, { code: "not_found", content: "Not found." }, 404);
    }),
  );
  return { server, setBase: (b) => (base = b) };
}

const tea = shopServer("tea-corner", { id: "TEA-1", title: "Synthetic Masala Chai" });
const kettles = shopServer("kettle-works", { id: "KET-1", title: "Synthetic Kettle" });
const broken = shopServer("broken-shop", { id: "X", title: "X" }, { down: true });
const teaBase = await listen(tea.server);
tea.setBase(teaBase);
const kettleBase = await listen(kettles.server);
kettles.setBase(kettleBase);
const brokenBase = await listen(broken.server);
broken.setBase(brokenBase);

const api = http.createServer((req, res) =>
  collect(req, (body) => {
    const path = req.url.split("?")[0];
    if (req.method === "GET" && path === "/api/v1/network/merchants") {
      return json(res, {
        shops: [
          { slug: "tea-corner", name: "Tea Corner", categories: ["tea"], profile_url: `${teaBase}/.well-known/ucp` },
          { slug: "kettle-works", name: "Kettle Works", categories: [], profile_url: `${kettleBase}/.well-known/ucp` },
          { slug: "broken-shop", name: "Broken", categories: [], profile_url: `${brokenBase}/.well-known/ucp` },
          { slug: "Bad Slug", name: "Ignored", profile_url: `${teaBase}/.well-known/ucp` },
          { slug: "no-profile", name: "Ignored", profile_url: "ftp://example.test/.well-known/ucp" },
        ],
      });
    }
    calls.push({ shop: "gateway", method: req.method, path, body, headers: req.headers });
    if (req.headers.authorization !== `Bearer ${SERVICE}`) return json(res, { detail: { code: "unauthorized" } }, 401);
    const match = path.match(/^\/api\/v1\/network\/shops\/([a-z0-9-]+)\/checkout-sessions(?:\/([^/]+))?$/);
    if (!match) return json(res, { detail: "Not Found" }, 404);
    if (match[1] === "kettle-works") return json(res, { detail: { code: "shop_unavailable", content: "Paused." } }, 404);
    if (req.method === "POST") return json(res, { id: "chk_net_1", status: "incomplete", line_items: body.line_items }, 201);
    return json(res, { id: match[2], status: "ready_for_complete" });
  }),
);
const apiBase = await listen(api);

const handler = createHostedHandler({
  profileUrl: "",
  apiKey: "",
  agentProfile: AGENT,
  network: { directoryUrl: `${apiBase}/api/v1/network/merchants`, serviceToken: SERVICE },
  allowAnonymous: true,
  allowedHosts: new Set(["127.0.0.1"]),
  perClientPerMinute: 1000,
  globalPerMinute: 1000,
  version: "test",
  log: () => {},
});
const hosted = http.createServer((req, res) => void handler(req, res));
const base = await listen(hosted);

let passed = 0;
const check = async (label, fn) => {
  await fn();
  passed += 1;
  console.log(`ok - ${label}`);
};

async function connect() {
  const client = new Client({ name: "network-test", version: "1.0.0" });
  await client.connect(new StreamableHTTPClientTransport(new URL(`${base}/mcp`)));
  return client;
}

try {
  await check("exposes the network tools and nothing that can pay", async () => {
    const client = await connect();
    const names = (await client.listTools()).tools.map((t) => t.name).sort();
    assert.deepEqual(names, [...NETWORK_TOOLS].sort());
    for (const banned of ["complete_checkout", "cancel_checkout", "create_cart", "get_order"]) assert.ok(!names.includes(banned));
    await client.close();
  });

  await check("list_shops keeps only valid directory entries", async () => {
    const client = await connect();
    const result = await client.callTool({ name: "list_shops", arguments: {} });
    assert.deepEqual(
      result.structuredContent.shops.map((s) => s.shop),
      ["tea-corner", "kettle-works", "broken-shop"],
    );
    await client.close();
  });

  await check("search without a shop fans out, tags every product and reports the shop that failed", async () => {
    const client = await connect();
    const before = calls.length;
    const result = await client.callTool({ name: "search_catalog", arguments: { request: { query: "chai" } } });
    assert.notEqual(result.isError, true);
    const { results, unavailable, shops_searched } = result.structuredContent;
    assert.equal(shops_searched, 3);
    assert.deepEqual(results.map((r) => r.shop).sort(), ["kettle-works", "tea-corner"]);
    const tea = results.find((r) => r.shop === "tea-corner");
    assert.equal(tea.products[0].shop, "tea-corner");
    assert.equal(tea.name, "Tea Corner");
    assert.deepEqual(unavailable, [{ shop: "broken-shop", code: "unavailable" }]);
    const searches = calls.slice(before).filter((c) => c.path === "/ucp/catalog/search");
    assert.equal(searches.length, 3);
    for (const call of searches) {
      assert.equal(call.headers.authorization, undefined);
      assert.equal(call.headers["ucp-agent"], `profile="${AGENT}"`);
    }
    await client.close();
  });

  await check("search and lookup in one shop; an unknown shop is refused before any call", async () => {
    const client = await connect();
    const one = await client.callTool({ name: "search_catalog", arguments: { shop: "kettle-works", request: { query: "kettle" } } });
    assert.equal(one.structuredContent.shop, "kettle-works");
    assert.equal(one.structuredContent.products[0].title, "Synthetic Kettle");
    const lookup = await client.callTool({ name: "lookup_catalog", arguments: { shop: "tea-corner", request: { ids: ["TEA-1"] } } });
    assert.equal(lookup.structuredContent.products[0].id, "TEA-1");
    const before = calls.length;
    const unknown = await client.callTool({ name: "lookup_catalog", arguments: { shop: "nobody-here", request: { ids: [] } } });
    assert.equal(unknown.isError, true);
    assert.equal(unknown.structuredContent.error.code, "unknown_shop");
    assert.equal(calls.length, before);
    await client.close();
  });

  await check("checkouts go through the gateway with the service credential and name the shop", async () => {
    const client = await connect();
    const created = await client.callTool({
      name: "create_checkout",
      arguments: { shop: "tea-corner", checkout: { line_items: [{ item: { id: "TEA-1" }, quantity: 1 }] } },
    });
    assert.notEqual(created.isError, true);
    assert.equal(created.structuredContent.id, "chk_net_1");
    assert.equal(created.structuredContent.shop, "tea-corner");
    const post = calls.at(-1);
    assert.equal(post.shop, "gateway");
    assert.equal(post.path, "/api/v1/network/shops/tea-corner/checkout-sessions");
    assert.equal(post.headers.authorization, `Bearer ${SERVICE}`);
    assert.match(post.headers["idempotency-key"], /^[0-9a-f-]{36}$/);

    const updated = await client.callTool({
      name: "update_checkout",
      arguments: { shop: "tea-corner", id: "chk_net_1", checkout: { id: "chk_net_1" } },
    });
    assert.equal(updated.structuredContent.status, "ready_for_complete");
    assert.equal(calls.at(-1).method, "PUT");
    const read = await client.callTool({ name: "get_checkout", arguments: { shop: "tea-corner", id: "chk_net_1" } });
    assert.equal(read.structuredContent.id, "chk_net_1");
    assert.equal(calls.at(-1).headers["idempotency-key"], undefined);
    await client.close();
  });

  await check("gateway refusals come back as tool errors with their code", async () => {
    const client = await connect();
    const refused = await client.callTool({
      name: "create_checkout",
      arguments: { shop: "kettle-works", checkout: { line_items: [] } },
    });
    assert.equal(refused.isError, true);
    assert.deepEqual(refused.structuredContent.error, { status: 404, code: "shop_unavailable", content: "Paused." });
    await client.close();
  });

  await check("network mode needs a directory URL and a service token of at least 32 characters", async () => {
    const base = { profileUrl: "", apiKey: "", agentProfile: AGENT, allowAnonymous: true, allowedHosts: new Set(["x"]), perClientPerMinute: 1, globalPerMinute: 1, version: "t" };
    assert.throws(() => createHostedHandler({ ...base, network: { directoryUrl: "http://evil.example/api/v1/network/merchants", serviceToken: SERVICE } }), /MCP_NETWORK_DIRECTORY_URL/);
    assert.throws(() => createHostedHandler({ ...base, network: { directoryUrl: `${apiBase}/api/v1/other`, serviceToken: SERVICE } }), /MCP_NETWORK_DIRECTORY_URL/);
    assert.throws(() => createHostedHandler({ ...base, network: { directoryUrl: `${apiBase}/api/v1/network/merchants`, serviceToken: "short" } }), /AGENTICORG_NETWORK_SERVICE_TOKEN/);
  });
} finally {
  for (const server of [hosted, api, tea.server, kettles.server, broken.server]) server.close();
}
console.log(`${passed} network checks passed`);
