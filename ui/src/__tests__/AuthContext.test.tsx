import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  AuthProvider,
  shouldHydrateSessionForPath,
  useAuth,
} from "../contexts/AuthContext";

function Probe() {
  const auth = useAuth();
  return (
    <div>
      <span data-testid="hydrating">{String(auth.isHydrating)}</span>
      <span data-testid="authenticated">{String(auth.isAuthenticated)}</span>
      <span data-testid="email">{auth.user?.email ?? ""}</span>
    </div>
  );
}

function renderAt(pathname: string) {
  render(
    <MemoryRouter initialEntries={[pathname]}>
      <AuthProvider>
        <Probe />
      </AuthProvider>
    </MemoryRouter>,
  );
}

describe("AuthProvider route-aware session hydration", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("does not call /auth/me on public routes", async () => {
    renderAt("/");

    await waitFor(() => {
      expect(screen.getByTestId("hydrating")).toHaveTextContent("false");
    });

    expect(fetch).not.toHaveBeenCalled();
    expect(screen.getByTestId("authenticated")).toHaveTextContent("false");
  });

  it("hydrates from /auth/me on protected dashboard routes", async () => {
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      json: async () => ({
        email: "admin@example.com",
        name: "Admin",
        role: "admin",
        domain: "ops",
        tenant_id: "tenant-1",
        onboarding_complete: true,
      }),
    } as Response);

    renderAt("/dashboard/agents");

    await waitFor(() => {
      expect(fetch).toHaveBeenCalledWith(
        "/api/v1/auth/me",
        expect.objectContaining({ credentials: "include" }),
      );
      expect(screen.getByTestId("hydrating")).toHaveTextContent("false");
      expect(screen.getByTestId("authenticated")).toHaveTextContent("true");
    });
    expect(screen.getByTestId("email")).toHaveTextContent("admin@example.com");
  });

  it("keeps the protected route list explicit", () => {
    expect(shouldHydrateSessionForPath("/")).toBe(false);
    expect(shouldHydrateSessionForPath("/pricing")).toBe(false);
    expect(shouldHydrateSessionForPath("/blog/some-post")).toBe(false);
    expect(shouldHydrateSessionForPath("/dashboard-public")).toBe(false);
    expect(shouldHydrateSessionForPath("/dashboard")).toBe(true);
    expect(shouldHydrateSessionForPath("/dashboard/billing/callback")).toBe(true);
    expect(shouldHydrateSessionForPath("/onboarding")).toBe(true);
  });
});
