import test from "node:test";
import assert from "node:assert/strict";
import {
  CSP_HASH_CHUNK_MAX,
  chunkCspHashes,
  cspHash,
  extractObjectBlocks,
  outputPathsForRoute,
  replaceGeneratedCspHashReferences,
  renderStaticHtml,
} from "./generate-static-seo.mjs";
import { buildSitemap } from "./generate-sitemap.mjs";
import { submitIndexNow } from "./submit-indexing.mjs";

const manifest = {
  site: {
    name: "AgenticOrg",
    url: "https://agenticorg.ai",
    language: "en-IN",
    description: "A governed enterprise AI agent platform.",
    defaultImage: "https://agenticorg.ai/og-image.png",
  },
  landingFaqs: [
    { question: "What is AgenticOrg?", answer: "A governed AI agent platform." },
  ],
};

const baseHtml = [
  "<!doctype html><html><head>",
  "<title>Old homepage title</title>",
  '<meta name="description" content="Old description">',
  '<meta property="og:title" content="Old title">',
  '<script type="application/ld+json">{"@type":"AggregateRating"}</script>',
  "</head><body><noscript>Old homepage copy</noscript>",
  '<div id="root"></div></body></html>',
].join("");

test("inline JSON-LD CSP hashes are deterministic", () => {
  assert.equal(
    cspHash(""),
    "sha256-47DEQpj8HBSa+/TImW+5JCeuQeRkm5NMpJWZG3hSuFU=",
  );
});

test("JSON-LD CSP hashes stay in nginx-safe configuration chunks", () => {
  const hashes = Array.from(
    { length: 120 },
    (_, index) => "sha256-" + String(index).padStart(44, "0"),
  );
  const chunks = chunkCspHashes(hashes);
  assert.ok(chunks.length > 1);
  assert.ok(chunks.every((chunk) => chunk.length <= CSP_HASH_CHUNK_MAX));
  const recovered = chunks.flatMap((chunk) =>
    [...chunk.matchAll(/'([^']+)'/g)].map((match) => match[1]),
  );
  assert.deepEqual(recovered, hashes);
});

test("CSP synchronization preserves non-generated script sources", () => {
  const input = "script-src 'self' 'sha256-old=' $csp_jsonld_hashes_9 https://cdn.example.com https://accounts.google.com;";
  const result = replaceGeneratedCspHashReferences(
    input,
    "$csp_jsonld_hashes_1 $csp_jsonld_hashes_2",
  );
  assert.equal(
    result,
    "script-src 'self' $csp_jsonld_hashes_1 $csp_jsonld_hashes_2 https://cdn.example.com https://accounts.google.com;",
  );
});

test("TypeScript data scanner skips type-annotation brackets and nested braces", () => {
  const source = [
    "export const ITEMS: Item[] = [",
    '  { slug: "one", nested: { text: "a } brace" } },',
    '  { slug: "two", values: [{ ok: true }] },',
    "];",
  ].join("\n");
  const blocks = extractObjectBlocks(source, "ITEMS");
  assert.equal(blocks.length, 2);
  assert.match(blocks[0], /slug: "one"/);
  assert.match(blocks[1], /slug: "two"/);
});

test("landing shell replaces inherited metadata and owns the only FAQ graph", () => {
  const route = {
    path: "/",
    name: "AgenticOrg",
    title: "Governed Enterprise AI Agents | AgenticOrg",
    description: "Build and operate scoped AI agents with human approval and audit evidence.",
    summary: "Governed agents with explicit controls.",
    schemaType: "WebPage",
    kind: "static",
    index: true,
  };
  const html = renderStaticHtml(baseHtml, route, manifest);
  assert.equal((html.match(/name="description"/g) || []).length, 1);
  assert.equal((html.match(/rel="canonical"/g) || []).length, 1);
  assert.match(html, /https:\/\/agenticorg\.ai\/"/);
  assert.match(html, /"@type":"FAQPage"/);
  assert.doesNotMatch(html, /AggregateRating/);
  assert.match(html, /data-static-seo="true"/);
});

test("non-root shells have truthful breadcrumb JSON-LD without landing FAQ", () => {
  const route = {
    path: "/blog/evidence-first-agents",
    name: "Evidence-first agents",
    title: "Evidence-first agents | AgenticOrg Blog",
    description: "How source evidence and scoped tools improve governed agent workflows.",
    summary: "A concise evidence-first guide.",
    schemaType: "BlogPosting",
    kind: "blog",
    index: true,
    author: "AgenticOrg",
    datePublished: "2026-07-01",
    lastmod: "2026-07-02",
  };
  const html = renderStaticHtml(baseHtml, route, manifest);
  assert.match(html, /"@type":"BreadcrumbList"/);
  assert.match(html, /"@type":"BlogPosting"/);
  assert.doesNotMatch(html, /"@type":"FAQPage"/);
  assert.match(html, /https:\/\/agenticorg\.ai\/blog\/evidence-first-agents/);
});

test("sitemap contains only canonical indexable URLs and meaningful lastmod", () => {
  const xml = buildSitemap([
    { path: "/", index: true, lastmod: "2026-07-01" },
    { path: "/pricing", index: true },
    { path: "/login", index: false, lastmod: "2026-07-01" },
  ], "https://agenticorg.ai");
  assert.match(xml, /<loc>https:\/\/agenticorg\.ai\/<\/loc>/);
  assert.match(xml, /<lastmod>2026-07-01<\/lastmod>/);
  assert.match(xml, /<loc>https:\/\/agenticorg\.ai\/pricing<\/loc>/);
  assert.doesNotMatch(xml, /login|changefreq|priority/);
});

test("listing routes with children get both flat and directory-index shells", () => {
  const routes = [
    { path: "/blog" },
    { path: "/blog/post" },
  ];
  const outputs = outputPathsForRoute("C:/site/ui", routes[0], routes)
    .map((path) => path.replaceAll("\\", "/"));
  assert.deepEqual(outputs, [
    "C:/site/ui/dist/blog.html",
    "C:/site/ui/dist/blog/index.html",
  ]);
});

test("IndexNow submission uses the canonical host and surfaces acceptance", async () => {
  let request;
  const status = await submitIndexNow(
    ["https://agenticorg.ai/pricing"],
    "valid-indexnow-key",
    async (url, init) => {
      request = { url, init };
      return new Response("", { status: 202 });
    },
  );

  assert.equal(status, 202);
  assert.equal(request.url, "https://api.indexnow.org/indexnow");
  const body = JSON.parse(request.init.body);
  assert.equal(body.host, "agenticorg.ai");
  assert.equal(
    body.keyLocation,
    "https://agenticorg.ai/valid-indexnow-key.txt",
  );
  assert.deepEqual(body.urlList, ["https://agenticorg.ai/pricing"]);
});

test("IndexNow rejection fails instead of being silently ignored", async () => {
  await assert.rejects(
    submitIndexNow(
      ["https://agenticorg.ai/pricing"],
      "valid-indexnow-key",
      async () => new Response("invalid key", { status: 403 }),
    ),
    /HTTP 403: invalid key/,
  );
});
