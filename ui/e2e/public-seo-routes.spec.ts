// SPDX-License-Identifier: Apache-2.0
import { expect, test } from "@playwright/test";

test("public content is available in HTML without JavaScript", async ({ browser, baseURL }) => {
  const context = await browser.newContext({ javaScriptEnabled: false });
  try {
    const page = await context.newPage();
    await page.goto(`${baseURL}/blog/shopify-merchant-seller-commerce-agent-oacp`);
    await expect(page.getByRole("heading", { level: 1 })).toHaveCount(1);
    await expect(page.getByRole("heading", { name: "Gated Reference Journey" })).toBeVisible();
    await expect(page.getByText("Shopify remains the operational source of record", { exact: false })).toBeVisible();
  } finally {
    await context.close();
  }
});

test("public links resolve to real routes and unknown paths are 404", async ({ page, request }) => {
  await page.goto("/resources/agent-activity-audit-and-live-feed");
  await page.getByRole("link", { name: "Explore the platform" }).click();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveCount(1);

  await page.goto("/docs/api-sdk-mcp");
  await page.getByRole("link", { name: "external A2A buyer access" }).click();
  await expect(page).toHaveURL(/\/docs\/seller-a2a-commerce-journey$/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveCount(1);

  expect((await request.get("/platform")).status()).toBe(404);
  expect((await request.get("/a2a-interoperability.md")).status()).toBe(404);
});

test("founder byline has a visible, factual author bio", async ({ page, request }) => {
  const path = "/blog/ai-agents-for-ca-firms-gst-tds-automation";
  const response = await request.get(path);
  expect(response.status()).toBe(200);
  expect(await response.text()).toContain("Founder &amp; CEO of Orchestrum Technologies LLP");

  await page.goto(path);
  await expect(page.getByRole("heading", { name: "About the author" })).toBeVisible();
  await expect(page.getByRole("link", { name: "X", exact: true })).toHaveAttribute("href", "https://x.com/mishrak_sanjeev");
  await expect(page.getByRole("link", { name: "LinkedIn" })).toHaveAttribute("href", "https://www.linkedin.com/in/sanjeev-kumar-a184174");
});
