// SPDX-License-Identifier: Apache-2.0
import { expect, test } from "./helpers/test";
import { APP, E2E_TOKEN, authenticate, requireAuth } from "./helpers/auth";

test.describe("Knowledge upload and tool-free creation @knowledge @agent-create", () => {
  test.beforeEach(async ({ page }) => {
    requireAuth();
    await authenticate(page);
  });

  test("a short document is indexed, searchable and opens its real cited excerpt", async ({ page, request }) => {
    const marker = `synthetic-browser-${Date.now()}`;
    const filename = `${marker}.txt`;
    let documentId: string | undefined;
    try {
      await page.goto(`${APP}/dashboard/knowledge`);
      await expect(page.getByRole("heading", { name: "Knowledge Base", exact: true })).toBeVisible();
      const response = page.waitForResponse((res) => res.url().includes("/knowledge/upload") && res.request().method() === "POST");
      await page.locator('input[type="file"]').setInputFiles({ name: filename, mimeType: "text/plain", buffer: Buffer.from(`${marker} review code 40721.`) });
      const upload = await response;
      expect(upload.status()).toBe(201);
      documentId = (await upload.json()).document_id;
      const row = page.getByRole("row").filter({ hasText: filename });
      await expect(row).toContainText("indexed", { timeout: 90_000 });
      await page.getByPlaceholder("Test a query against the knowledge base...").fill(marker);
      await page.getByRole("button", { name: "Search", exact: true }).click();
      const result = page.getByTestId("kb-search-result").filter({ hasText: marker }).first();
      await expect(result).toContainText("40721");
      await result.getByRole("button", { name: "Open excerpt" }).click();
      await expect(page.getByTestId("kb-excerpt")).toContainText(marker);
    } finally {
      if (documentId) {
        const removed = await request.delete(`${APP}/api/v1/knowledge/documents/${documentId}`, { headers: { Authorization: `Bearer ${E2E_TOKEN}` } });
        expect(removed.ok()).toBeTruthy();
      }
    }
  });

  test("a transient indexing failure stays processing and recovers through the real worker", async ({ page, request }) => {
    test.skip(process.env.E2E_TRANSIENT_EMBEDDING_FIXTURE !== "1", "Requires the controlled local transient embedding fixture.");
    test.setTimeout(150_000);
    const marker = `synthetic-browser-recovery-${Date.now()}`;
    const filename = `${marker}.txt`;
    let documentId: string | undefined;
    try {
      await page.goto(`${APP}/dashboard/knowledge`);
      await expect(page.getByRole("heading", { name: "Knowledge Base", exact: true })).toBeVisible();
      const response = page.waitForResponse((res) => res.url().includes("/knowledge/upload") && res.request().method() === "POST");
      await page.locator('input[type="file"]').setInputFiles({ name: filename, mimeType: "text/plain", buffer: Buffer.from(`TRANSIENT COLD ${marker} review code 40722.`) });
      const upload = await response;
      expect(upload.status()).toBe(201);
      const accepted = await upload.json();
      documentId = accepted.document_id;
      expect(accepted.status).toBe("processing");
      const row = page.getByRole("row").filter({ hasText: filename });
      await expect(row).toContainText("processing");
      await expect(row).toContainText("Indexing temporarily unavailable. Retry pending.");
      await expect(row).toContainText("indexed", { timeout: 120_000 });
      await expect(row).not.toContainText("Retry pending");
      await page.getByPlaceholder("Test a query against the knowledge base...").fill(marker);
      await page.getByRole("button", { name: "Search", exact: true }).click();
      await expect(page.getByTestId("kb-search-result").filter({ hasText: marker }).first()).toContainText("40722");
    } finally {
      if (documentId) {
        const removed = await request.delete(`${APP}/api/v1/knowledge/documents/${documentId}`, { headers: { Authorization: `Bearer ${E2E_TOKEN}` } });
        expect(removed.ok()).toBeTruthy();
      }
    }
  });

  test("the manual wizard can create a reasoning-only agent with zero retries", async ({ page, request }) => {
    let agentId: string | undefined;
    try {
      await page.goto(`${APP}/dashboard/agents/new`);
      await page.getByTestId("skip-to-manual").click();
      await page.getByPlaceholder("e.g. Priya, Arjun, Maya").fill(`Synthetic reasoning ${Date.now()}`);
      await page.getByRole("button", { name: "Next", exact: true }).click();
      await expect(page.getByText("Step 2: Role", { exact: true })).toBeVisible();
      await page.getByRole("button", { name: "Next", exact: true }).click();
      await page.getByPlaceholder(/You are the/).fill("Answer synthetic arithmetic only. Do not use tools or customer data.");
      await page.getByRole("button", { name: "Next", exact: true }).click();
      await page.getByLabel("Use suggested tools when none selected").uncheck();
      const retries = page.locator('input[type="number"][min="0"][max="10"]');
      await retries.fill("0");
      await page.getByRole("button", { name: "Next", exact: true }).click();
      const created = page.waitForResponse((res) => /\/api\/v1\/agents$/.test(res.url()) && res.request().method() === "POST");
      await page.getByRole("button", { name: "Create as Shadow", exact: true }).click();
      const response = await created;
      expect(response.status()).toBe(201);
      agentId = (await response.json()).agent_id;
      expect(agentId).toBeTruthy();
      const detail = await request.get(`${APP}/api/v1/agents/${agentId}`, { headers: { Authorization: `Bearer ${E2E_TOKEN}` } });
      expect(detail.status()).toBe(200);
      expect(await detail.json()).toMatchObject({ authorized_tools: [], connector_ids: [], max_retries: 0 });
      await expect(page.getByTestId("agent-metrics")).toBeVisible();
      await page.screenshot({ path: "test-results/reasoning-only-agent-desktop.png", fullPage: true });
      await page.setViewportSize({ width: 390, height: 844 });
      await expect(page.getByRole("button", { name: "Chat with Agent", exact: true })).toBeInViewport();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
      const metricsFit = await page.getByTestId("agent-metrics").locator("h3").evaluateAll((headings) =>
        headings.every((heading) => heading.scrollWidth <= heading.clientWidth),
      );
      expect(metricsFit).toBeTruthy();
      await page.screenshot({ path: "test-results/reasoning-only-agent-mobile.png", fullPage: true });
    } finally {
      if (agentId) {
        const removed = await request.delete(`${APP}/api/v1/agents/${agentId}`, { headers: { Authorization: `Bearer ${E2E_TOKEN}` } });
        expect(removed.ok()).toBeTruthy();
      }
    }
  });
});
