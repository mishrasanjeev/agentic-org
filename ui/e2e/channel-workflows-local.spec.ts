// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
import { expect, test } from "@playwright/test";
import { randomBytes } from "node:crypto";

test("BYO channel setup and documentation on desktop and mobile", async ({ page, baseURL }, testInfo) => {
  test.skip(!baseURL || !["127.0.0.1", "localhost", "ui"].includes(new URL(baseURL).hostname), "Disposable local Docker tenant only");
  test.setTimeout(120_000);
  await expect.poll(async () => (await page.request.get("/api/v1/health")).status(), { timeout: 60_000 }).toBe(200);
  const run = randomBytes(6).toString("hex");
  const signup = await page.request.post("/api/v1/auth/signup", { data: {
    org_name: `Channel browser ${run}`, admin_name: "Channel Test Admin",
    admin_email: `browser-${run}@example.test`, password: randomBytes(24).toString("hex") + "aA9!",
  } });
  expect(signup.status()).toBe(201);
  const auth = await signup.json();
  const headers = { Authorization: `Bearer ${auth.access_token}` };
  expect((await page.request.put("/api/v1/org/onboarding", { headers,
    data: { onboarding_complete: true, onboarding_step: 4 } })).ok()).toBeTruthy();
  const workflow = await page.request.post("/api/v1/workflows", { headers, data: {
    name: `Channel browser flow ${run}`,
    definition: { steps: [{ id: "check", name: "Check request", type: "condition", condition: "true" }] },
  } });
  expect(workflow.status()).toBe(201);
  const target = (await workflow.json()).workflow_id;
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }]) {
    await page.setViewportSize(viewport);
    await page.goto("/dashboard/channels");
    await expect(page.getByRole("heading", { level: 1, name: "Channels", exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Connect channel", exact: true }).click();
    await page.getByRole("combobox", { name: "Channel", exact: true }).selectOption("signed_webhook");
    await page.getByLabel("Connection name").fill(`Browser inbox ${viewport.width}`);
    await page.getByRole("button", { name: "Next", exact: true }).click();
    await page.getByRole("combobox", { name: "Destination type", exact: true }).selectOption("workflow");
    await page.getByRole("combobox", { name: "Workflow", exact: true }).selectOption(target);
    await page.getByRole("button", { name: "Next", exact: true }).click();
    await page.getByRole("button", { name: "Generate secret" }).click();
    await expect(page.getByLabel("Webhook Secret")).toHaveAttribute("type", "password");
    await page.getByRole("button", { name: "Save connection" }).click();
    await expect(page.getByRole("heading", { name: `Browser inbox ${viewport.width}`, exact: true })).toBeVisible();
    await expect(page.getByLabel("Enabled", { exact: true })).not.toBeChecked();
    await expect(page.getByRole("button", { name: "Link my messaging account" })).toHaveCount(0);
    await page.getByLabel("Enabled", { exact: true }).click();
    await expect(page.getByLabel("Enabled", { exact: true })).toBeChecked();
    await expect(page.getByRole("button", { name: "Link my messaging account" })).toBeVisible();
    await page.getByRole("button", { name: "Replace credentials" }).click();
    await page.getByRole("button", { name: "Generate secret" }).click();
    await page.getByRole("button", { name: "Replace and disable" }).click();
    await expect(page.getByRole("status")).toContainText("Credentials replaced");
    await expect(page.getByRole("button", { name: "Replace credentials" })).toBeEnabled();
    await expect(page.getByLabel("Enabled", { exact: true })).not.toBeChecked();
    await expect(page.getByLabel("Webhook Secret")).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
    await page.screenshot({ path: testInfo.outputPath(`channels-${viewport.width}.png`), fullPage: true });
    await page.getByRole("button", { name: "Connect channel", exact: true }).click();
    await page.getByRole("combobox", { name: "Channel", exact: true }).selectOption("telegram");
    await page.getByLabel("Connection name").fill(`Telegram setup ${viewport.width}`);
    await page.getByRole("button", { name: "Next", exact: true }).click();
    await page.getByRole("combobox", { name: "Destination type", exact: true }).selectOption("workflow");
    await page.getByRole("combobox", { name: "Workflow", exact: true }).selectOption(target);
    await page.getByRole("button", { name: "Next", exact: true }).click();
    await page.getByLabel("Access Token", { exact: true }).fill("123:synthetic");
    await page.getByRole("button", { name: "Generate secret" }).click();
    await page.getByRole("button", { name: "Save connection" }).click();
    await expect(page.getByRole("heading", { name: `Telegram setup ${viewport.width}`, exact: true })).toBeVisible();
    // Simulate a provider setup rejection only. Never contact Telegram with synthetic credentials.
    await page.route("**/channels/bindings/*/connect-telegram", (route) => route.fulfill({
      status: 422, contentType: "application/json", body: JSON.stringify({ detail: "telegram_setup_rejected" }),
    }));
    await page.getByRole("button", { name: "Connect Telegram", exact: true }).click();
    await expect(page.getByRole("alert")).toContainText("telegram_setup_rejected");
    await expect(page.getByLabel("Enabled", { exact: true })).not.toBeChecked();
    await page.route("**/channels/bindings/*/connect-telegram", (route) => route.fulfill({
      status: 409, contentType: "application/json", body: JSON.stringify({ detail: "telegram_bot_connected_elsewhere" }),
    }));
    await page.getByRole("button", { name: "Connect Telegram", exact: true }).click();
    await expect(page.getByRole("alert")).toContainText("telegram_bot_connected_elsewhere");
    await expect(page.getByLabel("Enabled", { exact: true })).not.toBeChecked();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
    await page.screenshot({ path: testInfo.outputPath(`telegram-setup-${viewport.width}.png`), fullPage: true });
    await page.goto("/docs/channel-workflows");
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await expect(page.getByText("gemini-3.5-flash-lite", { exact: false }).first()).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
    await page.screenshot({ path: testInfo.outputPath(`channel-guide-${viewport.width}.png`), fullPage: true });
  }
  expect(errors).toEqual([]);
});
