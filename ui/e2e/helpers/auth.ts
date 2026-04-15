/**
 * Auth helper for Playwright E2E regression tests.
 *
 * Seeds both `localStorage.token` AND `localStorage.user` so ProtectedRoute
 * treats the session as fully hydrated. Using only `token` hits `/auth/me`,
 * which since PR #103 defaults `onboarding_complete=false` for safety —
 * redirecting the test user to `/onboarding` and breaking every dashboard
 * assertion.
 */
import type { Page } from "@playwright/test";

const E2E_USER = {
  email: "ceo@agenticorg.local",
  name: "CEO",
  role: "ceo",
  domain: "general",
  tenant_id: "e2e-tenant",
  onboardingComplete: true,
};

/**
 * Seed a valid token + fully-hydrated user into localStorage.
 *
 * @param page - Playwright page
 * @param token - E2E auth token from login response
 */
export async function seedAuth(page: Page, token: string): Promise<void> {
  await page.evaluate(
    ([tkn, user]) => {
      localStorage.setItem("token", tkn as string);
      localStorage.setItem("user", JSON.stringify(user));
    },
    [token, E2E_USER],
  );
}
