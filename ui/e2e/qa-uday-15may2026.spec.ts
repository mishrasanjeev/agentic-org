import { expect, test } from "@playwright/test";
import { authenticate } from "./helpers/auth";

test("Uday 2026-05-15: Zoho Books registers through generic form without OAuth redirect", async ({
  page,
}) => {
  let createBody: any = null;
  let oauthInitiateCalled = false;
  let externalZohoOpened = false;

  await authenticate(page);
  await page.route("**/api/v1/connectors/oauth/initiate", async (route) => {
    oauthInitiateCalled = true;
    await route.fulfill({
      status: 500,
      contentType: "application/json",
      body: JSON.stringify({ detail: "oauth/initiate must not be called" }),
    });
  });
  await page.route("**accounts.zoho**", async (route) => {
    externalZohoOpened = true;
    await route.abort();
  });
  await page.route("**/api/v1/connectors", async (route) => {
    if (route.request().method() === "POST") {
      createBody = route.request().postDataJSON();
      await route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify({
          id: "connector-zoho",
          connector_id: "connector-zoho",
          name: "zoho_books",
          category: "finance",
          auth_type: "oauth2",
          status: "active",
          has_credentials: true,
        }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ items: [], total: 0, page: 1, per_page: 20 }),
    });
  });

  await page.goto("/dashboard/connectors/new");

  const providerSelect = page.getByTestId("provider-select");
  await expect(providerSelect).toBeVisible();
  await expect(providerSelect.locator("option")).toHaveCount(1);
  await expect(providerSelect.locator("option")).toHaveText(
    "Custom / Generic Connector",
  );

  await page.getByPlaceholder(/zoho_books/).fill("zoho_books");
  await page.getByPlaceholder("https://api.example.com").fill(
    "https://www.zohoapis.in/books/v3",
  );
  await page
    .locator("select")
    .filter({ has: page.locator('option[value="oauth2"]') })
    .selectOption("oauth2");
  await page.getByPlaceholder("Enter client ID").fill("client-id");
  await page.getByPlaceholder("Enter client secret").fill("client-secret");
  await page.locator("textarea").fill(
    JSON.stringify(
      {
        organization_id: "60069102279",
        refresh_token: "refresh-token",
      },
      null,
      2,
    ),
  );

  await expect(
    page.getByRole("button", { name: "Authorize Connector" }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "Register Connector" }).click();

  await expect(page).toHaveURL(/\/dashboard\/connectors$/);
  expect(oauthInitiateCalled).toBe(false);
  expect(externalZohoOpened).toBe(false);
  expect(createBody).toMatchObject({
    name: "zoho_books",
    category: "finance",
    base_url: "https://www.zohoapis.in/books/v3",
    auth_type: "oauth2",
    auth_config: {
      client_id: "client-id",
      client_secret: "client-secret",
      organization_id: "60069102279",
      refresh_token: "refresh-token",
    },
  });
  expect(createBody.auth_config).not.toHaveProperty("region");
  expect(createBody.auth_config).not.toHaveProperty("data_center");
});
