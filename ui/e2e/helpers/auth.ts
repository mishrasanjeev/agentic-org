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
 * Authenticate the page by seeding the real demo profile into localStorage.
 *
 * Resolves the profile from `/api/v1/auth/me` the first time so the seeded
 * `user` matches what ProtectedRoute would have hydrated naturally — most
 * critically, the real `role` so sidebar filtering works.
 */
export async function authenticate(page: Page): Promise<void> {
  if (!E2E_TOKEN) return;
  await page.goto(`${APP}/login`, { waitUntil: "domcontentloaded" });
  const profile = await getProfile(page);
  await page.evaluate(
    ([tkn, user]) => {
      localStorage.setItem("token", tkn as string);
      localStorage.setItem("user", JSON.stringify(user));
    },
    [E2E_TOKEN, profile],
  );
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
