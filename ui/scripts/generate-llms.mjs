#!/usr/bin/env node
/**
 * Generate public llms.txt documents from repository sources.
 *
 * Default: write ui/dist/llms.txt and ui/dist/llms-full.txt.
 * --sync-public: also refresh the tracked ui/public copies.
 * --check: fail when the tracked copies are stale.
 */
import {
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  writeFileSync,
} from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const UI_ROOT = join(SCRIPT_DIR, "..");
const REPO_ROOT = join(UI_ROOT, "..");
const PUBLIC_DIR = join(UI_ROOT, "public");
const DIST_DIR = join(UI_ROOT, "dist");
const SITE = "https://agenticorg.ai";
const APP = "https://app.agenticorg.ai";
const REPO = "https://github.com/mishrasanjeev/agentic-org";
const FACTS = APP + "/api/v1/product-facts";

const text = (path) => readFileSync(path, "utf8");
const normalized = (value) => value.replace(/\r\n/g, "\n").trimEnd() + "\n";
const clean = (value) =>
  String(value || "").replace(/\s+/g, " ").replace(/\|/g, "\\|").trim();
const humanize = (value) =>
  String(value || "")
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
const url = (path) => (path === "/" ? SITE : SITE + path);

function json(path) {
  try {
    return JSON.parse(text(path));
  } catch {
    return null;
  }
}

function unique(items, key) {
  const seen = new Set();
  return items.filter((item) => {
    if (!item[key] || seen.has(item[key])) return false;
    seen.add(item[key]);
    return true;
  });
}

function groups(items, key) {
  const result = new Map();
  for (const item of items) {
    const group = item[key] || "other";
    if (!result.has(group)) result.set(group, []);
    result.get(group).push(item);
  }
  return result;
}

function projectMetadata() {
  const source = text(join(REPO_ROOT, "pyproject.toml"));
  const start = source.indexOf("[project]");
  const end = source.indexOf("\n[", start + 1);
  const project = source.slice(start, end < 0 ? source.length : end);
  const version = project.match(/^version\s*=\s*"([^"]+)"/m)?.[1];
  const python = project.match(/^requires-python\s*=\s*"([^"]+)"/m)?.[1];
  const react = json(join(UI_ROOT, "package.json"))?.dependencies?.react;
  const reactMajor = react?.match(/\d+/)?.[0];
  if (!version || !python || !reactMajor) {
    throw new Error("Unable to read project version, Python requirement, or React version");
  }
  return { version, python, reactMajor };
}

function field(block, name) {
  const match = block.match(
    new RegExp('"' + name + '"\\s*:\\s*(?:"([^"]*)"|([\\d_]+))'),
  );
  return match ? match[1] ?? Number(match[2].replace(/_/g, "")) : "";
}

function pricing() {
  const source = normalized(text(join(REPO_ROOT, "core", "billing", "limits.py")));
  const start = source.indexOf("PLAN_PRICING:");
  const end = source.indexOf("\n]\n", start);
  if (start < 0 || end < 0) throw new Error("PLAN_PRICING was not found");
  const section = source.slice(start, end + 3);
  const starts = [...section.matchAll(/"plan"\s*:\s*"([^"]+)"/g)];
  const plans = starts.map((entry, index) => {
    const block = section.slice(entry.index, starts[index + 1]?.index ?? section.length);
    const featureSource =
      block.match(/"features"\s*:\s*\[([\s\S]*?)\]/)?.[1] || "";
    return {
      id: entry[1],
      label: field(block, "label") || humanize(entry[1]),
      usd: field(block, "price_usd"),
      inr: field(block, "price_inr"),
      agents: field(block, "agents"),
      runs: field(block, "runs"),
      storage: field(block, "storage"),
      features: [...featureSource.matchAll(/"([^"]+)"/g)].map((match) => match[1]),
    };
  });
  if (plans.map((plan) => plan.id).sort().join(",") !== "enterprise,free,pro") {
    throw new Error("Expected Free, Pro, and Enterprise PLAN_PRICING entries");
  }
  return plans;
}

const AGENT_DIRS = [
  ["finance", "Finance"],
  ["hr", "HR"],
  ["marketing", "Marketing"],
  ["ops", "Operations"],
  ["backoffice", "Back Office"],
  ["comms", "Communications"],
];

function agents() {
  const result = [];
  for (const [directory, domainLabel] of AGENT_DIRS) {
    const path = join(REPO_ROOT, "core", "agents", directory);
    if (!existsSync(path)) continue;
    for (const file of readdirSync(path).sort()) {
      if (!file.endsWith(".py") || file.startsWith("__")) continue;
      const source = text(join(path, file));
      const type = source.match(/agent_type\s*=\s*["']([^"']+)["']/)?.[1];
      if (type) result.push({ type, label: humanize(type), domain: directory, domainLabel });
    }
  }
  return unique(result, "type").sort((a, b) => a.type.localeCompare(b.type));
}

const CONNECTOR_DIRS = [
  ["finance", "Finance"],
  ["hr", "HR"],
  ["marketing", "Marketing"],
  ["ops", "Operations"],
  ["comms", "Communications and Cloud"],
];

function connectors() {
  const result = [];
  for (const [directory, categoryLabel] of CONNECTOR_DIRS) {
    const path = join(REPO_ROOT, "connectors", directory);
    if (!existsSync(path)) continue;
    for (const file of readdirSync(path).sort()) {
      if (!file.endsWith(".py") || file.startsWith("__")) continue;
      const source = text(join(path, file));
      const name = source.match(/^\s*name\s*=\s*["']([^"']+)["']/m)?.[1];
      const auth =
        source.match(/^\s*auth_type\s*=\s*["']([^"']+)["']/m)?.[1] || "";
      if (name) {
        result.push({
          name,
          label: humanize(name),
          auth,
          category: directory,
          categoryLabel,
        });
      }
    }
  }
  return unique(result, "name").sort((a, b) => a.name.localeCompare(b.name));
}

function editorial(path, kind) {
  const source = text(path);
  const slugs = [...source.matchAll(/^\s*slug:\s*"([^"]+)"/gm)];
  const titles = [...source.matchAll(/^\s*title:\s*"([^"]+)"/gm)];
  return unique(
    slugs.map((slug, index) => ({
      slug: slug[1],
      title: titles[index]?.[1] || humanize(slug[1]),
      kind,
    })),
    "slug",
  );
}

const DEFAULT_PAGES = [
  ["/", "Home", "Product overview, boundaries, governance, and direct answers."],
  ["/pricing", "Pricing", "Hosted prices, usage limits, and support options."],
  ["/playground", "Playground", "Interactive agent experience."],
  ["/evals", "Evaluation matrix", "Agent evaluation and readiness dimensions."],
  ["/integration-workflow", "Integration workflow", "Connector authorization and governed tool execution."],
  ["/open-agentic-commerce-protocol", "Open Agentic Commerce Protocol", "OACP ownership, evidence, cache, and execution boundaries."],
  ["/how-grantex-works", "How Grantex works", "Trust authority and authorization responsibilities."],
  ["/solutions/ca-firms", "CA firms", "Multi-company accounting workflows."],
  ["/solutions/cfo", "CFO solution", "Governed finance operations."],
  ["/solutions/chro", "CHRO solution", "Governed HR operations."],
  ["/solutions/cmo", "CMO solution", "Governed marketing operations."],
  ["/solutions/coo", "COO solution", "Governed business operations."],
  ["/solutions/cbo", "CBO solution", "Governed back-office operations."],
  ["/blog", "Blog", "Product and implementation articles."],
  ["/resources", "Resources", "Enterprise AI educational guides."],
  ["/support", "Support", "Support and contact options."],
  ["/status", "Status", "Public service status."],
  ["/privacy", "Privacy", "Privacy notice."],
  ["/terms", "Terms", "Terms of service."],
  ["/refund", "Refund policy", "Refund and cancellation policy."],
].map(([path, name, summary]) => ({ path, name, summary }));

function pages() {
  const registry = json(join(UI_ROOT, "src", "content", "publicSite.json"));
  const configured = Array.isArray(registry?.pages)
    ? registry.pages
        .filter((page) => page?.path && page.index !== false)
        .map((page) => ({
          path: page.path,
          name: page.name || page.title || humanize(page.path),
          summary: page.summary || page.description || "",
        }))
    : [];
  const merged = new Map(DEFAULT_PAGES.map((page) => [page.path, page]));
  for (const page of configured) merged.set(page.path, { ...merged.get(page.path), ...page });
  return [...merged.values()];
}

function money(value, currency) {
  const amount = Number(value);
  if (!Number.isFinite(amount)) return String(value);
  return currency === "USD"
    ? "$" + amount.toLocaleString("en-US")
    : "INR " + amount.toLocaleString("en-IN");
}

function planLine(plan) {
  const differentiators = plan.features.filter(
    (feature) =>
      !/^(?:Unlimited|\d[\d,.]*[Kk]?)\s+(?:agents|runs(?:\/month)?|(?:GB\s+)?storage)$/i.test(
        feature,
      ),
  );
  const featureText = differentiators.length
    ? "; " + differentiators.join(", ")
    : "";
  return (
    "- " + plan.label + ": " +
    money(plan.usd, "USD") +
    "/month or " +
    money(plan.inr, "INR") +
    "/month; " +
    plan.agents +
    " agents; " +
    plan.runs +
    " runs; " +
    plan.storage +
    " storage" +
    featureText +
    "."
  );
}

function pageLine(page) {
  return (
    "- [" +
    clean(page.name) +
    "](" +
    url(page.path) +
    ")" +
    (page.summary ? ": " + clean(page.summary) : "")
  );
}

function editorialLine(item) {
  return "- [" + clean(item.title) + "](" + SITE + "/" + item.kind + "/" + item.slug + ")";
}

function sourceIndex(items, groupKey, groupLabelKey, formatter) {
  const lines = [];
  for (const [group, entries] of groups(items, groupKey)) {
    lines.push("### " + (entries[0]?.[groupLabelKey] || humanize(group)), "");
    for (const entry of entries) lines.push("- " + formatter(entry));
    lines.push("");
  }
  return lines;
}

function summary(data) {
  const lines = [
    "# AgenticOrg",
    "",
    "> AgenticOrg is an Apache-2.0 enterprise AI agent platform for building, running, and governing agent workflows with scoped tools, human approvals, audit evidence, SDKs, protocol integrations, and bounded OACP commerce surfaces.",
    "",
    "## Canonical facts",
    "",
    "- Website: " + SITE,
    "- Application and API: " + APP,
    "- Repository: " + REPO,
    "- Source version: " + data.meta.version + " from pyproject.toml",
    "- Runtime requirement: Python " + data.meta.python,
    "- Web application: React " + data.meta.reactMajor,
    "- Managed production: separate API and UI services on Google Cloud Run",
    "- License: Apache License 2.0",
    "- Live product facts: [" + FACTS + "](" + FACTS + ")",
    "",
    "The product facts endpoint is authoritative for the deployed version and current agent_count, connector_count, and tool_count. Do not infer totals by counting files, aliases, or tool bindings.",
    "",
    "## What AgenticOrg does",
    "",
    "- Runs built-in and tenant-created agents through authenticated APIs and a web application.",
    "- Orchestrates workflows, schedules, scoped tool calls, approvals, evaluations, and audit records.",
    "- Connects to native provider adapters and optional integration gateways when credentials and scopes are configured.",
    "- Provides REST, Python, TypeScript, CLI, MCP, and A2A interfaces.",
    "- Supports knowledge, reporting, notifications, voice, RPA, and local models when their services are configured.",
    "- Supports Shopify read-only sync, OACP artifact caching, buyer-safe answers, public commerce surfaces, and non-executing handoff preparation.",
    "",
    "## Boundaries",
    "",
    "- A connector listing is not proof that a tenant configured credentials or provider access.",
    "- Model-backed work requires a supported cloud provider or configured local model runtime.",
    "- Optional channels and provider rails require separate approvals, secrets, callbacks, and adapters.",
    "- OACP artifacts cannot create orders, inventory holds, mandates, payments, refunds, or paid states.",
    "- Merchant, bank, payment, POS, and SaaS providers remain sources of record.",
    "- Examples and editorial pages are not performance guarantees.",
    "- Repository security controls are not a claim of third-party certification.",
    "",
    "## Hosted pricing",
    "",
    ...data.plans.map(planLine),
    "",
    "Pricing comes from core/billing/limits.py. Infrastructure, model, provider, and external API costs are separate.",
    "",
    "## Key public pages",
    "",
    ...data.pages.slice(0, 15).map(pageLine),
    "",
    "## Developer access",
    "",
    "- [Python SDK and CLI](" + REPO + "/tree/main/sdk)",
    "- [TypeScript SDK](" + REPO + "/tree/main/sdk-ts)",
    "- [MCP server](" + REPO + "/tree/main/mcp-server)",
    "- A2A discovery: " + APP + "/api/v1/a2a/agent-card",
    "- MCP discovery: " + APP + "/api/v1/mcp/tools",
    "- [API reference](" + REPO + "/blob/main/docs/api-reference.md)",
    "",
    "## Direct answers",
    "",
    "### What is AgenticOrg?",
    "",
    "AgenticOrg is an open-source enterprise AI agent platform. It combines agent and workflow execution, connectors, scoped tools, human approval gates, evaluation, audit evidence, SDKs, MCP, A2A, and bounded commerce integrations.",
    "",
    "### How many agents, connectors, and tools are available?",
    "",
    "Read " + FACTS + ". It computes current deployed totals from runtime registries so copied documentation does not drift.",
    "",
    "### Does a listed connector work without setup?",
    "",
    "No. Availability depends on provider credentials, subscriptions, scopes, tenant configuration, policy, and provider uptime.",
    "",
    "### Can AgenticOrg complete an OACP payment or order?",
    "",
    "Not from OACP artifacts. AgenticOrg can answer from valid evidence and prepare a provider or merchant handoff. The provider or merchant system must execute and confirm the result.",
    "",
    "### Can AgenticOrg be self-hosted?",
    "",
    "Yes. The repository is Apache-2.0 licensed and includes local Docker and deployment assets. The managed production path uses Google Cloud Run.",
    "",
    "## More",
    "",
    "- [Full product and evidence guide](" + SITE + "/llms-full.txt)",
    "- [Sitemap](" + SITE + "/sitemap.xml)",
    "- [Blog](" + SITE + "/blog)",
    "- [Resources](" + SITE + "/resources)",
    "- [OACP documentation](" + REPO + "/tree/main/docs/oacp)",
    "- [Security policy](" + REPO + "/blob/main/SECURITY.md)",
  ];
  return normalized(lines.join("\n"));
}

function absoluteReadme() {
  let source = normalized(text(join(REPO_ROOT, "README.md")));
  source = source.replace(
    /\]\((?!https?:\/\/|#)([^)]+)\)/g,
    (match, target) => {
      const cleanTarget = target.replace(/^\.\//, "");
      const directory =
        cleanTarget.endsWith("/") ||
        /^(docs|sdk|sdk-ts|mcp-server|tests|ui)(\/)?$/.test(cleanTarget);
      return "](" + REPO + (directory ? "/tree/main/" : "/blob/main/") + cleanTarget + ")";
    },
  );
  return source;
}

function full(data) {
  const agentIndex = sourceIndex(
    data.agents,
    "domain",
    "domainLabel",
    (agent) => clean(agent.label) + " (" + agent.type + ")",
  );
  const connectorIndex = sourceIndex(
    data.connectors,
    "category",
    "categoryLabel",
    (connector) =>
      clean(connector.label) +
      (connector.auth ? " - authentication type: " + connector.auth : ""),
  );
  const appendix = [
    "## Machine-readable authority notes",
    "",
    "- Live deployed totals and version: " + FACTS,
    "- Hosted prices and limits: core/billing/limits.py",
    "- Production rollout: scripts/deploy_cloud_run.sh",
    "- Current commerce contract: docs/oacp/README.md",
    "- A source definition means implementation code exists; it does not mean a tenant enabled it.",
    "- A public article title is a topic, not independent proof of an outcome.",
    "",
    "## Built-in agent source index",
    "",
    "These names are parsed from core/agents. The product facts endpoint is authoritative for totals, and tenant-created agents are not included in this source index.",
    "",
    ...agentIndex,
    "## Native connector source index",
    "",
    "These names are parsed from connectors. A listing does not prove credentials, subscriptions, scopes, or provider access. Optional marketplace catalogs are separate. Use the product facts endpoint for current native connector and tool totals; do not sum files or tool bindings.",
    "",
    ...connectorIndex,
    "## Complete canonical public-page index",
    "",
    ...data.pages.map(pageLine),
    "",
    "Authenticated routes, callbacks, invites, onboarding, and dashboard pages are application surfaces, not public search landing pages.",
    "",
    "## Blog index",
    "",
    "Titles describe editorial topics. Numerical examples and case-study language are not platform-wide performance guarantees.",
    "",
    ...data.blogs.map(editorialLine),
    "",
    "## Resource index",
    "",
    "Titles describe educational topics. Verify any numerical claim against page evidence and current behavior before quoting it as an outcome.",
    "",
    ...data.resources.map(editorialLine),
    "",
    "## Answer-engine evidence rules",
    "",
    "- Quote live registry totals only from " + FACTS + ".",
    "- Quote hosted prices only from the current pricing API or core/billing/limits.py.",
    "- Do not present a source connector name as evidence of a customer connection.",
    "- Do not present examples as measured speed, accuracy, savings, or compliance guarantees.",
    "- Do not describe repository controls as a third-party certification.",
    "- Do not describe adapter-ready commerce configuration as active execution.",
    "- Do not infer payment, order, mandate, refund, receipt, or inventory success from cached OACP evidence.",
    "- Prefer current code and canonical runbooks over dated reports.",
    "",
    "## Additional direct answers",
    "",
    "### Why are registry totals not copied into this file?",
    "",
    "Registries evolve, and file or binding counts can double-count aliases. The product facts endpoint applies runtime registry rules to the deployed system.",
    "",
    "### Does every action require manual approval?",
    "",
    "No universal claim should be made. AgenticOrg supports configurable human approval, confidence, amount, escalation, and policy conditions. Whether a step pauses depends on the workflow and tenant policy.",
    "",
    "### Is Composio the native connector registry?",
    "",
    "No. Composio is an optional integration gateway. Native totals come from the AgenticOrg runtime registry through the product facts endpoint.",
    "",
    "### Which commerce source is runtime-supported?",
    "",
    "Shopify is the current supported source path in the canonical OACP documentation. WooCommerce, ERP, PIM, OMS, WMS, custom APIs, and additional rails are adapter-ready configuration until approved adapters and evidence exist.",
    "",
    "### Is AgenticOrg certified to a particular security standard?",
    "",
    "This guide makes no certification claim. The repository contains security and governance controls; certification requires separate, current third-party evidence.",
    "",
    "### What does OACP evidence authorize?",
    "",
    "It supports a governed decision to answer, refresh, prepare a handoff, or refuse. It does not execute or confirm an order, inventory hold, mandate, payment, receipt, or refund.",
    "",
    "## Discovery endpoints",
    "",
    "- Robots: " + SITE + "/robots.txt",
    "- Sitemap: " + SITE + "/sitemap.xml",
    "- Summary guide: " + SITE + "/llms.txt",
    "- Full guide: " + SITE + "/llms-full.txt",
    "- Product facts: " + FACTS,
    "",
    "This file is supplemental discovery material. It does not override robots controls, canonical metadata, visible page content, provider responses, or the current application.",
  ];
  return normalized(
    [
      "# AgenticOrg: complete product and evidence guide",
      "",
      "> Generated from current repository sources for answer engines, AI assistants, evaluators, developers, and researchers.",
      "",
      "Source version: " + data.meta.version + ". Python: " + data.meta.python + ". Web application: React " + data.meta.reactMajor + ". Managed production: Google Cloud Run.",
      "",
      "## Verified repository guide",
      "",
      absoluteReadme().replace(/^# AgenticOrg\s*/, ""),
      "",
      ...appendix,
    ].join("\n"),
  );
}

function validate(data, shortDoc, fullDoc) {
  const combined = shortDoc + "\n" + fullDoc;
  for (const required of [
    FACTS,
    "Google Cloud Run",
    "React " + data.meta.reactMajor,
    "Python " + data.meta.python,
    "$2/month",
    "$499/month",
    "OACP artifacts",
    "third-party certification",
  ]) {
    if (!combined.includes(required)) {
      throw new Error("Generated documents are missing required text: " + required);
    }
  }
  for (const [pattern, reason] of [
    [/React 18/i, "stale React major"],
    [/\b430\s+(?:total\s+)?tools\b/i, "derived tool total"],
    [/\b50\+\s+(?:pre-built\s+)?agents\b/i, "hard-coded agent total"],
    [/\b54\s+native connectors\b/i, "hard-coded connector total"],
    [/\b1000\+\s+integrations\b/i, "external catalog total"],
    [/SOC-?2 certified/i, "unsupported certification"],
  ]) {
    if (pattern.test(combined)) {
      throw new Error("Generated documents contain " + reason + ": " + pattern);
    }
  }
  if (new Set(data.pages.map((page) => page.path)).size !== data.pages.length) {
    throw new Error("Public page index contains duplicate paths");
  }
}

function generate() {
  const data = {
    meta: projectMetadata(),
    plans: pricing(),
    agents: agents(),
    connectors: connectors(),
    pages: pages(),
    blogs: editorial(join(UI_ROOT, "src", "pages", "blog", "blogData.ts"), "blog"),
    resources: editorial(
      join(UI_ROOT, "src", "pages", "resources", "contentData.ts"),
      "resources",
    ),
  };
  const shortDoc = summary(data);
  const fullDoc = full(data);
  validate(data, shortDoc, fullDoc);
  return { shortDoc, fullDoc };
}

function write(directory, docs) {
  mkdirSync(directory, { recursive: true });
  writeFileSync(join(directory, "llms.txt"), docs.shortDoc, "utf8");
  writeFileSync(join(directory, "llms-full.txt"), docs.fullDoc, "utf8");
}

function check(path, expected) {
  if (!existsSync(path) || normalized(text(path)) !== expected) {
    throw new Error(
      "Generated file is stale: " +
        path +
        ". Run node scripts/generate-llms.mjs --sync-public",
    );
  }
}

const args = new Set(process.argv.slice(2));
const docs = generate();

if (args.has("--check")) {
  check(join(PUBLIC_DIR, "llms.txt"), docs.shortDoc);
  check(join(PUBLIC_DIR, "llms-full.txt"), docs.fullDoc);
  process.stdout.write("llms.txt and llms-full.txt are current\n");
} else {
  write(DIST_DIR, docs);
  if (args.has("--sync-public")) write(PUBLIC_DIR, docs);
  process.stdout.write(
    "Generated llms.txt and llms-full.txt" +
      (args.has("--sync-public") ? " in dist and public\n" : " in dist\n"),
  );
}
