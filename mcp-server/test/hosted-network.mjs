// SPDX-License-Identifier: Apache-2.0
// Hosted endpoint in network mode (src/network.ts): shops from a stub directory, catalog calls to each
// shop's public routes, checkouts through a stub network gateway with the service credential.
// Two stub UCP shops and one stub AgenticOrg API on loopback; synthetic data only.
import assert from "node:assert/strict";
import http from "node:http";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import { createHostedHandler } from "../dist/http.js";
import { NETWORK_TOOLS, fanOutRequest, sameProducts, shuffled, validGtin } from "../dist/network.js";

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
      if (path === "/ucp/catalog/search") {
        const variants = product.variants ? { variants: product.variants } : {};
        return json(res, { products: [{ id: product.id, title: product.title, ...variants }] });
      }
      if (path === "/ucp/catalog/lookup") return json(res, { products: [{ id: product.id, title: product.title }] });
      return json(res, { code: "not_found", content: "Not found." }, 404);
    }),
  );
  return { server, setBase: (b) => (base = b) };
}

// Both shops sell the same synthetic item (one barcode) at different prices.
const sameItem = (id, amount) => [
  { id, price: { amount, currency: "INR" }, barcodes: [{ type: "GTIN", value: "8901234567890" }] },
];
const tea = shopServer("tea-corner", { id: "TEA-1", title: "Synthetic Masala Chai", variants: sameItem("TEA-1-V", 50000) });
const kettles = shopServer("kettle-works", { id: "KET-1", title: "Synthetic Kettle", variants: sameItem("KET-1-V", 45000) });
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
    const match = path.match(/^\/api\/v1\/network\/shops\/([a-z0-9-]+)\/checkout-sessions(?:\/([^/]+))?(\/complete)?$/);
    if (!match) return json(res, { detail: "Not Found" }, 404);
    if (match[1] === "kettle-works") return json(res, { detail: { code: "shop_unavailable", content: "Paused." } }, 404);
    if (match[3]) {
      if (match[2] === "chk_not_ready") {
        return json(res, { detail: { code: "payment_execution_not_allowed", message: "No." } }, 422);
      }
      return json(res, {
        id: match[2],
        status: "requires_escalation",
        continue_url: "https://tea-corner.myshopify.com/cart/123:1",
        messages: [{ type: "error", code: "payment_handoff", severity: "requires_buyer_input", content: "Open continue_url." }],
      });
    }
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
    const tools = (await client.listTools()).tools;
    const names = tools.map((t) => t.name).sort();
    assert.deepEqual(names, [...NETWORK_TOOLS].sort());
    for (const banned of ["cancel_checkout", "create_cart", "get_order"]) assert.ok(!names.includes(banned));
    // The hand-off tool takes the shop and the checkout id and nothing that could carry a payment.
    const complete = tools.find((t) => t.name === "complete_checkout");
    assert.deepEqual(Object.keys(complete.inputSchema.properties).sort(), ["id", "shop"]);
    await client.close();
  });

  await check("complete_checkout returns the shop's payment link and sends no payment data", async () => {
    const client = await connect();
    const done = await client.callTool({ name: "complete_checkout", arguments: { shop: "tea-corner", id: "chk_net_1" } });
    assert.notEqual(done.isError, true);
    assert.equal(done.structuredContent.status, "requires_escalation");
    assert.equal(done.structuredContent.payment_link, "https://tea-corner.myshopify.com/cart/123:1");
    assert.equal(done.structuredContent.shop, "tea-corner");
    const post = calls.at(-1);
    assert.equal(post.method, "POST");
    assert.equal(post.path, "/api/v1/network/shops/tea-corner/checkout-sessions/chk_net_1/complete");
    assert.deepEqual(post.body, {});
    assert.equal(post.headers.authorization, `Bearer ${SERVICE}`);
    const refused = await client.callTool({ name: "complete_checkout", arguments: { shop: "tea-corner", id: "chk_not_ready" } });
    assert.equal(refused.isError, true);
    assert.equal(refused.structuredContent.error.code, "payment_execution_not_allowed");
    await client.close();
  });

  await check("list_shops keeps only valid directory entries", async () => {
    const client = await connect();
    const result = await client.callTool({ name: "list_shops", arguments: {} });
    assert.deepEqual(
      result.structuredContent.shops.map((s) => s.shop).sort(),
      ["broken-shop", "kettle-works", "tea-corner"],
    );
    await client.close();
  });

  await check("search without a shop fans out, tags every product and reports the shop that failed", async () => {
    const client = await connect();
    const before = calls.length;
    const result = await client.callTool({ name: "search_catalog", arguments: { request: { query: "chai" } } });
    assert.notEqual(result.isError, true);
    const { results, unavailable, shops_searched, shops_in_network, shops_in_random_order } = result.structuredContent;
    assert.equal(shops_searched, 3);
    assert.equal(shops_in_network, 3);
    assert.equal(shops_in_random_order, true);
    assert.deepEqual(results.map((r) => r.shop).sort(), ["kettle-works", "tea-corner"]);
    const tea = results.find((r) => r.shop === "tea-corner");
    assert.equal(tea.products[0].shop, "tea-corner");
    assert.equal(tea.name, "Tea Corner");
    assert.deepEqual(unavailable, [{ shop: "broken-shop", code: "unavailable" }]);
    const searches = calls.slice(before).filter((c) => c.path === "/ucp/catalog/search");
    assert.equal(searches.length, 3);
    for (const call of searches) {
      assert.deepEqual(call.body.pagination, { limit: 3 }, "a small page from each shop");
      assert.equal(call.headers.authorization, undefined);
      assert.equal(call.headers["ucp-agent"], `profile="${AGENT}"`);
    }
    await client.close();
  });

  await check("the same product at several shops is listed with each offer, lowest price first", async () => {
    const client = await connect();
    const result = await client.callTool({ name: "search_catalog", arguments: { request: { query: "chai" } } });
    const [group] = result.structuredContent.same_products;
    assert.equal(group.barcode, "8901234567890");
    assert.deepEqual(
      group.offers.map((o) => [o.shop, o.variant_id, o.price.amount]),
      [
        ["kettle-works", "KET-1-V", 45000],
        ["tea-corner", "TEA-1-V", 50000],
      ],
    );
    await client.close();
  });

  await check("shops come back in a fresh random order, and no shop is always first", async () => {
    const client = await connect();
    const firsts = new Set();
    for (let i = 0; i < 24; i += 1) {
      const result = await client.callTool({ name: "search_catalog", arguments: { request: { query: "chai" } } });
      firsts.add(result.structuredContent.results[0].shop);
    }
    assert.deepEqual([...firsts].sort(), ["kettle-works", "tea-corner"]);
    await client.close();
  });

  await check("exclude_shops and only_shops apply before the shuffle and are echoed in the result", async () => {
    const client = await connect();
    let before = calls.length;
    const excluded = await client.callTool({
      name: "search_catalog",
      arguments: { request: { query: "chai" }, exclude_shops: ["tea-corner", "gone-shop"] },
    });
    assert.notEqual(excluded.isError, true);
    const body = excluded.structuredContent;
    assert.deepEqual(body.results.map((r) => r.shop), ["kettle-works"]);
    assert.deepEqual(body.shops_excluded, ["gone-shop", "tea-corner"]);
    assert.deepEqual(body.unknown_shops, ["gone-shop"]);
    assert.equal(body.shops_searched, 2);
    assert.equal(body.shops_in_network, 3);
    assert.equal(body.same_products, undefined, "the excluded shop's offer is not grouped in either");
    const asked = calls.slice(before).filter((c) => c.path === "/ucp/catalog/search").map((c) => c.shop).sort();
    assert.deepEqual(asked, ["broken-shop", "kettle-works"], "an excluded shop is never asked");

    before = calls.length;
    const only = await client.callTool({
      name: "search_catalog",
      arguments: { request: { query: "chai" }, only_shops: ["tea-corner", "kettle-works"] },
    });
    assert.deepEqual(only.structuredContent.only_shops, ["kettle-works", "tea-corner"]);
    assert.deepEqual(only.structuredContent.unavailable, []);
    assert.equal(only.structuredContent.shops_searched, 2);
    assert.equal(calls.slice(before).filter((c) => c.shop === "broken-shop").length, 0);

    // The shops that remain still come back in a fresh random order.
    const firsts = new Set();
    for (let i = 0; i < 24; i += 1) {
      const again = await client.callTool({
        name: "search_catalog",
        arguments: { request: { query: "chai" }, exclude_shops: ["broken-shop"] },
      });
      firsts.add(again.structuredContent.results[0].shop);
    }
    assert.deepEqual([...firsts].sort(), ["kettle-works", "tea-corner"]);

    before = calls.length;
    const refused = await client.callTool({
      name: "search_catalog",
      arguments: { shop: "tea-corner", request: { query: "chai" }, exclude_shops: ["tea-corner"] },
    });
    assert.equal(refused.isError, true);
    assert.equal(refused.structuredContent.error.code, "shop_excluded");
    assert.equal(calls.length, before, "nothing is asked of a shop the buyer excluded");

    const tooMany = await client.callTool({
      name: "search_catalog",
      arguments: { request: { query: "chai" }, exclude_shops: Array.from({ length: 51 }, (_, i) => `shop-${i}0`) },
    });
    assert.equal(tooMany.isError, true, "at most 50 shops in exclude_shops");
    const badSlug = await client.callTool({
      name: "search_catalog",
      arguments: { request: { query: "chai" }, only_shops: ["Not A Slug"] },
    });
    assert.equal(badSlug.isError, true);
    await client.close();
  });

  await check("shuffling is a permutation, and offers group by barcode across shops only", async () => {
    const items = Array.from({ length: 60 }, (_, i) => i);
    let seed = 7;
    const random = () => ((seed = (seed * 16807) % 2147483647) - 1) / 2147483646;
    const mixed = shuffled(items, random);
    assert.notDeepEqual(mixed, items);
    assert.deepEqual([...mixed].sort((a, b) => a - b), items);
    assert.equal(items[0], 0, "the input is not changed");
    const v = (id, amount, currency, value = "8901234567890") => ({
      id,
      price: { amount, currency },
      barcodes: [{ type: "GTIN", value }],
    });
    const groups = sameProducts([
      { shop: "a", products: [{ id: "p1", title: "Ghee", variants: [v("a1", 900, "INR"), v("a2", 10, "USD", "4006381333931")] }] },
      { shop: "b", products: [{ id: "p2", title: "Ghee", variants: [v("b1", 800, "INR"), { id: "b2", barcodes: [{ type: "GTIN", value: "12AB" }] }] }] },
      { shop: "c", products: [{ id: "p3", title: "Ghee", variants: [{ id: "c1", barcodes: [{ type: "GTIN", value: "8901234567890" }] }] }] },
    ]);
    assert.equal(groups.length, 1, "a barcode at one shop only, or not a GTIN, is no group");
    assert.deepEqual(groups[0].offers.map((o) => o.shop), ["b", "a", "c"], "priced first, lowest first; unpriced last");

    // The main currency decides the comparison, not the currency code; a shop appears once, at its cheapest.
    const mixed2 = sameProducts([
      { shop: "aed-shop", products: [{ id: "x", variants: [v("x1", 1, "AED")] }] },
      { shop: "inr-1", products: [{ id: "y", variants: [v("y1", 900, "INR"), v("y2", 700, "INR")] }] },
      { shop: "inr-2", products: [{ id: "z", variants: [v("z1", 800, "INR")] }] },
    ]);
    assert.deepEqual(mixed2[0].offers.map((o) => [o.shop, o.variant_id]), [["inr-1", "y2"], ["inr-2", "z1"], ["aed-shop", "x1"]]);
    assert.equal(validGtin("8901234567890"), true);
    assert.equal(validGtin("8901234567891"), false, "wrong check digit");
    assert.equal(validGtin("1"), false);
    assert.equal(validGtin("١٢٣٤٥٦٧٨"), false, "only ASCII digits");
    const noCheck = sameProducts([
      { shop: "a", products: [{ id: "p", variants: [v("a1", 1, "INR", "12345678")] }] },
      { shop: "b", products: [{ id: "q", variants: [v("b1", 1, "INR", "12345678")] }] },
    ]);
    assert.deepEqual(noCheck, [], "a barcode with a wrong check digit groups nothing");
  });

  await check("each shop of a network search gets a small page, never a cursor from another shop", async () => {
    assert.deepEqual(fanOutRequest({ query: "chai" }), { query: "chai", pagination: { limit: 3 } });
    assert.deepEqual(fanOutRequest({ query: "chai", pagination: { limit: 50, cursor: "abc" } }), {
      query: "chai",
      pagination: { limit: 10 },
    });
    assert.deepEqual(fanOutRequest({ pagination: { limit: 0 } }), { pagination: { limit: 1 } });
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
