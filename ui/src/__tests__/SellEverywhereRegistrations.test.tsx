// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

const get = vi.fn();
const setup = { list: vi.fn(), get: vi.fn() };

vi.mock("@/lib/api", () => ({ default: {} }));
vi.mock("@/components/commerce/PaymentSettings", () => ({ default: () => <div>Payment settings</div> }));
vi.mock("../lib/api", () => ({
  sellEverywhereApi: { get: (...a: unknown[]) => get(...a), put: vi.fn(), publish: vi.fn() },
  merchantSetupApi: {
    list: (...a: unknown[]) => setup.list(...a),
    get: (...a: unknown[]) => setup.get(...a),
    create: vi.fn(),
    connectShopify: vi.fn(),
    uploadCatalog: vi.fn(),
    syncCatalog: vi.fn(),
    savePolicies: vi.fn(),
    saveShopDetails: vi.fn(),
    saveOrderLimits: vi.fn(),
  },
  extractApiError: (_e: unknown, fallback: string) => fallback,
}));

import SellEverywhere from "@/pages/SellEverywhere";

const FEED = "https://tea-corner.agenticorg.ai/feeds/google.xml";

function networkView(programs: Record<string, unknown>) {
  return {
    terms_version: "2026-10-25",
    suggested_slug: "tea-corner",
    listing: {
      slug: "tea-corner",
      status: "listed",
      display_name: "Tea Corner",
      serving_host: "tea-corner.agenticorg.ai",
      subdomain_host: "tea-corner.agenticorg.ai",
      terms_version: "2026-10-25",
      opted_in_at: "2026-10-25T00:00:00Z",
    },
    surfaces: {
      web: { status: "live", host: "tea-corner.agenticorg.ai" },
      mcp: { status: "live", server_url: "https://mcp.agenticorg.ai/mcp" },
      telegram: { status: "live", link: null },
      payments: { status: "live", mode: "plural_hosted" },
      ...programs,
    },
    platform_subdomains: { apex: "agenticorg.ai", in_use: 3, cap: 20 },
    grantex_enrolment: { status: "active", message: "Enrolled." },
    publication: { published: true, host: "tea-corner.agenticorg.ai", refusal: null },
    artifacts: { status: "ready", reason: null },
  };
}

const setupView = {
  merchant: { merchant_id: "tea-1", seller_agent_id: "seller_tea", display_name: "Tea Corner", categories: ["tea"] },
  catalog: { status: "ready", source: "catalog_upload", product_count: 3, currency: "INR" },
  policies: { status: "set", returns: "Returns within 7 days.", shipping: "Ships in 3 days.", pickup: "" },
  order_limits: { status: "set", currency: "INR", max_order_amount: "5000.00", max_daily_amount: "20000.00", max_quantity_per_order: 5 },
  missing: [],
  ready_to_turn_on: true,
};

function row(title: string) {
  const heading = screen.getByText(title);
  const item = heading.closest("li");
  if (!item) throw new Error(`no row for ${title}`);
  return within(item);
}

async function renderWith(programs: Record<string, unknown>) {
  setup.list.mockResolvedValue({
    data: { merchants: [{ merchant_id: "tea-1", seller_agent_id: "seller_tea", display_name: "Tea Corner" }], count: 1 },
  });
  setup.get.mockResolvedValue({ data: setupView });
  get.mockResolvedValue({ data: networkView(programs) });
  render(
    <MemoryRouter>
      <SellEverywhere />
    </MemoryRouter>,
  );
  await screen.findByText("Where buyers can find you");
}

describe("Sell everywhere: outside programs", () => {
  beforeEach(() => {
    get.mockReset();
    Object.values(setup).forEach((fn) => fn.mockReset());
  });

  it("shows each program's state and the feed address for feed programs", async () => {
    await renderWith({
      chatgpt: { status: "submitted", reason: null },
      google: { status: "live", reason: null, feed_url: FEED },
      microsoft: { status: "submitted", reason: null, feed_url: FEED },
      perplexity: { status: "registration_pending", reason: null, feed_url: FEED },
    });
    expect(row("Google Gemini and AI Mode").getByText("Live")).toBeInTheDocument();
    expect(row("Google Gemini and AI Mode").getByText(FEED)).toBeInTheDocument();
    expect(row("Google Gemini and AI Mode").getByRole("button", { name: "Copy Google product feed address" })).toBeInTheDocument();
    expect(row("Microsoft Copilot").getByText("Submitted, waiting for approval")).toBeInTheDocument();
    expect(row("Perplexity").getByText("Coming: AgenticOrg has not registered yet")).toBeInTheDocument();
    const mcp = row("ChatGPT, Claude and other AI apps");
    expect(mcp.getByText("ChatGPT app directory:")).toBeInTheDocument();
    expect(mcp.getByText("Submitted, waiting for approval")).toBeInTheDocument();
  }, 20000);

  it("says why a shop is not eligible yet", async () => {
    await renderWith({
      chatgpt: { status: "not_eligible", reason: "no_eligible_products" },
      google: { status: "not_eligible", reason: "catalog_not_verified", feed_url: null },
      microsoft: { status: "not_eligible", reason: "web_address_not_live", feed_url: null },
      perplexity: { status: "not_eligible", reason: "no_eligible_products", feed_url: null },
    });
    expect(row("Google Gemini and AI Mode").getByText("Not eligible yet")).toBeInTheDocument();
    expect(row("Google Gemini and AI Mode").getByText("Waiting for Grantex to verify your catalog.")).toBeInTheDocument();
    expect(row("Microsoft Copilot").getByText("Waiting for your web address to go live.")).toBeInTheDocument();
    expect(
      row("Perplexity").getByText("No product has a price, a stock figure and an image yet."),
    ).toBeInTheDocument();
    expect(screen.queryByText(FEED)).not.toBeInTheDocument();
  }, 20000);

  it("still renders a view from an older API without the chatgpt entry", async () => {
    await renderWith({
      google: { status: "registration_pending" },
      microsoft: { status: "registration_pending" },
      perplexity: { status: "registration_pending" },
    });
    expect(screen.queryByText("ChatGPT app directory:")).not.toBeInTheDocument();
    expect(screen.getAllByText("Coming: AgenticOrg has not registered yet")).toHaveLength(3);
  }, 20000);
});
