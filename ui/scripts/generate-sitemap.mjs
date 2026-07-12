#!/usr/bin/env node
/**
 * Generate sitemap.xml from the same canonical route catalog as static HTML.
 *
 * Default output is dist/sitemap.xml so builds do not dirty the repository.
 * Pass --sync-public when intentionally refreshing the tracked development
 * snapshot at public/sitemap.xml.
 */
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import {
  loadRouteDescriptors,
  UI_ROOT,
} from "./generate-static-seo.mjs";

const escapeXml = (value) =>
  String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&apos;");

const canonicalUrl = (siteUrl, path) => {
  const base = siteUrl.replace(/\/+$/, "");
  return path === "/" ? base + "/" : base + path;
};

export function buildSitemap(routes, siteUrl) {
  const indexable = routes.filter((route) => route.index !== false);
  const paths = new Set();
  for (const route of indexable) {
    if (paths.has(route.path)) {
      throw new Error("Duplicate indexable route in sitemap: " + route.path);
    }
    paths.add(route.path);
  }
  const entries = indexable.map((route) => {
    const lastmod = route.lastmod &&
      /^\d{4}-\d{2}-\d{2}$/.test(route.lastmod)
      ? "\n    <lastmod>" + route.lastmod + "</lastmod>"
      : "";
    return [
      "  <url>",
      "    <loc>" + escapeXml(canonicalUrl(siteUrl, route.path)) + "</loc>" +
        lastmod,
      "  </url>",
    ].join("\n");
  });
  return [
    '<?xml version="1.0" encoding="UTF-8"?>',
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    entries.join("\n"),
    "</urlset>",
    "",
  ].join("\n");
}

export function generateSitemap(
  root = UI_ROOT,
  { syncPublic = false } = {},
) {
  const { manifest, routes } = loadRouteDescriptors(root);
  const sitemap = buildSitemap(routes, manifest.site.url);
  const outputs = [join(root, "dist/sitemap.xml")];
  if (syncPublic) outputs.push(join(root, "public/sitemap.xml"));
  for (const output of outputs) {
    mkdirSync(dirname(output), { recursive: true });
    writeFileSync(output, sitemap, "utf8");
  }
  return {
    count: routes.filter((route) => route.index !== false).length,
    outputs,
  };
}

const isMain = process.argv[1] &&
  resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isMain) {
  const result = generateSitemap(UI_ROOT, {
    syncPublic: process.argv.includes("--sync-public"),
  });
  console.log(
    "Generated sitemap with " + result.count + " canonical, indexable URLs: " +
    result.outputs.join(", "),
  );
}