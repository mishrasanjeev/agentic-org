// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const get = vi.fn();
const post = vi.fn();

vi.mock("@/lib/api", () => ({ default: { get: (...a: unknown[]) => get(...a), post: (...a: unknown[]) => post(...a), delete: vi.fn() } }));
vi.mock("@/contexts/AuthContext", () => ({ useAuth: () => ({ user: { role: "analyst", email: "me@example.com" } }) }));

import AccountSecurity from "@/pages/AccountSecurity";

describe("Account security", () => {
  beforeEach(() => {
    get.mockReset();
    post.mockReset();
  });

  it("walks through QR code, authenticator code and recovery codes", async () => {
    get
      .mockResolvedValueOnce({ data: { enabled: false, enabled_at: null, recovery_codes_remaining: 0 } })
      .mockResolvedValueOnce({ data: { enabled: true, enabled_at: "2026-10-25T00:00:00Z", recovery_codes_remaining: 10 } });
    post
      .mockResolvedValueOnce({
        data: { qr_code: "data:image/svg+xml;base64,PHN2Zy8+", secret: "ABCD EFGH", account: "me@example.com", issuer: "AgenticOrg" },
      })
      .mockResolvedValueOnce({ data: { enabled: true, recovery_codes: ["aaaaa-bbbbb", "ccccc-ddddd"] } });

    render(<AccountSecurity />);
    fireEvent.click(await screen.findByRole("button", { name: "Set up authenticator app" }));

    const qr = await screen.findByAltText("QR code to add AgenticOrg to your authenticator app");
    expect(qr).toHaveAttribute("src", "data:image/svg+xml;base64,PHN2Zy8+");
    expect(screen.getByTestId("mfa-secret")).toHaveTextContent("ABCD EFGH");

    fireEvent.change(screen.getByLabelText("Code from the app"), { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: "Verify and turn on" }));

    await waitFor(() => expect(post).toHaveBeenCalledWith("/auth/mfa/enable", { code: "123456" }));
    expect(await screen.findByText("aaaaa-bbbbb")).toBeInTheDocument();
    expect(await screen.findByText("On")).toBeInTheDocument();
    expect(screen.queryByTestId("members-mfa")).not.toBeInTheDocument();
  }, 20000);

  it("needs a code to turn it off", async () => {
    get.mockResolvedValue({ data: { enabled: true, enabled_at: null, recovery_codes_remaining: 9 } });
    post.mockRejectedValueOnce({ response: { data: { detail: "That code is not right" } } });

    render(<AccountSecurity />);
    fireEvent.click(await screen.findByRole("button", { name: "Turn off" }));
    fireEvent.change(screen.getByLabelText("Code"), { target: { value: "000000" } });
    fireEvent.click(screen.getByRole("button", { name: "Turn off" }));

    expect(await screen.findByText("That code is not right")).toBeInTheDocument();
    expect(post).toHaveBeenCalledWith("/auth/mfa/disable", { code: "000000" });
  }, 20000);
});
