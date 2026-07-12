#!/usr/bin/env node
/**
 * Fail the build when public route metadata, generated shells, sitemap,
 * robots policy, and Nginx crawl behavior drift apart.
 */
import { existsSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import {
  cspHash,
  loadRouteDescriptors,
  outputPathsForRoute,
  UI_ROOT,
} from "./generate-static-seo.mjs";
import { readHostedIndexNowKey } from "./submit-indexing.mjs";

const countMatches = (text, pattern) => [...text.matchAll(pattern)].length;
const htmlDecode = (value) =>
  String(value).replace(/&(?:amp|quot|#39|lt|gt);/g, (entity) => {
    switch (entity) {
      case "&amp;": return "&";
      case "&quot;": return '"';
      case "&#39;": return "'";
      case "&lt;": return "<";
      case "&gt;": return ">";
      default: return entity;
    }
  });

const canonicalUrl = (siteUrl, path) => {
  const base = siteUrl.replace(/\/+$/, "");
  return path === "/" ? base + "/" : base + path;
};

function attribute(html, elementPattern, attributeName) {
  const element = html.match(elementPattern);
  if (!element) return "";
  const match = element[0].match(
    new RegExp("\\b" + attributeName + "=([\"'])([\\s\\S]*?)\\1", "i"),
  );
  return match ? htmlDecode(match[2]) : "";
}

function collectTypes(value, result = []) {
  if (Array.isArray(value)) {
    for (const item of value) collectTypes(item, result);
  } else if (value && typeof value === "object") {
    if (typeof value["@type"] === "string") result.push(value["@type"]);
    for (const item of Object.values(value)) collectTypes(item, result);
  }
  return result;
}

function sitemapLocations(xml) {
  return [...xml.matchAll(/<loc>([^<]+)<\/loc>/g)].map((match) =>
    htmlDecode(match[1]),
  );
}

function noindexMapPatterns(config) {
  const mapBlock = config.match(
    /map\s+\$request_uri\s+\$seo_robots_tag\s*\{([\s\S]*?)^\}/m,
  );
  if (!mapBlock) return [];
  return [...mapBlock[1].matchAll(
    /^\s*~(\S+)\s+"noindex,\s*nofollow";\s*$/gm,
  )].map((match) => new RegExp(match[1]));
}

const hasNoindexHeader = (patterns, uri) =>
  patterns.some((pattern) => pattern.test(uri));

export function verifySeo(root = UI_ROOT) {
  const errors = [];
  const warnings = [];
  const fail = (message) => errors.push(message);
  const { manifest, routes } = loadRouteDescriptors(root);
  const siteUrl = manifest.site.url;
  const paths = new Set();
  const titles = new Map();
  const descriptions = new Map();
  const routeCspHashes = new Set();

  if (manifest.pages.length < 20) {
    fail("publicSite.json should enumerate every static public/auth route");
  }
  for (const route of routes) {
    if (paths.has(route.path)) fail("duplicate route path: " + route.path);
    paths.add(route.path);
    if (!route.name || !route.title || !route.description) {
      fail("incomplete metadata: " + route.path);
      continue;
    }
    if (route.title.length < 10 || route.title.length > 120) {
      warnings.push("unusual title length (" + route.title.length + "): " + route.path);
    }
    if (route.description.length < 40 || route.description.length > 240) {
      warnings.push(
        "unusual description length (" + route.description.length + "): " + route.path,
      );
    }
    const previousTitle = titles.get(route.title);
    if (previousTitle) {
      fail("duplicate title: " + previousTitle + " and " + route.path);
    }
    titles.set(route.title, route.path);
    const previousDescription = descriptions.get(route.description);
    if (previousDescription) {
      fail("duplicate description: " + previousDescription + " and " + route.path);
    }
    descriptions.set(route.description, route.path);

    const expectedCanonical = canonicalUrl(siteUrl, route.path);
    const outputs = outputPathsForRoute(root, route, routes);
    for (const output of outputs) {
      if (!existsSync(output)) {
        fail("missing generated HTML: " + output);
        continue;
      }
      const html = readFileSync(output, "utf8");
      const label = route.path + " (" + output + ")";
      if (countMatches(html, /<title\b[^>]*>[\s\S]*?<\/title>/gi) !== 1) {
        fail("expected exactly one title: " + label);
      }
      if (countMatches(html, /<meta\s+[^>]*name=["']description["'][^>]*>/gi) !== 1) {
        fail("expected exactly one meta description: " + label);
      }
      if (countMatches(html, /<link\s+[^>]*rel=["']canonical["'][^>]*>/gi) !== 1) {
        fail("expected exactly one canonical: " + label);
      }
      const helmetManaged = [
        /<title\b[^>]*data-rh=["']true["'][^>]*>/i,
        /<meta\s+[^>]*name=["']description["'][^>]*data-rh=["']true["'][^>]*>/i,
        /<meta\s+[^>]*name=["']robots["'][^>]*data-rh=["']true["'][^>]*>/i,
        /<link\s+[^>]*rel=["']canonical["'][^>]*data-rh=["']true["'][^>]*>/i,
        /<meta\s+[^>]*property=["']og:title["'][^>]*data-rh=["']true["'][^>]*>/i,
        /<meta\s+[^>]*name=["']twitter:title["'][^>]*data-rh=["']true["'][^>]*>/i,
        /<script\s+[^>]*type=["']application\/ld\+json["'][^>]*data-rh=["']true["'][^>]*>/i,
      ];
      for (const pattern of helmetManaged) {
        if (!pattern.test(html)) {
          fail("hydration-managed metadata is missing data-rh in " + label);
        }
      }

      const canonical = attribute(
        html,
        /<link\s+[^>]*rel=["']canonical["'][^>]*>/i,
        "href",
      );
      if (canonical !== expectedCanonical) {
        fail("canonical mismatch for " + label + ": " + canonical);
      }
      const description = attribute(
        html,
        /<meta\s+[^>]*name=["']description["'][^>]*>/i,
        "content",
      );
      if (description !== route.description) {
        fail("description mismatch: " + label);
      }
      const robots = attribute(
        html,
        /<meta\s+[^>]*name=["']robots["'][^>]*>/i,
        "content",
      );
      if (route.index === false && !robots.includes("noindex")) {
        fail("noindex route is missing noindex: " + label);
      }
      if (route.index !== false && !robots.includes("index")) {
        fail("indexable route is missing index: " + label);
      }
      const required = [
        /<meta\s+[^>]*name=["']googlebot["'][^>]*>/i,
        /<meta\s+[^>]*name=["']bingbot["'][^>]*>/i,
        /<meta\s+[^>]*property=["']og:locale["'][^>]*>/i,
        /<meta\s+[^>]*property=["']og:title["'][^>]*>/i,
        /<meta\s+[^>]*property=["']og:description["'][^>]*>/i,
        /<meta\s+[^>]*property=["']og:url["'][^>]*>/i,
        /<meta\s+[^>]*property=["']og:image["'][^>]*>/i,
        /<meta\s+[^>]*property=["']og:image:alt["'][^>]*>/i,
        /<meta\s+[^>]*name=["']twitter:card["'][^>]*>/i,
        /<meta\s+[^>]*name=["']twitter:title["'][^>]*>/i,
        /<meta\s+[^>]*name=["']twitter:description["'][^>]*>/i,
        /<meta\s+[^>]*name=["']twitter:url["'][^>]*>/i,
        /<meta\s+[^>]*name=["']twitter:image["'][^>]*>/i,
        /<meta\s+[^>]*name=["']twitter:image:alt["'][^>]*>/i,
      ];
      for (const pattern of required) {
        const exactPattern = new RegExp(pattern.source, "gi");
        if (countMatches(html, exactPattern) !== 1) {
          fail("expected exactly one of each social metadata field in " + label);
        }
      }
      if (!/<noscript><main data-static-seo="true">/.test(html)) {
        fail("missing static noscript summary: " + label);
      }

      const scripts = [...html.matchAll(
        /<script\s+type=["']application\/ld\+json["'][^>]*>([\s\S]*?)<\/script>/gi,
      )];
      if (scripts.length !== 1) {
        fail("expected one generated JSON-LD graph: " + label);
      }
      for (const script of scripts) {
        routeCspHashes.add(cspHash(script[1]));
        try {
          const data = JSON.parse(script[1]);
          const types = collectTypes(data);
          if (types.includes("AggregateRating") || types.includes("SearchAction")) {
            fail("unsupported structured-data claim in " + label);
          }
          if (route.path === "/" && !types.includes("FAQPage")) {
            fail("landing page must contain registry-backed FAQPage JSON-LD");
          }
          if (route.path !== "/" && types.includes("FAQPage")) {
            fail("FAQPage JSON-LD leaked from landing page into " + label);
          }
          if (route.path !== "/" && !types.includes("BreadcrumbList")) {
            fail("non-root page is missing BreadcrumbList: " + label);
          }
        } catch (error) {
          fail("invalid JSON-LD in " + label + ": " + error.message);
        }
      }
    }
  }

  const privatePaths = [
    "/login", "/signup", "/forgot-password", "/reset-password", "/invite",
    "/accept-invite",
    "/sso/callback", "/onboarding",
  ];
  for (const path of privatePaths) {
    const route = routes.find((candidate) => candidate.path === path);
    if (!route || route.index !== false) fail("private route must be registered noindex: " + path);
  }

  const sitemapPath = join(root, "dist/sitemap.xml");
  if (!existsSync(sitemapPath)) {
    fail("dist/sitemap.xml is missing");
  } else {
    const xml = readFileSync(sitemapPath, "utf8");
    if (/<(?:changefreq|priority)>/.test(xml)) {
      fail("sitemap contains ignored changefreq/priority hints");
    }
    const actual = sitemapLocations(xml);
    const expected = routes
      .filter((route) => route.index !== false)
      .map((route) => canonicalUrl(siteUrl, route.path));
    if (actual.length !== new Set(actual).size) fail("sitemap contains duplicate URLs");
    const missing = expected.filter((url) => !actual.includes(url));
    const extra = actual.filter((url) => !expected.includes(url));
    if (missing.length) fail("sitemap missing: " + missing.join(", "));
    if (extra.length) fail("sitemap has non-indexable/unknown URLs: " + extra.join(", "));
    for (const match of xml.matchAll(/<lastmod>([^<]+)<\/lastmod>/g)) {
      if (!/^\d{4}-\d{2}-\d{2}$/.test(match[1])) {
        fail("invalid sitemap lastmod: " + match[1]);
      }
    }
  }

  const robots = readFileSync(join(root, "public/robots.txt"), "utf8");
  const userAgents = [...robots.matchAll(/^User-agent:\s*(.+)$/gim)]
    .map((match) => match[1].trim());
  if (userAgents.length !== 1 || userAgents[0] !== "*") {
    fail("robots.txt must use one wildcard group so rules cannot be overridden");
  }
  for (const path of ["/dashboard", "/api/", "/ws/"]) {
    if (!robots.includes("Disallow: " + path)) {
      fail("robots.txt does not exclude " + path);
    }
  }
  if (!robots.includes("Sitemap: " + siteUrl.replace(/\/+$/, "") + "/sitemap.xml")) {
    fail("robots.txt sitemap URL does not match the canonical host");
  }

  try {
    readHostedIndexNowKey(join(root, "public"));
  } catch (error) {
    fail("invalid hosted IndexNow key: " + error.message);
  }

  for (const configName of ["nginx.conf", "nginx.cloudrun.conf.template"]) {
    const config = readFileSync(join(root, configName), "utf8");
    if (countMatches(config, /^\s*add_header Content-Security-Policy\b.*$/gm) !== 1) {
      fail(configName + " must contain one active Content-Security-Policy header");
    }
    const configLines = config.replaceAll("\r\n", "\n").split("\n");
    if (configLines.some((line) => Buffer.byteLength(line, "utf8") >= 4096)) {
      fail(configName + " contains an nginx parameter line of 4096+ characters");
    }
    const cspHeader = configLines.find((line) =>
      line.includes("add_header Content-Security-Policy")
    ) || "";
    if (!cspHeader.includes("$csp_jsonld_hashes_")) {
      fail(configName + " must reference bounded JSON-LD CSP hash variables");
    }
    const imageSources = cspHeader.match(/img-src\s+([^;]+);/)?.[1] ?? "";
    if (!imageSources.split(/\s+/).some((source) =>
      /^https:\/\/www\.googletagmanager\.com$/.test(source)
    )) {
      fail(configName + " must allow the trusted Google Tag Manager image endpoint");
    }
    if (countMatches(config, /# BEGIN GENERATED JSON-LD CSP HASHES/g) !== 1 ||
        countMatches(config, /# END GENERATED JSON-LD CSP HASHES/g) !== 1) {
      fail(configName + " must contain exactly one generated JSON-LD CSP hash block");
    }
    const definedHashVariables = [...config.matchAll(
      /set\s+\$(csp_jsonld_hashes_\d+)\s+"[^"]*";/g,
    )].map((match) => match[1]);
    const referencedHashVariables = [...cspHeader.matchAll(
      /\$(csp_jsonld_hashes_\d+)/g,
    )].map((match) => match[1]);
    if (definedHashVariables.length === 0 ||
        new Set(definedHashVariables).size !== definedHashVariables.length ||
        definedHashVariables.join("|") !== referencedHashVariables.join("|")) {
      fail(configName + " must reference every generated CSP hash chunk exactly once");
    }
    for (const hash of routeCspHashes) {
      if (!config.includes("'" + hash + "'")) {
        fail(configName + " is missing generated JSON-LD CSP hash " + hash);
      }
    }
    if (config.includes("sub_filter")) fail(configName + " still uses sub_filter");
    if (!config.includes("add_header_inherit merge;")) {
      fail(configName + " does not preserve server security headers in locations");
    }
    if (!config.includes("try_files $uri $uri.html /index.html;")) {
      fail(configName + " does not resolve generated route shells");
    }
    if (!config.includes("try_files $uri $uri.html =404;")) {
      fail(configName + " does not return 404 for unknown content slugs");
    }
    if (!config.includes("X-Robots-Tag $seo_robots_tag always")) {
      fail(configName + " is missing private-route X-Robots-Tag");
    }
    const noindexPatterns = noindexMapPatterns(config);
    if (noindexPatterns.length === 0) {
      fail(configName + " is missing the $seo_robots_tag noindex map rules");
    } else {
      const noindexRoutes = routes.filter((route) => route.index === false);
      for (const route of noindexRoutes) {
        const variants = [
          route.path,
          route.path + "/",
          route.path + "?source=seo-verifier",
          route.path + "/?source=seo-verifier",
        ];
        for (const uri of variants) {
          if (!hasNoindexHeader(noindexPatterns, uri)) {
            fail(configName + " does not map registered noindex URI " + uri);
          }
        }
      }
      for (const route of routes.filter((candidate) => candidate.index !== false)) {
        for (const uri of [route.path, route.path + "?source=seo-verifier"]) {
          if (hasNoindexHeader(noindexPatterns, uri)) {
            fail(configName + " incorrectly maps indexable URI " + uri + " to noindex");
          }
        }
      }
      for (const route of noindexRoutes.filter((candidate) =>
        candidate.path.startsWith("/solutions/")
      )) {
        if (!hasNoindexHeader(noindexPatterns, route.path + "/confirmation")) {
          fail(configName + " does not preserve noindex on campaign subpaths: " + route.path);
        }
        if (hasNoindexHeader(noindexPatterns, route.path + "-guide")) {
          fail(configName + " overmatches campaign siblings: " + route.path);
        }
      }
      for (const uri of ["/dashboard", "/dashboard/agents/agent-1", "/dashboard?tab=agents"]) {
        if (!hasNoindexHeader(noindexPatterns, uri)) {
          fail(configName + " does not preserve private dashboard noindex for " + uri);
        }
      }
      if (hasNoindexHeader(noindexPatterns, "/login/help")) {
        fail(configName + " broadens the exact auth-flow rule to /login/help");
      }
    }
    for (const alias of ["/contact", "/privacy-policy", "/terms-of-service", "/refund-policy", "/cancellation"]) {
      if (!config.includes("location = " + alias + " {")) {
        fail(configName + " is missing canonical redirect for " + alias);
      }
    }
  }
  return { errors, warnings, routeCount: routes.length };
}

const isMain = process.argv[1] &&
  resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isMain) {
  const result = verifySeo();
  for (const warning of result.warnings) console.warn("SEO warning: " + warning);
  if (result.errors.length) {
    for (const error of result.errors) console.error("SEO error: " + error);
    process.exitCode = 1;
  } else {
    console.log(
      "SEO verification passed for " + result.routeCount +
      " route descriptors, generated HTML, sitemap, robots, and Nginx.",
    );
  }
}
