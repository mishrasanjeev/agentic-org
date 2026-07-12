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
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const UI_ROOT = join(SCRIPT_DIR, "..");
const PUBLIC_ROOT = join(UI_ROOT, "public");
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

export function readHostedIndexNowKey(publicRoot = PUBLIC_ROOT) {
  const fileNames = readdirSync(publicRoot);
  const descriptiveFiles = fileNames.filter((name) => /indexnow/i.test(name));
  if (descriptiveFiles.length) {
    throw new Error(
      "IndexNow key filenames must not be descriptive: " +
      descriptiveFiles.join(", "),
    );
  }

  const keyFiles = fileNames.filter((name) => /^[0-9a-f]{32}\.txt$/.test(name));
  if (keyFiles.length !== 1) {
    throw new Error(
      "Expected exactly one 32-character generated IndexNow key file in public/.",
    );
  }

  const fileName = keyFiles[0];
  const key = fileName.slice(0, -4);
  const contents = readFileSync(join(publicRoot, fileName), "utf8").trim();
  if (contents !== key) {
    throw new Error("IndexNow key file content must match its filename.");
  }
  return key;
}

export async function submitIndexNow(
  urls,
  key = readHostedIndexNowKey(),
  fetchImpl = fetch,
) {
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
