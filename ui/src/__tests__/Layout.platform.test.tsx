// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
/**
 * Layout: the Platform nav entry is offered to administrators only, and only
 * once GET /insights/status answers platform_operator: true. The status is
 * read once for both the Insights and the Platform entries.
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

function statusAnswers(status: Record<string, unknown>) {
  mockGet.mockImplementation((url: string) => {
    if (url === "/insights/status") return Promise.resolve({ data: status });
    return Promise.resolve({ data: { enabled: false } });
  });
}

const insightsStatusCalls = () => mockGet.mock.calls.filter(([url]) => url === "/insights/status").length;

describe("Layout Platform entry", () => {
  beforeEach(() => {
    mockGet.mockReset();
    mockRole = "admin";
  });

  it("is shown to the platform operator, linking to the platform view", async () => {
    statusAnswers({ enabled: true, views: [], platform_operator: true });
    renderLayout();
    expect(screen.queryAllByText("Platform")).toHaveLength(0);
    await waitFor(() => expect(screen.getAllByText("Platform").length).toBeGreaterThan(0));
    expect(screen.getAllByText("Platform")[0].closest("a")).toHaveAttribute("href", "/dashboard/platform");
    expect(screen.getAllByText("Insights").length).toBeGreaterThan(0);
    expect(insightsStatusCalls()).toBe(1);
  });

  it("does not depend on Insights being on for the tenant", async () => {
    statusAnswers({ enabled: false, views: [], platform_operator: true });
    renderLayout();
    await waitFor(() => expect(screen.getAllByText("Platform").length).toBeGreaterThan(0));
    expect(screen.queryAllByText("Insights")).toHaveLength(0);
  });

  it("stays hidden when the caller is not the platform operator", async () => {
    statusAnswers({ enabled: true, views: [], platform_operator: false });
    renderLayout();
    await waitFor(() => expect(screen.getAllByText("Insights").length).toBeGreaterThan(0));
    expect(screen.queryAllByText("Platform")).toHaveLength(0);
  });

  it("stays hidden when the status cannot be read", async () => {
    mockGet.mockRejectedValue(new Error("503"));
    renderLayout();
    await waitFor(() => expect(mockGet).toHaveBeenCalledWith("/insights/status"));
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.queryAllByText("Platform")).toHaveLength(0);
  });

  it("is never offered to a role other than admin", async () => {
    mockRole = "cfo";
    statusAnswers({ enabled: true, views: [], platform_operator: true });
    renderLayout();
    await waitFor(() => expect(screen.getAllByText("Insights").length).toBeGreaterThan(0));
    expect(screen.queryAllByText("Platform")).toHaveLength(0);
  });
});
