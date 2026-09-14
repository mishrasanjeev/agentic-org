/**
 * Bug sheet 2026-09-14 rows 17-19/30/52 — nav and route guards for the
 * agent-creator roles.
 *
 * Agents, Org Chart, Approvals and Connectors share one role list
 * (lib/roles.ts) between Layout nav and App route guards. domain_lead and
 * developer are not admitted to /dashboard, so they land on the agent fleet.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { describe, expect, it, vi } from "vitest";

let mockUser: Record<string, unknown> | null = null;

vi.mock("../contexts/AuthContext", async () => {
  const actual = await vi.importActual<typeof import("../contexts/AuthContext")>("../contexts/AuthContext");
  return {
    ...actual,
    useAuth: () => ({ user: mockUser, logout: vi.fn(), isAuthenticated: true, isHydrating: false }),
  };
});

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
import ProtectedRoute from "@/components/ProtectedRoute";
import { defaultLandingForRole } from "@/contexts/AuthContext";
import {
  AGENT_CREATOR_ROLES,
  APPROVAL_ROLES,
  CONNECTOR_ROLES,
  agentDomainsForUser,
  canManageAgent,
  canManageConnector,
} from "@/lib/roles";

function setUser(role: string, domain = "") {
  mockUser = { user_id: `u-${role}`, email: `${role}@example.com`, name: role, role, domain, tenant_id: "t1" };
}

function navLabels(): string[] {
  const nav = document.querySelector("aside nav");
  return Array.from(nav?.querySelectorAll("a") ?? []).map((a) => a.textContent || "");
}

describe("ownership nav and route guards (bug sheet 2026-09-14)", () => {
  it.each(["developer", "domain_lead"])("%s sees Agents, Connectors and Approvals nav", (role) => {
    setUser(role, "finance");
    render(
      <MemoryRouter initialEntries={["/dashboard/agents"]}>
        <Layout>
          <div>child</div>
        </Layout>
      </MemoryRouter>,
    );
    const labels = navLabels();
    expect(labels).toEqual(expect.arrayContaining(["Agents", "Connectors", "Approvals", "Org Chart"]));
    expect(labels).not.toContain("Create from SOP");
    expect(labels).not.toContain("Dashboard");
  });

  it("auditor does not get the agent-creator nav entries", () => {
    setUser("auditor");
    render(
      <MemoryRouter initialEntries={["/dashboard"]}>
        <Layout>
          <div>child</div>
        </Layout>
      </MemoryRouter>,
    );
    const labels = navLabels();
    expect(labels).not.toContain("Agents");
    expect(labels).not.toContain("Connectors");
    expect(labels).not.toContain("Approvals");
  });

  it.each([
    ["developer", AGENT_CREATOR_ROLES],
    ["developer", CONNECTOR_ROLES],
    ["developer", APPROVAL_ROLES],
    ["domain_lead", AGENT_CREATOR_ROLES],
    ["cfo", CONNECTOR_ROLES],
  ])("route guard admits %s", (role, roles) => {
    setUser(role, "finance");
    render(
      <MemoryRouter initialEntries={["/guarded"]}>
        <Routes>
          <Route path="/guarded" element={<ProtectedRoute allowedRoles={roles}><div>guarded page</div></ProtectedRoute>} />
          <Route path="/dashboard/access-denied" element={<div>access denied</div>} />
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByText("guarded page")).toBeInTheDocument();
  });

  it("route guard still denies auditor on the agent-creator routes", () => {
    setUser("auditor");
    render(
      <MemoryRouter initialEntries={["/guarded"]}>
        <Routes>
          <Route path="/guarded" element={<ProtectedRoute allowedRoles={AGENT_CREATOR_ROLES}><div>guarded page</div></ProtectedRoute>} />
          <Route path="/dashboard/access-denied" element={<div>access denied</div>} />
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByText("access denied")).toBeInTheDocument();
  });

  it("domain_lead and developer land on the agent fleet after login", () => {
    expect(defaultLandingForRole("developer")).toBe("/dashboard/agents");
    expect(defaultLandingForRole("domain_lead")).toBe("/dashboard/agents");
    expect(defaultLandingForRole("cfo")).toBe("/dashboard");
    expect(defaultLandingForRole("merchant")).toBe("/dashboard/commerce-runtime");
  });

  it("ownership helpers mirror the backend contract", () => {
    const owner = { user_id: "u1", role: "cfo", domain: "finance" };
    expect(canManageAgent(owner, { visibility: "personal", owner_user_id: "u1" })).toBe(true);
    expect(canManageAgent(owner, { visibility: "personal", owner_user_id: "u2" })).toBe(false);
    expect(canManageAgent(owner, { visibility: "tenant", owner_user_id: "u1" })).toBe(false);
    expect(canManageAgent({ user_id: "a", role: "admin" }, { visibility: "tenant", owner_user_id: null })).toBe(true);
    expect(canManageConnector(owner, { owner_user_id: "u1" })).toBe(true);
    expect(canManageConnector(owner, { owner_user_id: null })).toBe(false);
    expect(agentDomainsForUser({ role: "chro", domain: "" })).toEqual(["hr"]);
    expect(agentDomainsForUser({ role: "domain_lead", domain: "" })).toEqual([]);
    expect(agentDomainsForUser({ role: "developer" })).toHaveLength(6);
  });
});
