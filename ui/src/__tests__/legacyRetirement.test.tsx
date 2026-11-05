// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
/**
 * The retired dashboard pages: each old URL redirects to where its features live now (keeping a deep link's
 * query where asked), the sidebar no longer offers them, and the run debugger is on the Insights run page.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { describe, expect, it, vi } from "vitest";

import appSource from "../App.tsx?raw";
import layoutSource from "../components/Layout.tsx?raw";

const authState = vi.hoisted(() => ({ role: "cfo" }));
vi.mock("@/contexts/AuthContext", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/contexts/AuthContext")>();
  return { ...actual, useAuth: () => ({ user: { role: authState.role } }) };
});

import LegacyRedirect, { redirectTarget } from "@/components/LegacyRedirect";

function Where() {
  const location = useLocation();
  return <div data-testid="where">{location.pathname + location.search}</div>;
}

function landAt(initial: string, element: React.ReactNode) {
  render(
    <MemoryRouter initialEntries={[initial]}>
      <Routes>
        <Route path="/old" element={element} />
        <Route path="*" element={<Where />} />
      </Routes>
    </MemoryRouter>,
  );
  return screen.getByTestId("where").textContent;
}

describe("redirectTarget", () => {
  it("drops the old query unless asked, and never overrides the target's own parameters", () => {
    expect(redirectTarget("/dashboard/insights?view=finance", "?x=1", false)).toBe("/dashboard/insights?view=finance");
    expect(redirectTarget("/dashboard/marketing-ops", "?panel=data-readiness", true)).toBe(
      "/dashboard/marketing-ops?panel=data-readiness",
    );
    expect(redirectTarget("/a?view=finance", "?view=hr&panel=x", true)).toBe("/a?view=finance&panel=x");
    expect(redirectTarget("/a", "", true)).toBe("/a");
  });
});

describe("LegacyRedirect", () => {
  it("sends an old URL on, with a deep link's query", () => {
    expect(landAt("/old?panel=kpi-lineage", <LegacyRedirect to="/dashboard/marketing-ops" keepQuery />)).toBe(
      "/dashboard/marketing-ops?panel=kpi-lineage",
    );
  });

  it("picks the target by role", () => {
    authState.role = "cfo";
    const to = (role: string) => (role === "auditor" ? "/dashboard/insights/explore/audit" : "/dashboard/audit");
    expect(landAt("/old", <LegacyRedirect to={to} />)).toBe("/dashboard/audit");
  });
});

describe("App routes and the sidebar", () => {
  const redirects: Array<[string, string]> = [
    ["/dashboard/ceo", "/dashboard/insights?view=executive"],
    ["/dashboard/cfo", "/dashboard/insights?view=finance"],
    ["/dashboard/chro", "/dashboard/insights?view=hr"],
    ["/dashboard/coo", "/dashboard/insights?view=operations"],
    ["/dashboard/cbo", "/dashboard/insights?view=back_office"],
    ["/dashboard/costs", "/dashboard/insights?view=finance"],
    ["/dashboard/partner", "/dashboard/insights?view=partner"],
    ["/dashboard/cmo", "/dashboard/marketing-ops"],
    ["/dashboard/abm", "/dashboard/marketing-ops/abm"],
    ["/dashboard/scopes", "/dashboard/enforce-audit"],
    ["/dashboard/observability", "/dashboard/monitoring"],
  ];

  it.each(redirects)("%s redirects to %s", (from, to) => {
    const at = appSource.indexOf(`path="${from}"`);
    expect(at).toBeGreaterThan(-1);
    const block = appSource.slice(at, appSource.indexOf("/>", appSource.indexOf("</ProtectedRoute>", at)));
    expect(block).toContain(`<LegacyRedirect to="${to}"`);
  });

  it("serves the new homes and offers them in the sidebar instead of the retired pages", () => {
    for (const path of ["/dashboard/marketing-ops", "/dashboard/marketing-ops/abm", "/dashboard/monitoring"]) {
      expect(appSource).toContain(`path="${path}"`);
    }
    for (const retired of ['"/dashboard/cfo"', '"/dashboard/partner"', '"/dashboard/observatory"', '"/dashboard/scopes"']) {
      expect(layoutSource).not.toContain(`path: ${retired}`);
    }
    expect(layoutSource).toContain('path: "/dashboard/marketing-ops"');
    expect(layoutSource).toContain('path: "/dashboard/monitoring"');
  });
});
