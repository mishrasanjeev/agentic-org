// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

const get = vi.fn();
const put = vi.fn();
const publish = vi.fn();
const setup = {
  list: vi.fn(),
  get: vi.fn(),
  create: vi.fn(),
  connectShopify: vi.fn(),
  uploadCatalog: vi.fn(),
  syncCatalog: vi.fn(),
  savePolicies: vi.fn(),
  saveOrderLimits: vi.fn(),
};

vi.mock("@/lib/api", () => ({ default: {} }));
vi.mock("../lib/api", () => ({
  sellEverywhereApi: {
    get: (...a: unknown[]) => get(...a),
    put: (...a: unknown[]) => put(...a),
    publish: (...a: unknown[]) => publish(...a),
  },
  merchantSetupApi: {
    list: (...a: unknown[]) => setup.list(...a),
    get: (...a: unknown[]) => setup.get(...a),
    create: (...a: unknown[]) => setup.create(...a),
    connectShopify: (...a: unknown[]) => setup.connectShopify(...a),
    uploadCatalog: (...a: unknown[]) => setup.uploadCatalog(...a),
    syncCatalog: (...a: unknown[]) => setup.syncCatalog(...a),
    savePolicies: (...a: unknown[]) => setup.savePolicies(...a),
    saveOrderLimits: (...a: unknown[]) => setup.saveOrderLimits(...a),
  },
  extractApiError: (_e: unknown, fallback: string) => fallback,
}));

import SellEverywhere from "@/pages/SellEverywhere";

const surfaces = (overrides: Record<string, unknown> = {}) => ({
  web: { status: "not_started", host: null },
  mcp: { status: "off", server_url: "https://mcp.agenticorg.ai/mcp" },
  telegram: { status: "off", link: null },
  google: { status: "registration_pending" },
  microsoft: { status: "registration_pending" },
  perplexity: { status: "registration_pending" },
  payments: { status: "off" },
  ...overrides,
});

const view = (overrides: Record<string, unknown> = {}) => ({
  terms_version: "2026-10-25",
  suggested_slug: "tea-corner",
  listing: null,
  surfaces: surfaces(),
  platform_subdomains: { apex: "agenticorg.ai", in_use: 3, cap: 20 },
  grantex_enrolment: { status: "not_started", message: "Not enrolled yet." },
  publication: { published: false, host: null, refusal: "serving_host_not_attested" },
  artifacts: { status: "missing", reason: "verified_artifacts_missing:catalog" },
  ...overrides,
});

const setupView = (overrides: Record<string, unknown> = {}) => ({
  merchant: {
    merchant_id: "tea-corner-1a2b3c",
    seller_agent_id: "seller_agent_tea",
    display_name: "Tea Corner",
    categories: ["tea"],
  },
  catalog: {
    status: "ready",
    source: "catalog_upload",
    shop_domain: null,
    product_count: 3,
    last_synced_at: "2026-10-29T08:00:00Z",
    currency: "INR",
    last_error: null,
    webhook_status: null,
    webhook_path: null,
  },
  policies: { status: "set", returns: "Returns within 7 days.", shipping: "Ships in 3 to 5 days.", pickup: "" },
  order_limits: {
    status: "set",
    currency: "INR",
    max_order_amount: "5000.00",
    max_daily_amount: "20000.00",
    max_quantity_per_order: 5,
  },
  missing: [],
  ready_to_turn_on: true,
  ...overrides,
});

const merchant = {
  merchant_id: "tea-corner-1a2b3c",
  seller_agent_id: "seller_agent_tea",
  display_name: "Tea Corner",
  catalog_source: "catalog_upload",
};

const listedListing = {
  slug: "tea-corner",
  status: "listed",
  display_name: "Tea Corner",
  serving_host: null,
  subdomain_host: "tea-corner.agenticorg.ai",
  terms_version: "2026-10-25",
  opted_in_at: "2026-10-25T00:00:00Z",
};

function renderPage() {
  render(
    <MemoryRouter>
      <SellEverywhere />
    </MemoryRouter>,
  );
}

describe("Sell everywhere", () => {
  beforeEach(() => {
    get.mockReset();
    put.mockReset();
    publish.mockReset();
    Object.values(setup).forEach((fn) => fn.mockReset());
  });

  it("loads the only shop, then turns the network on once the terms are accepted", async () => {
    setup.list.mockResolvedValue({ data: { merchants: [merchant], count: 1 } });
    setup.get.mockResolvedValue({ data: setupView() });
    get.mockResolvedValue({ data: view() });
    put.mockResolvedValue({
      data: view({
        listing: listedListing,
        surfaces: surfaces({
          web: { status: "pending_mapping", host: "tea-corner.agenticorg.ai" },
          mcp: { status: "live", server_url: "https://mcp.agenticorg.ai/mcp" },
          telegram: { status: "live", link: "https://t.me/ExampleShopBot?start=tea-corner" },
        }),
        grantex_enrolment: {
          status: "unavailable",
          message: "Waiting: Grantex enrolment is not available on this deployment yet.",
          waiting: true,
        },
        publication: { published: true, host: "tea-corner.agenticorg.ai", refusal: null },
        artifacts: { status: "ready", reason: null },
      }),
    });
    renderPage();

    const slug = await screen.findByLabelText("Shop name on the network");
    expect(setup.get).toHaveBeenCalledWith("tea-corner-1a2b3c");
    expect(slug).toHaveValue("tea-corner");
    expect(screen.getByText("Payment settings load here.")).toBeInTheDocument();
    const turnOn = screen.getByRole("button", { name: "Turn on Sell everywhere" });
    expect(turnOn).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(turnOn);

    await waitFor(() =>
      expect(put).toHaveBeenCalledWith("tea-corner-1a2b3c", { listed: true, accept_terms: true, slug: "tea-corner" }),
    );
    expect(await screen.findByText("Published at https://tea-corner.agenticorg.ai")).toBeInTheDocument();
    expect(screen.getByText(/Grantex enrolment is not available/)).toBeInTheDocument();
    expect(screen.getByText("https://t.me/ExampleShopBot?start=tea-corner")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Refresh listing" })).toBeInTheDocument();
    expect(screen.getByLabelText("Shop name on the network")).toBeDisabled();
  }, 20000);

  it("keeps Turn on disabled and names what is missing until the order limits are set", async () => {
    setup.list.mockResolvedValue({ data: { merchants: [merchant], count: 1 } });
    setup.get.mockResolvedValue({
      data: setupView({
        order_limits: {
          status: "missing",
          currency: null,
          max_order_amount: null,
          max_daily_amount: null,
          max_quantity_per_order: null,
        },
        missing: ["order_limits"],
        ready_to_turn_on: false,
      }),
    });
    get.mockResolvedValue({ data: view() });
    setup.saveOrderLimits.mockResolvedValue({ data: setupView() });
    renderPage();

    expect(await screen.findByText("Finish your order limits first.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("checkbox"));
    expect(screen.getByRole("button", { name: "Turn on Sell everywhere" })).toBeDisabled();

    fireEvent.change(screen.getByLabelText("Currency"), { target: { value: "inr" } });
    fireEvent.change(screen.getByLabelText("Largest single order"), { target: { value: "5000" } });
    fireEvent.change(screen.getByLabelText("Most per day"), { target: { value: "1000" } });
    fireEvent.change(screen.getByLabelText("Most items per order"), { target: { value: "5" } });
    // A daily maximum below the per-order maximum is not accepted.
    expect(screen.getByRole("button", { name: "Save limits" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Most per day"), { target: { value: "20000" } });
    fireEvent.click(screen.getByRole("button", { name: "Save limits" }));
    await waitFor(() =>
      expect(setup.saveOrderLimits).toHaveBeenCalledWith("tea-corner-1a2b3c", {
        currency: "INR",
        max_order_amount: "5000",
        max_daily_amount: "20000",
        max_quantity_per_order: 5,
      }),
    );
    expect(await screen.findByText("Order limits saved.")).toBeInTheDocument();
  }, 20000);

  it("creates a shop without a hand-typed id and connects Shopify, clearing the token", async () => {
    setup.list.mockResolvedValue({ data: { merchants: [], count: 0 } });
    const fresh = setupView({
      catalog: {
        status: "not_uploaded",
        source: "catalog_upload",
        shop_domain: null,
        product_count: 0,
        last_synced_at: null,
        currency: null,
        last_error: null,
        webhook_status: null,
        webhook_path: null,
      },
      missing: ["catalog", "policies", "order_limits"],
      ready_to_turn_on: false,
    });
    setup.create.mockResolvedValue({ data: fresh });
    get.mockResolvedValue({ data: view() });
    setup.connectShopify.mockResolvedValue({
      data: {
        connection: { status: "shopify_connector_configured" },
        sync: { product_count: 12 },
        sync_error: null,
        setup: setupView({
          catalog: {
            status: "ready",
            source: "shopify",
            shop_domain: "tea-corner.myshopify.com",
            product_count: 12,
            last_synced_at: "2026-10-29T08:00:00Z",
            currency: "INR",
            last_error: null,
            webhook_status: "blocked_missing_webhook_secret",
            webhook_path: "/api/v1/webhooks/shopify/t/0123456789abcdef0123456789abcdef",
          },
        }),
      },
    });
    renderPage();

    fireEvent.change(await screen.findByLabelText("Shop name"), { target: { value: "Tea Corner" } });
    fireEvent.click(screen.getByRole("button", { name: "Create shop" }));
    await waitFor(() => expect(setup.create).toHaveBeenCalledWith({ display_name: "Tea Corner", categories: undefined }));

    fireEvent.click(await screen.findByLabelText("Shopify"));
    fireEvent.change(screen.getByLabelText("Store address"), { target: { value: "tea-corner.myshopify.com" } });
    fireEvent.change(screen.getByLabelText("Admin API access token"), { target: { value: "fixture-admin-token-value" } });
    fireEvent.click(screen.getByRole("button", { name: "Connect Shopify" }));
    await waitFor(() =>
      expect(setup.connectShopify).toHaveBeenCalledWith("tea-corner-1a2b3c", {
        shop_domain: "tea-corner.myshopify.com",
        admin_access_token: "fixture-admin-token-value",
      }),
    );
    expect(await screen.findByText(/12 products read from tea-corner.myshopify.com/)).toBeInTheDocument();
    expect(screen.getByLabelText("Admin API access token")).toHaveValue("");
    expect(screen.getByText(/\/api\/v1\/webhooks\/shopify\/t\//)).toBeInTheDocument();
  }, 20000);

  it("lists each problem in a refused catalog file", async () => {
    setup.list.mockResolvedValue({ data: { merchants: [merchant], count: 1 } });
    setup.get.mockResolvedValue({ data: setupView() });
    get.mockResolvedValue({ data: view() });
    setup.uploadCatalog.mockRejectedValue({
      response: {
        status: 422,
        data: {
          detail: {
            code: "catalog_upload_invalid",
            message: "2 problem(s) in the upload",
            errors: [
              { row: 1, column: "price", message: "must be greater than zero" },
              { row: 2, column: "sku", message: "repeats an earlier sku" },
            ],
          },
        },
      },
    });
    renderPage();

    const input = await screen.findByLabelText("Catalog file");
    const file = new File(["sku,title,price,currency,quantity\n"], "catalog.csv", { type: "text/csv" });
    Object.defineProperty(file, "text", { value: () => Promise.resolve("sku,title,price,currency,quantity\n") });
    fireEvent.change(input, { target: { files: [file] } });
    expect(await screen.findByText("Row 1, price: must be greater than zero")).toBeInTheDocument();
    expect(screen.getByText("Row 2, sku: repeats an earlier sku")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("2 problem(s) in the upload");
    expect(setup.uploadCatalog).toHaveBeenCalledWith("tea-corner-1a2b3c", {
      format: "csv",
      content: "sku,title,price,currency,quantity\n",
    });
  }, 20000);

  it("says when the deployment has not turned the network on", async () => {
    setup.list.mockResolvedValue({ data: { merchants: [merchant], count: 1 } });
    setup.get.mockResolvedValue({ data: setupView() });
    get.mockRejectedValue({ response: { status: 404, data: { detail: "Not Found" } } });
    renderPage();
    expect(await screen.findByText("Sell everywhere is not enabled for this deployment yet.")).toBeInTheDocument();
  }, 20000);

  it("retries go-live with Publish and shows the exact refusal", async () => {
    const listed = { listing: { ...listedListing, subdomain_host: null } };
    setup.list.mockResolvedValue({ data: { merchants: [merchant], count: 1 } });
    setup.get.mockResolvedValue({ data: setupView() });
    get.mockResolvedValue({ data: view(listed) });
    publish.mockResolvedValue({
      data: view({
        ...listed,
        publication: { published: false, host: "tea-corner.agenticorg.ai", refusal: "namespace_not_authority_bound" },
      }),
    });
    renderPage();

    expect(await screen.findByText(/Waiting for your web address/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Publish" }));
    await waitFor(() => expect(publish).toHaveBeenCalledWith("tea-corner-1a2b3c"));
    expect(
      await screen.findByText("Your web address cannot carry your shop profile. Contact support."),
    ).toBeInTheDocument();
  }, 20000);
});
