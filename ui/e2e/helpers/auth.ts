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
import type { Page, Locator } from "@playwright/test";

export const APP = process.env.BASE_URL || "https://app.agenticorg.ai";
export const E2E_TOKEN = process.env.E2E_TOKEN || "";
export const canAuth = !!E2E_TOKEN;

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
  await setSessionToken(page, E2E_TOKEN);
  await page.goto(`${APP}/login`, { waitUntil: "domcontentloaded" });
}

function _appHost(): string {
  // Strip protocol + path so Playwright's addCookies receives a bare host.
  return APP.replace(/^https?:\/\//, "").replace(/\/.*$/, "");
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
