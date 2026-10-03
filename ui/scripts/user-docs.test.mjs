// SPDX-License-Identifier: Apache-2.0
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { loadUserGuides, renderGuide } from "./generate-user-docs.mjs";
import {
  loadRouteDescriptors,
  renderStaticHtml,
} from "./generate-static-seo.mjs";
import { buildSitemap } from "./generate-sitemap.mjs";

test("missing authored sources cannot silently overwrite the manual with an empty build", () => {
  assert.throws(() => loadUserGuides(new URL("./missing-build-root/ui", import.meta.url).pathname),
    /Missing user-guide manifest/);
});

test("both production UI images preserve the repository layout and include authored guides", () => {
  for (const file of ["../../Dockerfile.ui", "../../Dockerfile.ui.cloudrun"]) {
    const dockerfile = readFileSync(new URL(file, import.meta.url), "utf8");
    assert.match(dockerfile, /WORKDIR \/app\/ui/);
    assert.match(dockerfile, /COPY docs\/ \.\.\/docs\//);
    assert.match(dockerfile, /COPY --from=builder \/app\/ui\/dist/);
    assert.match(dockerfile, /COPY api\/v1\/model_gateway\.py \.\.\/api\/v1\/model_gateway\.py/);
    assert.match(dockerfile, /COPY api\/v1\/guardrails\.py \.\.\/api\/v1\/guardrails\.py/);
    assert.match(dockerfile, /COPY api\/v1\/observability\.py \.\.\/api\/v1\/observability\.py/);
    assert.match(dockerfile, /COPY core\/governance\/model_gateway\.py \.\.\/core\/governance\/model_gateway\.py/);
    assert.match(dockerfile, /COPY core\/governance\/guardrails\/ \.\.\/core\/governance\/guardrails\//);
    assert.match(dockerfile, /COPY observability\/workload\.py observability\/timeline\.py \.\.\/observability\//);
  }
  const dockerignore = readFileSync(new URL("../../.dockerignore", import.meta.url), "utf8");
  assert.match(dockerignore, /^!docs\/a2a-commerce-demo\.md$/m);
  assert.match(dockerignore, /^!docs\/a2a-interoperability\.md$/m);
});

test("the complete manual has maintained source references and five BFSI playbooks", () => {
  const manual = loadUserGuides();
  assert.ok(manual.articles.length >= 30);
  assert.ok(manual.articles.some((article) => article.slug === "external-buyer-a2a-demo"));
  const journey = manual.articles.find((article) => article.slug === "seller-a2a-commerce-journey");
  assert.ok(journey);
  assert.equal(journey.reviewed, "2026-10-01");
  assert.match(journey.html, /Pine Labs Plural/);
  assert.match(journey.html, /allowedToExecute/);
  assert.match(journey.html, /Transaction map: from question to a real sale/);
  assert.match(journey.html, /Requires verified callbacks, merchant execution and reconciliation/);
  assert.equal(
    manual.articles.filter((article) => article.group === "BFSI Playbooks")
      .length,
    5,
  );
  assert.equal(manual.groups.length, 5);
  assert.ok(
    manual.articles.every(
      (article) => article.sources.length && article.headings.length >= 3,
    ),
  );
  for (const article of manual.articles.filter(
    (item) => item.group === "BFSI Playbooks",
  )) {
    assert.match(article.markdown, /example|fictional|synthetic/i);
    assert.match(article.html, /class="docs-flow docs-process-map" aria-label="Process map"/);
    const stepCount = [...article.html.matchAll(/class="docs-process-content"/g)].length;
    assert.ok(stepCount >= 5, article.slug);
    assert.equal([...article.html.matchAll(/class="docs-process-owner"/g)].length, stepCount);
    assert.match(article.html, /Human decision/);
    assert.match(article.html, /Blocked \/ exception/);
  }
  const commerce = manual.articles.find((article) => article.slug === "commerce");
  assert.match(commerce.html, /href="\/docs\/bfsi-merchant-services#process-map"/);
});

test("tracked reader data matches the authored Markdown and manifest", () => {
  const { articles, ...manifest } = loadUserGuides();
  const generated = JSON.parse(
    readFileSync(
      new URL("../src/content/userDocs.generated.json", import.meta.url),
      "utf8",
    ),
  );
  const expected = {
    ...manifest,
    articles: articles.map(({ markdown: _markdown, ...article }) => article),
  };
  assert.deepEqual(
    generated,
    expected,
    "Run node scripts/generate-user-docs.mjs after editing guides.",
  );
});

test("Markdown escapes raw HTML, unsafe links and diagram labels", () => {
  for (const [script, image, scheme] of [
    ["script", "img", "javascript"],
    ["SCRIPT", "IMG", "JAVASCRIPT"],
    ["ScRiPt", "ImG", "JaVaScRiPt"],
  ]) {
    const { html } = renderGuide(
      `<${script}>alert(1)</${script}>\n\n[unsafe](${scheme}:alert(1))\n\n\`\`\`flow\n<${image} src=x onerror=alert(1)> | <${script}>unsafe</${script}>\n\`\`\``,
    );
    assert.doesNotMatch(
      html,
      /<\s*script\b|<\s*img\b|href\s*=\s*["']?\s*javascript:/i,
    );
    assert.ok(html.includes(`&lt;${script}&gt;`));
    assert.ok(html.includes(`&lt;${image}`));
    assert.match(html, /aria-label="Workflow"/);
  }
});

test("process maps preserve old flows and escape every richer field", () => {
  const legacy = renderGuide("```flow\nStart | Review A | B\nFinish | Record result\n```");
  assert.match(legacy.html, /aria-label="Workflow"/);
  assert.match(legacy.html, /<p>Review A \| B<\/p>/);
  assert.doesNotMatch(legacy.html, /docs-process-map/);

  const rich = renderGuide(
    "```flow\n<Start> | Read <source> & check | Owner: <script>team</script> | Human decision: Review <evidence> | If blocked: Stop <img src=x onerror=alert(1)>\n```");
  assert.match(rich.html, /aria-label="Process map"/);
  assert.match(rich.html, /Owner:<\/span> &lt;script&gt;team&lt;\/script&gt;/);
  assert.match(rich.html, /Human decision<\/strong><p>Review &lt;evidence&gt;<\/p>/);
  assert.match(rich.html, /Blocked \/ exception<\/strong><p>Stop &lt;img/);
  assert.doesNotMatch(rich.html, /<script>|<img/);
  assert.throws(
    () => renderGuide("```flow\nStart | Read | Owner: Team\nFinish | Done\n```"),
    /Every process-map step needs an Owner/,
  );
  assert.throws(
    () => renderGuide("```flow\nStart | Read | Owner: Team | Unknown: Value\n```"),
    /Invalid flow field/,
  );
  assert.throws(
    () => renderGuide("```flow\nStart | Read | Human decision: Approve\n```"),
    /Owner field before other fields/,
  );
});

test("Windows and Linux checkouts generate identical reader data", () => {
  const markdown = "## A guide\n\n```flow\nStart | Read the source\nFinish | Review the evidence\n```\n";
  assert.deepEqual(renderGuide(markdown.replaceAll("\n", "\r\n")), renderGuide(markdown));
});

test("heading anchors are stable and tables have independent keyboard scroll regions", () => {
  const rendered = renderGuide(
    "## Next steps\n\n## Next steps\n\n| Field | Value |\n| --- | --- |\n| Test | Good |\n",
  );
  assert.deepEqual(
    rendered.headings.map((heading) => heading.id),
    ["next-steps", "next-steps-2"],
  );
  assert.match(
    rendered.html,
    /tabindex="0" role="region" aria-label="Reference table"/,
  );
});

test("all guides have crawlable complete text, canonical URLs and sitemap entries", () => {
  const { manifest, routes } = loadRouteDescriptors();
  const sitemap = buildSitemap(routes, manifest.site.url);
  const base =
    '<html><head><title>Old title</title></head><body><div id="root"></div></body></html>';
  const manual = loadUserGuides();
  for (const article of manual.articles) {
    const route = routes.find((item) => item.path === `/docs/${article.slug}`);
    assert.ok(route, article.slug);
    const html = renderStaticHtml(base, route, manifest);
    assert.ok(html.includes(article.html), article.slug);
    assert.match(html, /<noscript>/);
    assert.match(html, /"@type":"TechArticle"/);
    assert.ok(sitemap.includes(`https://agenticorg.ai/docs/${article.slug}`));
    assert.equal((html.match(/rel="canonical"/g) || []).length, 1);
  }
  assert.ok(
    routes
      .find((item) => item.path === "/docs")
      .bodyHtml.includes("BFSI Playbooks"),
  );
  const overview = routes.find((item) => item.path === "/docs");
  assert.match(overview.bodyHtml, /href="#bfsi-playbooks"/);
  assert.match(overview.bodyHtml, /<section id="bfsi-playbooks">/);
  for (const article of manual.articles.filter((item) => item.group === "BFSI Playbooks")) {
    const route = routes.find((item) => item.path === `/docs/${article.slug}`);
    const html = renderStaticHtml(base, route, manifest);
    assert.match(html, /<noscript><main[^>]+data-static-seo="true"/);
    assert.match(html, /class="docs-flow docs-process-map" aria-label="Process map"/);
    assert.match(html, /Human decision/);
    assert.match(html, /Blocked \/ exception/);
  }
});

test("both nginx targets reject unknown guides and redirect only the documentation host root", () => {
  for (const file of ["../nginx.conf", "../nginx.cloudrun.conf.template"]) {
    const config = readFileSync(new URL(file, import.meta.url), "utf8");
    assert.match(
      config,
      /location \^~ \/docs\/ \{[^}]*try_files \$uri \$uri\.html =404;/,
    );
    assert.match(
      config,
      /if \(\$host = docs\.agenticorg\.ai\) \{ return 302 \/docs; \}/,
    );
    assert.match(
      config,
      /location = \/ \{\s*absolute_redirect off;\s*if \(\$host = docs\.agenticorg\.ai\) \{ return 302 \/docs; \}/,
    );
  }
});

test("both nginx targets declare one text MIME type instead of appending duplicate headers", () => {
  for (const file of ["../nginx.conf", "../nginx.cloudrun.conf.template"]) {
    const config = readFileSync(new URL(file, import.meta.url), "utf8");
    assert.doesNotMatch(config, /add_header\s+Content-Type\b/i);
    const blocks = [...config.matchAll(
      /location = \/(?:health|llms(?:-full)?\.txt)\s*\{[^}]*\}/g,
    )];
    assert.equal(blocks.length, 3);
    for (const [block] of blocks) {
      assert.match(block, /default_type text\/plain;/);
      assert.match(block, /charset utf-8;/);
    }
  }
});

test("hosted screen shortcuts point to implemented application routes", () => {
  const app = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
  const guide = loadUserGuides().articles.find(
    (article) => article.slug === "start-here",
  );
  const paths = [
    ...guide.markdown.matchAll(
      /https:\/\/app\.agenticorg\.ai(\/dashboard[^)\s]*)/g,
    ),
  ].map((match) => match[1]);
  assert.ok(paths.length >= 16);
  for (const path of paths) assert.ok(app.includes(`path="${path}"`), path);
});
