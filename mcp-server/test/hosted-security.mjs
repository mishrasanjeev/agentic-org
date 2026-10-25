// SPDX-License-Identifier: Apache-2.0
import assert from "node:assert/strict";
import http from "node:http";
import { test } from "node:test";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import { configFromEnv, createHostedHandler, createRateLimiter } from "../dist/http.js";

const ACCESS = "synthetic-caller-token-0123456789abcdef";
const config = {
  profileUrl: "http://127.0.0.1:9/.well-known/ucp",
  agentProfile: "https://platform.example/profile.json",
  apiKey: "synthetic-platform-key",
  accessToken: ACCESS,
  allowedHosts: new Set(["127.0.0.1"]),
  perClientPerMinute: 1,
  globalPerMinute: 20,
  version: "test",
  log: () => {},
};

async function withServer(settings, check) {
  const handler = createHostedHandler({ ...config, ...settings });
  const server = http.createServer((req, res) => void handler(req, res));
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  try { await check(base); }
  finally { await new Promise(resolve => server.close(resolve)); }
}

async function post(base, headers = {}) {
  const response = await fetch(`${base}/mcp`, {
    method: "POST",
    headers: { "content-type": "application/json", accept: "application/json, text/event-stream", ...headers },
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/list", params: {} }),
  });
  await response.text();
  return response;
}

test("anonymous, wrong and upstream platform credentials cannot reach merchant discovery", async () => {
  let discoveries = 0;
  const merchant = http.createServer((_req, res) => {
    discoveries++;
    res.writeHead(503);
    res.end("{}");
  });
  await new Promise(resolve => merchant.listen(0, "127.0.0.1", resolve));
  try {
    await withServer({ profileUrl: `http://127.0.0.1:${merchant.address().port}/.well-known/ucp` }, async base => {
      for (const authorization of [undefined, "Bearer wrong", `Bearer ${config.apiKey}`, "Basic invalid"]) {
        const response = await post(base, authorization ? { authorization } : {});
        assert.equal(response.status, 401);
        assert.match(response.headers.get("www-authenticate"), /^Bearer /);
      }
      assert.equal(discoveries, 0);
      assert.equal((await fetch(`${base}/healthz`)).status, 200);
      assert.equal((await post(base, { authorization: `Bearer ${ACCESS}` })).status, 503);
      assert.equal(discoveries, 1);
    });
  } finally { await new Promise(resolve => merchant.close(resolve)); }
});

test("untrusted forwarding prefixes cannot evade the client limit", async () => {
  for (const trustedProxyHops of [0, 1, 2]) {
    await withServer({ trustedProxyHops }, async base => {
      const suffix = trustedProxyHops === 2 ? ", 192.0.2.20" : "";
      const headers = prefix => ({ authorization: `Bearer ${ACCESS}`, "x-forwarded-for": `${prefix}, 198.51.100.10${suffix}` });
      assert.equal((await post(base, headers("203.0.113.1"))).status, 503);
      const blocked = await post(base, headers("203.0.113.2"));
      assert.equal(blocked.status, 429, `proxy hops ${trustedProxyHops}`);
      assert.ok(Number(blocked.headers.get("retry-after")) > 0);
      const other = await post(base, {
        authorization: `Bearer ${ACCESS}`,
        "x-forwarded-for": `203.0.113.3, 198.51.100.11${suffix}`,
      });
      assert.equal(other.status, trustedProxyHops ? 503 : 429);
    });
  }
});

test("invalid proxy suffixes share the socket budget and IPv6 clients remain distinct", async () => {
  for (const trustedProxyHops of [1, 2]) {
    await withServer({ trustedProxyHops }, async base => {
      const headers = { authorization: `Bearer ${ACCESS}` };
      assert.equal((await post(base, headers)).status, 503);
      for (const forwarded of ["invalid", "198.51.100.10, invalid"]) {
        assert.equal((await post(base, { ...headers, "x-forwarded-for": forwarded })).status, 429);
      }
    });
  }
  await withServer({ trustedProxyHops: 2 }, async base => {
    const headers = address => ({ authorization: `Bearer ${ACCESS}`, "x-forwarded-for": `untrusted, ${address}, 2001:db8::20` });
    assert.equal((await post(base, headers("2001:db8::a"))).status, 503);
    assert.equal((await post(base, headers("2001:db8::a"))).status, 429);
    assert.equal((await post(base, headers("2001:db8::b"))).status, 503);
  });
});

test("invalid limits and unsafe caller credentials fail closed at startup", () => {
  for (const invalid of [0, -1, NaN, Infinity, 1.5, Number.MAX_SAFE_INTEGER + 1]) {
    assert.throws(() => createRateLimiter(invalid, 10), /positive integer/);
    assert.throws(() => createRateLimiter(10, invalid), /positive integer/);
  }
  assert.throws(() => createHostedHandler({ ...config, accessToken: "" }), /MCP_ACCESS_TOKEN/);
  assert.throws(() => createHostedHandler({ ...config, accessToken: "short" }), /MCP_ACCESS_TOKEN/);
  assert.throws(() => createHostedHandler({ ...config, accessToken: "invalid token".repeat(4) }), /MCP_ACCESS_TOKEN/);
  assert.throws(() => createHostedHandler({ ...config, accessToken: ACCESS, apiKey: ACCESS }), /MCP_ACCESS_TOKEN/);
  for (const trustedProxyHops of [-1, 0.5, NaN, Infinity, 17]) {
    assert.throws(() => createHostedHandler({ ...config, trustedProxyHops }), /non-negative integer/);
  }
  const before = { ...process.env };
  try {
    Object.assign(process.env, {
      AGENTICORG_UCP_PROFILE_URL: config.profileUrl,
      AGENTICORG_UCP_AGENT_PROFILE: config.agentProfile,
      AGENTICORG_API_KEY: config.apiKey,
      MCP_ACCESS_TOKEN: ACCESS,
      MCP_ALLOWED_HOSTS: "127.0.0.1",
    });
    for (const name of ["MCP_RATE_PER_CLIENT_PER_MINUTE", "MCP_RATE_GLOBAL_PER_MINUTE"]) {
      for (const invalid of ["", "NaN", "0", "-1", "1.5", "Infinity"]) {
        process.env[name] = invalid;
        assert.throws(configFromEnv, /positive integer/);
      }
      delete process.env[name];
    }
    process.env.MCP_TRUSTED_PROXY_HOPS = "-1";
    assert.throws(configFromEnv, /non-negative integer/);
  } finally {
    for (const name of Object.keys(process.env)) if (!(name in before)) delete process.env[name];
    Object.assign(process.env, before);
  }
});

test("anonymous mode is an explicit opt-in and still applies every other guard", async () => {
  // Opt-in: no token and the flag serves without credentials; a token is then not required.
  await withServer({ accessToken: undefined, allowAnonymous: true }, async base => {
    assert.equal((await post(base)).status, 503); // reaches the (stub-less) shop: no 401
    assert.equal((await post(base)).status, 429); // per-client limit still applies
    const wrongHost = await new Promise(resolveStatus => {
      const req = http.request(`${base}/mcp`, { method: "POST", headers: { Host: "evil.example", "content-type": "application/json" } }, r => resolveStatus(r.statusCode));
      req.end("{}");
    });
    assert.equal(wrongHost, 403); // host allowlist still applies
  });
  // Never both, never neither.
  assert.throws(() => createHostedHandler({ ...config, allowAnonymous: true }), /not both/);
  assert.throws(() => createHostedHandler({ ...config, accessToken: undefined }), /MCP_ACCESS_TOKEN/);
  assert.throws(() => createHostedHandler({ ...config, accessToken: undefined, allowAnonymous: false }), /MCP_ACCESS_TOKEN/);
  const before = { ...process.env };
  try {
    Object.assign(process.env, {
      AGENTICORG_UCP_PROFILE_URL: config.profileUrl,
      AGENTICORG_UCP_AGENT_PROFILE: config.agentProfile,
      AGENTICORG_API_KEY: config.apiKey,
      MCP_ALLOWED_HOSTS: "127.0.0.1",
    });
    delete process.env.MCP_ACCESS_TOKEN;
    delete process.env.MCP_ALLOW_ANONYMOUS;
    assert.throws(configFromEnv, /MCP_ACCESS_TOKEN is required/);
    process.env.MCP_ALLOW_ANONYMOUS = "yes";
    assert.throws(configFromEnv, /true or false/);
    process.env.MCP_ALLOW_ANONYMOUS = "true";
    const anonymousConfig = configFromEnv();
    assert.equal(anonymousConfig.allowAnonymous, true);
    assert.equal(anonymousConfig.accessToken, undefined);
    process.env.MCP_ACCESS_TOKEN = ACCESS;
    assert.throws(configFromEnv, /not both/);
  } finally {
    for (const name of Object.keys(process.env)) if (!(name in before)) delete process.env[name];
    Object.assign(process.env, before);
  }
});

test("anonymous clients can use only the shop tools with the operator's fixed binding", async () => {
  const calls = [];
  const product = { id: "p1", title: "Synthetic Chai", variants: [{ id: "v1", price: { amount: 44900, currency: "INR" } }] };
  const privateMarker = "synthetic-merchant-internal-only";
  let merchantBase;
  let checkout;
  const merchant = http.createServer((req, res) => {
    let raw = "";
    req.setEncoding("utf8");
    req.on("data", chunk => { raw += chunk; });
    req.on("end", () => {
      const json = (body, status = 200) => {
        res.writeHead(status, { "content-type": "application/json" });
        res.end(JSON.stringify(body));
      };
      const path = req.url.split("?")[0];
      const body = raw ? JSON.parse(raw) : undefined;
      calls.push({ method: req.method, path, body, headers: req.headers });
      if (req.method === "GET" && path === "/.well-known/ucp") {
        const capabilities = ["catalog.search", "catalog.lookup", "checkout", "cart", "order"];
        return json({ ucp: {
          version: "2026-04-08",
          services: { "dev.ucp.shopping": [{ version: "2026-04-08", transport: "rest", endpoint: `${merchantBase}/ucp` }] },
          capabilities: Object.fromEntries(capabilities.map(name => [`dev.ucp.shopping.${name}`, [{ version: "2026-04-08" }]])),
        } });
      }
      if (req.method === "POST" && ["/ucp/catalog/search", "/ucp/catalog/lookup"].includes(path)) {
        return json({ products: [product] });
      }
      const create = req.method === "POST" && path === "/ucp/checkout-sessions";
      const update = req.method === "PUT" && path === "/ucp/checkout-sessions/chk_anonymous";
      if (create || (update && checkout)) {
        checkout = {
          id: "chk_anonymous", status: "incomplete", currency: "INR", line_items: body.line_items,
          totals: [{ type: "total", amount: 44900 * body.line_items[0].quantity }], messages: [],
        };
        return json({ ...checkout, "ai.agenticorg.commerce": { internal: privateMarker } }, create ? 201 : 200);
      }
      if (req.method === "GET" && path === "/ucp/checkout-sessions/chk_anonymous" && checkout) {
        return json({ ...checkout, "ai.agenticorg.commerce": { internal: privateMarker } });
      }
      return json({ code: "not_found", content: "Not found." }, 404);
    });
  });
  await new Promise(resolve => merchant.listen(0, "127.0.0.1", resolve));
  merchantBase = `http://127.0.0.1:${merchant.address().port}`;
  try {
    await withServer({
      profileUrl: `${merchantBase}/.well-known/ucp`, accessToken: undefined, allowAnonymous: true,
      perClientPerMinute: 100, globalPerMinute: 100,
    }, async base => {
      const client = new Client({ name: "anonymous-hosted-test", version: "1.0.0" });
      try {
        await client.connect(new StreamableHTTPClientTransport(new URL(`${base}/mcp`)));
        assert.deepEqual((await client.listTools()).tools.map(tool => tool.name).sort(), [
          "create_checkout", "get_checkout", "lookup_catalog", "search_catalog", "update_checkout",
        ]);
        for (const [name, request] of [
          ["search_catalog", { query: "chai" }], ["lookup_catalog", { ids: ["p1"] }],
        ]) {
          const result = await client.callTool({ name, arguments: { request } });
          assert.notEqual(result.isError, true);
          assert.deepEqual(result.structuredContent, { products: [product] });
          assert.deepEqual(calls.at(-1).body, request);
          assert.equal(calls.at(-1).headers.authorization, undefined);
        }
        const meta = { "ucp-agent": { profile: "http://127.0.0.1:9/caller-profile.json" } };
        const steps = [
          { name: "create_checkout", arguments: { meta, checkout: { line_items: [{ item: { id: "v1" }, quantity: 1 }] } } },
          { name: "get_checkout", arguments: { meta, id: "chk_anonymous" } },
          { name: "update_checkout", arguments: { meta, id: "chk_anonymous", checkout: { line_items: [{ item: { id: "v1" }, quantity: 2 }] } } },
        ];
        for (const [index, step] of steps.entries()) {
          const result = await client.callTool(step);
          assert.notEqual(result.isError, true);
          const quantity = index === 2 ? 2 : 1;
          const expected = {
            id: "chk_anonymous", status: "incomplete", currency: "INR",
            line_items: [{ item: { id: "v1" }, quantity }],
            totals: [{ type: "total", amount: 44900 * quantity }], messages: [],
          };
          assert.deepEqual(result.structuredContent, expected);
          assert.deepEqual(JSON.parse(result.content[0].text), expected);
          assert.equal(calls.at(-1).headers.authorization, `Bearer ${config.apiKey}`);
          assert.equal(calls.at(-1).headers["ucp-agent"], `profile="${config.agentProfile}"`);
          assert.ok(!JSON.stringify(result).includes(config.apiKey));
          assert.ok(!JSON.stringify(result).includes(privateMarker));
        }
        assert.deepEqual(calls.map(({ method, path }) => [method, path]), [
          ["GET", "/.well-known/ucp"], ["POST", "/ucp/catalog/search"], ["POST", "/ucp/catalog/lookup"],
          ["POST", "/ucp/checkout-sessions"], ["GET", "/ucp/checkout-sessions/chk_anonymous"],
          ["PUT", "/ucp/checkout-sessions/chk_anonymous"],
        ]);
        assert.ok(calls.every(call => call.headers.host === new URL(merchantBase).host));
        assert.equal(calls[0].headers.authorization, undefined);
      } finally { await client.close(); }
    });
  } finally { await new Promise(resolve => merchant.close(resolve)); }
});
