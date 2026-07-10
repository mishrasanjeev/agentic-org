import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { mockPost } = vi.hoisted(() => ({ mockPost: vi.fn() }));

vi.mock("@/lib/api", () => ({
  default: { post: mockPost },
  extractApiError: (error: { response?: { data?: { detail?: string } } }, fallback: string) =>
    error.response?.data?.detail || fallback,
}));

vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => ({ isAuthenticated: false }),
}));

import BillingCallback from "@/pages/BillingCallback";

function renderCallback() {
  return render(
    <MemoryRouter
      initialEntries={[
        "/dashboard/billing/callback?provider=plural&order_id=order-123&payment=success",
      ]}
    >
      <BillingCallback />
    </MemoryRouter>,
  );
}

describe("BillingCallback Plural verification", () => {
  beforeEach(() => {
    mockPost.mockReset();
  });

  it("uses the shared API client and trusts the verified provider status", async () => {
    mockPost.mockResolvedValue({ status: 200, data: { status: "AUTHORIZED" } });

    renderCallback();

    expect(await screen.findByText("Payment Successful!")).toBeInTheDocument();
    expect(mockPost).toHaveBeenCalledWith(
      "/billing/order-status",
      { order_id: "order-123" },
      expect.objectContaining({ validateStatus: expect.any(Function) }),
    );
  });

  it("surfaces verification failures instead of trusting the success query parameter", async () => {
    mockPost.mockRejectedValue({
      response: { status: 403, data: { detail: "CSRF token mismatch" } },
    });

    renderCallback();

    expect(await screen.findByText("Payment Verification Unavailable")).toBeInTheDocument();
    expect(screen.getByText("CSRF token mismatch")).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.queryByText("Payment Successful!")).not.toBeInTheDocument();
      expect(screen.queryByText("Payment Pending")).not.toBeInTheDocument();
    });
  });
});
