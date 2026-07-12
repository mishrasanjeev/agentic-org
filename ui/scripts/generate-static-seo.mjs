#!/usr/bin/env node
/**
 * Generate crawlable HTML shells for every public route after Vite builds.
 *
 * The application remains a client-rendered SPA. These shells give crawlers
 * and link unfurlers route-specific source HTML before JavaScript executes.
 */
import {
  existsSync,
  mkdirSync,
  readFileSync,
  writeFileSync,
} from "node:fs";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { dirname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
export const UI_ROOT = resolve(SCRIPT_DIR, "..");
const REPO_ROOT = resolve(UI_ROOT, "..");

const decodeQuoted = (value) => JSON.parse('"' + value + '"');

function stringField(block, name) {
  const match = block.match(
    new RegExp("\\b" + name + ':\\s*"((?:\\\\.|[^"\\\\])*)"'),
  );
  return match ? decodeQuoted(match[1]) : "";
}

function stringArrayField(block, name) {
  const startMatch = new RegExp("\\b" + name + ":\\s*\\[").exec(block);
  if (!startMatch) return [];
  let depth = 1;
  let quote = "";
  let escaped = false;
  let end = -1;
  const bodyStart = startMatch.index + startMatch[0].length;
  for (let index = bodyStart; index < block.length; index += 1) {
    const char = block[index];
    if (quote) {
      if (escaped) escaped = false;
      else if (char === "\\") escaped = true;
      else if (char === quote) quote = "";
      continue;
    }
    if (char === '"' || char === "'" || char === String.fromCharCode(96)) {
      quote = char;
    } else if (char === "[") {
      depth += 1;
    } else if (char === "]") {
      depth -= 1;
      if (depth === 0) {
        end = index;
        break;
      }
    }
  }
  if (end < 0) return [];
  return [...block.slice(bodyStart, end).matchAll(/"((?:\\.|[^"\\])*)"/g)]
    .map((match) => decodeQuoted(match[1]));
}

/**
 * Extract top-level object literals from a named TypeScript data array without
 * executing TypeScript. Nested objects and braces inside strings are supported.
 */
export function extractObjectBlocks(source, arrayName) {
  const marker = new RegExp("export\\s+const\\s+" + arrayName + "\\b").exec(source);
  if (!marker) throw new Error("Could not find " + arrayName + " in content source");
  const assignment = source.indexOf("=", marker.index);
  const arrayStart = source.indexOf("[", assignment);
  if (arrayStart < 0) throw new Error("Could not find " + arrayName + " array");

  const blocks = [];
  let objectStart = -1;
  let braceDepth = 0;
  let bracketDepth = 1;
  let quote = "";
  let escaped = false;
  let lineComment = false;
  let blockComment = false;

  for (let index = arrayStart + 1; index < source.length; index += 1) {
    const char = source[index];
    const next = source[index + 1];
    if (lineComment) {
      if (char === "\n") lineComment = false;
      continue;
    }
    if (blockComment) {
      if (char === "*" && next === "/") {
        blockComment = false;
        index += 1;
      }
      continue;
    }
    if (quote) {
      if (escaped) escaped = false;
      else if (char === "\\") escaped = true;
      else if (char === quote) quote = "";
      continue;
    }
    if (char === "/" && next === "/") {
      lineComment = true;
      index += 1;
    } else if (char === "/" && next === "*") {
      blockComment = true;
      index += 1;
    } else if (char === '"' || char === "'" || char === String.fromCharCode(96)) {
      quote = char;
    } else if (char === "{") {
      if (braceDepth === 0) objectStart = index;
      braceDepth += 1;
    } else if (char === "}") {
      braceDepth -= 1;
      if (braceDepth === 0 && objectStart >= 0) {
        blocks.push(source.slice(objectStart, index + 1));
        objectStart = -1;
      }
    } else if (braceDepth === 0 && char === "[") {
      bracketDepth += 1;
    } else if (braceDepth === 0 && char === "]") {
      bracketDepth -= 1;
      if (bracketDepth === 0) break;
    }
  }
  return blocks;
}

export function gitLastModified(filePath, repoRoot = REPO_ROOT) {
  try {
    const path = relative(repoRoot, filePath).split(sep).join("/");
    const date = execFileSync(
      "git",
      ["log", "-1", "--format=%cs", "--", path],
      { cwd: repoRoot, encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] },
    ).trim();
    return /^\d{4}-\d{2}-\d{2}$/.test(date) ? date : undefined;
  } catch {
    return undefined;
  }
}

export function readPublicSite(root = UI_ROOT) {
  const path = join(root, "src/content/publicSite.json");
  const value = JSON.parse(readFileSync(path, "utf8"));
  if (!value || !value.site || !value.site.url || !Array.isArray(value.pages)) {
    throw new Error("publicSite.json must define site.url and pages[]");
  }
  return value;
}

function normalizePath(path) {
  if (typeof path !== "string" || !path.startsWith("/")) {
    throw new Error("Invalid public route path: " + String(path));
  }
  return path === "/" ? "/" : path.replace(/\/+$/, "");
}

function parseFaqs(block) {
  const pattern = /\{\s*q:\s*"((?:\\.|[^"\\])*)"\s*,\s*a:\s*"((?:\\.|[^"\\])*)"\s*\}/g;
  return [...block.matchAll(pattern)].map((match) => ({
    question: decodeQuoted(match[1]),
    answer: decodeQuoted(match[2]),
  }));
}

export function loadRouteDescriptors(root = UI_ROOT) {
  const manifest = readPublicSite(root);
  const repoRoot = resolve(root, "..");
  const manifestPath = join(root, "src/content/publicSite.json");
  const staticLastmod = gitLastModified(manifestPath, repoRoot);
  const staticRoutes = manifest.pages.map((page) => ({
    ...page,
    path: normalizePath(page.path),
    index: page.index !== false,
    kind: "static",
    lastmod: page.lastmod || staticLastmod,
  }));

  const blogPath = join(root, "src/pages/blog/blogData.ts");
  const blogs = extractObjectBlocks(readFileSync(blogPath, "utf8"), "BLOG_POSTS")
    .map((block) => {
      const title = stringField(block, "title");
      const date = stringField(block, "date");
      return {
        path: "/blog/" + stringField(block, "slug"),
        name: title,
        title: title + " | AgenticOrg Blog",
        description: stringField(block, "description"),
        summary: stringField(block, "description"),
        section: "Blog",
        schemaType: "BlogPosting",
        index: true,
        kind: "blog",
        lastmod: date || gitLastModified(blogPath, repoRoot),
        datePublished: date || undefined,
        author: stringField(block, "author") || manifest.site.name,
        keywords: stringArrayField(block, "keywords"),
      };
    });

  const resourcePath = join(root, "src/pages/resources/contentData.ts");
  const resourceLastmod = gitLastModified(resourcePath, repoRoot);
  const resources = extractObjectBlocks(
    readFileSync(resourcePath, "utf8"),
    "CONTENT_PAGES",
  ).map((block) => ({
    path: "/resources/" + stringField(block, "slug"),
    name: stringField(block, "title"),
    title: stringField(block, "metaTitle") || stringField(block, "title"),
    description: stringField(block, "metaDescription"),
    summary: stringField(block, "metaDescription"),
    section: "Resources",
    schemaType: "TechArticle",
    index: true,
    kind: "resource",
    lastmod: resourceLastmod,
    author: manifest.site.name,
    keywords: stringArrayField(block, "keywords"),
    faqs: parseFaqs(block),
  }));

  const routes = [...staticRoutes, ...blogs, ...resources];
  if (!routes.some((route) => route.path === "/")) {
    routes.unshift({
      path: "/",
      name: manifest.site.name,
      title: manifest.site.name,
      description: manifest.site.description,
      summary: manifest.site.description,
      section: "Home",
      schemaType: "WebPage",
      index: true,
      kind: "static",
      lastmod: staticLastmod,
    });
  }
  return { manifest, routes };
}

const escapeHtml = (value) =>
  String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");

const canonicalUrl = (site, path) => {
  const base = site.url.replace(/\/+$/, "");
  return path === "/" ? base + "/" : base + path;
};

function breadcrumbs(route, site) {
  const crumbs = [{ name: "Home", url: canonicalUrl(site, "/") }];
  if (route.kind === "blog") {
    crumbs.push({ name: "Blog", url: canonicalUrl(site, "/blog") });
  } else if (route.kind === "resource") {
    crumbs.push({ name: "Resources", url: canonicalUrl(site, "/resources") });
  }
  if (route.path !== "/") {
    crumbs.push({
      name: route.name || route.title,
      url: canonicalUrl(site, route.path),
    });
  }
  return crumbs;
}

function compact(value) {
  if (Array.isArray(value)) return value.map(compact);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value)
        .filter(([, item]) => item !== undefined)
        .map(([key, item]) => [key, compact(item)]),
    );
  }
  return value;
}

function buildJsonLd(route, manifest) {
  const site = manifest.site;
  const url = canonicalUrl(site, route.path);
  const crumbs = breadcrumbs(route, site);
  const base = site.url.replace(/\/+$/, "");
  const organizationId = base + "/#organization";
  const websiteId = base + "/#website";
  const pageId = url + "#webpage";
  const pageTypes = new Set([
    "AboutPage", "CollectionPage", "ContactPage", "ItemPage",
    "ProfilePage", "QAPage", "WebPage",
  ]);
  const graph = [
    {
      "@type": "Organization",
      "@id": organizationId,
      name: site.name,
      url: site.url,
      logo: site.logo ? { "@type": "ImageObject", url: site.logo } : undefined,
      email: site.email || undefined,
      sameAs: site.github ? [site.github] : undefined,
    },
    {
      "@type": "WebSite",
      "@id": websiteId,
      url: base + "/",
      name: site.name,
      description: site.description,
      publisher: { "@id": organizationId },
      inLanguage: site.language || "en",
    },
    {
      "@type": pageTypes.has(route.schemaType) ? route.schemaType : "WebPage",
      "@id": pageId,
      url,
      name: route.title,
      description: route.description,
      isPartOf: { "@id": websiteId },
      about: { "@id": organizationId },
      inLanguage: site.language || "en",
      breadcrumb: crumbs.length > 1 ? { "@id": url + "#breadcrumb" } : undefined,
    },
  ];
  if (crumbs.length > 1) {
    graph.push({
      "@type": "BreadcrumbList",
      "@id": url + "#breadcrumb",
      itemListElement: crumbs.map((crumb, index) => ({
        "@type": "ListItem",
        position: index + 1,
        name: crumb.name,
        item: crumb.url,
      })),
    });
  }
  if (route.kind === "blog" || route.kind === "resource" || route.schemaType === "TechArticle") {
    graph.push({
      "@type": route.kind === "blog" ? "BlogPosting" : "TechArticle",
      "@id": url + "#article",
      headline: route.name,
      description: route.description,
      url,
      mainEntityOfPage: { "@id": pageId },
      datePublished: route.datePublished || undefined,
      dateModified: route.datePublished || undefined,
      author: { "@type": "Organization", name: route.author || site.name },
      publisher: { "@id": organizationId },
      image: site.defaultImage || undefined,
      keywords: route.keywords && route.keywords.length
        ? route.keywords.join(", ")
        : undefined,
    });
  }
  // FAQ schema is intentionally limited to the landing page. It comes from
  // the same registry as the visible Landing FAQ section.
  if (route.path === "/" && Array.isArray(manifest.landingFaqs)) {
    graph.push({
      "@type": "FAQPage",
      "@id": url + "#faq",
      mainEntity: manifest.landingFaqs.map((faq) => ({
        "@type": "Question",
        name: faq.question,
        acceptedAnswer: { "@type": "Answer", text: faq.answer },
      })),
    });
  }
  return compact({ "@context": "https://schema.org", "@graph": graph });
}

function stripRouteMetadata(html) {
  const names = [
    "description", "robots", "googlebot", "bingbot", "twitter:card",
    "twitter:site", "twitter:creator", "twitter:title", "twitter:description",
    "twitter:url", "twitter:image", "twitter:image:alt",
  ];
  for (const name of names) {
    const escapedName = name.replace(":", "\\:");
    html = html.replace(
      new RegExp("<meta\\s+[^>]*name=[\"']" + escapedName + "[\"'][^>]*>\\s*", "gi"),
      "",
    );
  }
  html = html.replace(/<meta\s+[^>]*property=["'](?:og|article):[^"']+["'][^>]*>\s*/gi, "");
  html = html.replace(/<link\s+[^>]*rel=["']canonical["'][^>]*>\s*/gi, "");
  html = html.replace(/<script\s+type=["']application\/ld\+json["'][^>]*>[\s\S]*?<\/script>\s*/gi, "");
  html = html.replace(/<noscript\b[^>]*>[\s\S]*?<\/noscript>\s*/gi, "");
  return html;
}

export function renderStaticHtml(baseHtml, route, manifest) {
  const site = manifest.site;
  const url = canonicalUrl(site, route.path);
  const image = site.defaultImage;
  const imageAlt = route.path === "/"
    ? "AgenticOrg governed enterprise AI agent platform"
    : route.name + " - " + site.name;
  const robots = route.index === false
    ? "noindex, nofollow"
    : "index, follow, max-image-preview:large, max-snippet:-1, max-video-preview:-1";
  let html = stripRouteMetadata(baseHtml);
  html = html.replace(
    /<title\b[^>]*>[\s\S]*?<\/title>/i,
    '<title data-rh="true">' + escapeHtml(route.title) + "</title>",
  );
  const tags = [
    '<meta name="description" content="' + escapeHtml(route.description) + '" data-rh="true" />',
    '<meta name="robots" content="' + robots + '" data-rh="true" />',
    '<meta name="googlebot" content="' + robots + '" data-rh="true" />',
    '<meta name="bingbot" content="' + robots + '" data-rh="true" />',
    '<link rel="canonical" href="' + escapeHtml(url) + '" data-rh="true" />',
    '<meta property="og:type" content="' + (route.kind === "static" && route.schemaType !== "TechArticle" ? "website" : "article") + '" data-rh="true" />',
    '<meta property="og:site_name" content="' + escapeHtml(site.name) + '" data-rh="true" />',
    '<meta property="og:locale" content="' + escapeHtml(site.locale || "en_US") + '" data-rh="true" />',
    '<meta property="og:title" content="' + escapeHtml(route.title) + '" data-rh="true" />',
    '<meta property="og:description" content="' + escapeHtml(route.description) + '" data-rh="true" />',
    '<meta property="og:url" content="' + escapeHtml(url) + '" data-rh="true" />',
    image ? '<meta property="og:image" content="' + escapeHtml(image) + '" data-rh="true" />' : "",
    image ? '<meta property="og:image:width" content="1200" data-rh="true" />' : "",
    image ? '<meta property="og:image:height" content="630" data-rh="true" />' : "",
    image ? '<meta property="og:image:alt" content="' + escapeHtml(imageAlt) + '" data-rh="true" />' : "",
    '<meta name="twitter:card" content="summary_large_image" data-rh="true" />',
    '<meta name="twitter:title" content="' + escapeHtml(route.title) + '" data-rh="true" />',
    '<meta name="twitter:description" content="' + escapeHtml(route.description) + '" data-rh="true" />',
    '<meta name="twitter:url" content="' + escapeHtml(url) + '" data-rh="true" />',
    image ? '<meta name="twitter:image" content="' + escapeHtml(image) + '" data-rh="true" />' : "",
    image ? '<meta name="twitter:image:alt" content="' + escapeHtml(imageAlt) + '" data-rh="true" />' : "",
    route.datePublished
      ? '<meta property="article:published_time" content="' + route.datePublished + '" data-rh="true" />'
      : "",
    route.lastmod
      ? '<meta property="article:modified_time" content="' + route.lastmod + '" data-rh="true" />'
      : "",
    '<script type="application/ld+json" data-rh="true">' +
      JSON.stringify(buildJsonLd(route, manifest)).replaceAll("<", "\\u003c") +
      "</script>",
  ].filter(Boolean).join("\n  ");
  html = html.replace(
    "</head>",
    "  <!-- route-seo:generated -->\n  " + tags + "\n</head>",
  );

  const crumbs = breadcrumbs(route, site);
  const faqHtml = route.path === "/" && Array.isArray(manifest.landingFaqs)
    ? "<section><h2>Frequently asked questions</h2>" +
      manifest.landingFaqs.map((faq) =>
        "<h3>" + escapeHtml(faq.question) + "</h3><p>" +
        escapeHtml(faq.answer) + "</p>",
      ).join("") + "</section>"
    : "";
  const crumbHtml = crumbs.map((crumb) =>
    '<a href="' + escapeHtml(crumb.url) + '">' + escapeHtml(crumb.name) + "</a>",
  ).join(" / ");
  const noscript =
    '<noscript><main data-static-seo="true"><nav aria-label="Breadcrumb">' +
    crumbHtml + "</nav><h1>" + escapeHtml(route.name || route.title) +
    "</h1><p>" + escapeHtml(route.summary || route.description) + "</p>" +
    faqHtml + '<p><a href="' + escapeHtml(url) + '">View this page on ' +
    escapeHtml(site.name) + "</a></p></main></noscript>";
  return html.replace(
    '<div id="root"></div>',
    noscript + '\n  <div id="root"></div>',
  );
}

export function outputPathsForRoute(root, route, allRoutes) {
  const dist = join(root, "dist");
  if (route.path === "/") return [join(dist, "index.html")];
  const segments = route.path.slice(1).split("/");
  const flat = join(dist, ...segments) + ".html";
  const hasChildren = allRoutes.some(
    (candidate) => candidate.path.startsWith(route.path + "/"),
  );
  return hasChildren
    ? [flat, join(dist, ...segments, "index.html")]
    : [flat];
}

export function cspHash(body) {
  return "sha256-" + createHash("sha256").update(body, "utf8").digest("base64");
}

/**
 * Synchronize hashes for source index JSON-LD and every generated route graph.
 * This is intentionally opt-in: normal builds stay hermetic. Run after content
 * or publicSite metadata changes, then commit both Nginx configs.
 */
export function syncRouteCsp(root = UI_ROOT, routes = loadRouteDescriptors(root).routes) {
  const bodies = [];
  const sourceIndex = readFileSync(join(root, "index.html"), "utf8")
    .replaceAll("\r\n", "\n");
  const sourcePattern =
    /<script type="application\/ld\+json">\n(.*?)\n  <\/script>/gs;
  for (const match of sourceIndex.matchAll(sourcePattern)) bodies.push(match[1]);

  for (const route of routes) {
    const output = outputPathsForRoute(root, route, routes)[0];
    const html = readFileSync(output, "utf8");
    const match = html.match(
      /<script\s+type=["']application\/ld\+json["'][^>]*>([\s\S]*?)<\/script>/i,
    );
    if (!match) throw new Error("Generated JSON-LD missing from " + output);
    bodies.push(match[1]);
  }
  const hashes = [...new Set(bodies.map(cspHash))];
  if (!hashes.length) throw new Error("No JSON-LD bodies found for CSP sync");
  const hashList = hashes.map((hash) => "'" + hash + "'").join(" ");
  const pattern =
    /(script-src 'self')(?: '(?:sha256-[A-Za-z0-9+/=]+|unsafe-inline)')*( https:\/\/accounts\.google\.com)/;

  const configs = ["nginx.conf", "nginx.cloudrun.conf.template"];
  for (const name of configs) {
    const path = join(root, name);
    const text = readFileSync(path, "utf8");
    if (!pattern.test(text)) throw new Error("script-src hash list not found in " + name);
    writeFileSync(path, text.replace(pattern, "$1 " + hashList + "$2"), "utf8");
  }
  return { hashes, configs };
}
export function generateStaticSeo(root = UI_ROOT) {
  const { manifest, routes } = loadRouteDescriptors(root);
  const indexPath = join(root, "dist/index.html");
  if (!existsSync(indexPath)) {
    throw new Error("Vite output not found at " + indexPath + "; run vite build first");
  }
  const baseHtml = readFileSync(indexPath, "utf8");
  for (const route of routes) {
    if (!route.title || !route.description || !route.name) {
      throw new Error("Incomplete SEO metadata for " + route.path);
    }
    const rendered = renderStaticHtml(baseHtml, route, manifest);
    for (const outputPath of outputPathsForRoute(root, route, routes)) {
      mkdirSync(dirname(outputPath), { recursive: true });
      writeFileSync(outputPath, rendered, "utf8");
    }
  }
  return { routes, count: routes.length };
}

const isMain = process.argv[1] &&
  resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isMain) {
  const result = generateStaticSeo();
  console.log("Generated route-specific HTML for " + result.count + " routes.");
  if (process.argv.includes("--sync-csp")) {
    const synced = syncRouteCsp(UI_ROOT, result.routes);
    console.log("Synchronized " + synced.hashes.length + " JSON-LD CSP hashes.");
  }
}
