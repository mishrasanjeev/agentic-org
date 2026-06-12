import { expect, test } from "@playwright/test";

const adminUser = {
  email: "uday.chauhan@edumatica.io",
  name: "Uday Chauhan",
  role: "admin",
  domain: "all",
  tenant_id: "11111111-1111-1111-1111-111111111111",
  onboarding_complete: true,
};

const agentDetail = {
  id: "11111111-1111-1111-1111-111111111111",
  name: "CA Assistant",
  employee_name: "CA Assistant",
  agent_type: "tax_compliance",
  domain: "finance",
  status: "shadow",
  version: "1.0.0",
  confidence_floor: 0.88,
  shadow_sample_count: 0,
  shadow_accuracy_current: null,
  created_at: "2026-06-09T00:00:00Z",
  authorized_tools: [],
  connector_ids: [],
  config: {},
  cost_controls: {},
};

test.beforeEach(async ({ page }) => {
  await page.route("**/api/v1/auth/me", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(adminUser),
    });
  });

  await page.route("**/api/v1/product-facts", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ connector_count: 55, agent_count: 26 }),
    });
  });
});

test.describe("Uday CA/Marketing 2026-06-09 regressions", () => {
  test("ConnectorCreate submits Custom auth type with custom JSON config", async ({ page }) => {
    let captured: Record<string, unknown> | null = null;
    await page.route("**/api/v1/connectors", async (route) => {
      captured = JSON.parse(route.request().postData() || "{}");
      await route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify({ id: "connector-1", status: "created" }),
      });
    });

    await page.goto("/dashboard/connectors/new", { waitUntil: "domcontentloaded" });

    const authSelect = page
      .locator("label", { hasText: "Auth Type" })
      .locator("..")
      .locator("select");
    await expect(authSelect).toContainText("Custom");
    await authSelect.selectOption("custom");

    await page
      .getByPlaceholder("e.g. zoho_books, Slack, SAP S/4HANA")
      .fill("custom_marketing_api");
    await page.getByPlaceholder("https://api.example.com").fill("https://api.example.com");
    await page
      .locator("textarea")
      .fill('{"header_name":"X-Api-Key","token_prefix":"Token"}');
    await page.getByRole("button", { name: "Register Connector" }).click();

    await expect.poll(() => captured).not.toBeNull();
    expect(captured?.auth_type).toBe("custom");
    expect(captured?.auth_config).toEqual({
      header_name: "X-Api-Key",
      token_prefix: "Token",
    });
  });

  test("Agent run result extracts readable text from raw response objects", async ({ page }) => {
    await page.route("**/api/v1/agents/11111111-1111-1111-1111-111111111111", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(agentDetail),
      });
    });

    await page.route(
      "**/api/v1/agents/11111111-1111-1111-1111-111111111111/run",
      async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            status: "completed",
            confidence: 0.91,
            output: {
              raw_output: {
                text:
                  "Invoice Details\nInvoice Number: INV-000001\nCustomer Name: Ramesh Chauhan\nAmount: INR 5,000",
                metadata: { trace_id: "trace-1" },
              },
              signature: "sig",
              extras: { debug: true },
            },
          }),
        });
      },
    );

    await page.goto("/dashboard/agents/11111111-1111-1111-1111-111111111111", {
      waitUntil: "domcontentloaded",
    });
    await page.getByRole("button", { name: "Run Agent" }).click();
    await page.getByPlaceholder("What should the agent do?").fill("Show invoice details");
    await page.getByRole("button", { name: /^Run$/ }).click();

    const result = page.getByTestId("agent-run-result");
    await expect(result).toContainText("Invoice Details");
    await expect(result).toContainText("Invoice Number: INV-000001");
    await expect(result).not.toContainText("raw_output");
    await expect(result).not.toContainText("signature");
    await expect(result).not.toContainText("metadata");
    await expect(result).not.toContainText("trace_id");
  });
});
