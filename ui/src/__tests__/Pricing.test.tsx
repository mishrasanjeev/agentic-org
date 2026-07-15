import { render, screen, waitFor } from "@testing-library/react";
import { HelmetProvider } from "react-helmet-async";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import Pricing from "../pages/Pricing";

const apiMock = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("@/lib/api", () => ({ default: apiMock }));

const SENTINEL_CATALOG = {
  schema_version: "agenticorg.billing-plans.v1",
  catalog_version: "sentinel.1",
  complete: true,
  plan_count: 2,
  plans: [
    {
      plan_id: "alpha",
      display_name: "Sentinel Alpha",
      display_order: 20,
      prices: [{ currency: "USD", amount_minor: 12_345, interval: "month" }],
      limits: { agent_count: 7, agent_runs: 321, agent_runs_interval: "month", storage_bytes: 2_147_483_648 },
      signup_available: false,
      checkout_mode: "hosted",
    },
    {
      plan_id: "beta",
      display_name: "Sentinel Beta",
      display_order: 10,
      prices: [{ currency: "INR", amount_minor: 45_600, interval: "month" }],
      limits: { agent_count: null, agent_runs: null, agent_runs_interval: "month", storage_bytes: null },
      signup_available: true,
      checkout_mode: "none",
    },
  ],
};

function renderPricing() {
  return render(<HelmetProvider><MemoryRouter><Pricing /></MemoryRouter></HelmetProvider>);
}

async function expectCatalogFailure() {
  await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("complete billing catalog is unavailable"));
  expect(screen.queryAllByTestId("plan-card")).toHaveLength(0);
  expect(document.body).not.toHaveTextContent(/Start Free|Sentinel Alpha|Sentinel Beta/);
}

describe("Pricing", () => {
  beforeEach(() => apiMock.get.mockReset());

  it("renders only exact facts returned by a complete versioned catalog", async () => {
    apiMock.get.mockResolvedValueOnce({ data: SENTINEL_CATALOG });
    renderPricing();
    expect(apiMock.get).toHaveBeenCalledWith("/billing/plans");
    expect(await screen.findByRole("heading", { name: "Sentinel Alpha" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Sentinel Beta" })).toBeInTheDocument();
    expect(screen.getAllByText("$123.45 / month").length).toBeGreaterThan(0);
    expect(screen.getByText("Agents: 7")).toBeInTheDocument();
    expect(screen.getByText("Agent runs per month: 321")).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent(/Priority support|Custom SLA|SSO/i);
    await waitFor(() => expect(screen.queryByRole("status")).not.toBeInTheDocument());
  });

  it("fails closed when the catalog request fails", async () => {
    apiMock.get.mockRejectedValueOnce(new Error("catalog unavailable"));
    renderPricing();
    await expectCatalogFailure();
  });

  it("fails closed for an empty legacy response", async () => {
    apiMock.get.mockResolvedValueOnce({ data: [] });
    renderPricing();
    await expectCatalogFailure();
  });

  it("fails closed when plan_count proves the response is partial", async () => {
    apiMock.get.mockResolvedValueOnce({ data: { ...SENTINEL_CATALOG, plan_count: 3 } });
    renderPricing();
    await expectCatalogFailure();
  });
});
