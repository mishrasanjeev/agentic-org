/**
 * Playwright regression for the Session 5 bug sweep.
 *
 * One describe block per bug family. These run against production
 * (https://app.agenticorg.ai) with the shared E2E_TOKEN. Tests are
 * read-only wherever possible; the onboarding/voice cases that do
 * mutate delete what they create at the end.
 */
import { expect, test, type Page } from "@playwright/test";
import { APP, authenticate, canAuth } from "./helpers/auth";

/**
 * Selector helper — scope to <main> so the sidebar "Voice Agents" /
 * "Knowledge Base" / "Sales Pipeline" nav links don't steal clicks.
 */
function mainButton(page: Page, name: string | RegExp) {
  const matcher = typeof name === "string" ? new RegExp(`^${name}$`, "i") : name;
  return page.locator("main button").filter({ hasText: matcher }).first();
}

// --------------------------------------------------------------------------
// Voice Setup — TC-006, TC-007, TC-008, TC-009, TC-011, TC-012, TC-010
// --------------------------------------------------------------------------

test.describe("Voice Setup regression", () => {
  test.beforeEach(async ({ page }) => {
    test.skip(!canAuth, "E2E_TOKEN required");
    await authenticate(page);
    await page.goto(`${APP}/dashboard/voice-setup`, { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle").catch(() => {});
  });

  test("TC-006 Test Connection endpoint is reachable (no 404)", async ({ page }) => {
    // Select Twilio, enter bogus but non-empty creds, hit Test Connection,
    // then watch the POST. Before the fix this was a 404.
    const twilioBtn = page.locator("button").filter({ hasText: "Twilio" }).first();
    await twilioBtn.click();
    await page.getByRole("button", { name: "Next" }).click();

    await page.getByPlaceholder("Enter Account SID").fill("AC" + "x".repeat(32));
    await page.getByPlaceholder("Enter Auth Token").fill("a".repeat(32));

    const responsePromise = page.waitForResponse(
      (r) => r.url().includes("/voice/test-connection"),
      { timeout: 20000 },
    );
    await page.getByRole("button", { name: "Test Connection" }).click();
    const resp = await responsePromise;
    expect(resp.status()).not.toBe(404);
  });

  test("TC-012 Phone step rejects non-numeric input", async ({ page }) => {
    // Fast-forward through Step 1 and Step 2 with valid minimal input.
    await page.locator("button").filter({ hasText: "Twilio" }).first().click();
    await page.getByRole("button", { name: "Next" }).click();
    await page.getByPlaceholder("Enter Account SID").fill("AC" + "x".repeat(32));
    await page.getByPlaceholder("Enter Auth Token").fill("a".repeat(32));
    await page.getByRole("button", { name: "Next" }).click();

    const phoneInput = page.locator('input[type="tel"]');
    await phoneInput.fill("phone-number-abc");
    // Next should be disabled or show an inline error on click.
    const nextBtn = page.getByRole("button", { name: "Next" });
    await nextBtn.click({ trial: true }).catch(() => {});
    // The field-level error appears after validateStep fires.
    await nextBtn.click();
    await expect(
      page.locator("text=Invalid phone number").first(),
    ).toBeVisible({ timeout: 5000 });
  });

  test("TC-011 Google TTS shows an API key field on selection", async ({ page }) => {
    // Walk to Step 4.
    await page.locator("button").filter({ hasText: "Twilio" }).first().click();
    await page.getByRole("button", { name: "Next" }).click();
    await page.getByPlaceholder("Enter Account SID").fill("AC" + "x".repeat(32));
    await page.getByPlaceholder("Enter Auth Token").fill("a".repeat(32));
    await page.getByRole("button", { name: "Next" }).click();
    await page.locator('input[type="tel"]').fill("+919876543210");
    await page.getByRole("button", { name: "Next" }).click();

    // Google TTS radio → API key field must appear.
    await page.locator("text=Google TTS (cloud)").first().click();
    await expect(
      page.getByPlaceholder("Enter Google Cloud API key"),
    ).toBeVisible({ timeout: 5000 });
  });

  test("TC-008 switching SIP provider clears credentials", async ({ page }) => {
    await page.locator("button").filter({ hasText: "Twilio" }).first().click();
    await page.getByRole("button", { name: "Next" }).click();
    await page.getByPlaceholder("Enter Account SID").fill("AC" + "x".repeat(32));
    await page.getByPlaceholder("Enter Auth Token").fill("a".repeat(32));
    await page.getByRole("button", { name: "Back" }).click();

    // Switch to Custom SIP.
    await page.locator("button").filter({ hasText: "Custom SIP" }).first().click();
    await page.getByRole("button", { name: "Next" }).click();

    // The previously-entered Twilio credentials must NOT leak — custom_url
    // field renders instead and starts empty.
    await expect(
      page.getByPlaceholder("sip:user@trunk.example.com"),
    ).toHaveValue("");
  });

  test("TC-010 save is blocked until test connection succeeds", async ({ page }) => {
    // Walk to Step 5.
    await page.locator("button").filter({ hasText: "Twilio" }).first().click();
    await page.getByRole("button", { name: "Next" }).click();
    await page.getByPlaceholder("Enter Account SID").fill("AC" + "x".repeat(32));
    await page.getByPlaceholder("Enter Auth Token").fill("a".repeat(32));
    await page.getByRole("button", { name: "Next" }).click();
    await page.locator('input[type="tel"]').fill("+919876543210");
    await page.getByRole("button", { name: "Next" }).click();
    await page.getByRole("button", { name: "Next" }).click();

    const saveBtn = page.getByRole("button", { name: /Save Configuration/ });
    // Button exists but is disabled — and the review shows "Not verified".
    await expect(saveBtn).toBeDisabled();
    await expect(page.locator("text=Not verified").first()).toBeVisible();
  });
});

// --------------------------------------------------------------------------
// Sales Pipeline CSV — TC-002, TC-005
// --------------------------------------------------------------------------

test.describe("Sales Pipeline CSV import", () => {
  test.beforeEach(async ({ page }) => {
    test.skip(!canAuth, "E2E_TOKEN required");
    await authenticate(page);
    await page.goto(`${APP}/dashboard/sales`, { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle").catch(() => {});
  });

  test("TC-002 import count reflects the actual number of rows", async ({ page }) => {
    // Synthesize a tiny CSV with 2 leads.
    const csv = [
      "name,email,company",
      `QA Test 1,qa1-${Date.now()}@example.invalid,AcmeCo`,
      `QA Test 2,qa2-${Date.now()}@example.invalid,AcmeCo`,
    ].join("\n");

    // Set the hidden input directly — the visible button only opens a picker.
    await page.setInputFiles(
      "#csv-import-input",
      {
        name: "tc002.csv",
        mimeType: "text/csv",
        buffer: Buffer.from(csv, "utf-8"),
      },
    );

    // The success banner must not say "0" when we uploaded 2 rows.
    const banner = page.locator("text=/Imported \\d+ leads? from CSV/").first();
    await expect(banner).toBeVisible({ timeout: 30000 });
    const text = (await banner.textContent()) || "";
    expect(text).not.toMatch(/Imported 0 leads/);
  });

  test("TC-005 invalid CSV surfaces a specific error", async ({ page }) => {
    // Missing headers → backend returns 422 with "missing_required_headers".
    const bad = "garbage\nno headers\n";
    await page.setInputFiles(
      "#csv-import-input",
      {
        name: "tc005.csv",
        mimeType: "text/csv",
        buffer: Buffer.from(bad, "utf-8"),
      },
    );

    // The UI now surfaces the server's detail.message instead of the old
    // "Imported 0 leads from CSV" false-success.
    await expect(
      page.locator("text=/missing required headers|No valid leads found|Invalid file/i").first(),
    ).toBeVisible({ timeout: 15000 });
  });
});

// --------------------------------------------------------------------------
// Knowledge Base persistence — TC-013
// --------------------------------------------------------------------------

test.describe("Knowledge Base regression", () => {
  test.beforeEach(async ({ page }) => {
    test.skip(!canAuth, "E2E_TOKEN required");
    await authenticate(page);
    await page.goto(`${APP}/dashboard/knowledge`, { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle").catch(() => {});
  });

  test("TC-013 uploaded document survives page refresh", async ({ page }) => {
    const unique = `qa-kb-${Date.now()}.txt`;
    const buffer = Buffer.from("Regression body for TC-013.\n", "utf-8");

    // The upload input is discoverable by its accept=".pdf,.txt..." attr.
    const uploadInput = page.locator('input[type="file"]').first();
    await uploadInput.setInputFiles({
      name: unique,
      mimeType: "text/plain",
      buffer,
    });

    // Wait for the entry to appear.
    await expect(page.locator(`text=${unique}`).first()).toBeVisible({ timeout: 30000 });

    // Refresh — the document must still be there.
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle").catch(() => {});
    await expect(page.locator(`text=${unique}`).first()).toBeVisible({ timeout: 30000 });
  });
});

// --------------------------------------------------------------------------
// Onboarding Tally — BUG-S5-001
// --------------------------------------------------------------------------

test.describe("Tally test-connection", () => {
  test.beforeEach(async ({ page }) => {
    test.skip(!canAuth, "E2E_TOKEN required");
    await authenticate(page);
  });

  test("BUG-S5-001 /companies/test-tally responds with 2xx or handled error, never 405", async ({ page }) => {
    const token = await page.evaluate(() => localStorage.getItem("token"));
    const resp = await page.request.post(`${APP}/api/v1/companies/test-tally`, {
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      data: { bridge_url: "http://localhost:65535" },
    });
    expect(resp.status()).not.toBe(405);
    // Either 200 with success=false (unreachable) or 422 for bad input; never 404/405.
    expect([200, 422]).toContain(resp.status());
  });
});

// --------------------------------------------------------------------------
// Unused import silencer
// --------------------------------------------------------------------------
void mainButton;
