// SPDX-License-Identifier: Apache-2.0
import test from "node:test";
import assert from "node:assert/strict";
import { evalsMarkup, visibleMarkup } from "./prerender-public.mjs";

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
