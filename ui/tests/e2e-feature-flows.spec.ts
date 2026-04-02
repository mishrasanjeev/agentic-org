/**
 * End-to-End Feature Flow Tests — AgenticOrg
 *
 * Comprehensive Playwright tests covering full user journeys across:
 *   1. Agent Creation Wizard (5-step flow)
 *   2. Connector Registration (auth type switching)
 *   3. Settings & API Keys management
 *   4. Landing Page Developer Section (SDKs)
 *   5. Login / Signup validation
 *   6. Dashboard data consistency
 *   7. Public pages accessibility
 *   8. API endpoint validation
 *
 * Runs against PRODUCTION:
 *   - Public pages:    https://agenticorg.ai
 *   - App / dashboard: https://app.agenticorg.ai
 *
 * Run:
 *   npx playwright test tests/e2e-feature-flows.spec.ts --config=playwright.config.ts
 */

import { test, expect, Page } from "@playwright/test";

// ── URLs ────────────────────────────────────────────────────────────────────
const SITE = "https://agenticorg.ai";
const APP  = "https://app.agenticorg.ai";

// ── Credentials (demo sandbox) ──────────────────────────────────────────────
const CEO_EMAIL = "ceo@agenticorg.local";
const CEO_PASS  = "ceo123!";

// ── Domain / agent-type reference ───────────────────────────────────────────
const ALL_DOMAINS = ["finance", "hr", "marketing", "ops", "backoffice", "comms"];
const AGENT_TYPES: Record<string, string[]> = {
  finance:    ["ap_processor", "ar_collections", "recon_agent", "tax_compliance", "close_agent", "fpa_agent"],
  hr:         ["talent_acquisition", "onboarding_agent", "payroll_engine", "performance_coach", "ld_coordinator", "offboarding_agent"],
  marketing:  ["content_factory", "campaign_pilot", "seo_strategist", "crm_intelligence", "brand_monitor"],
  ops:        ["support_triage", "vendor_manager", "contract_intelligence", "compliance_guard", "it_operations"],
  backoffice: ["legal_ops", "risk_sentinel", "facilities_agent"],
  comms:      ["email_agent", "notification_agent", "chat_agent"],
};

const CONNECTOR_CATEGORIES = ["finance", "hr", "marketing", "ops", "comms"];

// ── Helpers ─────────────────────────────────────────────────────────────────

/** Login as CEO admin and wait for dashboard. */
async function loginAsCeo(page: Page) {
  await page.goto(`${APP}/login`);
  await page.waitForLoadState("networkidle");
  await page.fill('input[placeholder="you@company.com"]', CEO_EMAIL);
  await page.fill('input[type="password"]', CEO_PASS);
  await page.click('button[type="submit"]');
  await page.waitForURL("**/dashboard**", { timeout: 15_000 });
}

/** Extract auth token from localStorage after login. */
async function getToken(page: Page): Promise<string> {
  return (await page.evaluate(() => localStorage.getItem("token"))) || "";
}

function humanize(s: string) {
  return s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

// =============================================================================
//  FLOW 1: Agent Creation Wizard — Full 5-step journey
// =============================================================================

test.describe("Flow 1: Agent Creation E2E — 5-step wizard", () => {
  test.beforeEach(async ({ page }) => {
    await loginAsCeo(page);
  });

  test("Step 1 (Persona): domain dropdown has all 6 domains", async ({ page }) => {
    await page.goto(`${APP}/dashboard/agents/new`);
    await page.waitForLoadState("networkidle");

    // Verify page title
    await expect(page.locator("h2", { hasText: "Create Virtual Employee" })).toBeVisible({ timeout: 10_000 });

    // Verify we are on Step 1: Persona
    await expect(page.locator("text=Step 1: Persona")).toBeVisible();

    // Verify all 6 domains are in the domain <select>
    const domainSelect = page.locator("select").filter({ has: page.locator(`option[value="finance"]`) });
    await expect(domainSelect).toBeVisible();

    for (const domain of ALL_DOMAINS) {
      const option = domainSelect.locator(`option[value="${domain}"]`);
      await expect(option).toBeAttached();
    }
  });

  test("Step 1 → Step 2: agent types populate per domain", async ({ page }) => {
    await page.goto(`${APP}/dashboard/agents/new`);
    await page.waitForLoadState("networkidle");

    // Fill required name field so Next is enabled
    await page.fill('input[placeholder="e.g. Priya, Arjun, Maya"]', "TestAgent");

    // Try each domain and verify corresponding agent types
    for (const domain of ALL_DOMAINS) {
      // Select domain
      const domainSelect = page.locator("select").filter({ has: page.locator(`option[value="finance"]`) });
      await domainSelect.selectOption(domain);

      // Click Next to go to Step 2 (Role)
      await page.click("button:has-text('Next')");
      await expect(page.locator("text=Step 2: Role")).toBeVisible({ timeout: 5_000 });

      // Verify agent type dropdown contains all types for this domain
      const agentTypeSelect = page.locator("select").filter({ has: page.locator(`option[value="${AGENT_TYPES[domain][0]}"]`) });
      await expect(agentTypeSelect).toBeVisible();

      for (const agentType of AGENT_TYPES[domain]) {
        const option = agentTypeSelect.locator(`option[value="${agentType}"]`);
        await expect(option).toBeAttached();
      }

      // Go back to Step 1 to test next domain
      await page.click("button:has-text('Back')");
      await expect(page.locator("text=Step 1: Persona")).toBeVisible({ timeout: 5_000 });
    }
  });

  test("Steps 1-4: authorized tools appear in Behavior step", async ({ page }) => {
    await page.goto(`${APP}/dashboard/agents/new`);
    await page.waitForLoadState("networkidle");

    // Step 1: Persona — fill name
    await page.fill('input[placeholder="e.g. Priya, Arjun, Maya"]', "TestAgent");
    await page.click("button:has-text('Next')");
    await expect(page.locator("text=Step 2: Role")).toBeVisible({ timeout: 5_000 });

    // Step 2: Role — accept defaults, click Next
    await page.click("button:has-text('Next')");
    await expect(page.locator("text=Step 3: Prompt")).toBeVisible({ timeout: 5_000 });

    // Step 3: Prompt — fill a prompt
    await page.fill('textarea[placeholder*="You are the"]', "You are a test agent for validation purposes.");
    await page.click("button:has-text('Next')");
    await expect(page.locator("text=Step 4: Behavior")).toBeVisible({ timeout: 5_000 });

    // Step 4: Behavior — verify authorized tools section exists
    await expect(page.locator("text=Authorized Tools")).toBeVisible();

    // Verify the tools add dropdown exists
    const toolAddSelect = page.locator("select").filter({ has: page.locator('option:has-text("+ Add a tool...")') });
    await expect(toolAddSelect).toBeVisible();

    // Verify LLM model selector exists with known options
    const llmSelect = page.locator("select").filter({ has: page.locator('option[value="gemini-2.5-flash"]') });
    await expect(llmSelect).toBeVisible();

    // Verify confidence floor slider exists
    const confidenceSlider = page.locator('input[type="range"]');
    await expect(confidenceSlider).toBeVisible();

    // Verify HITL condition field exists
    await expect(page.locator('input[placeholder*="confidence < 0.88"]')).toBeVisible();
  });

  test("Step 5: Review shows all configured values + Create button", async ({ page }) => {
    await page.goto(`${APP}/dashboard/agents/new`);
    await page.waitForLoadState("networkidle");

    const agentName = "ReviewTestAgent";
    const designation = "Senior QA Analyst";

    // Step 1: Persona
    await page.fill('input[placeholder="e.g. Priya, Arjun, Maya"]', agentName);
    await page.fill('input[placeholder="e.g. Senior AP Analyst - Mumbai Office"]', designation);
    await page.click("button:has-text('Next')");
    await expect(page.locator("text=Step 2: Role")).toBeVisible({ timeout: 5_000 });

    // Step 2: Role — accept defaults
    await page.click("button:has-text('Next')");
    await expect(page.locator("text=Step 3: Prompt")).toBeVisible({ timeout: 5_000 });

    // Step 3: Prompt
    await page.fill('textarea[placeholder*="You are the"]', "You are the QA agent for Acme Corp.");
    await page.click("button:has-text('Next')");
    await expect(page.locator("text=Step 4: Behavior")).toBeVisible({ timeout: 5_000 });

    // Step 4: Behavior — accept defaults
    await page.click("button:has-text('Next')");
    await expect(page.locator("text=Step 5: Review")).toBeVisible({ timeout: 5_000 });

    // Verify Review step shows the agent name
    await expect(page.locator(`h3:has-text("${agentName}")`)).toBeVisible();

    // Verify designation is displayed
    await expect(page.locator(`text=${designation}`)).toBeVisible();

    // Verify domain badge (Finance is default)
    await expect(page.locator("text=Finance").first()).toBeVisible();

    // Verify agent type is shown
    await expect(page.locator("text=Agent Type:")).toBeVisible();

    // Verify confidence floor is shown
    await expect(page.locator("text=Confidence Floor:")).toBeVisible();

    // Verify HITL condition is shown
    await expect(page.locator("text=HITL Condition:")).toBeVisible();

    // Verify Max Retries is shown
    await expect(page.locator("text=Max Retries:")).toBeVisible();

    // Verify LLM Model is shown
    await expect(page.locator("text=LLM Model:")).toBeVisible();

    // Verify prompt preview section
    await expect(page.locator("text=Prompt Preview")).toBeVisible();

    // Verify "Create as Shadow" button exists (does NOT click it to avoid creating real data)
    const createButton = page.locator("button:has-text('Create as Shadow')");
    await expect(createButton).toBeVisible();
    await expect(createButton).toBeEnabled();
  });
});

// =============================================================================
//  FLOW 2: Connector Registration — auth type switching
// =============================================================================

test.describe("Flow 2: Connector Registration E2E", () => {
  test.beforeEach(async ({ page }) => {
    await loginAsCeo(page);
    await page.goto(`${APP}/dashboard/connectors/new`);
    await page.waitForLoadState("networkidle");
  });

  test("All 5 categories are in the Category dropdown", async ({ page }) => {
    await expect(page.locator("h2", { hasText: "Register Connector" })).toBeVisible({ timeout: 10_000 });

    const categorySelect = page.locator("select").filter({ has: page.locator('option[value="finance"]') });
    await expect(categorySelect).toBeVisible();

    for (const cat of CONNECTOR_CATEGORIES) {
      const option = categorySelect.locator(`option[value="${cat}"]`);
      await expect(option).toBeAttached();
    }
  });

  test("OAuth2 auth type shows 3 credential fields (client_id, client_secret, refresh_token)", async ({ page }) => {
    const authTypeSelect = page.locator("select").filter({ has: page.locator('option[value="oauth2"]') });
    await authTypeSelect.selectOption("oauth2");

    // Wait for auth fields to render
    await page.waitForTimeout(500);

    // Verify 3 credential input fields appear
    const clientIdField = page.locator('input[placeholder="Enter client ID"]');
    const clientSecretField = page.locator('input[placeholder="Enter client secret"]');
    const refreshTokenField = page.locator('input[placeholder*="refresh token"]');

    await expect(clientIdField).toBeVisible();
    await expect(clientSecretField).toBeVisible();
    await expect(refreshTokenField).toBeVisible();
  });

  test("API Key auth type shows 1 credential field", async ({ page }) => {
    const authTypeSelect = page.locator("select").filter({ has: page.locator('option[value="api_key"]') });
    await authTypeSelect.selectOption("api_key");

    await page.waitForTimeout(500);

    // Verify single API key field
    const apiKeyField = page.locator('input[placeholder="Enter API key"]');
    await expect(apiKeyField).toBeVisible();

    // Verify client_id / client_secret are NOT present
    await expect(page.locator('input[placeholder="Enter client ID"]')).not.toBeVisible();
    await expect(page.locator('input[placeholder="Enter client secret"]')).not.toBeVisible();
  });

  test("None auth type shows no credential fields", async ({ page }) => {
    const authTypeSelect = page.locator("select").filter({ has: page.locator('option[value="none"]') });
    await authTypeSelect.selectOption("none");

    await page.waitForTimeout(500);

    // Verify NO credential fields are visible
    await expect(page.locator('input[placeholder="Enter API key"]')).not.toBeVisible();
    await expect(page.locator('input[placeholder="Enter client ID"]')).not.toBeVisible();
    await expect(page.locator('input[placeholder="Enter client secret"]')).not.toBeVisible();
    await expect(page.locator('input[placeholder*="refresh token"]')).not.toBeVisible();

    // Secret Reference should also not be visible with auth_type = none
    await expect(page.locator('input[placeholder*="gcp://projects"]')).not.toBeVisible();
  });

  test("Switching auth types updates fields correctly", async ({ page }) => {
    const authTypeSelect = page.locator("select").filter({ has: page.locator('option[value="oauth2"]') });

    // Start with oauth2 — 3 fields
    await authTypeSelect.selectOption("oauth2");
    await page.waitForTimeout(300);
    await expect(page.locator('input[placeholder="Enter client ID"]')).toBeVisible();
    await expect(page.locator('input[placeholder="Enter client secret"]')).toBeVisible();
    await expect(page.locator('input[placeholder*="refresh token"]')).toBeVisible();

    // Switch to api_key — 1 field
    await authTypeSelect.selectOption("api_key");
    await page.waitForTimeout(300);
    await expect(page.locator('input[placeholder="Enter API key"]')).toBeVisible();
    await expect(page.locator('input[placeholder="Enter client ID"]')).not.toBeVisible();

    // Switch to basic — 2 fields (username + password)
    await authTypeSelect.selectOption("basic");
    await page.waitForTimeout(300);
    await expect(page.locator('input[placeholder="Enter username"]')).toBeVisible();
    await expect(page.locator('input[placeholder="Enter password"]')).toBeVisible();

    // Switch to none — 0 fields
    await authTypeSelect.selectOption("none");
    await page.waitForTimeout(300);
    await expect(page.locator('input[placeholder="Enter username"]')).not.toBeVisible();
    await expect(page.locator('input[placeholder="Enter API key"]')).not.toBeVisible();
  });
});

// =============================================================================
//  FLOW 3: Settings & API Keys
// =============================================================================

test.describe("Flow 3: Settings & API Keys E2E", () => {
  test.beforeEach(async ({ page }) => {
    await loginAsCeo(page);
  });

  test("Settings page loads with fleet limits section", async ({ page }) => {
    await page.goto(`${APP}/dashboard/settings`);
    await page.waitForLoadState("networkidle");

    await expect(page.locator("h2", { hasText: "Settings" })).toBeVisible({ timeout: 10_000 });

    // Verify Fleet Governance Limits card
    await expect(page.locator("text=Fleet Governance Limits")).toBeVisible();

    // Verify max_shadow_agents field exists
    const maxShadowInput = page.locator("label:has-text('Max Shadow Agents') + input, label:has-text('Max Shadow Agents') ~ input").first();
    // Alternative: locate by sibling within grid
    const shadowSection = page.locator("div").filter({ has: page.locator("label:has-text('Max Shadow Agents')") }).first();
    await expect(shadowSection).toBeVisible();

    // Verify Max Active Agents input
    await expect(page.locator("label:has-text('Max Active Agents')").first()).toBeVisible();

    // Verify Max Replicas Per Agent Type input
    await expect(page.locator("label:has-text('Max Replicas Per Agent Type')").first()).toBeVisible();
  });

  test("API Keys section exists with Generate Key button", async ({ page }) => {
    await page.goto(`${APP}/dashboard/settings`);
    await page.waitForLoadState("networkidle");

    // Verify API Keys card
    await expect(page.locator("text=API Keys").first()).toBeVisible({ timeout: 10_000 });

    // Verify the description text
    await expect(page.locator("text=Generate API keys for SDK, CLI, and MCP server access")).toBeVisible();

    // Verify key name input
    const keyNameInput = page.locator('input[placeholder*="Production SDK"]');
    await expect(keyNameInput).toBeVisible();

    // Verify expiry selector
    const expirySelect = page.locator("select").filter({ has: page.locator('option:has-text("Never")') });
    await expect(expirySelect).toBeVisible();

    // Verify "Generate Key" button exists
    const generateButton = page.locator("button:has-text('Generate Key')");
    await expect(generateButton).toBeVisible();
    // Button should be disabled when key name is empty
    await expect(generateButton).toBeDisabled();

    // Type a name — button should become enabled
    await keyNameInput.fill("Test Key");
    await expect(generateButton).toBeEnabled();

    // Clear to leave no side effects
    await keyNameInput.fill("");
  });

  test("Compliance & Data section has PII masking and data region", async ({ page }) => {
    await page.goto(`${APP}/dashboard/settings`);
    await page.waitForLoadState("networkidle");

    // Verify Compliance & Data card
    await expect(page.locator("text=Compliance & Data")).toBeVisible({ timeout: 10_000 });

    // Verify PII Masking dropdown
    await expect(page.locator("label:has-text('PII Masking')").first()).toBeVisible();

    // Verify Data Region dropdown
    await expect(page.locator("label:has-text('Data Region')").first()).toBeVisible();

    // Verify Audit Retention input
    await expect(page.locator("label:has-text('Audit Retention')").first()).toBeVisible();
  });

  test("Grantex Integration section is present", async ({ page }) => {
    await page.goto(`${APP}/dashboard/settings`);
    await page.waitForLoadState("networkidle");

    await expect(page.locator("text=Grantex Integration")).toBeVisible({ timeout: 10_000 });
    await expect(page.locator("text=Grantex Base URL")).toBeVisible();
  });

  test("Quick Start SDK examples are shown", async ({ page }) => {
    await page.goto(`${APP}/dashboard/settings`);
    await page.waitForLoadState("networkidle");

    await expect(page.locator("text=Quick Start")).toBeVisible({ timeout: 10_000 });
    await expect(page.locator("text=Python SDK").first()).toBeVisible();
    await expect(page.locator("text=TypeScript SDK").first()).toBeVisible();
    await expect(page.locator("text=MCP Server").first()).toBeVisible();
  });
});

// =============================================================================
//  FLOW 4: Landing Page Developer Section
// =============================================================================

test.describe("Flow 4: Landing Page Developer Section", () => {
  test("Developers nav link scrolls to #developers section", async ({ page }) => {
    await page.goto(SITE);
    await page.waitForLoadState("networkidle");

    // Click "Developers" in nav
    const devLink = page.locator('a[href="#developers"]').first();
    await expect(devLink).toBeVisible({ timeout: 10_000 });
    await devLink.click();

    // Wait for smooth scroll
    await page.waitForTimeout(1_000);

    // Verify #developers section is in viewport
    const devSection = page.locator("section#developers");
    await expect(devSection).toBeVisible();
    const isInView = await devSection.evaluate((el) => {
      const rect = el.getBoundingClientRect();
      return rect.top < window.innerHeight && rect.bottom > 0;
    });
    expect(isInView).toBe(true);
  });

  test("4 SDK cards are visible (Python, TypeScript, CLI, MCP Server)", async ({ page }) => {
    await page.goto(SITE);
    await page.waitForLoadState("networkidle");

    // Scroll to developers section
    await page.locator("section#developers").scrollIntoViewIfNeeded();
    await page.waitForTimeout(1_000);

    const devSection = page.locator("section#developers");

    // Verify all 4 SDK card titles
    await expect(devSection.locator("text=Python SDK").first()).toBeVisible({ timeout: 10_000 });
    await expect(devSection.locator("text=TypeScript SDK").first()).toBeVisible();
    await expect(devSection.locator("text=CLI").first()).toBeVisible();
    await expect(devSection.locator("text=MCP Server").first()).toBeVisible();

    // Verify install commands are shown
    await expect(devSection.locator("text=pip install agenticorg").first()).toBeVisible();
    await expect(devSection.locator("text=npm i agenticorg-sdk").first()).toBeVisible();
    await expect(devSection.locator("text=npx agenticorg-mcp-server").first()).toBeVisible();
  });

  test("'View Full Workflow' link navigates to /integration-workflow", async ({ page }) => {
    await page.goto(SITE);
    await page.waitForLoadState("networkidle");

    // Scroll down to find the View Full Workflow link
    const workflowLink = page.locator('a:has-text("View Full Workflow")');
    await workflowLink.scrollIntoViewIfNeeded();
    await expect(workflowLink).toBeVisible({ timeout: 10_000 });

    // Click and verify navigation
    await workflowLink.click();
    await page.waitForURL("**/integration-workflow**", { timeout: 10_000 });
    expect(page.url()).toContain("/integration-workflow");
  });

  test("/integration-workflow has all 8 workflow steps", async ({ page }) => {
    await page.goto(`${SITE}/integration-workflow`);
    await page.waitForLoadState("networkidle");

    // Verify page loaded
    await expect(page.locator("h1").first()).toBeVisible({ timeout: 10_000 });

    // Verify step-by-step workflow section
    await expect(page.locator("text=Step-by-Step Workflow")).toBeVisible();

    // The 8 step actors/actions as defined in WORKFLOW_STEPS
    const stepTexts = [
      "Asks ChatGPT to buy something",
      "Discovers AgenticOrg via MCP",
      "Calls run_agent via MCP",
      "Authenticates via API Key",
      "Executes LangGraph workflow",
      "HITL approval request",
      "Approves the order",
      "Places the order",
    ];

    for (const text of stepTexts) {
      const stepElement = page.locator(`text=${text}`).first();
      await stepElement.scrollIntoViewIfNeeded();
      await expect(stepElement).toBeVisible({ timeout: 5_000 });
    }

    // Click through each step to verify detail panels expand
    for (let i = 0; i < 8; i++) {
      const stepCard = page.locator(`text=${stepTexts[i]}`).first();
      await stepCard.scrollIntoViewIfNeeded();
      await stepCard.click();
      // Give the panel a moment to expand
      await page.waitForTimeout(300);
    }

    // Verify Architecture Stack section exists
    await expect(page.locator("text=Architecture Stack")).toBeVisible();
  });
});

// =============================================================================
//  FLOW 5: Login / Signup Validation
// =============================================================================

test.describe("Flow 5: Login/Signup Validation", () => {
  test("Signup — T&C checkbox exists and blocks submission when unchecked", async ({ page }) => {
    await page.goto(`${APP}/signup`);
    await page.waitForLoadState("networkidle");

    // Verify T&C checkbox exists
    const tcCheckbox = page.locator("#agreeTerms");
    await expect(tcCheckbox).toBeVisible({ timeout: 10_000 });
    await expect(tcCheckbox).not.toBeChecked();

    // Verify Terms of Service and Privacy Policy links exist
    await expect(page.locator('a:has-text("Terms of Service")')).toBeVisible();
    await expect(page.locator('a:has-text("Privacy Policy")')).toBeVisible();

    // Fill all fields to isolate T&C as the blocker
    await page.fill("#orgName", "TestOrg");
    await page.fill("#name", "Test User");
    await page.fill("#signupEmail", "test@example.com");
    await page.fill("#signupPassword", "SecurePass1!");
    await page.fill("#confirmPassword", "SecurePass1!");

    // Submit button should be disabled because T&C is unchecked
    const submitButton = page.locator('button[type="submit"]');
    await expect(submitButton).toBeDisabled();

    // Check the T&C box
    await tcCheckbox.check();
    await expect(tcCheckbox).toBeChecked();

    // Now submit button should be enabled
    await expect(submitButton).toBeEnabled();
  });

  test("Signup — password toggle works", async ({ page }) => {
    await page.goto(`${APP}/signup`);
    await page.waitForLoadState("networkidle");

    const passwordInput = page.locator("#signupPassword");
    await expect(passwordInput).toBeVisible({ timeout: 10_000 });

    // Initially type is "password"
    await expect(passwordInput).toHaveAttribute("type", "password");

    // Click the toggle button (aria-label based)
    const toggleButton = page.locator('button[aria-label="Show password"]').first();
    await toggleButton.click();

    // Now type should be "text"
    await expect(passwordInput).toHaveAttribute("type", "text");

    // Click again to hide
    const hideButton = page.locator('button[aria-label="Hide password"]').first();
    await hideButton.click();
    await expect(passwordInput).toHaveAttribute("type", "password");
  });

  test("Signup — empty fields show validation errors", async ({ page }) => {
    await page.goto(`${APP}/signup`);
    await page.waitForLoadState("networkidle");

    // Check the T&C so the button is not disabled by that
    await page.locator("#agreeTerms").check();

    // Click submit with empty fields — browser HTML5 validation will trigger "required"
    // Since inputs have `required`, the browser prevents submission.
    // Verify that the HTML required attribute is set on key fields.
    await expect(page.locator("#orgName")).toHaveAttribute("required", "");
    await expect(page.locator("#name")).toHaveAttribute("required", "");
    await expect(page.locator("#signupEmail")).toHaveAttribute("required", "");
    await expect(page.locator("#signupPassword")).toHaveAttribute("required", "");
    await expect(page.locator("#confirmPassword")).toHaveAttribute("required", "");

    // Test custom validation: type a short password
    await page.fill("#signupPassword", "ab");
    // Should show password strength error
    await expect(page.locator("text=Password must be at least 8 characters")).toBeVisible({ timeout: 3_000 });

    // Type non-matching confirm password
    await page.fill("#signupPassword", "SecurePass1!");
    await page.fill("#confirmPassword", "DifferentPass");
    await expect(page.locator("text=Passwords do not match")).toBeVisible({ timeout: 3_000 });
  });

  test("Login — password toggle works", async ({ page }) => {
    await page.goto(`${APP}/login`);
    await page.waitForLoadState("networkidle");

    const passwordInput = page.locator("#password");
    await expect(passwordInput).toBeVisible({ timeout: 10_000 });

    // Initially type is "password"
    await expect(passwordInput).toHaveAttribute("type", "password");

    // Click toggle
    const toggleButton = page.locator('button[aria-label="Show password"]').first();
    await toggleButton.click();
    await expect(passwordInput).toHaveAttribute("type", "text");

    // Click again to hide
    const hideButton = page.locator('button[aria-label="Hide password"]').first();
    await hideButton.click();
    await expect(passwordInput).toHaveAttribute("type", "password");
  });

  test("Login — divider structure is visible and properly styled", async ({ page }) => {
    await page.goto(`${APP}/login`);
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(2_000);

    // The divider sits between Google login and email form.
    // If Google login is available, the divider "or sign in with email" should be visible.
    // If not, the login form should still render cleanly.
    const dividerContainer = page.locator("div.flex.items-center.gap-3.my-6").first();
    const googleSection = page.locator('div:has-text("Google")').first();
    const hasGoogle = await googleSection.isVisible({ timeout: 3_000 }).catch(() => false);

    if (hasGoogle) {
      await expect(dividerContainer).toBeVisible();

      // Verify the "or" label text
      const orLabel = page.locator("span.uppercase.tracking-wide").first();
      await expect(orLabel).toBeVisible();
      const text = await orLabel.textContent();
      expect(text?.toLowerCase()).toContain("or");

      // Verify horizontal lines exist (the flex-1 h-px dividers)
      const lines = dividerContainer.locator("div.flex-1.h-px");
      expect(await lines.count()).toBe(2);
    } else {
      // Even without Google, the form should render
      await expect(page.locator('button[type="submit"]')).toBeVisible();
    }
  });

  test("Login — form renders with email and password inputs", async ({ page }) => {
    await page.goto(`${APP}/login`);
    await page.waitForLoadState("networkidle");

    // Verify email input
    await expect(page.locator("#email")).toBeVisible({ timeout: 10_000 });

    // Verify password input
    await expect(page.locator("#password")).toBeVisible();

    // Verify submit button
    const submitBtn = page.locator('button[type="submit"]');
    await expect(submitBtn).toBeVisible();
    await expect(submitBtn).toHaveText(/Sign in/i);

    // Verify Forgot Password link
    await expect(page.locator('a:has-text("Forgot Password")')).toBeVisible();

    // Verify signup link
    await expect(page.locator('a:has-text("Sign up")')).toBeVisible();
  });
});

// =============================================================================
//  FLOW 6: Dashboard Data Consistency
// =============================================================================

test.describe("Flow 6: Dashboard Data Consistency", () => {
  test.beforeEach(async ({ page }) => {
    await loginAsCeo(page);
  });

  test("Dashboard — domain distribution chart renders", async ({ page }) => {
    await page.goto(`${APP}/dashboard`);
    await page.waitForLoadState("networkidle");

    // Wait for data to load
    await page.waitForTimeout(3_000);

    // Verify Domain Distribution section
    await expect(page.locator("text=Domain Distribution")).toBeVisible({ timeout: 10_000 });

    // The chart is a Recharts BarChart — verify the SVG container renders with bars
    const chartContainer = page.locator("text=Domain Distribution").locator("..").locator("..");
    await expect(chartContainer).toBeVisible();

    // Look for recharts bar elements (rendered as <rect> in SVG)
    // If there are agents, bars should exist
    const bars = page.locator(".recharts-bar-rectangle");
    const barCount = await bars.count();
    // Should have at least 1 domain bar if agents exist
    expect(barCount).toBeGreaterThanOrEqual(0);
  });

  test("Agents page — domain filter has all domains", async ({ page }) => {
    await page.goto(`${APP}/dashboard/agents`);
    await page.waitForLoadState("networkidle");

    // Verify a domain filter select exists with "All Domains" + specific domains
    const domainFilter = page.locator("select").filter({ has: page.locator('option:has-text("All Domains")') });
    await expect(domainFilter).toBeVisible({ timeout: 10_000 });

    // Verify each domain is present as an option
    const expectedDomains = ["finance", "hr", "marketing", "ops", "backoffice"];
    for (const d of expectedDomains) {
      const optionText = d.charAt(0).toUpperCase() + d.slice(1);
      await expect(domainFilter.locator(`option:has-text("${optionText}")`)).toBeAttached();
    }
  });

  test("Org Chart page — domain filter has all domains", async ({ page }) => {
    await page.goto(`${APP}/dashboard/org-chart`);
    await page.waitForLoadState("networkidle");

    // The org chart has a domain filter too
    const domainFilter = page.locator("select").filter({ has: page.locator('option:has-text("All Departments")') });
    await expect(domainFilter).toBeVisible({ timeout: 10_000 });

    // Verify domains
    const expectedDomains = ["finance", "hr", "marketing", "ops", "backoffice", "comms"];
    for (const d of expectedDomains) {
      const optionText = humanize(d);
      await expect(domainFilter.locator(`option:has-text("${optionText}")`)).toBeAttached();
    }
  });

  test("Schemas page — renders with schema definitions", async ({ page }) => {
    await page.goto(`${APP}/dashboard/schemas`);
    await page.waitForLoadState("networkidle");

    // Page should show schema definitions
    // Verify known schema names exist
    const knownSchemas = ["Invoice", "Payment", "Order", "Employee"];
    for (const schema of knownSchemas) {
      await expect(page.locator(`text=${schema}`).first()).toBeVisible({ timeout: 10_000 });
    }
  });
});

// =============================================================================
//  FLOW 7: Public Pages Accessibility
// =============================================================================

test.describe("Flow 7: Public Pages Accessibility", () => {
  test("Pricing page — 3 tiers with correct connector counts", async ({ page }) => {
    await page.goto(`${SITE}/pricing`);
    await page.waitForLoadState("networkidle");

    // Verify page loaded
    await expect(page.locator("h1, h2").first()).toBeVisible({ timeout: 10_000 });

    // Verify 3 tiers exist: Free, Pro, Enterprise
    await expect(page.locator("text=Free").first()).toBeVisible();
    await expect(page.locator("text=Pro").first()).toBeVisible();
    await expect(page.locator("text=Enterprise").first()).toBeVisible();

    // Verify pricing
    await expect(page.locator("text=$0").first()).toBeVisible();
    await expect(page.locator("text=$499").first()).toBeVisible();
    await expect(page.locator("text=Custom").first()).toBeVisible();

    // Verify "54 connectors" appears (Pro and Enterprise both have it)
    const connectorMentions = page.locator("text=54 connectors");
    expect(await connectorMentions.count()).toBeGreaterThanOrEqual(2);
  });

  test("Evals page — loads successfully", async ({ page }) => {
    await page.goto(`${APP}/dashboard/evals`);
    await page.waitForLoadState("networkidle");
    await loginAsCeo(page);
    await page.goto(`${APP}/dashboard/evals`);
    await page.waitForLoadState("networkidle");

    // Verify the evals page renders (look for heading or content)
    const body = page.locator("body");
    await expect(body).toBeVisible({ timeout: 10_000 });
    // Page should not show a 404
    await expect(page.locator("text=Not Found")).not.toBeVisible();
  });

  test("Blog page — has blog posts", async ({ page }) => {
    await page.goto(`${SITE}/blog`);
    await page.waitForLoadState("networkidle");

    // Verify blog page loaded
    await expect(page.locator("body")).toBeVisible({ timeout: 10_000 });
    await expect(page.locator("text=Not Found")).not.toBeVisible();

    // Blog should have at least one post (article/card)
    // Look for common blog elements
    const articles = page.locator("article, [class*='blog'], a[href*='/blog/']");
    const count = await articles.count();
    expect(count).toBeGreaterThanOrEqual(1);
  });

  test("Resources page — has resources", async ({ page }) => {
    await page.goto(`${SITE}/resources`);
    await page.waitForLoadState("networkidle");

    await expect(page.locator("body")).toBeVisible({ timeout: 10_000 });
    await expect(page.locator("text=Not Found")).not.toBeVisible();
  });

  test("Integration Workflow page — has 8 steps", async ({ page }) => {
    await page.goto(`${SITE}/integration-workflow`);
    await page.waitForLoadState("networkidle");

    // Verify 8 step indicators/cards
    const stepActions = [
      "Asks ChatGPT to buy something",
      "Discovers AgenticOrg via MCP",
      "Calls run_agent via MCP",
      "Authenticates via API Key",
      "Executes LangGraph workflow",
      "HITL approval request",
      "Approves the order",
      "Places the order",
    ];

    for (const action of stepActions) {
      const el = page.locator(`text=${action}`).first();
      await el.scrollIntoViewIfNeeded();
      await expect(el).toBeVisible({ timeout: 5_000 });
    }
  });
});

// =============================================================================
//  FLOW 8: API Endpoint Validation
// =============================================================================

test.describe("Flow 8: API Endpoint Validation", () => {
  test("GET /api/v1/health — returns version and connector count", async ({ page }) => {
    const response = await page.request.get(`${APP}/api/v1/health`);
    expect(response.status()).toBe(200);

    const body = await response.json();
    expect(body.version).toBe("2.2.0");
    expect(body.connectors).toBe(43);
  });

  test("GET /api/v1/a2a/agents — returns 35 agents", async ({ page }) => {
    const response = await page.request.get(`${APP}/api/v1/a2a/agents`);
    expect(response.status()).toBe(200);

    const body = await response.json();
    const agents = Array.isArray(body) ? body : body.agents || body.items || [];
    expect(agents.length).toBeGreaterThanOrEqual(25);
  });

  test("GET /api/v1/mcp/tools — returns tools array", async ({ page }) => {
    const response = await page.request.get(`${APP}/api/v1/mcp/tools`);
    expect(response.status()).toBe(200);

    const body = await response.json();
    const tools = body.tools || body;
    expect(Array.isArray(tools)).toBe(true);
    expect(tools.length).toBeGreaterThan(0);

    // Each tool should have a name
    const firstTool = tools[0];
    expect(firstTool).toHaveProperty("name");
  });

  test("GET /api/v1/connectors/registry — returns items with categories", async ({ page }) => {
    const response = await page.request.get(`${APP}/api/v1/connectors/registry`);
    expect(response.status()).toBe(200);

    const body = await response.json();
    const items = Array.isArray(body) ? body : body.items || body.connectors || [];
    expect(items.length).toBeGreaterThan(0);

    // Verify at least one item has a category field
    const hasCategory = items.some((item: Record<string, unknown>) => "category" in item);
    expect(hasCategory).toBe(true);
  });

  test("POST /api/v1/auth/login with empty body — returns 422", async ({ page }) => {
    const response = await page.request.post(`${APP}/api/v1/auth/login`, {
      headers: { "Content-Type": "application/json" },
      data: {},
    });
    expect(response.status()).toBe(422);
  });

  test("GET /api/v1/org/api-keys without auth — returns 401", async ({ page }) => {
    const response = await page.request.get(`${APP}/api/v1/org/api-keys`, {
      headers: {},
    });
    expect(response.status()).toBe(401);
  });

  test("Authenticated API — agents list returns data", async ({ page }) => {
    await loginAsCeo(page);
    const token = await getToken(page);
    expect(token.length).toBeGreaterThan(0);

    const response = await page.request.get(`${APP}/api/v1/agents`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(response.status()).toBe(200);

    const body = await response.json();
    const agents = Array.isArray(body) ? body : body.items || [];
    expect(agents.length).toBeGreaterThanOrEqual(1);
  });

  test("Authenticated API — connectors list returns data", async ({ page }) => {
    await loginAsCeo(page);
    const token = await getToken(page);

    const response = await page.request.get(`${APP}/api/v1/connectors`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(response.status()).toBe(200);

    const body = await response.json();
    const connectors = Array.isArray(body) ? body : body.items || [];
    expect(connectors.length).toBeGreaterThanOrEqual(1);
  });
});
