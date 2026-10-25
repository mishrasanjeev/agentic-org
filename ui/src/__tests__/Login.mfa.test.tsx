// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const login = vi.fn();
const completeMfa = vi.fn();
const navigate = vi.fn();

vi.mock("@/contexts/AuthContext", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/contexts/AuthContext")>();
  return { ...actual, useAuth: () => ({ login, loginWithGoogle: vi.fn(), completeMfa }) };
});

vi.mock("react-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router")>();
  return { ...actual, useNavigate: () => navigate };
});

vi.mock("@/contexts/BrandingContext", () => ({
  useBranding: () => ({ productName: "AgenticOrg", logoUrl: null }),
}));

import Login from "@/pages/Login";

function signIn() {
  render(
    <MemoryRouter initialEntries={["/login"]}>
      <Login />
    </MemoryRouter>,
  );
  fireEvent.change(screen.getByLabelText("Email"), { target: { value: "owner@example.com" } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: "CorrectPass1" } });
  fireEvent.click(screen.getByRole("button", { name: "Sign in with email" }));
}

describe("Login with two-factor sign-in", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ json: async () => ({}) }));
    login.mockReset();
    completeMfa.mockReset();
    navigate.mockReset();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("asks for the authenticator code and signs in only after it", async () => {
    login.mockResolvedValue({ mfaRequired: true, challenge: "c-123", expiresIn: 300 });
    completeMfa.mockResolvedValue({ role: "admin", email: "owner@example.com" });
    signIn();

    const code = await screen.findByLabelText("Authentication code");
    expect(navigate).not.toHaveBeenCalled();
    expect(screen.queryByLabelText("Password")).not.toBeInTheDocument();

    fireEvent.change(code, { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: "Verify and sign in" }));

    await waitFor(() => expect(completeMfa).toHaveBeenCalledWith("c-123", "123456"));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith("/dashboard"));
  }, 20000);

  it("returns to the password form when the challenge has expired", async () => {
    login.mockResolvedValue({ mfaRequired: true, challenge: "c-123", expiresIn: 300 });
    completeMfa.mockRejectedValue(new Error("This sign-in has expired. Sign in again."));
    signIn();

    fireEvent.change(await screen.findByLabelText("Authentication code"), { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: "Verify and sign in" }));

    expect(await screen.findByText("This sign-in has expired. Sign in again.")).toBeInTheDocument();
    expect(screen.getByLabelText("Password")).toHaveValue("");
    expect(navigate).not.toHaveBeenCalled();
  }, 20000);

  it("keeps the code step after a wrong code", async () => {
    login.mockResolvedValue({ mfaRequired: true, challenge: "c-123", expiresIn: 300 });
    completeMfa.mockRejectedValue(new Error("That code is not right"));
    signIn();

    fireEvent.change(await screen.findByLabelText("Authentication code"), { target: { value: "000000" } });
    fireEvent.click(screen.getByRole("button", { name: "Verify and sign in" }));

    expect(await screen.findByText("That code is not right")).toBeInTheDocument();
    expect(screen.getByLabelText("Authentication code")).toHaveValue("");
  }, 20000);
});
