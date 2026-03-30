#!/usr/bin/env node
/**
 * Submit all sitemap URLs to Google Indexing API + IndexNow (Bing/Yandex).
 *
 * Setup (one-time):
 *   1. Create GCP service account:
 *      gcloud iam service-accounts create indexing-bot --display-name="GSC Indexing Bot"
 *      gcloud iam service-accounts keys create ui/keys/gsc-indexing.json \
 *        --iam-account=indexing-bot@<PROJECT_ID>.iam.gserviceaccount.com
 *
 *   2. Add the service account email as OWNER in Google Search Console:
 *      → GSC → Settings → Users and permissions → Add user
 *      → Paste: indexing-bot@<PROJECT_ID>.iam.gserviceaccount.com → Owner
 *
 *   3. Enable Indexing API:
 *      gcloud services enable indexing.googleapis.com
 *
 *   4. Set env var:
 *      export GSC_SERVICE_ACCOUNT_KEY=ui/keys/gsc-indexing.json
 *
 * Usage:
 *   node scripts/submit-indexing.mjs              # submit all sitemap URLs
 *   node scripts/submit-indexing.mjs --check      # check indexing status (dry run)
 *   node scripts/submit-indexing.mjs --url https://agenticorg.ai/blog/my-post  # single URL
 */
import { readFileSync } from "fs";
import { dirname, join } from "path";
import { fileURLToPath } from "url";
import { createSign } from "crypto";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, "..");
const SITE = "https://agenticorg.ai";

// ── Parse args ──────────────────────────────────────────────────────────────
const args = process.argv.slice(2);
const CHECK_ONLY = args.includes("--check");
const SINGLE_URL = args.find((_, i) => args[i - 1] === "--url");

// ── Extract URLs from sitemap.xml ───────────────────────────────────────────
function getSitemapUrls() {
  const xml = readFileSync(join(ROOT, "public/sitemap.xml"), "utf-8");
  return [...xml.matchAll(/<loc>([^<]+)<\/loc>/g)].map((m) => m[1]);
}

// ── Google Indexing API via Service Account JWT ─────────────────────────────
async function getGoogleAccessToken(keyFile) {
  const key = JSON.parse(readFileSync(keyFile, "utf-8"));
  const now = Math.floor(Date.now() / 1000);
  const header = Buffer.from(
    JSON.stringify({ alg: "RS256", typ: "JWT" }),
  ).toString("base64url");
  const payload = Buffer.from(
    JSON.stringify({
      iss: key.client_email,
      scope: "https://www.googleapis.com/auth/indexing",
      aud: "https://oauth2.googleapis.com/token",
      iat: now,
      exp: now + 3600,
    }),
  ).toString("base64url");
  const sig = createSign("RSA-SHA256")
    .update(`${header}.${payload}`)
    .sign(key.private_key, "base64url");
  const jwt = `${header}.${payload}.${sig}`;

  const res = await fetch("https://oauth2.googleapis.com/token", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: `grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer&assertion=${jwt}`,
  });
  const data = await res.json();
  if (!data.access_token) throw new Error(`Auth failed: ${JSON.stringify(data)}`);
  return data.access_token;
}

async function submitToGoogle(urls, token) {
  console.log(`\n🔍 Google Indexing API — ${urls.length} URLs\n`);
  let ok = 0,
    fail = 0;
  for (const url of urls) {
    try {
      const res = await fetch(
        "https://indexing.googleapis.com/v3/urlNotifications:publish",
        {
          method: "POST",
          headers: {
            Authorization: `Bearer ${token}`,
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ url, type: "URL_UPDATED" }),
        },
      );
      const data = await res.json();
      if (res.ok) {
        console.log(`  OK  ${url}`);
        ok++;
      } else {
        console.log(`  FAIL ${url} — ${data.error?.message || res.status}`);
        fail++;
      }
      // Rate limit: 200 req/day, keep it safe
      await new Promise((r) => setTimeout(r, 250));
    } catch (e) {
      console.log(`  ERR  ${url} — ${e.message}`);
      fail++;
    }
  }
  console.log(`\nGoogle: ${ok} submitted, ${fail} failed`);
}

// ── IndexNow (Bing, Yandex, Naver, Seznam) ─────────────────────────────────
async function submitToIndexNow(urls) {
  // Key file must exist at public/<key>.txt and contain the key itself
  const INDEXNOW_KEY = "agenticorg-indexnow-key";
  console.log(`\n🔍 IndexNow (Bing/Yandex) — ${urls.length} URLs\n`);
  try {
    const res = await fetch("https://api.indexnow.org/indexnow", {
      method: "POST",
      headers: { "Content-Type": "application/json; charset=utf-8" },
      body: JSON.stringify({
        host: "agenticorg.ai",
        key: INDEXNOW_KEY,
        keyLocation: `${SITE}/${INDEXNOW_KEY}.txt`,
        urlList: urls,
      }),
    });
    if (res.ok || res.status === 202) {
      console.log(`  OK  ${urls.length} URLs accepted (HTTP ${res.status})`);
    } else {
      const text = await res.text();
      console.log(`  FAIL HTTP ${res.status} — ${text}`);
    }
  } catch (e) {
    console.log(`  ERR  ${e.message}`);
  }
}

// ── Main ────────────────────────────────────────────────────────────────────
async function main() {
  const urls = SINGLE_URL ? [SINGLE_URL] : getSitemapUrls();
  console.log(`Sitemap: ${urls.length} URLs`);
  if (CHECK_ONLY) {
    urls.forEach((u) => console.log(`  ${u}`));
    return;
  }

  // Google Indexing API
  const keyFile =
    process.env.GSC_SERVICE_ACCOUNT_KEY ||
    join(ROOT, "keys/gsc-indexing.json");
  try {
    readFileSync(keyFile);
    const token = await getGoogleAccessToken(keyFile);
    await submitToGoogle(urls, token);
  } catch (e) {
    if (e.code === "ENOENT") {
      console.log(
        `\n⚠  Google Indexing skipped — key not found at ${keyFile}`,
      );
      console.log(`   Run setup: see comments at top of this script\n`);
    } else {
      console.log(`\n⚠  Google Indexing error: ${e.message}\n`);
    }
  }

  // IndexNow (no auth needed, just key file in public/)
  await submitToIndexNow(urls);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
