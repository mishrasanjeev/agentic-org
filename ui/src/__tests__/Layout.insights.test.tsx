// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
/**
 * Layout: the Insights nav entry is offered to admin and cfo only, and only
 * once GET /insights/status answers enabled.
 */
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockGet = vi.fn();
let mockRole = "admin";

vi.mock("../lib/api", () => ({
  default: { get: (...args: unknown[]) => mockGet(...args), interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } } },
}));

vi.mock("../contexts/AuthContext", () => ({
  useAuth: () => ({
    user: { email: "u@acme.example", name: "U", role: mockRole, tenant_id: "t1", org_name: "Acme" },
    logout: vi.fn(),
    isAuthenticated: true,
  }),
}));

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (_key: string, fallback?: string) => fallback ?? _key,
    i18n: { language: "en", changeLanguage: vi.fn(), on: vi.fn(), off: vi.fn() },
  }),
}));

vi.mock("../components/HITLBadge", () => ({ default: () => null }));
vi.mock("../components/NLQueryBar", () => ({ default: () => null }));
vi.mock("../components/ChatPanel", () => ({ default: () => null }));
vi.mock("../components/CompanySwitcher", () => ({ default: () => null }));
vi.mock("../components/NotificationBell", () => ({ default: () => null }));

import Layout from "@/components/Layout";

function renderLayout() {
  return render(
    <MemoryRouter initialEntries={["/dashboard"]}>
      <Layout>
        <div>child</div>
      </Layout>
    </MemoryRouter>,
  );
}

function statusAnswers(insightsEnabled: boolean) {
  mockGet.mockImplementation((url: string) => {
    if (url === "/insights/status") return Promise.resolve({ data: { enabled: insightsEnabled, views: [] } });
    return Promise.resolve({ data: { enabled: false } });
  });
}

describe("Layout Insights entry", () => {
  beforeEach(() => {
    mockGet.mockReset();
  });

  it.each(["admin", "cfo"])("%s sees Insights once the status says enabled", async (role) => {
    mockRole = role;
    statusAnswers(true);
    renderLayout();
    expect(screen.queryAllByText("Insights")).toHaveLength(0);
    await waitFor(() => expect(screen.getAllByText("Insights").length).toBeGreaterThan(0));
    const link = screen.getAllByText("Insights")[0].closest("a");
    expect(link).toHaveAttribute("href", "/dashboard/insights");
  });

  it("stays hidden when Insights is off", async () => {
    mockRole = "admin";
    statusAnswers(false);
    renderLayout();
    await waitFor(() => expect(mockGet).toHaveBeenCalledWith("/insights/status"));
    expect(screen.queryAllByText("Insights")).toHaveLength(0);
  });

  it("is offered to the COO, who opens the explorers for their domain", async () => {
    mockRole = "coo";
    statusAnswers(true);
    renderLayout();
    expect((await screen.findAllByText("Insights")).length).toBeGreaterThan(0);
  });

  it("is never offered to roles outside Insights", async () => {
    mockRole = "analyst";
    statusAnswers(true);
    renderLayout();
    await waitFor(() => expect(mockGet).toHaveBeenCalled());
    // Let the status promises settle before asserting absence.
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.queryAllByText("Insights")).toHaveLength(0);
  });
});
