// SPDX-License-Identifier: Apache-2.0
// Hosted Streamable HTTP endpoint (src/http.ts): only the shop tools, the operator's profile and key,
// host allowlist, method, size and rate limits. A stub UCP merchant on loopback; synthetic data only.
import assert from "node:assert/strict";
import http from "node:http";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import { createHostedHandler, createRateLimiter, HOSTED_TOOLS } from "../dist/http.js";

const KEY = "synthetic-hosted-platform-key";
const AGENT = "https://platform.example/profiles/demo-buyer.json";
const calls = [];
let merchantBase = "";

const merchant = http.createServer((req, res) => {
  let raw = "";
  req.setEncoding("utf8");
  req.on("data", (c) => (raw += c));
  req.on("end", () => {
    const json = (body, status = 200) => {
      res.writeHead(status, { "content-type": "application/json" });
      res.end(JSON.stringify(body));
    };
    const path = req.url.split("?")[0];
    if (req.method === "GET" && path === "/.well-known/ucp") {
      const cap = { version: "2026-04-08" };
      const names = [
        "dev.ucp.shopping.catalog.search",
        "dev.ucp.shopping.catalog.lookup",
        "dev.ucp.shopping.checkout",
        "dev.ucp.shopping.cart",
        "dev.ucp.shopping.order",
      ];
      return json({
        ucp: {
          version: "2026-04-08",
          services: { "dev.ucp.shopping": [{ version: "2026-04-08", transport: "rest", endpoint: `${merchantBase}/ucp` }] },
          capabilities: Object.fromEntries(names.map((n) => [n, [cap]])),
        },
      });
    }
    calls.push({ method: req.method, path, body: raw ? JSON.parse(raw) : undefined, headers: req.headers });
    if (path === "/ucp/catalog/search") {
      return json({ products: [{ id: "p1", title: "Synthetic Chai", variants: [{ id: "v1", price: { amount: 44900, currency: "INR" } }] }] });
    }
    if (path === "/ucp/checkout-sessions" && req.method === "POST") {
      return json({ id: "chk_1", status: "incomplete", currency: "INR", line_items: [], totals: [{ type: "total", amount: 44900 }], messages: [] }, 201);
    }
    return json({ code: "not_found", content: "Not found." }, 404);
  });
});
await new Promise((r) => merchant.listen(0, "127.0.0.1", r));
merchantBase = `http://127.0.0.1:${merchant.address().port}`;

let clock = 1_000_000;
const handler = createHostedHandler({
  profileUrl: `${merchantBase}/.well-known/ucp`,
  agentProfile: AGENT,
  apiKey: KEY,
  allowedHosts: new Set(["127.0.0.1"]),
  perClientPerMinute: 1000,
  globalPerMinute: 1000,
  version: "test",
  log: () => {},
  now: () => clock,
});
const hosted = http.createServer((req, res) => void handler(req, res));
await new Promise((r) => hosted.listen(0, "127.0.0.1", r));
const base = `http://127.0.0.1:${hosted.address().port}`;

let passed = 0;
const check = async (label, fn) => {
  await fn();
  passed += 1;
  console.log(`ok - ${label}`);
};

async function connect() {
  const client = new Client({ name: "hosted-test", version: "1.0.0" });
  await client.connect(new StreamableHTTPClientTransport(new URL(`${base}/mcp`)));
  return client;
}

try {
  await check("exposes exactly the shop tools; no completion, cart, order, agent or seller tool", async () => {
    const client = await connect();
    const names = (await client.listTools()).tools.map((t) => t.name).sort();
    assert.deepEqual(names, [...HOSTED_TOOLS].sort());
    for (const banned of ["complete_checkout", "cancel_checkout", "create_cart", "get_order", "run_agent"]) {
      assert.ok(!names.includes(banned), banned);
    }
    await client.close();
  });

  await check("search reaches the shop without the platform key", async () => {
    const client = await connect();
    const result = await client.callTool({ name: "search_catalog", arguments: { request: { query: "chai" } } });
    assert.notEqual(result.isError, true);
    assert.equal(result.structuredContent.products[0].title, "Synthetic Chai");
    const call = calls.at(-1);
    assert.equal(call.path, "/ucp/catalog/search");
    assert.equal(call.headers.authorization, undefined);
    await client.close();
  });

  await check("checkout uses the operator key and the locked platform profile, whatever the caller sends", async () => {
    const client = await connect();
    const result = await client.callTool({
      name: "create_checkout",
      arguments: {
        meta: { "ucp-agent": { profile: "https://attacker.example/profile.json" } },
        checkout: { line_items: [{ item: { id: "v1" }, quantity: 1 }] },
      },
    });
    assert.notEqual(result.isError, true);
    const call = calls.at(-1);
    assert.equal(call.path, "/ucp/checkout-sessions");
    assert.equal(call.headers.authorization, `Bearer ${KEY}`);
    assert.equal(call.headers["ucp-agent"], `profile="${AGENT}"`);
    assert.ok(!JSON.stringify(result).includes(KEY), "the key is never returned");
    await client.close();
  });

  await check("health, unknown host, wrong method, unknown path and oversized body", async () => {
    assert.equal((await fetch(`${base}/healthz`)).status, 200);
    const wrongHost = await new Promise((resolveStatus) => {
      const req = http.request(`${base}/mcp`, { method: "POST", headers: { Host: "evil.example", "content-type": "application/json" } }, (r) => resolveStatus(r.statusCode));
      req.end("{}");
    });
    assert.equal(wrongHost, 403);
    const get = await fetch(`${base}/mcp`);
    assert.equal(get.status, 405);
    assert.equal(get.headers.get("allow"), "POST");
    assert.equal((await fetch(`${base}/other`, { method: "POST", body: "{}" })).status, 404);
    const big = await fetch(`${base}/mcp`, { method: "POST", headers: { "content-type": "application/json" }, body: "x".repeat(70 * 1024) });
    assert.equal(big.status, 413);
  });

  await check("rate limiter: per client, global, and a fresh window after a minute", async () => {
    let t = 0;
    const limit = createRateLimiter(2, 3, () => t);
    assert.equal(limit("a"), 0);
    assert.equal(limit("a"), 0);
    assert.ok(limit("a") > 0, "per-client cap");
    assert.equal(limit("b"), 0);
    assert.ok(limit("c") > 0, "global cap");
    t += 60_000;
    assert.equal(limit("a"), 0, "new window");
  });

  await check("a limited client gets 429 with Retry-After from the hosted endpoint", async () => {
    const tight = createHostedHandler({
      profileUrl: `${merchantBase}/.well-known/ucp`, agentProfile: AGENT, apiKey: KEY,
      allowedHosts: new Set(["127.0.0.1"]), perClientPerMinute: 1, globalPerMinute: 10, version: "test", log: () => {},
    });
    const srv = http.createServer((req, res) => void tight(req, res));
    await new Promise((r) => srv.listen(0, "127.0.0.1", r));
    const url = `http://127.0.0.1:${srv.address().port}/mcp`;
    const init = { jsonrpc: "2.0", id: 1, method: "tools/list", params: {} };
    const opts = { method: "POST", headers: { "content-type": "application/json", accept: "application/json, text/event-stream" }, body: JSON.stringify(init) };
    assert.notEqual((await fetch(url, opts)).status, 429);
    const second = await fetch(url, opts);
    assert.equal(second.status, 429);
    assert.ok(Number(second.headers.get("retry-after")) > 0);
    await new Promise((r) => srv.close(r));
  });
} finally {
  await new Promise((r) => hosted.close(r));
  await new Promise((r) => merchant.close(r));
}
console.log(`hosted http passed (${passed} checks)`);
