/**
 * Shared E2E helpers for the regression suite.
 *
 * What goes wrong without these and what each helper prevents:
 *   1. Hardcoding `localStorage.user` with a fake role (e.g. "ceo") breaks
 *      Layout sidebar filtering, which gates nav items by role. The CI demo
 *      account is `admin`, so anything else hides every CxO nav link.
 *   2. Hardcoding company UUIDs like "c1" makes CompanyDetail render empty
 *      against a real tenant — tabs, modals, and table rows all disappear.
 *   3. `getByText("Approvals", {exact:true}).first()` matches the sidebar
 *      nav link before the CompanyDetail tab. Tab clicks navigate to the
 *      wrong page entirely.
 *
 * All three patterns came up in `ca-firms.spec.ts` and the fixes are
 * generic. Use these helpers in every regression spec.
 */
import { readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { expect, test, type Page, type Locator } from "@playwright/test";

import { SessionKeeper, loginForToken, type LoginFailureLog } from "./session";

export const APP = process.env.BASE_URL || "https://app.agenticorg.ai";
/**
 * The shared E2E session token. A live binding: `ensureFreshE2EToken`
 * replaces it after logging in again, and every module that imports it sees
 * the new value. Read it where you use it; do not copy it into a constant.
 */
export let E2E_TOKEN = process.env.E2E_TOKEN || "";
export const canAuth = !!E2E_TOKEN;

/**
 * Failed logins, shared by every worker of this run. Workers are children of
 * the runner, so its pid identifies the run. Best effort: an unreadable or
 * unwritable file only means a worker may retry sooner.
 */
const _loginFailures: LoginFailureLog = {
  read() {
    try {
      const data = JSON.parse(readFileSync(_loginFailureFile(), "utf8"));
      return typeof data?.at === "number" && typeof data?.reason === "string" ? data : null;
    } catch {
      return null;
    }
  },
  write(at, reason) {
    try {
      writeFileSync(_loginFailureFile(), JSON.stringify({ at, reason }));
    } catch {
      // See above.
    }
  },
};

function _loginFailureFile(): string {
  return join(tmpdir(), `agenticorg-e2e-login-failures-${process.ppid}.json`);
}

const _session = new SessionKeeper(
  E2E_TOKEN,
  () => {
    const email = process.env.E2E_EMAIL;
    const password = process.env.E2E_PASSWORD;
    if (!email || !password) return Promise.reject(new Error("E2E_EMAIL and E2E_PASSWORD are not set"));
    return loginForToken(APP, email, password);
  },
  _loginFailures,
);

/**
 * Keep the shared session alive for runs longer than its 60-minute lifetime.
 *
 * The production suite runs for more than an hour on one token, so every
 * test after the first hour failed with 401s that looked like product bugs.
 * This logs in again (with `E2E_EMAIL` / `E2E_PASSWORD`) when less than 15
 * minutes are left, and throws with the reason once the token has expired
 * and cannot be renewed (see `SessionKeeper`). The fixtures in `./test` call
 * it when a worker starts and before every test; `authenticate`,
 * `getProfile` and `getCompanyId` call it too.
 */
export async function ensureFreshE2EToken(): Promise<string> {
  const token = await _session.fresh(Date.now());
  if (token !== E2E_TOKEN) {
    // The mint step in deploy.yml masks the first token; mask each new one
    // before anything can print it. Worker stdout reaches the Actions log as
    // whole lines, so the runner reads this as a command.
    if (process.env.GITHUB_ACTIONS === "true") console.log(`::add-mask::${token}`);
    E2E_TOKEN = token;
    // Specs that read process.env.E2E_TOKEN directly see the new token too.
    process.env.E2E_TOKEN = token;
  }
  return token;
}

export const DEMO_USER_CREDENTIALS = {
  email: process.env.AGENTICORG_DEMO_USER_EMAIL || "demo@cafirm.agenticorg.ai",
  password: process.env.AGENTICORG_DEMO_USER_PASSWORD || "local-e2e-demo-123!",
} as const;

export const DEMO_ROLE_CREDENTIALS = {
  ceo: {
    email: process.env.AGENTICORG_DEMO_CEO_EMAIL || "ceo@agenticorg.local",
    password: process.env.AGENTICORG_DEMO_CEO_PASSWORD || "local-e2e-ceo-123!",
  },
  cfo: {
    email: process.env.AGENTICORG_DEMO_CFO_EMAIL || "cfo@agenticorg.local",
    password: process.env.AGENTICORG_DEMO_CFO_PASSWORD || "local-e2e-cfo-123!",
  },
  chro: {
    email: process.env.AGENTICORG_DEMO_CHRO_EMAIL || "chro@agenticorg.local",
    password: process.env.AGENTICORG_DEMO_CHRO_PASSWORD || "local-e2e-chro-123!",
  },
  cmo: {
    email: process.env.AGENTICORG_DEMO_CMO_EMAIL || "cmo@agenticorg.local",
    password: process.env.AGENTICORG_DEMO_CMO_PASSWORD || "local-e2e-cmo-123!",
  },
  coo: {
    email: process.env.AGENTICORG_DEMO_COO_EMAIL || "coo@agenticorg.local",
    password: process.env.AGENTICORG_DEMO_COO_PASSWORD || "local-e2e-coo-123!",
  },
  auditor: {
    email: process.env.AGENTICORG_DEMO_AUDITOR_EMAIL || "auditor@agenticorg.local",
    password: process.env.AGENTICORG_DEMO_AUDITOR_PASSWORD || "local-e2e-auditor-123!",
  },
} as const;

type DemoAccount = keyof typeof DEMO_ROLE_CREDENTIALS | "user";

const DEMO_PASSWORD_ENV: Record<DemoAccount, string> = {
  user: "AGENTICORG_DEMO_USER_PASSWORD",
  ceo: "AGENTICORG_DEMO_CEO_PASSWORD",
  cfo: "AGENTICORG_DEMO_CFO_PASSWORD",
  chro: "AGENTICORG_DEMO_CHRO_PASSWORD",
  cmo: "AGENTICORG_DEMO_CMO_PASSWORD",
  coo: "AGENTICORG_DEMO_COO_PASSWORD",
  auditor: "AGENTICORG_DEMO_AUDITOR_PASSWORD",
};

function targetsLocalStack(): boolean {
  try {
    return ["localhost", "127.0.0.1", "[::1]"].includes(new URL(APP).hostname);
  } catch {
    return false;
  }
}

/**
 * Precondition for specs that log in as the seeded demo users.
 *
 * The default demo passwords above exist only in the local Docker stack. Any
 * other target (production) must supply them through the
 * ``AGENTICORG_DEMO_*_PASSWORD`` env vars. Without them the spec fails loudly,
 * matching ``requireAuth``. A pipeline that knowingly has no demo accounts sets
 * ``E2E_DEMO_ROLES=unavailable``: those tests are then skipped with this reason
 * in the report, and the workflow prints a warning, so the gap stays visible.
 */
export function requireDemoRoleCredentials(accounts: readonly DemoAccount[]): void {
  if (targetsLocalStack()) return;
  const missing = accounts.map((a) => DEMO_PASSWORD_ENV[a]).filter((name) => !process.env[name]);
  if (missing.length === 0) return;
  const reason = `demo-role credentials not configured for ${APP}: set ${missing.join(", ")}`;
  if (process.env.E2E_DEMO_ROLES === "unavailable") {
    test.skip(true, reason);
    return;
  }
  throw new Error(reason);
}

/**
 * Assert that the suite has authentication available. Throws if not.
 *
 * Policy: we do not skip tests on missing E2E_TOKEN — skipping silently
 * hides real coverage gaps in CI. A missing token is a configuration
 * failure that should fail the run loudly. Call this from beforeEach or
 * at the top of any spec that drives auth-gated flows.
 */
export function requireAuth(): void {
  if (!canAuth) {
    throw new Error(
      "E2E_TOKEN is required for this spec. Set the E2E_TOKEN env var — the suite runs against production and must have credentials.",
    );
  }
}

interface AuthUser {
  email: string;
  name: string;
  role: string;
  domain: string;
  tenant_id: string;
  onboardingComplete: boolean;
}

/** Cache the resolved profile across tests in one Playwright run. */
let _cachedProfile: AuthUser | null = null;
let _cachedCompanyId: string | null = null;

/**
 * Authenticate the page by seeding the agenticorg_session HttpOnly cookie
 * + the paired agenticorg_csrf cookie.
 *
 * SEC-002 (PR-F2, 2026-05-01): production browser auth no longer reads a
 * bearer token from localStorage. The HttpOnly session cookie is the only
 * valid carrier, so the e2e fixtures match that posture exactly. Specs that
 * called this helper get cookie-based auth automatically; specs that wrote
 * ``localStorage.setItem("token", ...)`` directly should switch to
 * ``setSessionToken`` (below).
 *
 * The CSRF cookie is also seeded here so mutating-method specs (POST/PUT
 * /PATCH/DELETE) automatically attach the X-CSRF-Token header via the same
 * double-submit pattern the app uses in production.
 */
export async function authenticate(page: Page): Promise<void> {
  requireAuth();
  await ensureFreshE2EToken();
  await setSessionToken(page, E2E_TOKEN);
  await page.goto(`${APP}/login`, { waitUntil: "domcontentloaded" });
}

function _appHost(): string {
  try {
    return new URL(APP).hostname;
  } catch {
    return APP.replace(/^https?:\/\//, "").replace(/[:/].*$/, "");
  }
}

/**
 * Seed the agenticorg_session + agenticorg_csrf cookies into the browser
 * context so subsequent navigations behave as if the user has a live
 * session. Replaces the old ``localStorage.setItem("token", ...)``
 * pattern that SEC-002 forbids.
 */
export async function setSessionToken(
  page: Page,
  token: string,
  csrfToken: string = "e2e-csrf-token",
): Promise<void> {
  const host = _appHost();
  const isHttps = APP.startsWith("https://");
  await page.context().addCookies([
    {
      name: "agenticorg_session",
      value: token,
      domain: host,
      path: "/",
      httpOnly: true,
      secure: isHttps,
      sameSite: "Lax",
    },
    {
      name: "agenticorg_csrf",
      value: csrfToken,
      domain: host,
      path: "/",
      // CSRF cookie is intentionally NOT HttpOnly — the SPA needs to
      // read it to echo back as X-CSRF-Token. Matches production.
      httpOnly: false,
      secure: isHttps,
      sameSite: "Lax",
    },
  ]);
}

/**
 * Assert that the page is behind a live session.
 *
 * A protected route sends the browser to /login once `/auth/me` fails, so
 * "the page rendered something" proves nothing on its own: a spec that only
 * checks the body passes on the login page. The Logout control is part of
 * the signed-in layout only.
 */
export async function expectSignedIn(page: Page): Promise<void> {
  await expect(page.getByRole("button", { name: "Logout" }).first()).toBeAttached({ timeout: 15_000 });
  await expect(page).not.toHaveURL(/\/login(?:[?#]|$)/);
}

/** Clear all cookies for the current page context — use to simulate logout. */
export async function clearSession(page: Page): Promise<void> {
  await page.context().clearCookies();
}

/**
 * Fetch the live profile from /auth/me using the E2E token.
 *
 * Only caches on successful API response. A transient network blip returns
 * the fallback without poisoning subsequent calls, so the next test (or
 * retry) still gets a fresh shot at the real profile — otherwise one bad
 * request at setup time could lock the whole run to the wrong identity.
 */
export async function getProfile(page: Page): Promise<AuthUser> {
  if (_cachedProfile) return _cachedProfile;
  await ensureFreshE2EToken();
  try {
    const resp = await page.request.get(`${APP}/api/v1/auth/me`, {
      headers: { Authorization: `Bearer ${E2E_TOKEN}` },
    });
    if (resp.ok()) {
      const data = await resp.json();
      _cachedProfile = {
        email: data.email,
        name: data.name,
        role: data.role,
        domain: data.domain,
        tenant_id: data.tenant_id,
        onboardingComplete: data.onboarding_complete ?? true,
      };
      return _cachedProfile;
    }
  } catch {
    // fall through to fallback, but don't cache — next call retries the API.
  }
  return {
    email: "demo@cafirm.agenticorg.ai",
    name: "Demo Partner",
    role: "admin",
    domain: "all",
    tenant_id: "58483c90-494b-445d-85c6-245a727fe372",
    onboardingComplete: true,
  };
}

/**
 * Fetch the first real company id for the authed tenant.
 *
 * Only caches on successful API response with a non-empty list. A
 * hardcoded UUID fallback would be wrong for any other tenant/environment
 * and would turn a transient list-endpoint blip into persistent false
 * negatives across the suite. When the API cannot answer, throw — the
 * caller's spec fails cleanly and retries on the next attempt.
 */
export async function getCompanyId(page: Page): Promise<string> {
  if (_cachedCompanyId) return _cachedCompanyId;
  await ensureFreshE2EToken();
  try {
    const resp = await page.request.get(
      `${APP}/api/v1/companies?page=1&per_page=1`,
      { headers: { Authorization: `Bearer ${E2E_TOKEN}` } },
    );
    if (resp.ok()) {
      const data = await resp.json();
      const items = Array.isArray(data) ? data : data?.items ?? [];
      if (items.length > 0 && items[0]?.id) {
        _cachedCompanyId = items[0].id as string;
        return _cachedCompanyId;
      }
    }
  } catch {
    // Fall through — we'd rather raise a clean error than silently
    // return a cross-tenant UUID.
  }
  throw new Error(
    "getCompanyId: /api/v1/companies returned no usable id. " +
      "Seed the demo tenant with at least one company before running the suite.",
  );
}

/**
 * Click-target locator that ignores the sidebar.
 *
 * `page.getByText("Approvals").first()` matches the sidebar nav link
 * before any in-page tab button. Use this for tabs, in-page buttons, and
 * any element that could be shadowed by an identical-named nav link.
 */
export function mainText(page: Page, text: string): Locator {
  return page.locator("main").getByText(text, { exact: true }).first();
}

/** Click a CompanyDetail tab without picking up the sidebar nav link. */
export function tabButton(page: Page, label: string | RegExp): Locator {
  const matcher = typeof label === "string" ? new RegExp(`^${label}$`) : label;
  return page.locator("main button").filter({ hasText: matcher }).first();
}

/** Reset the in-process cache. Useful between projects in one Playwright run. */
export function _resetHelpersCache(): void {
  _cachedProfile = null;
  _cachedCompanyId = null;
}
