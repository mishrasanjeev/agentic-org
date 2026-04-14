#!/usr/bin/env node
/**
 * Auto-generate sitemap.xml from blog + resource slugs.
 * Run: node scripts/generate-sitemap.mjs
 * Hooked into: "build" script in package.json
 */
import { readFileSync, writeFileSync } from "fs";
import { join, dirname } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, "..");

// ── Extract slugs from TS source files using regex ──────────────────────────
function extractSlugs(filePath) {
  const src = readFileSync(filePath, "utf-8");
  return [...src.matchAll(/slug:\s*"([^"]+)"/g)].map((m) => m[1]);
}

const blogSlugs = extractSlugs(join(ROOT, "src/pages/blog/blogData.ts"));
const resourceSlugs = extractSlugs(
  join(ROOT, "src/pages/resources/contentData.ts"),
);

// ── Static pages (priority, changefreq) ─────────────────────────────────────
const today = new Date().toISOString().slice(0, 10);

const staticPages = [
  { loc: "/", priority: "1.0", changefreq: "weekly" },
  { loc: "/pricing", priority: "0.9", changefreq: "monthly" },
  { loc: "/playground", priority: "0.9", changefreq: "weekly" },
  { loc: "/evals", priority: "0.9", changefreq: "weekly" },
  { loc: "/integration-workflow", priority: "0.9", changefreq: "monthly" },
  { loc: "/blog", priority: "0.8", changefreq: "weekly" },
  { loc: "/resources", priority: "0.8", changefreq: "weekly" },
  // CxO solution pages
  { loc: "/solutions/ca-firms", priority: "0.8", changefreq: "monthly" },
  { loc: "/solutions/cfo", priority: "0.8", changefreq: "monthly" },
  { loc: "/solutions/chro", priority: "0.8", changefreq: "monthly" },
  { loc: "/solutions/cmo", priority: "0.8", changefreq: "monthly" },
  { loc: "/solutions/coo", priority: "0.8", changefreq: "monthly" },
  { loc: "/solutions/cbo", priority: "0.8", changefreq: "monthly" },
  // Google Ads landing pages
  { loc: "/solutions/ai-invoice-processing", priority: "0.7", changefreq: "monthly" },
  { loc: "/solutions/automated-bank-reconciliation", priority: "0.7", changefreq: "monthly" },
  { loc: "/solutions/payroll-automation", priority: "0.7", changefreq: "monthly" },
];

// ── Build URL entries ───────────────────────────────────────────────────────
function entry({ loc, priority = "0.7", changefreq = "monthly" }) {
  return `  <url>
    <loc>https://agenticorg.ai${loc}</loc>
    <lastmod>${today}</lastmod>
    <changefreq>${changefreq}</changefreq>
    <priority>${priority}</priority>
  </url>`;
}

const urls = [
  ...staticPages.map((p) => entry(p)),
  ...blogSlugs.map((s) => entry({ loc: `/blog/${s}` })),
  ...resourceSlugs.map((s) => entry({ loc: `/resources/${s}` })),
];

const sitemap = `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
${urls.join("\n")}
</urlset>
<!-- Auto-generated: ${new Date().toISOString()} -->
`;

const outPath = join(ROOT, "public/sitemap.xml");
writeFileSync(outPath, sitemap, "utf-8");

const count = urls.length;
console.log(`sitemap.xml generated — ${count} URLs (${blogSlugs.length} blog + ${resourceSlugs.length} resources + ${staticPages.length} static)`);
