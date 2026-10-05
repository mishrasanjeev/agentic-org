// SPDX-License-Identifier: Apache-2.0
import test from "node:test";
import assert from "node:assert/strict";
import { visibleMarkup } from "./prerender-public.mjs";

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
