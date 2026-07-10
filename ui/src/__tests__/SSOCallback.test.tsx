import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { loginWithToken, navigate } = vi.hoisted(() => ({
  loginWithToken: vi.fn(),
  navigate: vi.fn(),
}));

vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => ({ loginWithToken }),
}));

vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>();
  return { ...actual, useNavigate: () => navigate };
});

import SSOCallback from "@/pages/SSOCallback";

describe("SSOCallback cookie-first completion", () => {
  beforeEach(() => {
    loginWithToken.mockReset();
    navigate.mockReset();
    window.history.replaceState(null, "", "/sso/callback");
  });

  it("hydrates the HttpOnly cookie session without requiring a bearer fragment", async () => {
    loginWithToken.mockResolvedValue(undefined);

    render(<SSOCallback />);

    await waitFor(() => {
      expect(loginWithToken).toHaveBeenCalledWith(undefined);
      expect(navigate).toHaveBeenCalledWith("/dashboard", { replace: true });
    });
  });

  it("renders a recoverable error when cookie hydration fails", async () => {
    loginWithToken.mockRejectedValue(new Error("Could not verify the SSO session"));

    render(<SSOCallback />);

    expect(await screen.findByText("SSO sign-in failed")).toBeInTheDocument();
    expect(screen.getByText("Could not verify the SSO session")).toBeInTheDocument();
    expect(navigate).not.toHaveBeenCalledWith("/dashboard", { replace: true });
  });
});
