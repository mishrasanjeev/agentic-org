import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { mockGet, mockPost } = vi.hoisted(() => ({
  mockGet: vi.fn(),
  mockPost: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  default: { get: mockGet, post: mockPost },
  extractApiError: () => "Agent run failed. Please try again.",
}));

vi.mock("@/components/Analytics", () => ({
  trackEvent: vi.fn(),
}));

vi.mock("react-helmet-async", () => ({
  Helmet: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

import Playground from "@/pages/Playground";

describe("Playground cookie authentication", () => {
  beforeEach(() => {
    mockGet.mockReset();
    mockPost.mockReset();
    mockGet.mockResolvedValue({ status: 401, data: {} });
  });

  it("does not auto-login anonymous visitors and shows a sign-in action", async () => {
    mockPost.mockResolvedValue({ status: 401, data: { detail: "Not authenticated" } });

    render(
      <MemoryRouter initialEntries={["/playground"]}>
        <Playground />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole("button", { name: /Review Sample Invoice/i }));

    expect(await screen.findByText("Sign in to run agents in the playground.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Sign in to continue" })).toHaveAttribute(
      "href",
      "/login?next=%2Fplayground",
    );
    expect(mockPost).toHaveBeenCalledWith(
      "/agents/a0000001-0000-0000-0001-000000000001/run",
      expect.objectContaining({ action: "process_invoice" }),
      expect.objectContaining({ validateStatus: expect.any(Function) }),
    );
  });
});
