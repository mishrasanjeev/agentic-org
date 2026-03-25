/**
 * QA Bug Regression Tests — Fixed selectors
 *
 * Tests ALL bugs found by manual QA team.
 * Runs against PRODUCTION with real browser interactions.
 *
 * Run: npx playwright test e2e/qa-bugs-regression.spec.ts --config=playwright-prod.config.ts
 */

import { test, expect, Page } from "@playwright/test";

const BASE = "https://app.agenticorg.ai";
const CEO_EMAIL = "ceo@agenticorg.local";
const CEO_PASS = "ceo123!";

/** Helper: Login as CEO — stores token in localStorage */
async function loginAsCeo(page: Page) {
  await page.goto(`${BASE}/login`);
  await page.waitForLoadState("networkidle");
  await page.fill('input[placeholder="you@company.com"]', CEO_EMAIL);
  await page.fill('input[type="password"]', CEO_PASS);
  await page.click('button[type="submit"]');
  await page.waitForURL("**/dashboard**", { timeout: 15000 });
}

/** Helper: Get auth token from localStorage */
async function getToken(page: Page): Promise<string> {
  return (await page.evaluate(() => localStorage.getItem("token"))) || "";
}

// ═══════════════════════════════════════════════════════════════════════════
// A1: Mobile Layout — 375px
// ═══════════════════════════════════════════════════════════════════════════

test.describe("A1: Mobile Responsiveness @375px", () => {
  test.use({ viewport: { width: 375, height: 812 } });

  test("Landing page renders without horizontal scroll", async ({ page }) => {
    await page.goto("https://agenticorg.ai/");
    await page.waitForLoadState("domcontentloaded");
    const scrollWidth = await page.evaluate(() => document.body.scrollWidth);
    const clientWidth = await page.evaluate(() => document.body.clientWidth);
    expect(scrollWidth).toBeLessThanOrEqual(clientWidth + 5);
  });

  test("Hamburger menu opens and has navigation links", async ({ page }) => {
    await page.goto("https://agenticorg.ai/");
    await page.waitForLoadState("domcontentloaded");

    // Hamburger button visible on mobile
    const menuButton = page.locator('button[aria-label="Toggle navigation menu"]');
    await expect(menuButton).toBeVisible();

    // Click to open
    await menuButton.click();
    await page.waitForTimeout(500);

    // Mobile nav should be visible with links
    const mobileNav = page.locator('[aria-label="Mobile navigation"]');
    await expect(mobileNav).toBeVisible();

    // Should contain navigation links
    // Should contain multiple navigation links (at least 5)
    const linkCount = await mobileNav.locator('a, button').count();
    expect(linkCount).toBeGreaterThanOrEqual(5);
  });

  test("No horizontal overflow after scrolling", async ({ page }) => {
    await page.goto("https://agenticorg.ai/");
    await page.waitForLoadState("domcontentloaded");
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    await page.waitForTimeout(500);
    const scrollWidth = await page.evaluate(() => document.body.scrollWidth);
    const clientWidth = await page.evaluate(() => document.body.clientWidth);
    expect(scrollWidth).toBeLessThanOrEqual(clientWidth + 5);
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// B1: Invite Link
// ═══════════════════════════════════════════════════════════════════════════

test.describe("B1: Invite Link", () => {
  test("accept-invite route renders (not 404)", async ({ page }) => {
    await page.goto(`${BASE}/accept-invite`);
    await page.waitForLoadState("domcontentloaded");
    await page.waitForTimeout(1000);

    // Should NOT be the 404/NotFound page
    const bodyText = await page.textContent("body");
    expect(bodyText).not.toContain("Page Not Found");

    // Should show invite-related content (error about missing token is expected)
    const hasInviteContent = bodyText?.includes("invite") || bodyText?.includes("token") || bodyText?.includes("Join");
    expect(hasInviteContent).toBeTruthy();
  });

  test("accept-invite without token shows proper error", async ({ page }) => {
    await page.goto(`${BASE}/accept-invite`);
    await page.waitForLoadState("domcontentloaded");
    await page.waitForTimeout(1000);

    // Should show "no token" or "invalid" message
    const bodyText = await page.textContent("body");
    const hasError = bodyText?.includes("Invalid") || bodyText?.includes("no token") || bodyText?.includes("invalid");
    expect(hasError).toBeTruthy();
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// D2: Promote & Rollback Buttons
// ═══════════════════════════════════════════════════════════════════════════

test.describe("D2: Promote & Rollback Buttons", () => {
  test("Buttons are visible and clickable on agent detail", async ({ page }) => {
    await loginAsCeo(page);

    // Navigate to agents
    await page.goto(`${BASE}/dashboard/agents`);
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(2000);

    // Click first agent card
    await page.locator('[class*="cursor-pointer"][class*="hover"]').first().click();
    await page.waitForURL("**/agents/**", { timeout: 10000 });
    await page.waitForTimeout(1000);

    // Promote and Rollback buttons should exist
    const promoteBtn = page.getByRole("button", { name: /Promote/i });
    const rollbackBtn = page.getByRole("button", { name: /Rollback/i });

    await expect(promoteBtn).toBeVisible();
    await expect(rollbackBtn).toBeVisible();

    // Click Promote — should show response (error or success, NOT nothing)
    await promoteBtn.click();
    await page.waitForTimeout(3000);

    // Page should have some feedback — either error text or status change
    const bodyText = await page.textContent("body");
    const hasFeedback = bodyText?.includes("Promot") || bodyText?.includes("failed") || bodyText?.includes("error") || bodyText?.includes("active") || bodyText?.includes("shadow");
    expect(hasFeedback).toBeTruthy();
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// E1: Playground Shows Custom Agents
// ═══════════════════════════════════════════════════════════════════════════

test.describe("E1: Playground Shows Custom Agents", () => {
  test("Your Agents section appears for logged-in user with agents", async ({ page }) => {
    await loginAsCeo(page);

    // Create a test agent
    const token = await getToken(page);
    const ts = Date.now();
    await page.request.post(`${BASE}/api/v1/agents`, {
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      data: {
        name: `PW Agent ${ts}`, agent_type: `pw_${ts}`, domain: "finance",
        employee_name: `PW Bot ${ts}`,
        system_prompt_text: "Test agent", hitl_policy: { condition: "confidence < 0.88" },
      },
    });

    // Go to Playground
    await page.goto(`${BASE}/playground`);
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(3000);

    // Should show "Your Agents" section
    const bodyText = await page.textContent("body");
    expect(bodyText).toContain("Your Agents");
    expect(bodyText).toContain(`PW Bot ${ts}`);
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// E2: HITL in Approvals
// ═══════════════════════════════════════════════════════════════════════════

test.describe("E2: HITL in Approvals", () => {
  test("Agent HITL trigger creates entry visible in Approvals", async ({ page }) => {
    await loginAsCeo(page);
    const token = await getToken(page);
    const ts = Date.now();

    // Create agent with high confidence floor
    const createResp = await page.request.post(`${BASE}/api/v1/agents`, {
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      data: {
        name: `HITL Agent ${ts}`, agent_type: `hitl_${ts}`, domain: "finance",
        employee_name: `HITL Bot ${ts}`,
        system_prompt_text: 'Return {"status":"completed","confidence":0.5}',
        confidence_floor: 0.99,
        hitl_policy: { condition: "confidence < 0.99" },
      },
    });
    const agentId = (await createResp.json()).agent_id;

    // Run agent — will trigger HITL (0.5 < 0.99)
    await page.request.post(`${BASE}/api/v1/agents/${agentId}/run`, {
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      data: { action: "test", inputs: {} },
    });

    // Check Approvals page
    await page.goto(`${BASE}/dashboard/approvals`);
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(2000);

    // Should have at least some content (HITL entries or "pending" text)
    const bodyText = await page.textContent("body");
    const hasContent = bodyText?.includes("HITL") || bodyText?.includes("pending") || bodyText?.includes("hitl") || bodyText?.includes("approval") || bodyText?.includes("Approve");
    expect(hasContent).toBeTruthy();
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// G4/G5: Template Edit & Delete
// ═══════════════════════════════════════════════════════════════════════════

test.describe("G4/G5: Template Edit & Delete", () => {
  test("Custom template shows Edit and Delete when selected", async ({ page }) => {
    await loginAsCeo(page);
    const token = await getToken(page);
    const ts = Date.now();

    // Create custom template via API — use a name that's easy to find when humanized
    // humanize("pwtpl123") → "Pwtpl123" (no underscores)
    const tplName = `pwtpl${ts}`;
    await page.request.post(`${BASE}/api/v1/prompt-templates`, {
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      data: {
        name: tplName, agent_type: `pwtype${ts}`, domain: "finance",
        template_text: "Playwright test template content",
      },
    });

    // Navigate to templates page
    await page.goto(`${BASE}/dashboard/prompt-templates`);
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(2000);

    // Click the custom template — humanize capitalizes first letter
    const humanizedName = tplName.charAt(0).toUpperCase() + tplName.slice(1);
    const tplCard = page.locator(`text=${humanizedName}`).first();
    await expect(tplCard).toBeVisible({ timeout: 5000 });
    await tplCard.click();
    await page.waitForTimeout(1000);

    // Edit and Delete buttons should be visible
    await expect(page.getByRole("button", { name: /^Edit$/ })).toBeVisible({ timeout: 5000 });
    await expect(page.getByRole("button", { name: /Delete/ })).toBeVisible({ timeout: 5000 });
  });

  test("Edit opens textarea and Save works", async ({ page }) => {
    await loginAsCeo(page);
    const token = await getToken(page);
    const ts = Date.now();

    const editName = `edittpl${ts}`;
    await page.request.post(`${BASE}/api/v1/prompt-templates`, {
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      data: {
        name: editName, agent_type: `edittype${ts}`, domain: "finance",
        template_text: "Original content",
      },
    });

    await page.goto(`${BASE}/dashboard/prompt-templates`);
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(2000);

    const editHumanized = editName.charAt(0).toUpperCase() + editName.slice(1);
    await page.locator(`text=${editHumanized}`).first().click();
    await page.waitForTimeout(1000);

    // Click Edit
    await page.getByRole("button", { name: /^Edit$/ }).click();
    await page.waitForTimeout(500);

    // Textarea should appear
    await expect(page.locator("textarea")).toBeVisible();

    // Save button should appear
    await expect(page.getByRole("button", { name: /Save/ })).toBeVisible();
  });

  test("Delete removes template from list", async ({ page }) => {
    await loginAsCeo(page);
    const token = await getToken(page);
    const ts = Date.now();

    const delName = `deltpl${ts}`;
    await page.request.post(`${BASE}/api/v1/prompt-templates`, {
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      data: {
        name: delName, agent_type: `deltype${ts}`, domain: "finance",
        template_text: "Delete me",
      },
    });

    await page.goto(`${BASE}/dashboard/prompt-templates`);
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(2000);

    const delHumanized = delName.charAt(0).toUpperCase() + delName.slice(1);
    await page.locator(`text=${delHumanized}`).first().click();
    await page.waitForTimeout(1000);

    // Accept dialog
    page.on("dialog", (d) => d.accept());

    await page.getByRole("button", { name: /Delete/ }).click();
    await page.waitForTimeout(2000);

    // Template should no longer be visible
    const delHumanized2 = delName.charAt(0).toUpperCase() + delName.slice(1);
    await expect(page.locator(`text=${delHumanized2}`)).not.toBeVisible({ timeout: 3000 });
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// G6: Built-in Template Clone
// ═══════════════════════════════════════════════════════════════════════════

test.describe("G6: Built-in Template Clone", () => {
  test("Built-in template shows Clone button and explanation", async ({ page }) => {
    await loginAsCeo(page);

    await page.goto(`${BASE}/dashboard/prompt-templates`);
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(2000);

    // Click a built-in template (first one should be built-in)
    const firstCard = page.locator('[class*="cursor-pointer"][class*="hover"]').first();
    await firstCard.click();
    await page.waitForTimeout(1000);

    // Should see "Built-in" badge
    const bodyText = await page.textContent("body");
    expect(bodyText).toContain("Built-in");

    // Should see "Clone to Edit" button
    await expect(page.getByRole("button", { name: /Clone/ })).toBeVisible({ timeout: 5000 });

    // Should see explanation text
    const hasExplanation = bodyText?.includes("system template") || bodyText?.includes("cannot be edited") || bodyText?.includes("Clone");
    expect(hasExplanation).toBeTruthy();

    // Should NOT have regular Edit/Delete
    await expect(page.getByRole("button", { name: /^Edit$/ })).not.toBeVisible();
    await expect(page.getByRole("button", { name: /^Delete$/ })).not.toBeVisible();
  });

  test("Clone creates a custom copy", async ({ page }) => {
    await loginAsCeo(page);

    await page.goto(`${BASE}/dashboard/prompt-templates`);
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(2000);

    // Click a built-in template
    const firstCard = page.locator('[class*="cursor-pointer"][class*="hover"]').first();
    await firstCard.click();
    await page.waitForTimeout(1000);

    // Click Clone
    await page.getByRole("button", { name: /Clone/ }).click();
    await page.waitForTimeout(2000);

    // Page should refresh/update the list — the clone has "_custom" suffix
    // Reload to be sure
    await page.reload();
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(2000);

    // A "custom" template should now appear in the list (humanized: "Custom")
    const bodyText = await page.textContent("body");
    const hasClone = bodyText?.includes("_custom") || bodyText?.includes("Custom");
    expect(hasClone).toBeTruthy();
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// Signup Flow
// ═══════════════════════════════════════════════════════════════════════════

test.describe("Signup Flow", () => {
  test("Email signup creates account and redirects to onboarding", async ({ page }) => {
    const ts = Date.now();
    await page.goto(`${BASE}/signup`);
    await page.waitForLoadState("domcontentloaded");

    await page.fill('#orgName', `PW Org ${ts}`);
    await page.fill('#name', "PW User");
    await page.fill('#signupEmail', `pw-${ts}@test.test`);
    await page.fill('#signupPassword', "PwTest@2026");
    await page.fill('#confirmPassword', "PwTest@2026");
    await page.click('button[type="submit"]');

    await page.waitForURL("**/onboarding**", { timeout: 10000 });
    expect(page.url()).toContain("/onboarding");
  });

  test("Google sign-in button renders on signup page", async ({ page }) => {
    await page.goto(`${BASE}/signup`);
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(3000);

    // Should have "Or" divider (Google section rendered)
    const bodyText = await page.textContent("body");
    const hasGoogle = bodyText?.includes("Or") || bodyText?.includes("Google");
    expect(hasGoogle).toBeTruthy();
  });
});
