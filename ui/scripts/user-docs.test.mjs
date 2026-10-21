// SPDX-License-Identifier: Apache-2.0
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { loadUserGuides, plainGuideMarkdown, renderGuide } from "./generate-user-docs.mjs";
import {
  loadRouteDescriptors,
  renderStaticHtml,
} from "./generate-static-seo.mjs";
import { buildSitemap } from "./generate-sitemap.mjs";

test("approved policy publication preserves current rights without software adoption", () => {
  const manual = loadUserGuides();
  assert.deepEqual(manual.articles.slice(0, 2).map((article) => article.slug), ["start-here", "first-agent"]);
  const guide = manual.articles.find((article) => article.slug === "licensing");
  assert.ok(guide);
  assert.equal(guide.group, "Govern And Operate");
  assert.ok(manual.articles.indexOf(guide) > manual.articles.findIndex((article) => article.slug === "billing-and-support"));
  assert.match(guide.html, /Approved for policy publication; not adopted by a software release/);
  assert.match(guide.html, /No effective date or covered release has been approved/);
  assert.match(guide.html, /personal and internal use is free/);
  assert.match(guide.html, /commercializing the platform requires an agreement with Orchestrum Technologies LLP/);
  assert.match(guide.html, /Already-granted Apache-2.0 permissions remain in place/);
  assert.match(guide.html, /bank or merchant serving its own customers/);
  assert.match(guide.html, /Third-party dependencies retain their own licenses/);
  assert.match(guide.html, /source-available,\s*not an OSI-approved open-source license/);
  assert.match(guide.html, /href="mailto:sanjeev@orchestrum.in"/);
  assert.match(readFileSync(new URL("../../LICENSE", import.meta.url), "utf8"), /Apache License/);
  assert.match(readFileSync(new URL("../../pyproject.toml", import.meta.url), "utf8"), /license = "Apache-2.0"/);
  for (const file of ["../../README.md", "../../LICENSING.md", "../../docs/terms-of-service.md"]) {
    assert.match(readFileSync(new URL(file, import.meta.url), "utf8"), /software adoption pending/);
  }
});

test("the concrete license and scope record cannot masquerade as an adopted license", () => {
  const read = (file) => readFileSync(new URL("../../" + file, import.meta.url), "utf8");
  const scope = JSON.parse(read("licensing/scope.json"));
  assert.equal(scope.schema_version, 1);
  assert.equal(scope.product, "AgenticOrg");
  assert.equal(scope.status, "approved_for_publication");
  assert.equal(scope.licensor, "Orchestrum Technologies LLP");
  assert.equal(scope.commercial_contact, "sanjeev@orchestrum.in");
  assert.equal(scope.proposed_license_id, "LicenseRef-Orchestrum-Self-Use-Commercial-1.0");
  assert.match(scope.reference_baseline_commit, /^[a-f0-9]{40}$/);
  assert.equal(scope.reference_baseline_is_cutoff, false);
  assert.deepEqual(scope.covered_files, []);
  for (const field of ["release_version", "effective_date"])
    assert.equal(scope[field], null, field);
  assert.equal(scope.owner_approval_reference, "owner-publication-approval-2026-10-12");
  assert.equal(scope.legal_review_reference, "owner-attested-legal-review-2026-10-12");
  assert.equal(scope.publication_approved_on, "2026-10-12");
  assert.equal(scope.existing_grants_policy, "preserve");
  assert.equal(scope.third_party_licenses_policy, "retain_original");
  assert.match(scope.sdk_and_protocol_policy, /preserve_published_grants/);
  const candidate = read(scope.license_text);
  assert.match(candidate, /Approved for policy publication; not adopted by a software release/);
  assert.match(candidate, /An agreement signed by an authorized Orchestrum representative/);
  assert.match(candidate, /merchant[\s\S]*selling its own goods/);
  assert.match(candidate, /implementation or consulting services/);
  assert.match(candidate, /Previously[\s\S]*published grants remain available/);
  assert.match(candidate, /Termination under this license does not terminate an independent legacy/);
  assert.match(candidate, /No governing law, venue or effective date has been selected/);
  assert.match(loadUserGuides().articles.find((article) => article.slug === "licensing").html, /zero covered files/);
});

test("existing covered users need an agreement while self-use and prior grants remain distinct", () => {
  const read = (file) => readFileSync(new URL("../../" + file, import.meta.url), "utf8");
  const guide = loadUserGuides().articles.find((article) => article.slug === "licensing");
  assert.ok(guide);
  assert.match(guide.html, /Existing users and new users/);
  assert.match(guide.html, /agreement requirement is not limited to new customers/);
  assert.match(guide.html, /Account age alone does not exempt/);
  assert.match(guide.html, /Give the notice and obtain the acceptance/);
  assert.match(guide.html, /No retroactive fees, automatic acceptance, account suspension or effective date/);
  assert.match(guide.html, /Self-use remains free of a software license fee/);
  assert.match(guide.html, /For previously distributed Apache-2.0 copies/);
  for (const file of ["README.md", "LICENSING.md", "docs/terms-of-service.md", "docs/user-guide/billing-and-support.md"]) {
    assert.match(read(file), /existing and new/i, file);
    assert.match(read(file), /notice[\s\S]*acceptance|notice and agreement terms/, file);
  }
  const candidate = read("licensing/ORCHESTRUM-SELF-USE-COMMERCIAL-1.0-DRAFT.md");
  assert.match(candidate, /applies equally to existing and new users of Covered Software/);
  assert.match(candidate, /Self-use under section 2 remains free/);
  assert.match(candidate, /does not withdraw the prior grants/);
  assert.match(candidate, /Publication does not backdate obligations or replace executed agreements/);
});

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
    assert.match(dockerfile, /COPY api\/v1\/spend\.py \.\.\/api\/v1\/spend\.py/);
    assert.match(dockerfile, /COPY core\/spend\/pricing\.py core\/spend\/reconcile\.py core\/spend\/gate\.py core\/spend\/access\.py \.\.\/core\/spend\//);
  }
  const dockerignore = readFileSync(new URL("../../.dockerignore", import.meta.url), "utf8");
  assert.match(dockerignore, /^!docs\/a2a-commerce-demo\.md$/m);
  assert.match(dockerignore, /^!docs\/a2a-interoperability\.md$/m);
  assert.match(dockerignore, /^!docs\/bfsi\/functional-coverage\.md$/m);
  assert.match(dockerignore, /^!docs\/bfsi\/capability-baseline\.json$/m);
  assert.match(dockerignore, /^!docs\/finops\/$/m);
  assert.match(dockerignore, /^!docs\/finops\/spend-intelligence\.md$/m);
  // Every repository source an OACP guide cites must be inside the UI image's build context.
  for (const source of new Set(loadUserGuides().articles
    .filter((article) => article.slug.startsWith("oacp-"))
    .flatMap((article) => article.sources)
    .filter((source) => source.startsWith("docs/")))) {
    assert.ok(dockerignore.split(/\r?\n/).includes(`!${source}`), source);
  }
});

test("the complete manual has maintained source references and six BFSI playbooks", () => {
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
    6,
  );
  assert.equal(manual.groups.length, 6);
  const coverage = manual.articles.find((article) => article.slug === "bfsi-capability-status");
  assert.ok(coverage);
  const baseline = JSON.parse(readFileSync(
    new URL("../../docs/bfsi/capability-baseline.json", import.meta.url), "utf8",
  ));
  const functional = baseline.capabilities.filter((item) => item.kind === "functional");
  const count = (status) => functional.filter((item) => item.status === status).length;
  assert.match(coverage.html, new RegExp(
    `<strong>${functional.length} functional capabilities</strong>: <strong>${count("covered")}\\s+covered, ${count("partial")} partial and ${count("none")} gaps</strong>`,
  ));
  const startHere = manual.articles.find((article) => article.slug === "start-here");
  assert.ok(startHere);
  assert.match(startHere.html, new RegExp(
    `${count("covered")} of ${functional.length} assessed\\s+functional capabilities are covered, ${count("partial")} are partial and ${count("none")} remain gaps`,
  ));
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
    assert.match(article.html, /href="\/docs\/bfsi-capability-status"/);
  }
  const spend = manual.articles.find((article) => article.slug === "ai-spend-intelligence");
  assert.ok(spend);
  assert.equal(spend.group, "Govern And Operate");
  assert.match(spend.html, /AGENTICORG_SPEND_INTELLIGENCE_ENABLED/);
  assert.match(spend.html, /class="docs-flow docs-process-map" aria-label="Process map"/);
  assert.equal([...spend.html.matchAll(/class="docs-process-content"/g)].length, 6);
  assert.ok(spend.sources.includes("docs/finops/spend-intelligence.md"));
  const commerce = manual.articles.find((article) => article.slug === "commerce");
  assert.match(commerce.html, /href="\/docs\/bfsi-merchant-services#process-map"/);
});

test("the linked functional overview agrees with the machine inventory by domain", () => {
  const baseline = JSON.parse(readFileSync(
    new URL("../../docs/bfsi/capability-baseline.json", import.meta.url), "utf8",
  ));
  const functional = baseline.capabilities.filter((item) => item.kind === "functional");
  const overview = readFileSync(
    new URL("../../docs/bfsi/functional-coverage.md", import.meta.url), "utf8",
  ).replaceAll("**", "");
  const domains = {
    Acquisition: "ACQ", "AI governance": "AIGOV", Content: "CONTENT",
    Conversation: "CONV", Evaluation: "EVAL", "Front end": "FE", FinOps: "FIN",
    "Model gateway": "GW", "Document intelligence": "IDP", "Lead intelligence": "LEAD",
    MLOps: "MLOPS", Observability: "OBS", Orchestration: "ORCH",
    "Prompt governance": "PROMPT", Retrieval: "RAG", "Agent registry": "REG",
    Speech: "SPEECH", "Trust controls": "TRUST", "Transaction intelligence": "TXN",
    "Vector infrastructure": "VEC",
  };
  assert.deepEqual(new Set(Object.values(domains)), new Set(functional.map((item) => item.domain)));
  for (const [label, domain] of [...Object.entries(domains), ["Total", null]]) {
    const items = domain ? functional.filter((item) => item.domain === domain) : functional;
    const counts = ["covered", "partial", "none"].map(
      (status) => items.filter((item) => item.status === status).length,
    );
    assert.ok(overview.includes(`| ${label} | ${items.length} | ${counts.join(" | ")} |`), label);
  }
  const count = (status) => functional.filter((item) => item.status === status).length;
  assert.match(overview, new RegExp(
    `${count("covered")} functional capabilities are covered, ${count("partial")} are partial,\\s*and ${count("none")} are gaps`,
  ));
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
  assert.equal(rich.html.includes("<script>"), false);
  assert.equal(rich.html.includes("<img src=x"), false);
  const upperCaseTag = renderGuide(
    "```flow\nStart | Read | Owner: <SCRIPT>team</SCRIPT>\n```",
  );
  assert.ok(upperCaseTag.html.includes("&lt;SCRIPT&gt;team&lt;/SCRIPT&gt;"));
  assert.equal(upperCaseTag.html.includes("<SCRIPT>"), false);
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

test("public A2A guide links to a hosted page, not a source Markdown soft 404", () => {
  const guide = loadUserGuides().articles.find((article) => article.slug === "api-sdk-mcp");
  assert.ok(guide);
  assert.match(guide.html, /href="\/docs\/seller-a2a-commerce-journey"/);
  assert.doesNotMatch(guide.html, /href="[^"]*a2a-interoperability\.md"/);
});

test("the OACP section has seven adaptive guides with summaries, figures and workflow cards", () => {
  const manual = loadUserGuides();
  const group = "OACP — agentic commerce, made safe";
  assert.ok(manual.groups.includes(group));
  const guides = manual.articles.filter((article) => article.group === group);
  assert.deepEqual(guides.map((guide) => guide.slug), [
    "oacp-overview", "oacp-how-it-works", "oacp-buyer-journey", "oacp-merchant-setup",
    "oacp-safety-and-trust", "oacp-feature-switches", "oacp-faq",
  ]);
  for (const guide of guides) {
    assert.equal(guide.colorScheme, "adaptive", guide.slug);
    assert.match(guide.html, /^<aside class="docs-summary" aria-label="In 30 seconds">/, guide.slug);
    assert.ok(guide.figures.length >= 1, guide.slug);
    for (const figure of guide.figures)
      assert.match(guide.html, new RegExp(`data-docs-figure="${figure.id}"`), guide.slug);
  }
  // Every other guide keeps the light scheme and has no figure placeholders.
  for (const guide of manual.articles.filter((article) => article.group !== group)) {
    assert.equal(guide.colorScheme, "light", guide.slug);
    assert.deepEqual(guide.figures, [], guide.slug);
  }
  const overview = guides[0];
  assert.match(overview.html, /does not replace them, and it is not a certification/);
  assert.match(overview.html, /Mastercard Agent Pay/);
  const journey = guides.find((guide) => guide.slug === "oacp-buyer-journey");
  assert.match(journey.html, /Intent/);
  assert.match(journey.html, /passkey/);
  assert.match(journey.html, /RBI AFA/);
  assert.ok([...journey.html.matchAll(/class="docs-process-owner"/g)].length >= 10);
  const setup = guides.find((guide) => guide.slug === "oacp-merchant-setup");
  // The checklist keeps one numbering across its five phases.
  assert.match(setup.html, /aria-label="Process map" start="20">/);
  assert.match(setup.html, /_agenticorg-ucp\.shop\.merchant\.example/);
  assert.match(setup.html, /off until a sandbox run passes/i);
  const switches = guides.find((guide) => guide.slug === "oacp-feature-switches");
  for (const flag of [
    "AGENTICORG_OACP_VERIFY_STRICT", "AGENTICORG_ARBITER_REQUIRE_VERIFIED_AUTHORITY",
    "AGENTICORG_ARBITER_REQUIRE_GRANTEX_GRANT", "AGENTICORG_ROUTE_ENFORCEMENT_MODE",
    "AGENTICORG_UCP_PROFILE_ENABLED", "AGENTICORG_UCP_CHECKOUT_ENABLED", "AGENTICORG_ACP_FEED_ENABLED",
    "AGENTICORG_ACP_CHECKOUT_ENABLED", "AGENTICORG_ACP_SIGNATURE_MODE", "PLURAL_PINE_LIVE_EXECUTION_ENABLED",
    "AGENTICORG_GRANTEX_MANDATES_ENABLED", "AGENTICORG_GRANTEX_MANDATE_CAPABILITY_EVIDENCE_ENABLED",
    "AGENTICORG_HTTPSIG_VERIFY_MODE", "AGENTICORG_HTTPSIG_SIGN_OUTBOUND", "AGENTICORG_AUDIT_WITNESS_ENABLED",
    "AGENTICORG_OACP_RETENTION_APPLY_ENABLED", "AGENTICORG_AUDIT_CHAIN_DEFAULT_ON",
  ])
    assert.match(switches.html, new RegExp(`<code>${flag}</code>`), flag);
  const faq = guides.find((guide) => guide.slug === "oacp-faq");
  assert.ok(faq.headings.filter((heading) => heading.level === 2).length >= 11);
});

test("figure, summary and numbered flow fences render safely and read as text", () => {
  const markdown = [
    "```summary", "First <b>point</b>", "Second point", "```", "",
    "## One", "",
    "```figure", "oacp-x | A <title> | A caption & more", "```", "",
    "```flow start=4", "Step | Body | Owner: Merchant", "```", "",
  ].join("\n");
  const { html, figures, searchText } = renderGuide(markdown);
  assert.deepEqual(figures, [{ id: "oacp-x", title: "A <title>", caption: "A caption & more" }]);
  assert.match(html, /<li>First &lt;b&gt;point&lt;\/b&gt;<\/li>/);
  assert.match(html, /data-docs-figure="oacp-x"/);
  assert.match(html, /A &lt;title&gt;/);
  assert.match(html, /aria-label="Process map" start="4"><li><span class="docs-flow-number" aria-hidden="true">4<\/span>/);
  assert.doesNotMatch(searchText, /oacp-x/);
  assert.match(searchText, /A caption & more/);
  const plain = plainGuideMarkdown(markdown);
  assert.match(plain, /^In 30 seconds:\n\n- First <b>point<\/b>\n- Second point/);
  assert.match(plain, /Figure: A <title>\. A caption & more/);
  assert.throws(() => renderGuide("```figure\nBad Id | t | c\n```"), /Figure fences need/);
  assert.throws(() => renderGuide("```figure\na | t\n```"), /Figure fences need/);
  assert.throws(() => renderGuide("```figure\na | t | c\n```\n\n```figure\na | t | c\n```"), /Duplicate figure/);
  assert.throws(() => renderGuide("```flow start=0\na | b\n```"), /starts at 1/);
});
