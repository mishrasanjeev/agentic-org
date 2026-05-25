import { expect, test } from "@playwright/test";

const user = {
  email: "qa-uday-25may2026@example.test",
  name: "Uday Chauhan",
  role: "admin",
  domain: "marketing",
  tenant_id: "11111111-1111-1111-1111-111111111111",
  onboarding_complete: true,
};

test("Uday 2026-05-25: CRM connector detail exposes operation tools", async ({
  page,
}) => {
  const tools = [
    "search_companies",
    "get_company",
    "update_company",
    "delete_company",
    "search_deals",
    "delete_deal",
    "delete_contact",
    "create_task",
    "create_note",
    "list_associations",
    "create_association",
    "validate_crm_access",
  ];

  await page.route("**/api/v1/auth/me", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(user),
    });
  });
  await page.route("**/api/v1/connectors/hubspot-test", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        connector_id: "hubspot-test",
        id: "hubspot-test",
        name: "hubspot",
        category: "marketing",
        description: "HubSpot CRM",
        base_url: "https://api.hubapi.com",
        auth_type: "oauth2",
        tool_functions: tools,
        data_schema_ref: null,
        rate_limit_rpm: 200,
        timeout_ms: 10000,
        status: "active",
        health_check_at: null,
        created_at: "2026-05-25T00:00:00Z",
      }),
    });
  });

  await page.goto("/dashboard/connectors/hubspot-test");

  await expect(page.getByText("Registered Tools (12)")).toBeVisible();
  for (const tool of tools) {
    await expect(page.getByText(tool)).toBeVisible();
  }
});
