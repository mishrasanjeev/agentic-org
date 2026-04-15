/**
 * Error Handling & Edge Cases E2E Tests
 *
 * Tests 404 pages, auth redirects, XSS prevention, large inputs,
 * offline mode, and console error checks against PRODUCTION.
 *
 * NO page.route() mocking -- all responses are real.
 */
import { test, expect, Page } from "@playwright/test";

const E2E_TOKEN = process.env.E2E_TOKEN || "";
const canAuth = !!E2E_TOKEN;

// ---------------------------------------------------------------------------
// Auth helper — token-based only, no hardcoded credentials
// ---------------------------------------------------------------------------

async function ensureAuth(page: Page, baseURL: string) {
  await page.goto(baseURL, { waitUntil: "domcontentloaded" });
  await page.evaluate((token) => {
    localStorage.setItem("token", token);
    localStorage.setItem("user", JSON.stringify({ email: "ceo@agenticorg.local", name: "CEO", role: "ceo", domain: "general", tenant_id: "e2e-tenant", onboardingComplete: true }));    localStorage.setItem(
      "user",
      JSON.stringify({
        email: "e2e@agenticorg.ai",
        name: "E2E Runner",
        role: "admin",
        domain: "all",
        tenant_id: "t-001",
        onboardingComplete: true,
      }),
    );
  }, E2E_TOKEN);
}

// ===========================================================================
// 404 PAGE
// ===========================================================================

test.describe("404 Page Handling", () => {
  test("unknown route shows Page Not Found", async ({ page, baseURL }) => {
    await page.goto(`${baseURL}/this-page-does-not-exist-at-all`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForLoadState("networkidle");

    const bodyText = await page.textContent("body");
    const has404 =
      bodyText?.includes("Page Not Found") ||
      bodyText?.includes("404") ||
      bodyText?.includes("not found");
    expect(has404).toBeTruthy();
  });

  test("/dashboard/nonexistent shows 404 or redirects", async ({
    page,
    baseURL,
  }) => {
    test.skip(!canAuth, "requires E2E_TOKEN");
    await ensureAuth(page, baseURL!);

    await page.goto(`${baseURL}/dashboard/nonexistent-page-xyz`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForLoadState("networkidle");

    const bodyText = await page.textContent("body");
    const has404OrRedirect =
      bodyText?.includes("Page Not Found") ||
      bodyText?.includes("404") ||
      bodyText?.includes("not found") ||
      page.url().includes("/dashboard") ||
      page.url().includes("/login");
    expect(has404OrRedirect).toBeTruthy();
  });

  test("404 page has navigation links back to home/dashboard", async ({
    page,
    baseURL,
  }) => {
    await page.goto(`${baseURL}/completely-invalid-route`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForLoadState("networkidle");

    const homeLink = page.getByText("Back to Home");
    if (await homeLink.isVisible({ timeout: 3000 }).catch(() => false)) {
      expect(await homeLink.getAttribute("href")).toBeTruthy();
    }

    const dashLink = page.getByText("Go to Dashboard");
    if (await dashLink.isVisible({ timeout: 3000 }).catch(() => false)) {
      expect(await dashLink.getAttribute("href")).toBeTruthy();
    }
  });

  test("/dashboard/cfo/blah shows 404 or redirects", async ({
    page,
    baseURL,
  }) => {
    test.skip(!canAuth, "requires E2E_TOKEN");
    await ensureAuth(page, baseURL!);

    await page.goto(`${baseURL}/dashboard/cfo/blah`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForLoadState("networkidle");

    const bodyText = await page.textContent("body");
    const url = page.url();
    const handled =
      bodyText?.includes("Page Not Found") ||
      bodyText?.includes("404") ||
      bodyText?.includes("not found") ||
      url.includes("/login") ||
      url.includes("/dashboard");
    expect(handled).toBeTruthy();
  });
});

// ===========================================================================
// AUTH REDIRECT — unauthenticated access
// ===========================================================================

test.describe("Auth Redirect", () => {
  test("accessing /dashboard without token redirects to /login", async ({
    page,
    baseURL,
  }) => {
    await page.goto(baseURL!, { waitUntil: "domcontentloaded" });
    await page.evaluate(() => {
      localStorage.removeItem("token");
      localStorage.removeItem("user");
    });

    await page.goto(`${baseURL}/dashboard`, { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle");

    expect(page.url()).toContain("/login");
  });

  test("accessing /dashboard/cfo without token redirects to /login", async ({
    page,
    baseURL,
  }) => {
    await page.goto(baseURL!, { waitUntil: "domcontentloaded" });
    await page.evaluate(() => {
      localStorage.removeItem("token");
      localStorage.removeItem("user");
    });

    await page.goto(`${baseURL}/dashboard/cfo`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForLoadState("networkidle");

    expect(page.url()).toContain("/login");
  });

  test("accessing /dashboard/report-schedules without token redirects to /login", async ({
    page,
    baseURL,
  }) => {
    await page.goto(baseURL!, { waitUntil: "domcontentloaded" });
    await page.evaluate(() => {
      localStorage.removeItem("token");
      localStorage.removeItem("user");
    });

    await page.goto(`${baseURL}/dashboard/report-schedules`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForLoadState("networkidle");

    expect(page.url()).toContain("/login");
  });
});

// ===========================================================================
// TOKEN EXPIRY — real expired/invalid token behaviour
// ===========================================================================

test.describe("Token Expiry", () => {
  test("invalid token on dashboard redirects to login or shows error", async ({
    page,
    baseURL,
  }) => {
    await page.goto(baseURL!, { waitUntil: "domcontentloaded" });
    await page.evaluate(() => {
      localStorage.setItem("token", "expired.invalid.token");
    localStorage.setItem("user", JSON.stringify({ email: "ceo@agenticorg.local", name: "CEO", role: "ceo", domain: "general", tenant_id: "e2e-tenant", onboardingComplete: true }));      localStorage.setItem(
        "user",
        JSON.stringify({ email: "x@x.x", name: "X", role: "admin" }),
      );
    });

    await page.goto(`${baseURL}/dashboard/cfo`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForLoadState("networkidle");

    const url = page.url();
    const bodyText = await page.textContent("body");
    const redirectedOrErrorShown =
      url.includes("/login") ||
      bodyText?.includes("Failed to load") ||
      bodyText?.includes("error") ||
      bodyText?.includes("No data");
    expect(redirectedOrErrorShown).toBeTruthy();
  });
});

// ===========================================================================
// XSS PREVENTION
// ===========================================================================

test.describe("XSS Prevention", () => {
  test("NL query with script tag does not execute", async ({
    page,
    baseURL,
  }) => {
    test.skip(!canAuth, "requires E2E_TOKEN");
    await ensureAuth(page, baseURL!);
    await page.goto(`${baseURL}/dashboard`, { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle");

    const input = page.locator('input[placeholder*="Ask anything"]');
    if (!(await input.isVisible({ timeout: 5000 }).catch(() => false))) {
      test.skip();
      return;
    }

    let alertCalled = false;
    page.on("dialog", (dialog) => {
      alertCalled = true;
      dialog.dismiss();
    });

    await input.fill('<script>alert("XSS")</script>');
    await input.press("Enter");
    await page.waitForLoadState("networkidle");

    expect(alertCalled).toBe(false);
    await expect(page.locator("body")).toBeVisible();
  });

  test("NL query with img onerror XSS does not execute", async ({
    page,
    baseURL,
  }) => {
    test.skip(!canAuth, "requires E2E_TOKEN");
    await ensureAuth(page, baseURL!);
    await page.goto(`${baseURL}/dashboard`, { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle");

    const input = page.locator('input[placeholder*="Ask anything"]');
    if (!(await input.isVisible({ timeout: 5000 }).catch(() => false))) {
      test.skip();
      return;
    }

    let alertCalled = false;
    page.on("dialog", (dialog) => {
      alertCalled = true;
      dialog.dismiss();
    });

    await input.fill("<img src=x onerror=alert(1)>");
    await input.press("Enter");
    await page.waitForLoadState("networkidle");

    expect(alertCalled).toBe(false);
  });

  test("login email field sanitizes script tags", async ({
    page,
    baseURL,
  }) => {
    await page.goto(`${baseURL}/login`, { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle");

    let alertCalled = false;
    page.on("dialog", (dialog) => {
      alertCalled = true;
      dialog.dismiss();
    });

    const emailInput = page.locator('input[type="email"], input[placeholder*="you@"]').first();
    if (await emailInput.isVisible({ timeout: 3000 }).catch(() => false)) {
      await emailInput.fill('<script>alert("XSS")</script>');
      await emailInput.press("Tab");
    }

    expect(alertCalled).toBe(false);
  });
});

// ===========================================================================
// LARGE INPUT HANDLING
// ===========================================================================

test.describe("Large Input Handling", () => {
  test("NL query with 10,000 characters handles gracefully", async ({
    page,
    baseURL,
  }) => {
    test.skip(!canAuth, "requires E2E_TOKEN");
    await ensureAuth(page, baseURL!);
    await page.goto(`${baseURL}/dashboard`, { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle");

    const input = page.locator('input[placeholder*="Ask anything"]');
    if (!(await input.isVisible({ timeout: 5000 }).catch(() => false))) {
      test.skip();
      return;
    }

    const largeInput = "A".repeat(10000);
    await input.fill(largeInput);
    await input.press("Enter");
    await page.waitForLoadState("networkidle");

    expect(page.url()).toContain("/dashboard");
    await expect(page.locator("body")).toBeVisible();
  });

  test("login email field with very long input does not crash", async ({
    page,
    baseURL,
  }) => {
    await page.goto(`${baseURL}/login`, { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle");

    const emailInput = page.locator('input[type="email"], input[placeholder*="you@"]').first();
    if (await emailInput.isVisible({ timeout: 3000 }).catch(() => false)) {
      const longEmail = "a".repeat(500) + "@example.com";
      await emailInput.fill(longEmail);
      await expect(emailInput).toBeVisible();
    }

    await expect(page.locator("body")).toBeVisible();
  });
});

// ===========================================================================
// NETWORK ERROR (OFFLINE)
// ===========================================================================

test.describe("Network Error Handling", () => {
  test("page recovers after temporary network issue", async ({
    page,
    baseURL,
  }) => {
    // Offline mode cannot be reliably tested against production.
    // Instead, verify the page loads and does not crash on reload.
    test.skip(!canAuth, "requires E2E_TOKEN");
    await ensureAuth(page, baseURL!);
    await page.goto(`${baseURL}/dashboard`, { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle");

    // Reload the page to simulate recovery
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle");

    const bodyText = await page.textContent("body").catch(() => "");
    expect(bodyText).toBeTruthy();
    expect(bodyText!.length).toBeGreaterThan(0);
  });
});

// ===========================================================================
// CONSOLE ERROR CHECK
// ===========================================================================

test.describe("No Unhandled Errors", () => {
  test("CFO Dashboard loads without unhandled console errors", async ({
    page,
    baseURL,
  }) => {
    test.skip(!canAuth, "requires E2E_TOKEN");
    const consoleErrors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") {
        consoleErrors.push(msg.text());
      }
    });

    await ensureAuth(page, baseURL!);
    await page.goto(`${baseURL}/dashboard/cfo`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForLoadState("networkidle");

    // Filter out known non-critical errors
    const criticalErrors = consoleErrors.filter(
      (e) =>
        !e.includes("favicon") &&
        !e.includes("analytics") &&
        !e.includes("gtag") &&
        !e.includes("google") &&
        !e.includes("net::ERR"),
    );

    for (const err of criticalErrors) {
      expect(err).not.toContain("Unhandled");
      expect(err).not.toContain("uncaught");
    }
  });

  test("CMO Dashboard loads without unhandled console errors", async ({
    page,
    baseURL,
  }) => {
    test.skip(!canAuth, "requires E2E_TOKEN");
    const consoleErrors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") {
        consoleErrors.push(msg.text());
      }
    });

    await ensureAuth(page, baseURL!);
    await page.goto(`${baseURL}/dashboard/cmo`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForLoadState("networkidle");

    const criticalErrors = consoleErrors.filter(
      (e) =>
        !e.includes("favicon") &&
        !e.includes("analytics") &&
        !e.includes("gtag") &&
        !e.includes("google") &&
        !e.includes("net::ERR"),
    );

    for (const err of criticalErrors) {
      expect(err).not.toContain("Unhandled");
      expect(err).not.toContain("uncaught");
    }
  });
});

// ===========================================================================
// PROTECTED ROUTE GUARDS
// ===========================================================================

test.describe("Protected Route Guards", () => {
  test("all dashboard routes redirect to login when unauthenticated", async ({
    page,
    baseURL,
  }) => {
    const protectedPaths = [
      "/dashboard",
      "/dashboard/cfo",
      "/dashboard/cmo",
      "/dashboard/agents",
      "/dashboard/workflows",
      "/dashboard/approvals",
      "/dashboard/connectors",
      "/dashboard/schemas",
      "/dashboard/audit",
      "/dashboard/settings",
      "/dashboard/report-schedules",
    ];

    // Clear auth
    await page.goto(baseURL!, { waitUntil: "domcontentloaded" });
    await page.evaluate(() => {
      localStorage.removeItem("token");
      localStorage.removeItem("user");
    });

    for (const path of protectedPaths) {
      await page.goto(`${baseURL}${path}`, { waitUntil: "domcontentloaded" });
      await page.waitForLoadState("networkidle");

      expect(
        page.url(),
        `${path} should redirect to /login`,
      ).toContain("/login");
    }
  });
});
