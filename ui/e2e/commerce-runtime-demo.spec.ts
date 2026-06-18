import { expect, test, type Page } from "@playwright/test";

const ARTIFACT_FAMILIES = [
  "merchant_profile",
  "seller_agent_card",
  "connector_evidence",
  "catalog_snapshot",
  "offer_price_snapshot",
  "inventory_snapshot",
  "policy_scope",
  "public_discovery_state",
  "mandate_capability",
  "protocol_adapter",
  "authority_request_status",
];

async function installCommerceRuntimeRoutes(page: Page) {
  const calls: Array<{ method: string; path: string; body: any }> = [];

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    const body = method === "GET" ? null : JSON.parse(request.postData() || "{}");
    calls.push({ method, path, body });

    if (path.endsWith("/api/v1/auth/me")) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          email: "admin@example.test",
          name: "Admin",
          role: "admin",
          domain: "example.test",
          tenant_id: "tenant_demo",
          onboarding_complete: true,
        }),
      });
      return;
    }

    if (path.endsWith("/api/v1/commerce/runtime/seller-agents/onboarding-packets")) {
      await route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify({
          packet: {
            packet_id: "packet_demo",
            tenant_id: "tenant_demo",
            merchant_id: body.merchant_id,
            seller_agent_id: body.seller_agent_id,
          },
        }),
      });
      return;
    }

    if (path.endsWith("/api/v1/commerce/runtime/seller-agents/shopify/sync")) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          evidence_id: "shopify_evidence_demo",
          product_count: 1,
          variant_count: 1,
        }),
      });
      return;
    }

    if (path.endsWith("/api/v1/commerce/runtime/products")) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          products: [
            {
              product_ref: "shopify:product:demo",
              title: "Demo Product",
              vendor: "Demo Vendor",
              product_type: "Accessories",
            },
          ],
        }),
      });
      return;
    }

    if (path.endsWith("/api/v1/commerce/runtime/authority/grantex/request")) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          status: "artifact_issuance_ready",
          artifacts: ARTIFACT_FAMILIES.map((family) => ({
            artifact_id: `artifact_${family}`,
            artifact_family: family,
            tenant_id: "tenant_demo",
            merchant_id: "merchant_demo",
            seller_agent_id: "seller_agent_demo",
            source_system: "shopify",
            issued_at: "2026-06-14T03:30:00.000Z",
            expires_at: "2026-06-14T03:45:00.000Z",
            allowed_to_execute: false,
            no_payment_execution: true,
          })),
        }),
      });
      return;
    }

    if (path.endsWith("/api/v1/commerce/runtime/artifacts/cache")) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ cached: true, artifact_count: body.artifacts?.length || 0 }),
      });
      return;
    }

    if (path.endsWith("/api/v1/commerce/runtime/buyer-sessions/ask")) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          status: "answered",
          source_label: "Source: Shopify via Grantex artifact",
          freshness_label: "Freshness: synced 12m ago",
          answer: "Demo Product is available from cached artifacts. Price is a snapshot.",
        }),
      });
      return;
    }

    if (path.endsWith("/api/v1/commerce/runtime/providers/plural-pine/mandate-capability/verify")) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          stored: true,
          evidence: {
            result_status: "unknown",
          },
        }),
      });
      return;
    }

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ items: [], total: 0, pages: 1 }),
    });
  });

  return calls;
}

test.describe("C6Z commerce runtime demo", () => {
  test("runs the seller onboarding to buyer cached-artifact UI path without execution tools", async ({
    page,
    baseURL,
  }) => {
    const calls = await installCommerceRuntimeRoutes(page);

    await page.goto(`${baseURL}/dashboard/commerce-runtime`, { waitUntil: "domcontentloaded" });
    await expect(page.getByText("Seller Commerce Agent")).toBeVisible();
    await expect(page.getByText("Buyer Session")).toBeVisible();

    await page.getByRole("button", { name: "Create" }).click();
    await expect(page.getByText("packet_demo")).toBeVisible();

    await page.getByRole("button", { name: "Sync" }).click();
    await expect(page.getByText("1 products, 1 variants")).toBeVisible();
    await expect(page.getByText("Demo Product")).toBeVisible();

    await page.getByRole("button", { name: "Issue" }).click();
    await expect(page.getByText("11 artifacts cached")).toBeVisible();

    await page.getByRole("button", { name: "Ask" }).click();
    await expect(page.getByText("Source: Shopify via Grantex artifact")).toBeVisible();
    await expect(page.getByText("Freshness: synced 12m ago")).toBeVisible();
    await expect(page.getByText("Price is a snapshot")).toBeVisible();

    await page.getByRole("button", { name: "Verify" }).click();
    await expect(page.getByText("Mandate capability")).toBeVisible();
    await expect(page.getByText("unknown")).toBeVisible();

    const createCall = calls.find((call) =>
      call.path.endsWith("/api/v1/commerce/runtime/seller-agents/onboarding-packets"),
    );
    expect(createCall?.body.requested_grantex_authority_scope.artifact_families).toEqual(
      ARTIFACT_FAMILIES,
    );
    expect(createCall?.body.connector_metadata.credential_ref).toBe("tenant_connector_config");

    const cacheCall = calls.find((call) =>
      call.path.endsWith("/api/v1/commerce/runtime/artifacts/cache"),
    );
    const askCall = calls.find((call) =>
      call.path.endsWith("/api/v1/commerce/runtime/buyer-sessions/ask"),
    );
    expect(cacheCall?.body.buyer_agent_id).toBe("buyer_agent_demo");
    expect(askCall?.body.buyer_agent_id).toBe("buyer_agent_demo");
    expect(askCall?.body.action_intent).toBe("non_binding_preview");

    const bodyText = await page.locator("body").innerText();
    expect(bodyText).not.toMatch(/create checkout|create order|create mandate|capture payment/i);
  });
});
