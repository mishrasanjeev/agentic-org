// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
/**
 * Where each role lands after sign-in, onboarding and Access Denied.
 *
 * The landing page must be a route that admits the role; otherwise the
 * person's first screen is Access Denied (an analyst used to land on
 * /dashboard, which only the CxO, admin and auditor roles may open).
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import appSource from "../App.tsx?raw";
import * as roleLists from "../lib/roles";

const authState = vi.hoisted(() => ({ role: "analyst" }));
const apiPut = vi.hoisted(() => vi.fn());

vi.mock("@/contexts/AuthContext", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/contexts/AuthContext")>();
  return {
    ...actual,
    useAuth: () => ({ user: { role: authState.role, email: "person@example.com" } }),
  };
});

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, default: { ...actual.default, put: apiPut } };
});

import { defaultLandingForRole } from "@/contexts/AuthContext";
import Onboarding from "@/pages/Onboarding";

/** allowedRoles of the route declared with exactly ``path`` in App.tsx. */
function allowedRolesFor(path: string): readonly string[] {
  const escaped = path.replace(/[/.*+?^${}()|[\]\\]/g, "\\$&");
  const match = new RegExp(
    `path="${escaped}"\\s*element={\\s*<ProtectedRoute allowedRoles={([^}]*)}`,
  ).exec(appSource);
  if (!match) throw new Error(`no ProtectedRoute for ${path}`);
  const expr = match[1].trim();
  if (expr.startsWith("[")) {
    return expr
      .slice(1, -1)
      .split(",")
      .map((r) => r.trim().replace(/^"|"$/g, ""))
      .filter(Boolean);
  }
  const named = (roleLists as Record<string, unknown>)[expr];
  if (!Array.isArray(named)) throw new Error(`unknown role list ${expr}`);
  return named as readonly string[];
}

describe("defaultLandingForRole", () => {
  it("sends each role to its expected page", () => {
    expect(defaultLandingForRole("admin")).toBe("/dashboard");
    expect(defaultLandingForRole("cfo")).toBe("/dashboard");
    expect(defaultLandingForRole("auditor")).toBe("/dashboard");
    expect(defaultLandingForRole("analyst")).toBe("/dashboard/workbench");
    expect(defaultLandingForRole("developer")).toBe("/dashboard/agents");
    expect(defaultLandingForRole("domain_lead")).toBe("/dashboard/agents");
    expect(defaultLandingForRole("merchant")).toBe("/dashboard/commerce-runtime");
  });

  it("puts the analyst on a workbench route that admits the analyst", () => {
    expect(roleLists.WORKBENCH_ROLES).toContain("analyst");
    expect(allowedRolesFor("/dashboard")).not.toContain("analyst");
  });

  it.each([...roleLists.ALL_ROLES])("lands %s on a route that admits it", (role) => {
    const landing = defaultLandingForRole(role);
    expect(allowedRolesFor(landing)).toContain(role);
  });
});

function WhereAmI() {
  const location = useLocation();
  return <div data-testid="landed">{location.pathname}</div>;
}

async function finishOnboardingAs(role: string): Promise<string> {
  authState.role = role;
  render(
    <MemoryRouter initialEntries={["/onboarding"]}>
      <Routes>
        <Route path="/onboarding" element={<Onboarding />} />
        <Route path="*" element={<WhereAmI />} />
      </Routes>
    </MemoryRouter>,
  );
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
  fireEvent.click(screen.getByRole("button", { name: "Skip" }));
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
  fireEvent.click(screen.getByRole("button", { name: "Go to Dashboard" }));
  const landed = await screen.findByTestId("landed");
  return landed.textContent ?? "";
}

describe("Onboarding finish", () => {
  beforeEach(() => {
    apiPut.mockReset();
    apiPut.mockResolvedValue({ data: {} });
    try {
      window.localStorage.clear();
    } catch {
      // storage unavailable in this environment
    }
  });

  it("lands an analyst on the workbench, not Access Denied", async () => {
    expect(await finishOnboardingAs("analyst")).toBe("/dashboard/workbench");
    await waitFor(() => expect(apiPut).toHaveBeenCalledWith("/org/onboarding", expect.anything()));
  });

  it("lands an admin on the dashboard", async () => {
    expect(await finishOnboardingAs("admin")).toBe("/dashboard");
  });

  it("lands a merchant on the commerce runtime even when saving fails", async () => {
    apiPut.mockRejectedValueOnce(new Error("offline"));
    expect(await finishOnboardingAs("merchant")).toBe("/dashboard/commerce-runtime");
  });
});
