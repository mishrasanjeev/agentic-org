#!/usr/bin/env node
/**
 * Notify IndexNow participants after a production deployment.
 *
 * Google discovers ordinary web pages through crawlable links and sitemap.xml;
 * its Indexing API is intentionally not used here because that API is limited
 * to JobPosting and BroadcastEvent pages.
 *
 * Usage:
 *   node scripts/submit-indexing.mjs
 *   node scripts/submit-indexing.mjs --check
 *   node scripts/submit-indexing.mjs --url https://agenticorg.ai/blog/example
 */
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const UI_ROOT = join(SCRIPT_DIR, "..");
const SITE = "https://agenticorg.ai";
const HOST = new URL(SITE).host;
const args = process.argv.slice(2);
const CHECK_ONLY = args.includes("--check");

function selectedUrls() {
  const explicit = [];
  for (let index = 0; index < args.length; index += 1) {
    if (args[index] === "--url" && args[index + 1]) {
      explicit.push(args[index + 1]);
      index += 1;
    }
  }
  if (explicit.length) return explicit;

  const sitemapPath = [
    join(UI_ROOT, "dist/sitemap.xml"),
    join(UI_ROOT, "public/sitemap.xml"),
  ].find(existsSync);
  if (!sitemapPath) {
    throw new Error("No generated sitemap found; run the sitemap generator first.");
  }
  const xml = readFileSync(sitemapPath, "utf8");
  return [...xml.matchAll(/<loc>([^<]+)<\/loc>/g)].map((match) => match[1]);
}

function validateUrls(values) {
  const unique = [];
  const seen = new Set();
  for (const value of values) {
    const url = new URL(value);
    if (url.protocol !== "https:" || url.host !== HOST) {
      throw new Error("IndexNow URL must use the canonical HTTPS host: " + value);
    }
    url.hash = "";
    const normalized = url.toString();
    if (!seen.has(normalized)) {
      seen.add(normalized);
      unique.push(normalized);
    }
  }
  if (unique.length === 0) throw new Error("No canonical URLs to submit.");
  if (unique.length > 10_000) throw new Error("IndexNow accepts at most 10,000 URLs.");
  return unique;
}

function readKey() {
  const key = (
    process.env.INDEXNOW_KEY ||
    readFileSync(join(UI_ROOT, "public/agenticorg-indexnow-key.txt"), "utf8")
  ).trim();
  if (!/^[A-Za-z0-9-]{8,128}$/.test(key)) {
    throw new Error("INDEXNOW_KEY must be 8-128 ASCII letters, digits, or hyphens.");
  }
  return key;
}

export async function submitIndexNow(urls, key = readKey(), fetchImpl = fetch) {
  const response = await fetchImpl("https://api.indexnow.org/indexnow", {
    method: "POST",
    headers: { "Content-Type": "application/json; charset=utf-8" },
    body: JSON.stringify({
      host: HOST,
      key,
      keyLocation: SITE + "/" + key + ".txt",
      urlList: urls,
    }),
  });
  if (response.status !== 200 && response.status !== 202) {
    const detail = (await response.text()).slice(0, 1_000);
    throw new Error(
      "IndexNow rejected the submission with HTTP " + response.status +
      (detail ? ": " + detail : ""),
    );
  }
  return response.status;
}

async function main() {
  const urls = validateUrls(selectedUrls());
  if (CHECK_ONLY) {
    console.log("IndexNow dry run: " + urls.length + " canonical URLs");
    for (const url of urls) console.log("  " + url);
    return;
  }
  const status = await submitIndexNow(urls);
  console.log(
    "IndexNow accepted " + urls.length + " canonical URLs (HTTP " + status + ").",
  );
}

const isMain = process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1];
if (isMain) {
  main().catch((error) => {
    console.error("IndexNow submission failed:", error.message);
    process.exitCode = 1;
  });
}
