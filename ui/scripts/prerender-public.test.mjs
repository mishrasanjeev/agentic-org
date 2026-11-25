// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
import test from "node:test";
import assert from "node:assert/strict";
import {
  assertNoPlaceholders,
  evalsMarkup,
  fetchProductFacts,
  productFactsSeedTag,
  visibleMarkup,
} from "./prerender-public.mjs";

const FACTS = { version: "4.8.0", connector_count: 57, agent_count: 29, tool_count: 406 };
const parseFacts = (value) => (value && value.agent_count > 0 ? value : null);
const jsonResponse = (status, body) => ({ ok: status >= 200 && status < 300, status, json: async () => body });

test("product facts are read from the configured endpoint", async () => {
  const calls = [];
  const facts = await fetchProductFacts(parseFacts, {
    url: "https://facts.example.test/api/v1/product-facts",
    fetchImpl: async (url, init) => { calls.push([url, init.headers.accept]); return jsonResponse(200, FACTS); },
    warn: () => assert.fail("no warning expected"),
  });
  assert.deepEqual(facts, FACTS);
  assert.deepEqual(calls, [["https://facts.example.test/api/v1/product-facts", "application/json"]]);
});

test("an unreachable, failing, or empty facts endpoint degrades to no seed", async () => {
  for (const fetchImpl of [
    async () => { throw new Error("offline"); },
    async () => jsonResponse(503, {}),
    async () => jsonResponse(200, { version: "", agent_count: 0 }),
  ]) {
    const warnings = [];
    const facts = await fetchProductFacts(parseFacts, {
      url: "https://facts.example.test/x",
      fetchImpl,
      warn: (message) => warnings.push(message),
    });
    assert.equal(facts, null);
    assert.equal(warnings.length, 1);
    assert.match(warnings[0], /Product facts not seeded from https:\/\/facts\.example\.test\/x/);
  }
});

test("the facts request can be switched off for offline builds", async () => {
  const facts = await fetchProductFacts(parseFacts, {
    url: "off",
    fetchImpl: async () => assert.fail("no request expected"),
  });
  assert.equal(facts, null);
});

test("the seed is a non-executable JSON block that cannot close its own tag", () => {
  const tag = productFactsSeedTag("agenticorg-product-facts", { ...FACTS, version: "4.8.0</script><b>" });
  assert.match(tag, /^<script type="application\/json" id="agenticorg-product-facts">/);
  assert.equal((tag.match(/<\/script>/g) || []).length, 1);
  assert.match(tag, /\\u003c\/script>\\u003cb>/);
});

test("prerendered pages never ship a loading or unavailable state", () => {
  assert.throws(
    () => assertNoPlaceholders("<p>Loading the complete billing catalog...</p>", "/pricing", false),
    /\/pricing contains the pricing loading banner/,
  );
  assert.throws(
    () => assertNoPlaceholders("<p>The complete billing catalog is unavailable.</p>", "/pricing", false),
    /pricing failure banner/,
  );
  assert.throws(() => assertNoPlaceholders("<div>Unavailable</div>", "/", true), /unavailable product count/);
  assert.throws(() => assertNoPlaceholders("<span>Version pending</span>", "/", true), /pending version/);
  // Without a seed the honest "Unavailable" copy is allowed; real values always are.
  assertNoPlaceholders("<div>Unavailable</div><span>Version pending</span>", "/", false);
  assertNoPlaceholders("<div>29</div><span>v4.8.0</span>", "/", true);
});

test("server-rendered content preserves one visible heading and real links", () => {
  const markup = visibleMarkup(
    '<title>Marketing</title><meta name="description" content="example">' +
    '<link rel="canonical" href="https://example.test/">' +
    '<script type="application/ld+json">{"@type":"WebPage"}</script>' +
    '<div><h1>Marketing</h1><a href="/docs">Documentation</a></div>',
  );
  assert.equal((markup.match(/<h1\b/g) || []).length, 1);
  assert.match(markup, /href="\/docs"/);
  assert.doesNotMatch(markup, /<title|<meta|rel="canonical"|application\/ld\+json/);
});

test("unexpected executable scripts fail the prerender build", () => {
  assert.throws(() => visibleMarkup('<div><h1>Unsafe</h1><script>console.log(1)</script></div>'));
});

test("evaluation shell explains dimensions without inventing live scores", () => {
  const markup = evalsMarkup({
    name: "Agent Evaluations",
    summary: "Assess quality, safety, and cost before promotion.",
  });
  assert.equal((markup.match(/<h1\b/g) || []).length, 1);
  assert.match(markup, /Scores and timestamps load from the live evaluation source/);
  assert.match(markup, /missing measurements are not passing results/);
  assert.match(markup, /href="\/docs"/);
  assert.doesNotMatch(markup, /<script|score="|passed="/);
});
