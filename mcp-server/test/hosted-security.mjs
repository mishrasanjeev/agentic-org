// SPDX-License-Identifier: Apache-2.0
import assert from "node:assert/strict";
import http from "node:http";
import { test } from "node:test";
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
