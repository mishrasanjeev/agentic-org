/**
 * Uday 2026-05-30 CA/CMO connector regression coverage.
 *
 * These tests are hermetic. API routes are mocked so the UI behavior is
 * verified without touching live Zoho Books or HubSpot accounts.
 */
import { expect, test, type Page, type Route } from "@playwright/test";
import { APP, setSessionToken } from "./helpers/auth";

const AGENT_ID = "e2e-uday30-agent";

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

async function mockApi(page: Page) {
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const method = route.request().method();

    if (path.endsWith("/api/v1/auth/me")) {
      return json(route, {
        email: "qa@agenticorg.ai",
        name: "QA Admin",
        role: "admin",
        domain: "all",
        tenant_id: "58483c90-494b-445d-85c6-245a727fe372",
        onboarding_complete: true,
      });
    }

    if (path.endsWith(`/api/v1/agents/${AGENT_ID}`) && method === "GET") {
      return json(route, {
        id: AGENT_ID,
        name: "CA Connector Agent",
        employee_name: "CA Connector Agent",
        agent_type: "ap_processor",
        domain: "finance",
        status: "shadow",
        version: "1.0.0",
        confidence_floor: 0.88,
        shadow_sample_count: 0,
        shadow_accuracy_current: null,
        created_at: "2026-05-30T00:00:00Z",
        authorized_tools: ["zoho_books:list_vendors", "hubspot:create_contact"],
        connector_ids: ["registry-zoho_books", "registry-hubspot"],
        config: {},
      });
    }

    if (path.endsWith(`/api/v1/agents/${AGENT_ID}/run`) && method === "POST") {
      return json(route, {
        run_id: "run-uday30",
        agent_id: AGENT_ID,
        status: "failed",
        output: {},
        confidence: 0.2,
        reasoning_trace: ["Tool call failed"],
        error: "connector tool failed",
        tool_calls: [
          {
            tool: "zoho_books__list_vendors",
            status: "error",
            result: JSON.stringify({
              error: "invalid_access_token",
              message: "Upstream zoho_books API returned HTTP 401: Invalid access token",
            }),
          },
        ],
      });
    }

    if (path.endsWith("/api/v1/chat/history")) {
      return json(route, { messages: [] });
    }

    if (path.endsWith("/api/v1/chat/query") && method === "POST") {
      return json(
        route,
        {
          detail: {
            error: "invalid_payload",
            message: "HubSpot create_contact requires properties.email or email.",
          },
        },
        400,
      );
    }

    return json(route, { items: [] });
  });

  await setSessionToken(page, "fake.e2e.session");
}

test.describe("Uday 30 May connector fixes", () => {
  test("Run Agent surfaces exact connector failure detail", async ({ page }) => {
    await mockApi(page);
    await page.goto(`${APP}/dashboard/agents/${AGENT_ID}`, {
      waitUntil: "domcontentloaded",
    });

    await page.getByRole("button", { name: "Run Agent" }).click();
    await page.getByPlaceholder("What should the agent do?").fill("Get all vendors from Zoho Books");
    await page.getByRole("button", { name: "Run", exact: true }).click();

    await expect(page.locator('[data-testid="agent-run-result"]')).toContainText(
      "zoho_books__list_vendors",
    );
    await expect(page.locator('[data-testid="agent-run-result"]')).toContainText(
      "Invalid access token",
    );
    await expect(page.locator("main")).toContainText("Agent run failed");
  });

  test("Chat with Agent shows API failure reason instead of generic text", async ({ page }) => {
    await mockApi(page);
    await page.goto(`${APP}/dashboard/agents/${AGENT_ID}`, {
      waitUntil: "domcontentloaded",
    });

    await page.getByRole("button", { name: "Chat with Agent" }).click();
    const agentChat = page.getByRole("dialog", { name: "Chat with CA Connector Agent" });
    await agentChat.getByPlaceholder("Type a message...").fill(
      "Create contact on HubSpot using properties JSON",
    );
    await expect(agentChat.getByRole("button", { name: "Send" })).toBeEnabled();
    await agentChat.getByRole("button", { name: "Send" }).click();

    await expect(agentChat.getByText("HubSpot create_contact requires properties.email or email.")).toBeVisible();
    await expect(page.getByText("Sorry, something went wrong. Please try again.")).toHaveCount(0);
  });
});
