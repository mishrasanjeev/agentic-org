// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { HelmetProvider } from "react-helmet-async";
import { describe, expect, it, vi } from "vitest";
import ProtectedRoute from "@/components/ProtectedRoute";
import ResourcePage from "@/pages/resources/ResourcePage";

vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => ({ isHydrating: true, isAuthenticated: false, user: null }),
}));
vi.mock("@/pages/resources/contentData", async importOriginal => {
  const original = await importOriginal<typeof import("@/pages/resources/contentData")>();
  return {
    ...original,
    CONTENT_PAGES: [{
      ...original.CONTENT_PAGES[0], slug: "encoding-check", relatedSlugs: [],
      sections: [{ heading: "Encoding checks", body: "\u2022 Caf\u00e9 \u2014 ready\n\u2022 \u0939\u093f\u0928\u094d\u0926\u0940\n\n1. First\n2. Second\n\nWait\u2026 then continue." }],
    }],
  };
});

describe("Encoding repair sibling paths", () => {
  it("renders the session hydration ellipsis without corrupting it", () => {
    render(<MemoryRouter><ProtectedRoute><p>Protected content</p></ProtectedRoute></MemoryRouter>);
    expect(screen.getByRole("status")).toHaveTextContent("Loading session\u2026");
    expect(screen.queryByText("Protected content")).not.toBeInTheDocument();
  });

  it("recognizes real Unicode bullets and preserves multilingual list contents", () => {
    render(<HelmetProvider><MemoryRouter initialEntries={["/resources/encoding-check"]}>
      <Routes><Route path="/resources/:slug" element={<ResourcePage />} /></Routes>
    </MemoryRouter></HelmetProvider>);
    for (const text of ["Caf\u00e9 \u2014 ready", "\u0939\u093f\u0928\u094d\u0926\u0940", "First", "Second"]) {
      expect(screen.getByText(text, { exact: true }).closest("li")).toBeInTheDocument();
    }
    expect(screen.getByText("Wait\u2026 then continue.")).toBeInTheDocument();
  });
});
