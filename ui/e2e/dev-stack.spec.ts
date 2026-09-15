// SPDX-License-Identifier: Apache-2.0
import { expect, test } from "@playwright/test";

// Runs against the local development stack only (see dev-stack.config.ts).
// Every response is real: the console's nginx, its /api proxy, the API and
// the database behind it.

test.describe("local dev stack @dev-stack", () => {
  test("console proxies the API and the API reports healthy", async ({ request }) => {
    const liveness = await request.get("/api/v1/health/liveness");
    expect(liveness.status()).toBe(200);
    expect(await liveness.json()).toMatchObject({ status: "alive" });

    const readiness = await request.get("/api/v1/health");
    expect(readiness.status()).toBe(200);
    expect(await readiness.json()).toMatchObject({ status: "healthy" });
  });

  test("login page renders the email sign-in form", async ({ page }) => {
    await page.goto("/login");
    await expect(page.locator('input[type="email"]')).toBeVisible({ timeout: 15_000 });
    await expect(page.locator('input[type="password"]')).toBeVisible();
    await expect(page.locator('button[type="submit"]')).toBeVisible();
  });

  test("a protected page without a session lands on the login page", async ({ page }) => {
    await page.goto("/dashboard");
    await expect(page).toHaveURL(/\/login/, { timeout: 15_000 });
  });

  test("the API rejects unknown credentials and the console stays signed out", async ({ page }) => {
    await page.goto("/login");
    await page.fill('input[type="email"]', "nobody@example.com");
    await page.fill('input[type="password"]', "not-a-real-password-1");
    const [response] = await Promise.all([
      page.waitForResponse((r) => r.url().includes("/api/v1/auth/login") && r.request().method() === "POST"),
      page.locator('button[type="submit"]').click(),
    ]);
    expect([401, 403, 429]).toContain(response.status());
    await expect(page).toHaveURL(/\/login/);
  });
});
