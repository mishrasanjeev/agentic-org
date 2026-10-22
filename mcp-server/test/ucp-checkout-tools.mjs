// SPDX-License-Identifier: Apache-2.0
import assert from "node:assert/strict";
import http from "node:http";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

// Stub merchant: /.well-known/ucp profiles (checkout on, checkout off, dual release,
// foreign endpoint) and the UCP REST binding under /ucp. Synthetic data only.

const calls = [];
const AGENT = "https://platform.example/profiles/shopper-01.json";
const OVERRIDE_AGENT = "https://platform.example/profiles/other.json";
const COMPLETE_KEY = "6f1c2a8e-3b7d-4c5e-9a10-2b3c4d5e6f70";
const CANCEL_KEY = "0b1c2d3e-4f50-4617-8899-aabbccddeeff";

let base = "";

const capability = { version: "2026-04-08", spec: "https://ucp.dev/specification/checkout", schema: "https://ucp.dev/schemas/shopping/checkout.json" };
function profile(capabilities, endpoint = `${base}/ucp`, version = "2026-04-08") {
  const caps = {};
  for (const name of capabilities) caps[name] = [{ ...capability, version }];
  return {
    ucp: {
      version,
      services: { "dev.ucp.shopping": [{ version, transport: "rest", endpoint, spec: "https://ucp.dev/specification/overview", schema: "https://ucp.dev/services/shopping/rest.openapi.json" }] },
      capabilities: caps,
      payment_handlers: {},
    },
    signing_keys: [],
  };
}
const ALL = ["dev.ucp.shopping.checkout", "dev.ucp.shopping.cart", "dev.ucp.shopping.order", "dev.ucp.shopping.catalog.search"];

const checkoutBody = (status, extra = {}) => ({
  ucp: { version: "2026-04-08", status: "success", capabilities: { "dev.ucp.shopping.checkout": [{ version: "2026-04-08" }] } },
  id: "cs_1",
  status,
  currency: "USD",
  line_items: [{ id: "li_1", item: { id: "TOTE-1", title: "Canvas Tote", price: 1299 }, quantity: 1 }],
  totals: [
    { type: "subtotal", amount: 1299 },
    { type: "total", amount: 1299 },
  ],
  messages: [],
  links: [],
  ...extra,
});

const api = http.createServer((req, res) => {
  let raw = "";
  req.setEncoding("utf8");
  req.on("data", (chunk) => {
    raw += chunk;
  });
  req.on("end", () => {
    const body = raw ? JSON.parse(raw) : undefined;
    const path = req.url?.split("?")[0];
    const json = (payload, status = 200) => {
      res.writeHead(status, { "content-type": "application/json" });
      res.end(JSON.stringify(payload));
    };
    if (req.method === "GET" && path === "/.well-known/ucp") return json(profile(ALL));
    if (req.method === "GET" && path === "/off/.well-known/ucp") return json(profile(["dev.ucp.shopping.catalog.search"]));
    if (req.method === "GET" && path === "/foreign/.well-known/ucp") return json(profile(ALL, "https://merchant.example/ucp"));
    if (req.method === "GET" && path === "/dual/.well-known/ucp") {
      const root = profile(["dev.ucp.shopping.checkout"], `${base}/ucp`, "2026-08-25");
      root.ucp.supported_versions = { "2026-04-08": `${base}/dual/.well-known/ucp/2026-04-08` };
      return json(root);
    }
    if (req.method === "GET" && path === "/dual/.well-known/ucp/2026-04-08") return json(profile(["dev.ucp.shopping.checkout"]));

    calls.push({
      method: req.method,
      path,
      body,
      authorization: req.headers.authorization,
      ucpAgent: req.headers["ucp-agent"],
      idempotencyKey: req.headers["idempotency-key"],
      requestId: req.headers["request-id"],
    });
    if (req.method === "POST" && path === "/ucp/checkout-sessions") return json(checkoutBody("ready_for_complete"), 201);
    if (req.method === "GET" && path === "/ucp/checkout-sessions/cs_1") return json(checkoutBody("ready_for_complete"));
    if (req.method === "PUT" && path === "/ucp/checkout-sessions/cs_1") return json(checkoutBody("ready_for_complete"));
    if (req.method === "POST" && path === "/ucp/checkout-sessions/cs_1/complete") {
      const vendor = body?.["ai.agenticorg.commerce"] ?? {};
      if (vendor.decide_only === true) {
        return json(
          checkoutBody("ready_for_complete", {
            messages: [{ type: "info", code: "decision_issued", content: "A sealed allow decision was issued." }],
            "ai.agenticorg.commerce": {
              decision: {
                decision_id: "dec_allow_1",
                action: "session_complete",
                outcome: "allow",
                rules_evaluated: ["internal_rule_a", "internal_rule_b"],
                failing_rule: null,
                reason: "internal reasoning text",
                evidence_hashes: ["sha256:internal"],
                payload_hash: "sha256:payload",
                buyer_authority_source: "caller_asserted",
                policy_version: "internal-policy-v1",
                decided_at: "2026-10-20T00:00:00Z",
                expires_at: "2026-10-20T00:05:00Z",
              },
            },
          }),
        );
      }
      if (vendor.decision_id === "dec_allow_1") {
        return json(checkoutBody("completed", { order: { id: "ord_1", permalink_url: "https://merchant.example/orders/ord_1" } }));
      }
      return json(
        checkoutBody("ready_for_complete", {
          messages: [{ type: "error", code: "decision_payload_mismatch", content: "The decision does not match.", severity: "recoverable" }],
        }),
      );
    }
    if (req.method === "POST" && path === "/ucp/checkout-sessions/cs_1/cancel") return json(checkoutBody("canceled"));
    if (req.method === "POST" && path === "/ucp/checkout-sessions/cs_done/cancel") {
      return json(
        {
          ucp: { version: "2026-04-08", status: "error" },
          messages: [{ type: "error", code: "invalid_state", content: "The checkout is already completed.", severity: "unrecoverable" }],
        },
        409,
      );
    }
    if (req.method === "GET" && path === "/ucp/checkout-sessions/cs_internal") {
      // A framework-shaped error with internal details must not reach the tool result.
      return json({ detail: { blockers: [{ rule: "internal_blocker", evidence: "secret-internal-state" }] } }, 500);
    }
    if (req.method === "POST" && path === "/ucp/checkout-sessions/cs_profile/cancel") {
      return json({ code: "profile_unreachable", content: "The platform profile could not be fetched." }, 424);
    }
    if (req.method === "POST" && path === "/ucp/carts") return json({ ucp: { version: "2026-04-08", status: "success" }, id: "cart_1", line_items: [] }, 201);
    if (req.method === "GET" && path === "/ucp/carts/cart_1") return json({ ucp: { version: "2026-04-08", status: "success" }, id: "cart_1", line_items: [] });
    if (req.method === "PUT" && path === "/ucp/carts/cart_1") return json({ ucp: { version: "2026-04-08", status: "success" }, id: "cart_1", line_items: [] });
    if (req.method === "POST" && path === "/ucp/carts/cart_1/cancel") return json({ ucp: { version: "2026-04-08", status: "success" }, id: "cart_1", status: "canceled" });
    if (req.method === "POST" && path === "/ucp/catalog/search") {
      return json({
        ucp: { version: "2026-04-08", capabilities: { "dev.ucp.shopping.catalog.search": [{ version: "2026-04-08" }] } },
        products: [{ id: "p_1", title: "Synthetic Masala Chai", variants: [{ id: "v_1", sku: "SYN-CHAI-250", price: { amount: 44900, currency: "INR" } }] }],
        pagination: { has_next_page: false, total_count: 1 },
      });
    }
    if (req.method === "GET" && path === "/ucp/orders/ord_1") return json({ ucp: { version: "2026-04-08", status: "success" }, id: "ord_1", checkout_id: "cs_1" });
    return json({ detail: "Not Found" }, 404);
  });
});

await new Promise((resolve) => api.listen(0, "127.0.0.1", resolve));
base = `http://127.0.0.1:${api.address().port}`;

async function connect(env) {
  const transport = new StdioClientTransport({
    command: process.execPath,
    args: ["dist/index.js"],
    env: { ...process.env, AGENTICORG_API_KEY: "mcp-test-key", AGENTICORG_BASE_URL: base, ...env },
    stderr: "pipe",
  });
  const client = new Client({ name: "agenticorg-ucp-tools-test", version: "1.0.0" });
  await client.connect(transport);
  return client;
}

const UCP_TOOLS = [
  "cancel_cart",
  "cancel_checkout",
  "complete_checkout",
  "create_cart",
  "create_checkout",
  "get_cart",
  "get_checkout",
  "get_order",
  "update_cart",
  "update_checkout",
];
const ucpNames = (tools) => tools.tools.map((t) => t.name).filter((n) => UCP_TOOLS.includes(n)).sort();
const callsTo = (suffix) => calls.filter((c) => c.path.endsWith(suffix));

let passed = 0;
const check = async (label, fn) => {
  await fn();
  passed += 1;
  console.log(`ok - ${label}`);
};

try {
  // --- gating -------------------------------------------------------------------------------
  for (const [label, env] of [
    ["no profile configured", {}],
    ["checkout not advertised (flag off)", { AGENTICORG_UCP_PROFILE_URL: `${base}/off/.well-known/ucp` }],
    ["REST endpoint on another origin", { AGENTICORG_UCP_PROFILE_URL: `${base}/foreign/.well-known/ucp` }],
    ["profile URL not a /.well-known/ucp", { AGENTICORG_UCP_PROFILE_URL: `${base}/ucp` }],
    ["profile unreachable", { AGENTICORG_UCP_PROFILE_URL: "http://127.0.0.1:1/.well-known/ucp" }],
    ["non-https profile host", { AGENTICORG_UCP_PROFILE_URL: "http://merchant.example/.well-known/ucp" }],
  ]) {
    await check(`no UCP tools when ${label}`, async () => {
      const client = await connect(env);
      try {
        assert.deepEqual(ucpNames(await client.listTools()), []);
        const seller = (await client.listTools()).tools.map((t) => t.name);
        assert.ok(seller.includes("seller.list_products"), "existing tools stay registered");
      } finally {
        await client.close();
      }
    });
  }

  await check("dual release profile follows the 2026-04-08 leaf", async () => {
    const client = await connect({ AGENTICORG_UCP_PROFILE_URL: `${base}/dual/.well-known/ucp`, AGENTICORG_UCP_AGENT_PROFILE: AGENT });
    try {
      assert.deepEqual(ucpNames(await client.listTools()), [
        "cancel_checkout",
        "complete_checkout",
        "create_checkout",
        "get_checkout",
        "update_checkout",
      ]);
    } finally {
      await client.close();
    }
  });

  await check("commerce-only mode keeps UCP tools when the profile serves them", async () => {
    const client = await connect({
      AGENTICORG_UCP_PROFILE_URL: `${base}/.well-known/ucp`,
      AGENTICORG_MCP_COMMERCE_ONLY: "true",
    });
    try {
      assert.deepEqual(ucpNames(await client.listTools()), UCP_TOOLS);
      assert.ok(!(await client.listTools()).tools.some((t) => t.name === "run_agent"));
    } finally {
      await client.close();
    }
  });

  // --- served -------------------------------------------------------------------------------
  const client = await connect({
    AGENTICORG_UCP_PROFILE_URL: `${base}/.well-known/ucp`,
    AGENTICORG_UCP_AGENT_PROFILE: AGENT,
  });
  try {
    const tools = await client.listTools();

    await check("all binding tools registered with annotations", async () => {
      assert.deepEqual(ucpNames(tools), UCP_TOOLS);
      const ann = Object.fromEntries(tools.tools.map((t) => [t.name, t.annotations]));
      for (const name of ["get_checkout", "get_cart", "get_order"]) {
        assert.equal(ann[name].readOnlyHint, true, name);
        assert.equal(ann[name].destructiveHint, false, name);
        assert.equal(ann[name].idempotentHint, true, name);
      }
      for (const name of ["create_checkout", "create_cart"]) {
        assert.equal(ann[name].readOnlyHint, false, name);
        assert.equal(ann[name].destructiveHint, false, name);
        assert.equal(ann[name].idempotentHint, false, name);
      }
      for (const name of ["update_checkout", "update_cart", "cancel_checkout", "cancel_cart"]) {
        assert.equal(ann[name].readOnlyHint, false, name);
        assert.equal(ann[name].destructiveHint, true, name);
        assert.equal(ann[name].idempotentHint, true, name);
      }
      assert.equal(ann.complete_checkout.destructiveHint, true);
      assert.equal(ann.complete_checkout.idempotentHint, false);
      assert.equal(ann.complete_checkout.readOnlyHint, false);
      for (const name of UCP_TOOLS) assert.equal(ann[name].openWorldHint, true, name);
      const complete = tools.tools.find((t) => t.name === "complete_checkout");
      assert.ok(complete.inputSchema.required.includes("meta"));
      assert.ok(complete.inputSchema.properties.meta.required.includes("idempotency-key"));
    });

    await check("create_checkout posts the checkout with UCP headers", async () => {
      const result = await client.callTool({
        name: "create_checkout",
        arguments: { checkout: { line_items: [{ item: { id: "TOTE-1" }, quantity: 1 }], currency: "USD" } },
      });
      assert.notEqual(result.isError, true);
      assert.equal(result.structuredContent.id, "cs_1");
      assert.equal(JSON.parse(result.content[0].text).status, "ready_for_complete");
      const call = callsTo("/ucp/checkout-sessions").at(-1);
      assert.equal(call.method, "POST");
      assert.equal(call.ucpAgent, `profile="${AGENT}"`);
      assert.equal(call.authorization, "Bearer mcp-test-key");
      assert.match(call.idempotencyKey, /^[0-9a-f-]{36}$/);
      assert.match(call.requestId, /^[0-9a-f-]{36}$/);
      assert.deepEqual(call.body.line_items, [{ item: { id: "TOTE-1" }, quantity: 1 }]);
    });

    await check("get and update checkout, meta overrides the platform profile", async () => {
      const got = await client.callTool({
        name: "get_checkout",
        arguments: { id: "cs_1", meta: { "ucp-agent": { profile: OVERRIDE_AGENT } } },
      });
      assert.equal(got.structuredContent.status, "ready_for_complete");
      const getCall = calls.filter((c) => c.method === "GET" && c.path === "/ucp/checkout-sessions/cs_1").at(-1);
      assert.equal(getCall.ucpAgent, `profile="${OVERRIDE_AGENT}"`);
      assert.equal(getCall.idempotencyKey, undefined);
      const key = "1a2b3c4d-1111-4222-8333-444455556666";
      const updated = await client.callTool({
        name: "update_checkout",
        arguments: { id: "cs_1", meta: { "idempotency-key": key }, checkout: { line_items: [] } },
      });
      assert.notEqual(updated.isError, true);
      const putCall = calls.filter((c) => c.method === "PUT").at(-1);
      assert.equal(putCall.idempotencyKey, key);
    });

    await check("complete_checkout without a decision is refused before any API call", async () => {
      const before = callsTo("/complete").length;
      const result = await client.callTool({
        name: "complete_checkout",
        arguments: { id: "cs_1", meta: { "idempotency-key": COMPLETE_KEY }, checkout: { payment: { instruments: [] } } },
      });
      assert.equal(result.isError, true);
      assert.equal(result.structuredContent.error.code, "decision_required");
      assert.equal(callsTo("/complete").length, before);
    });

    await check("complete_checkout with both decide_only and decision_id is refused", async () => {
      const before = callsTo("/complete").length;
      const both = await client.callTool({
        name: "complete_checkout",
        arguments: {
          id: "cs_1",
          meta: { "idempotency-key": COMPLETE_KEY },
          checkout: {},
          decide_only: true,
          decision_id: "dec_allow_1",
        },
      });
      assert.equal(both.isError, true);
      const conflicting = await client.callTool({
        name: "complete_checkout",
        arguments: {
          id: "cs_1",
          meta: { "idempotency-key": COMPLETE_KEY },
          checkout: { "ai.agenticorg.commerce": { decision_id: "dec_other" } },
          decision_id: "dec_allow_1",
        },
      });
      assert.equal(conflicting.isError, true);
      const falseOnly = await client.callTool({
        name: "complete_checkout",
        arguments: { id: "cs_1", meta: { "idempotency-key": COMPLETE_KEY }, checkout: { "ai.agenticorg.commerce": { decide_only: false } } },
      });
      assert.equal(falseOnly.structuredContent.error.code, "decision_required");
      assert.equal(callsTo("/complete").length, before);
    });

    await check("complete_checkout without an idempotency-key is rejected by the schema", async () => {
      const before = callsTo("/complete").length;
      let refused = false;
      try {
        const result = await client.callTool({
          name: "complete_checkout",
          arguments: { id: "cs_1", checkout: {}, decide_only: true },
        });
        refused = result.isError === true;
      } catch {
        refused = true;
      }
      assert.ok(refused);
      assert.equal(callsTo("/complete").length, before);
    });

    let decisionId = "";
    await check("decide_only returns a decision id and hides internal decision fields", async () => {
      const result = await client.callTool({
        name: "complete_checkout",
        arguments: {
          id: "cs_1",
          meta: { "idempotency-key": "2a2b3c4d-1111-4222-8333-444455556666" },
          checkout: {
            payment: { instruments: [{ id: "pi_1", handler_id: "mock", type: "card" }] },
            "ai.agenticorg.commerce": { buyer_authority: { mandate_ref: "mandate_demo" } },
          },
          decide_only: true,
        },
      });
      assert.notEqual(result.isError, true);
      const call = callsTo("/complete").at(-1);
      assert.deepEqual(call.body["ai.agenticorg.commerce"], {
        buyer_authority: { mandate_ref: "mandate_demo" },
        decide_only: true,
      });
      const decision = result.structuredContent["ai.agenticorg.commerce"].decision;
      assert.equal(decision.outcome, "allow");
      decisionId = decision.decision_id;
      assert.equal(decisionId, "dec_allow_1");
      for (const hidden of ["rules_evaluated", "reason", "evidence_hashes", "policy_version", "buyer_authority_source"]) {
        assert.ok(!(hidden in decision), hidden);
      }
      assert.doesNotMatch(result.content[0].text, /internal reasoning|internal_rule_a|internal-policy/);
      assert.equal(result.structuredContent.status, "ready_for_complete");
    });

    await check("complete_checkout with the decision id places the order", async () => {
      const result = await client.callTool({
        name: "complete_checkout",
        arguments: {
          id: "cs_1",
          meta: { "idempotency-key": COMPLETE_KEY },
          checkout: { payment: { instruments: [{ id: "pi_1", handler_id: "mock", type: "card" }] } },
          decision_id: decisionId,
        },
      });
      assert.notEqual(result.isError, true);
      assert.equal(result.structuredContent.status, "completed");
      assert.equal(result.structuredContent.order.id, "ord_1");
      const call = callsTo("/complete").at(-1);
      assert.equal(call.idempotencyKey, COMPLETE_KEY);
      assert.deepEqual(call.body["ai.agenticorg.commerce"], { decision_id: "dec_allow_1" });
      assert.ok(!("decide_only" in call.body["ai.agenticorg.commerce"]));
    });

    await check("decision id inside the checkout vendor member is accepted", async () => {
      const result = await client.callTool({
        name: "complete_checkout",
        arguments: {
          id: "cs_1",
          meta: { "idempotency-key": COMPLETE_KEY },
          checkout: { "ai.agenticorg.commerce": { decision_id: "dec_stale" } },
        },
      });
      // A refused decision is a business outcome (HTTP 200 with messages), not a tool error.
      assert.notEqual(result.isError, true);
      assert.equal(result.structuredContent.messages[0].code, "decision_payload_mismatch");
    });

    await check("cancel_checkout and errors map to MCP tool errors", async () => {
      const ok = await client.callTool({
        name: "cancel_checkout",
        arguments: { id: "cs_1", meta: { "idempotency-key": CANCEL_KEY } },
      });
      assert.equal(ok.structuredContent.status, "canceled");
      assert.deepEqual(callsTo("/ucp/checkout-sessions/cs_1/cancel").at(-1).body, {});

      const conflict = await client.callTool({
        name: "cancel_checkout",
        arguments: { id: "cs_done", meta: { "idempotency-key": CANCEL_KEY } },
      });
      assert.equal(conflict.isError, true);
      assert.deepEqual(conflict.structuredContent.error, {
        status: 409,
        code: "invalid_state",
        content: "The checkout is already completed.",
      });

      const discovery = await client.callTool({
        name: "cancel_checkout",
        arguments: { id: "cs_profile", meta: { "idempotency-key": CANCEL_KEY } },
      });
      assert.equal(discovery.isError, true);
      assert.equal(discovery.structuredContent.error.code, "profile_unreachable");

      const internal = await client.callTool({ name: "get_checkout", arguments: { id: "cs_internal" } });
      assert.equal(internal.isError, true);
      assert.equal(internal.structuredContent.error.code, "ucp_error");
      assert.doesNotMatch(JSON.stringify(internal), /blocker|secret-internal-state|internal_blocker/);

      const missing = await client.callTool({ name: "get_order", arguments: { id: "ord_missing" } });
      assert.equal(missing.isError, true);
      assert.equal(missing.structuredContent.error.code, "not_found");
      assert.equal(missing.structuredContent.error.status, 404);
    });

    await check("cart and order tools call the binding routes", async () => {
      const created = await client.callTool({ name: "create_cart", arguments: { cart: { line_items: [] } } });
      assert.equal(created.structuredContent.id, "cart_1");
      await client.callTool({ name: "get_cart", arguments: { id: "cart_1" } });
      await client.callTool({ name: "update_cart", arguments: { id: "cart_1", cart: { line_items: [] } } });
      const canceled = await client.callTool({
        name: "cancel_cart",
        arguments: { id: "cart_1", meta: { "idempotency-key": CANCEL_KEY } },
      });
      assert.equal(canceled.structuredContent.status, "canceled");
      const order = await client.callTool({ name: "get_order", arguments: { id: "ord_1" } });
      assert.equal(order.structuredContent.checkout_id, "cs_1");
      const seen = calls.map((c) => `${c.method} ${c.path}`);
      for (const route of ["POST /ucp/carts", "GET /ucp/carts/cart_1", "PUT /ucp/carts/cart_1", "POST /ucp/carts/cart_1/cancel", "GET /ucp/orders/ord_1"]) {
        assert.ok(seen.includes(route), route);
      }
    });

    await check("path ids are encoded", async () => {
      await client.callTool({ name: "get_checkout", arguments: { id: "../../api/v1/agents" } });
      assert.ok(calls.at(-1).path.startsWith("/ucp/checkout-sessions/"));
      assert.ok(!calls.some((c) => c.path.startsWith("/api/")));
      const before = calls.length;
      let refused = false;
      try {
        refused = (await client.callTool({ name: "get_checkout", arguments: { id: ".." } })).isError === true;
      } catch {
        refused = true;
      }
      assert.ok(refused);
      assert.equal(calls.length, before);
    });
  } finally {
    await client.close();
  }

  // --- public catalog -----------------------------------------------------------------------
  await check("catalog search is registered even when checkout is off, lookup only when advertised", async () => {
    const c = await connect({ AGENTICORG_UCP_PROFILE_URL: `${base}/off/.well-known/ucp` });
    try {
      const names = (await c.listTools()).tools.map((t) => t.name);
      assert.ok(names.includes("search_catalog"));
      assert.ok(!names.includes("lookup_catalog"), "lookup is not advertised by the stub");
      assert.deepEqual(ucpNames(await c.listTools()), []);
      const search = (await c.listTools()).tools.find((t) => t.name === "search_catalog");
      assert.equal(search.annotations.readOnlyHint, true);
    } finally {
      await c.close();
    }
  });

  await check("catalog search sends no credential, no idempotency key and needs no platform profile", async () => {
    const c = await connect({ AGENTICORG_UCP_PROFILE_URL: `${base}/off/.well-known/ucp` });
    try {
      const result = await c.callTool({
        name: "search_catalog",
        arguments: { request: { query: "chai", context: { currency: "INR" } } },
      });
      assert.notEqual(result.isError, true);
      assert.equal(result.structuredContent.products[0].variants[0].sku, "SYN-CHAI-250");
      const call = callsTo("/ucp/catalog/search").at(-1);
      assert.deepEqual(call.body, { query: "chai", context: { currency: "INR" } });
      assert.equal(call.authorization, undefined, "the API key never goes to a public route");
      assert.equal(call.idempotencyKey, undefined);
      assert.equal(call.ucpAgent, undefined);
      assert.ok(call.requestId);
    } finally {
      await c.close();
    }
  });

  await check("catalog search names the platform profile when one is configured", async () => {
    const c = await connect({ AGENTICORG_UCP_PROFILE_URL: `${base}/.well-known/ucp`, AGENTICORG_UCP_AGENT_PROFILE: AGENT });
    try {
      await c.callTool({ name: "search_catalog", arguments: { request: { query: "chai" } } });
      const call = callsTo("/ucp/catalog/search").at(-1);
      assert.equal(call.ucpAgent, `profile="${AGENT}"`);
      assert.equal(call.authorization, undefined);
    } finally {
      await c.close();
    }
  });

  await check("no platform profile is a tool error without an API call", async () => {
    const c = await connect({ AGENTICORG_UCP_PROFILE_URL: `${base}/.well-known/ucp` });
    try {
      const before = calls.length;
      const result = await c.callTool({ name: "get_checkout", arguments: { id: "cs_1" } });
      assert.equal(result.isError, true);
      assert.equal(result.structuredContent.error.code, "invalid_profile_url");
      assert.equal(calls.length, before);
    } finally {
      await c.close();
    }
  });
} finally {
  await new Promise((resolve) => api.close(resolve));
}

console.log(`ucp checkout tools passed (${passed} checks, ${calls.length} backend calls)`);
