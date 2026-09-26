// SPDX-License-Identifier: Apache-2.0
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

const get = vi.fn();
vi.mock("@/lib/api", () => ({
  default: { get: (...args: unknown[]) => get(...args) },
  extractApiError: (_err: unknown, fallback: string) => fallback,
}));
vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => ({ user: { role: "admin", user_id: "admin-1" } }),
}));

import Connectors from "@/pages/Connectors";

const base = {
  id: "c1", name: "Shopify", category: "ops", status: "active",
  auth_type: "oauth2", rate_limit_rpm: 30, visibility: "shared",
};

function readiness(state: string) {
  return {
    state, credential_state: state === "needs_credentials" ? "missing" : "configured",
    health_state: state === "recent_health" ? "recent" : "unverified",
    last_health_check: null, last_sync_at: null,
    scope_verification: "unverified", contract_verification: "unverified", error_budget: "unmeasured",
  };
}

describe("connector readiness evidence", () => {
  beforeEach(() => {
    get.mockReset();
    get.mockImplementation((url: string) => {
      if (url === "/connectors") return Promise.resolve({ data: { items: [
        { ...base, readiness: readiness("recent_health") },
        { ...base, id: "c2", name: "Slack", readiness: readiness("needs_credentials") },
        { ...base, id: "c3", name: "Legacy" },
      ] } });
      if (url === "/connectors/registry") return Promise.resolve({ data: { items: [], total: 0 } });
      if (url === "/connectors/c1/health") return Promise.resolve({ data: { healthy: true, name: "Shopify" } });
      return Promise.resolve({ data: {} });
    });
  });

  it("does not equate registration with healthy and treats old API rows as unverified", async () => {
    render(<MemoryRouter><Connectors /></MemoryRouter>);
    expect(await screen.findByText("Recently checked on page")).toBeInTheDocument();
    expect(screen.getByText("Needs attention on page")).toBeInTheDocument();
    expect(screen.getByText("Needs credentials")).toBeInTheDocument();
    expect(screen.getByText("Not verified")).toBeInTheDocument();
    expect(screen.queryByText("Unhealthy")).not.toBeInTheDocument();
    expect(screen.getByText("A recent health check is not proof of provider scopes, contract access, or production readiness.")).toBeInTheDocument();
  });

  it("refreshes the evidence projection after a health check", async () => {
    render(<MemoryRouter><Connectors /></MemoryRouter>);
    await screen.findByText("Shopify");
    fireEvent.click(screen.getAllByRole("button", { name: "Health Check" })[0]);
    await waitFor(() => expect(get.mock.calls.filter(([url]) => url === "/connectors")).toHaveLength(2));
  });

  it("uses server totals and allows paging beyond the first 50", async () => {
    get.mockImplementation((url: string, config?: { params?: { page: number } }) => {
      if (url === "/connectors") return Promise.resolve({ data: {
        items: [{ ...base, id: `c${config?.params?.page || 1}`, name: `Page ${config?.params?.page || 1}` }],
        total: 51,
      } });
      return Promise.resolve({ data: { items: [], total: 0 } });
    });
    render(<MemoryRouter><Connectors /></MemoryRouter>);
    await screen.findByText("Page 1 of 2");
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByText("Page 2 of 2")).toBeInTheDocument();
    expect(get).toHaveBeenCalledWith("/connectors", { params: { page: 2, per_page: 50 } });
  });
});
