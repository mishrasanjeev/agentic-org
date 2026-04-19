/**
 * In-product SDK snippet drift guard — Phase 2 SDK Contract.
 *
 * The Integrations page shows the canonical way to call the Python SDK.
 * Those lines must match the real SDK surface (see
 * docs/api/agent-run-contract.md). Whenever someone renames the
 * `AgentRunResult` type, changes the `.status` / `.confidence` / `.output`
 * property names, or reintroduces a raw-dict example, this spec fails.
 */
import { expect, test } from "@playwright/test";

const APP = process.env.BASE_URL || "https://app.agenticorg.ai";
const E2E_TOKEN = process.env.E2E_TOKEN || "";
const canAuth = !!E2E_TOKEN;

function requireAuth(): void {
  if (!canAuth) {
    throw new Error(
      "E2E_TOKEN is required for this spec. Set the E2E_TOKEN env var — the suite runs against production and must have credentials.",
    );
  }
}

test.describe("Integrations page SDK snippet @sdk @mcp", () => {
  test("Python snippet references AgentRunResult and canonical fields", async ({ page }) => {
    requireAuth();

    await page.goto(`${APP}/login`, { waitUntil: "domcontentloaded" });
    await page.evaluate((t) => {
      localStorage.setItem("token", t);
      localStorage.setItem(
        "user",
        JSON.stringify({
          email: "demo@cafirm.agenticorg.ai",
          name: "Demo Partner",
          role: "admin",
          domain: "all",
          tenant_id: "58483c90-494b-445d-85c6-245a727fe372",
          onboardingComplete: true,
        }),
      );
    }, E2E_TOKEN);

    await page.goto(`${APP}/dashboard/integrations`, { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle").catch(() => {});

    const snippet = page.getByTestId("sdk-snippet-python");
    await expect(snippet).toBeVisible({ timeout: 15000 });
    const text = (await snippet.textContent()) || "";

    // Canonical type + factory import.
    expect(text, "snippet imports canonical AgentRunResult").toContain("AgentRunResult");
    // Canonical field access, not dict subscripts.
    expect(text, "snippet uses .status attribute").toContain(".status");
    expect(text, "snippet uses .confidence attribute").toContain(".confidence");
    expect(text, "snippet uses .output attribute").toContain(".output");

    // Drift guards — old raw-dict examples must not come back.
    expect(text, "no result[\"output\"] dict subscript").not.toContain('result["output"]');
    expect(text, "no untyped `result =` without AgentRunResult annotation").toMatch(
      /result:\s*AgentRunResult/,
    );
  });
});
