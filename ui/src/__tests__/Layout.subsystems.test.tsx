// SPDX-License-Identifier: Apache-2.0
/**
 * Layout: an entry behind a default-off subsystem flag is shown only once its status says enabled.
 */
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";

const mockGet = vi.fn();

vi.mock("../lib/api", () => ({
  default: { get: (...args: unknown[]) => mockGet(...args), interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } } },
}));

vi.mock("../contexts/AuthContext", () => ({
  useAuth: () => ({
    user: { email: "coo@acme.example", name: "Demo COO", role: "coo", tenant_id: "t1", org_name: "Acme" },
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

describe("Layout subsystem gating", () => {
  it("shows a gated entry once its status says enabled and keeps the others hidden", async () => {
    mockGet.mockImplementation((url: string) => {
      if (url === "/txn/status") return Promise.resolve({ data: { enabled: true } });
      if (url === "/speech/status") return Promise.resolve({ data: { enabled: false } });
      return Promise.reject(new Error(`unexpected ${url}`));
    });
    renderLayout();
    expect(screen.queryAllByText("Transactions")).toHaveLength(0); // hidden until the status answers
    await waitFor(() => expect(screen.getAllByText("Transactions").length).toBeGreaterThan(0));
    expect(screen.queryAllByText("Calls")).toHaveLength(0);
    expect(screen.getAllByText("Approvals").length).toBeGreaterThan(0); // an ungated entry needs no status
  });

  it("keeps a gated entry hidden when the status cannot be read", async () => {
    mockGet.mockRejectedValue(new Error("404"));
    renderLayout();
    await waitFor(() => expect(mockGet).toHaveBeenCalledWith("/txn/status"));
    expect(screen.queryAllByText("Transactions")).toHaveLength(0);
    expect(screen.queryAllByText("Calls")).toHaveLength(0);
  });
});
