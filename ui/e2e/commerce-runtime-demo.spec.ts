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

    if (path.endsWith("/api/v1/commerce/runtime/bridges/surfaces")) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          surfaces: [
            { surface: "web", status: "bridge_ready" },
            { surface: "mcp_chatgpt_claude", status: "bridge_ready" },
            { surface: "openapi_gemini_perplexity", status: "bridge_ready" },
            { surface: "a2a", status: "bridge_ready" },
            { surface: "whatsapp", status: "config_missing", missing_config: ["WHATSAPP_APP_SECRET"] },
            { surface: "telegram", status: "config_missing", missing_config: ["TELEGRAM_WEBHOOK_SECRET_TOKEN"] },
          ],
        }),
      });
      return;
    }

    if (path.endsWith("/api/v1/commerce/runtime/protocol-adapters")) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          status: "adapter_payloads_ready",
          surfaces: [
            "schema_org_product_offer_jsonld",
            "ucp_capability_profile",
            "acp_commerce_interaction_profile",
            "ap2_mandate_payment_evidence_profile",
            "a2a_agent_card_task_metadata",
            "mcp_tool_resource_metadata",
            "openapi_buyer_safe_bridge_schema",
          ],
          source_label: "Source: Shopify via Grantex artifact",
          freshness_label: "Freshness: synced 12m ago",
        }),
      });
      return;
    }

    if (path.endsWith("/api/v1/commerce/runtime/purchase/prepare")) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          status: "blocked",
          source_label: "Source: Shopify via Grantex artifact",
          freshness_label: "Freshness: synced 12m ago",
          blocker: {
            code: "plural_pine_capability_missing_or_stale",
            action: "Configure PLURAL_PINE_CLIENT_ID and PLURAL_PINE_CLIENT_SECRET, then rerun capability verification.",
          },
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
    await expect(page.getByText("adapter_payloads_ready").first()).toBeVisible();

    await page.getByRole("button", { name: "Ask" }).click();
    await expect(page.getByText("Source: Shopify via Grantex artifact")).toBeVisible();
    await expect(page.getByText("Freshness: synced 12m ago")).toBeVisible();
    await expect(page.getByText("Price is a snapshot")).toBeVisible();

    await page.getByRole("button", { name: "Verify" }).click();
    await expect(page.getByText("Mandate capability")).toBeVisible();
    await expect(page.getByText("unknown").first()).toBeVisible();

    await page.getByRole("button", { name: "Channels" }).click();
    await expect(page.getByText("2 channel configs missing")).toBeVisible();
    await expect(page.getByText("4/6 ready")).toBeVisible();

    await page.getByRole("button", { name: "Prepare" }).click();
    await expect(page.getByText("blocked").first()).toBeVisible();
    await expect(page.getByText(/plural_pine_capability_missing_or_stale/)).toBeVisible();

    const createCall = calls.find((call) =>
      call.path.endsWith("/api/v1/commerce/runtime/seller-agents/onboarding-packets"),
    );
    expect(createCall?.body.requested_grantex_authority_scope.artifact_families).toEqual(
      ARTIFACT_FAMILIES,
    );
    expect(createCall?.body.connector_metadata.credential_ref).toBe("tenant_connector_config");
    expect(createCall?.body.shopify_shop_domain).toBe("mgx0n6-22.myshopify.com");
    expect(createCall?.body.permitted_sync_actions).toContain("read_inventory_snapshot");
    expect(createCall?.body.channel_capability_preferences.telegram).toBe(true);
    expect(createCall?.body.payment_mandate_rail_preference).toBe("plural_pine_p3p");

    const cacheCall = calls.find((call) =>
      call.path.endsWith("/api/v1/commerce/runtime/artifacts/cache"),
    );
    const askCall = calls.find((call) =>
      call.path.endsWith("/api/v1/commerce/runtime/buyer-sessions/ask"),
    );
    expect(cacheCall?.body.buyer_agent_id).toBe("buyer_agent_demo");
    expect(askCall?.body.buyer_agent_id).toBe("buyer_agent_demo");
    expect(askCall?.body.action_intent).toBe("non_binding_preview");
    const purchaseCall = calls.find((call) =>
      call.path.endsWith("/api/v1/commerce/runtime/purchase/prepare"),
    );
    expect(purchaseCall?.body.live_execution_approved).toBe(false);

    const bodyText = await page.locator("body").innerText();
    expect(bodyText).not.toMatch(/create checkout|create order|create mandate|capture payment/i);
  });
});
