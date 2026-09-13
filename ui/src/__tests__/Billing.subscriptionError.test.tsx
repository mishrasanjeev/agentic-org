import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import Billing from "../pages/Billing";

// Audit finding #14: a failed /billing/subscription lookup used to collapse to
// ``null`` and render a paid tenant as "free" with live Subscribe buttons.
const apiMock = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("@/lib/api", () => ({
  default: apiMock,
  extractApiError: (_e: unknown, fallback: string) => fallback,
}));

const CATALOG = {
  schema_version: "agenticorg.billing-plans.v1",
  catalog_version: "sentinel.1",
  complete: true,
  plan_count: 2,
  plans: [
    {
      plan_id: "free",
      display_name: "Free",
      display_order: 0,
      prices: [{ currency: "USD", amount_minor: 0, interval: "month" }],
      limits: { agent_count: 3, agent_runs: 100, agent_runs_interval: "month", storage_bytes: 1024 },
      signup_available: true,
      checkout_mode: "none",
    },
    {
      plan_id: "growth",
      display_name: "Growth",
      display_order: 10,
      prices: [{ currency: "USD", amount_minor: 49_900, interval: "month" }],
      limits: { agent_count: 25, agent_runs: 5000, agent_runs_interval: "month", storage_bytes: 1024 * 1024 },
      signup_available: true,
      checkout_mode: "hosted",
    },
  ],
};

function mockRoutes(subscriptionOk: boolean) {
  apiMock.get.mockImplementation((path: string) => {
    if (path === "/billing/plans") return Promise.resolve({ data: CATALOG });
    if (path === "/billing/subscription") {
      return subscriptionOk
        ? Promise.resolve({ data: { plan: "growth", is_paid: true, provider: "stripe" } })
        : Promise.reject(new Error("HTTP 503"));
    }
    if (path === "/billing/usage") return Promise.resolve({ data: { agent_runs: 1, agent_count: 1, storage_bytes: 10 } });
    return Promise.reject(new Error(`unexpected ${path}`));
  });
}

describe("Billing subscription failure handling", () => {
  beforeEach(() => {
    apiMock.get.mockReset();
    apiMock.post.mockReset();
  });

  it("renders the real plan when the subscription lookup succeeds", async () => {
    mockRoutes(true);
    render(<MemoryRouter><Billing /></MemoryRouter>);

    await waitFor(() => expect(screen.getByTestId("billing-usage")).toBeInTheDocument());
    expect(screen.getByText("growth")).toBeInTheDocument();
    expect(screen.queryByTestId("billing-subscription-error")).not.toBeInTheDocument();
  });

  it("does not render the tenant as 'free' and blocks plan changes when the lookup fails", async () => {
    mockRoutes(false);
    render(<MemoryRouter><Billing /></MemoryRouter>);

    expect(await screen.findByTestId("billing-subscription-error")).toBeInTheDocument();
    expect(screen.getByText("unknown")).toBeInTheDocument();
    expect(screen.queryByText("Current plan")).not.toBeInTheDocument();

    const upgrade = screen.getByRole("button", { name: /Upgrade to Growth/ });
    expect(upgrade).toBeDisabled();
    fireEvent.click(upgrade);
    expect(apiMock.post).not.toHaveBeenCalled();
  });
});
