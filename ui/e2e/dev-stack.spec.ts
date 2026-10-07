// SPDX-License-Identifier: Apache-2.0
import { expect, test } from "@playwright/test";

// Runs against the local development stack only (see dev-stack.config.ts).
// Every response is real: the console's nginx, its /api proxy, the API and
// the database behind it.

test.describe("local dev stack @dev-stack", () => {
  test("catalogue follows the registry flag and supports real banking pack cards", async ({ page }) => {
    const password = process.env.AGENTICORG_DEV_SEED_PASSWORD;
    expect(password, "Local seed password is required").toBeTruthy();
    await page.goto("/login?next=/dashboard/agent-catalogue");
    await page.fill('input[type="email"]', "approver.a@example.com");
    await page.fill('input[type="password"]', password!);
    await page.locator('button[type="submit"]').click();
    await expect(page).toHaveURL(/\/dashboard\/agent-catalogue/, { timeout: 15_000 });
    const request = page.context().request;
    const catalogue = await request.get("/api/v1/agent-registry");
    if (process.env.AGENTICORG_E2E_REGISTRY_ENABLED !== "true") {
      expect(catalogue.status()).toBe(409);
      await expect(page.getByTestId("catalogue-off")).toBeVisible();
      await expect(page.getByTestId("catalogue-templates-toggle")).toBeDisabled();
      return;
    }
    expect(catalogue.status()).toBe(200);
    const installedBefore = await request.get("/api/v1/packs/installed");
    expect(installedBefore.ok()).toBeTruthy();
    expect(JSON.stringify(await installedBefore.json())).not.toContain('"banking"');
    const install = await request.post("/api/v1/packs/banking/install");
    expect(install.status()).toBe(200);
    const pack = await install.json();
    try {
      expect(pack.agents_created).toHaveLength(5);
      expect(pack.agents_created.every((a: { mode: string }) => a.mode === "shadow")).toBeTruthy();
      expect(pack.workflows_created).toHaveLength(2);
      for (const workflow of pack.workflows_created) {
        expect(workflow.name.length).toBeLessThan(100);
        const details = await request.get(`/api/v1/workflows/${workflow.id}`);
        expect(details.status()).toBe(200);
        expect((await details.json()).trigger_type).toBe("manual");
      }
      const agent = pack.agents_created.find((a: { type: string }) => a.type === "kyc_reviewer");
      expect(agent).toBeTruthy();
      const card = await request.put(`/api/v1/agents/${agent.id}/card`, {
        data: { purpose: "Synthetic catalogue browser regression", risk_tier: "high", use_case: "review", channels: ["chat"] },
      });
      expect(card.status()).toBe(200);
      await page.reload();
      await page.getByRole("textbox", { name: "Search agents" }).fill("Synthetic catalogue browser regression");
      await page.getByRole("combobox", { name: "Domain" }).selectOption("ops");
      const row = page.getByTestId(`catalogue-row-${agent.id}`);
      await expect(row).toBeVisible();
      await expect(row).toContainText("draft");
      await expect(row).toContainText("shadow");
      await expect(page.getByTestId("catalogue-table").locator("tbody tr")).toHaveCount(1);
      await page.getByTestId("catalogue-templates-toggle").click();
      await expect(page.getByTestId("template-banking-kyc_reviewer")).toBeVisible();
      await page.setViewportSize({ width: 390, height: 844 });
      await expect(row.getByRole("link", { name: agent.name })).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
      await page.screenshot({ path: "test-results/catalogue-mobile.png", fullPage: true });
      await page.setViewportSize({ width: 1440, height: 1000 });
      await page.screenshot({ path: "test-results/catalogue-desktop.png", fullPage: true });
      await row.getByRole("link", { name: agent.name }).focus();
      await page.keyboard.press("Enter");
      await expect(page).toHaveURL(new RegExp(`/dashboard/agents/${agent.id}$`));
    } finally {
      const removed = await request.delete("/api/v1/packs/banking");
      expect(removed.status()).toBe(200);
    }
  });

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

    for (const [type, credentialPlaceholder] of [
      ["twilio", "Enter auth token"],
      ["gmail", "Enter refresh token"],
      ["pinelabs_plural", "Enter merchant ID"],
    ]) {
      await page.goto(`/dashboard/connectors/new?type=${type}`);
      await expect(page.getByTestId("provider-select")).toHaveValue(type);
      await expect(page.getByPlaceholder(credentialPlaceholder)).toBeVisible();
    }

    await page.goto("/dashboard/connectors/new?type=not_in_registry");
    await expect(page.getByRole("alert")).toContainText("not in the current registry");
    await expect(page.getByRole("button", { name: "Register Connector" })).toBeDisabled();
  });
});
