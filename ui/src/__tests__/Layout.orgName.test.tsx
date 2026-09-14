import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";

// QA sheet 2026-09-14 #27: the authenticated user's organization name was
// never shown in the header. Layout now renders it under the product name.

let mockUser: Record<string, unknown> | null = {
  email: "cfo@acme.example",
  name: "Demo CFO",
  role: "cfo",
  domain: "finance",
  tenant_id: "t1",
  org_name: "Acme Industries",
};

vi.mock("../contexts/AuthContext", () => ({
  useAuth: () => ({ user: mockUser, logout: vi.fn(), isAuthenticated: true }),
}));

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (_key: string, fallback?: string) => fallback ?? _key,
    i18n: { language: "en", changeLanguage: vi.fn(), on: vi.fn(), off: vi.fn() },
  }),
}));

vi.mock("../components/HITLBadge", () => ({ default: () => null }));
vi.mock("../components/NLQueryBar", () => ({ default: () => <div data-testid="nl-query-bar" /> }));
vi.mock("../components/ChatPanel", () => ({ default: () => null }));
vi.mock("../components/CompanySwitcher", () => ({ default: () => null }));
vi.mock("../components/NotificationBell", () => ({ default: () => null }));

import Layout from "@/components/Layout";

describe("Layout organization name", () => {
  it("renders the organization name from the auth context", () => {
    render(
      <MemoryRouter initialEntries={["/dashboard"]}>
        <Layout>
          <div>child</div>
        </Layout>
      </MemoryRouter>,
    );
    expect(screen.getByTestId("org-name")).toHaveTextContent("Acme Industries");
  });

  it("omits the organization line when the session has none", () => {
    mockUser = { ...mockUser, org_name: null };
    render(
      <MemoryRouter initialEntries={["/dashboard"]}>
        <Layout>
          <div>child</div>
        </Layout>
      </MemoryRouter>,
    );
    expect(screen.queryByTestId("org-name")).not.toBeInTheDocument();
  });

  // Bug sheet 2026-09-14 #53: chat now requires agents:write server-side, so
  // roles without it must not be offered a chat bar that always 403s.
  it.each([
    ["cfo", true],
    ["domain_lead", true],
    ["auditor", false],
    ["analyst", false],
    ["merchant", false],
  ])("chat entry point for role %s visible=%s", async (role, visible) => {
    mockUser = { ...mockUser, role };
    render(
      <MemoryRouter initialEntries={["/dashboard"]}>
        <Layout>
          <div>child</div>
        </Layout>
      </MemoryRouter>,
    );
    if (visible) {
      expect(await screen.findByTestId("nl-query-bar")).toBeInTheDocument();
    } else {
      expect(screen.queryByTestId("nl-query-bar")).not.toBeInTheDocument();
    }
  });
});
