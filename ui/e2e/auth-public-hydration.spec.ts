import { expect, test } from "@playwright/test";

const PUBLIC_BRANDING = {
  product_name: "AgenticOrg",
  logo_url: null,
  favicon_url: null,
  primary_color: "#7c3aed",
  accent_color: "#1e293b",
  support_email: "support@example.com",
  footer_text: null,
};

const PUBLIC_PRODUCT_FACTS = {
  version: "test",
  connector_count: 55,
  agent_count: 26,
  tool_count: 250,
};

async function installApiRoutes(page: import("@playwright/test").Page) {
  const authMeRequests: string[] = [];

  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;

    if (path.endsWith("/api/v1/auth/me")) {
      authMeRequests.push(route.request().url());
      await route.fulfill({
        status: 401,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Missing session cookie or Authorization header" }),
      });
      return;
    }

    if (path.endsWith("/api/v1/branding")) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(PUBLIC_BRANDING),
      });
      return;
    }

    if (path.endsWith("/api/v1/product-facts")) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(PUBLIC_PRODUCT_FACTS),
      });
      return;
    }

    if (path.endsWith("/api/v1/auth/config")) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ google_client_id: null }),
      });
      return;
    }

    await route.fulfill({
      status: 404,
      contentType: "application/json",
      body: JSON.stringify({ detail: "Not found in auth hydration regression stub" }),
    });
  });

  return authMeRequests;
}

test.describe("route-aware auth hydration", () => {
  test("public pages do not probe protected /auth/me on mount", async ({ page, baseURL }) => {
    const authMeRequests = await installApiRoutes(page);
    const publicPaths = ["/", "/pricing", "/blog", "/dashboard-public"];

    for (const path of publicPaths) {
      await page.goto(`${baseURL}${path}`, { waitUntil: "domcontentloaded" });
      await expect(page.locator("body")).toBeVisible();
      await page.waitForLoadState("networkidle").catch(() => {});
      await page.waitForTimeout(250);
      expect(authMeRequests, `${path} must not call /api/v1/auth/me`).toEqual([]);
    }
  });

  test("protected dashboard pages still hydrate through /auth/me", async ({
    page,
    baseURL,
  }) => {
    const authMeRequests = await installApiRoutes(page);

    await page.goto(`${baseURL}/dashboard`, { waitUntil: "domcontentloaded" });

    await expect.poll(() => authMeRequests.length).toBeGreaterThan(0);
    await expect(page).toHaveURL(/\/login/);
  });
});
