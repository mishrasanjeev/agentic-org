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

  test("native catalog registration persists the registry identity", async ({ page }) => {
    test.skip(!process.env.AGENTICORG_DEV_SEED_PASSWORD, "Local seed password is required");
    await page.goto("/login");
    await page.fill('input[type="email"]', "approver.a@example.com");
    await page.fill('input[type="password"]', process.env.AGENTICORG_DEV_SEED_PASSWORD!);
    await page.locator('button[type="submit"]').click();
    await expect(page).toHaveURL(/\/dashboard/, { timeout: 15_000 });

    await page.goto("/dashboard/connectors");
    const card = page.getByTestId("catalog-item-whatsapp");
    await expect(card).toBeVisible();
    const list = await page.context().request.get("/api/v1/connectors");
    expect(list.status()).toBe(200);
    const existing = await list.json();
    const alreadyRegistered = existing.items.some((item: { name: string }) => item.name === "whatsapp");
    if (alreadyRegistered) {
      await expect(card.getByText("Registered")).toBeVisible();
      await page.goto("/dashboard/connectors/new?type=whatsapp");
    } else {
      await card.getByRole("button", { name: "Register" }).click();
    }
    await expect(page).toHaveURL(/\/dashboard\/connectors\/new\?type=whatsapp/);
    await expect(page.getByTestId("provider-select")).toHaveValue("whatsapp");
    await expect(page.locator('input[readonly][value="whatsapp"]')).toBeVisible();
    if (!alreadyRegistered) {
      await page.getByPlaceholder("Enter access token").fill("synthetic-local-token");
      await page.getByPlaceholder("Enter phone number ID").fill("synthetic-local-phone-id");
      await page.getByRole("button", { name: "Register Connector" }).click();
      await expect(page).toHaveURL(/\/dashboard\/connectors$/, { timeout: 15_000 });
    }

    const response = await page.context().request.get("/api/v1/connectors");
    expect(response.status()).toBe(200);
    expect((await response.json()).items).toEqual(
      expect.arrayContaining([expect.objectContaining({ name: "whatsapp" })]),
    );

    await page.goto("/dashboard/connectors/new?type=not_in_registry");
    await expect(page.getByRole("alert")).toContainText("not in the current registry");
    await expect(page.getByRole("button", { name: "Register Connector" })).toBeDisabled();
  });
});
