import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
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
    // Anonymous: the agent-type lookup itself returns 401, so no run is attempted.
    expect(mockGet).toHaveBeenCalledWith(
      "/agents",
      expect.objectContaining({ params: expect.objectContaining({ domain: "finance" }) }),
    );
    expect(mockPost).not.toHaveBeenCalled();
  });

  it("resolves canned use-cases to the tenant's own agent by type", async () => {
    // Audit finding #12: agent ids are tenant-scoped; the playground must
    // never POST to a hard-coded UUID.
    mockGet.mockResolvedValue({
      status: 200,
      data: {
        items: [
          { id: "tenant-agent-77", agent_type: "ap_processor", status: "active", domain: "finance" },
          { id: "tenant-agent-78", agent_type: "recon_agent", status: "active", domain: "finance" },
        ],
        pages: 1,
      },
    });
    mockPost.mockResolvedValue({ status: 200, data: { status: "completed", confidence: 0.9 } });

    render(
      <MemoryRouter initialEntries={["/playground"]}>
        <Playground />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole("button", { name: /Review Sample Invoice/i }));

    await vi.waitFor(() => {
      expect(mockPost).toHaveBeenCalledWith(
        "/agents/tenant-agent-77/run",
        expect.objectContaining({ action: "process_invoice" }),
        expect.objectContaining({ validateStatus: expect.any(Function) }),
      );
    });
  });

  it("explains when the tenant has no agent of the requested type", async () => {
    mockGet.mockResolvedValue({ status: 200, data: { items: [], pages: 1 } });

    render(
      <MemoryRouter initialEntries={["/playground"]}>
        <Playground />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole("button", { name: /Review Sample Invoice/i }));

    expect(await screen.findByText(/No AP Processor agent \(type "ap_processor"\) is deployed/)).toBeInTheDocument();
    expect(mockPost).not.toHaveBeenCalled();
  });
});
