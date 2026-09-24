import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiMock = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock("@/lib/api", () => ({ default: apiMock }));
vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => ({ user: { role: "admin", tenant_id: "tenant-a" } }),
}));

import Observatory from "@/pages/Observatory";

describe("Observatory audit status", () => {
  beforeEach(() => {
    apiMock.get.mockReset();
  });

  it("describes polled audit rows without claiming a live feed or a full-day total", async () => {
    apiMock.get.mockResolvedValue({ data: { items: [
      { id: "audit-1", event_type: "agent.run", action: "Run finished", created_at: new Date().toISOString() },
    ] } });
    render(<Observatory />);

    expect(await screen.findByText("AUDIT POLL")).toBeInTheDocument();
    expect(screen.getByText("Recent Agent Audit")).toBeInTheDocument();
    expect(screen.getByText("Results Observed")).toBeInTheDocument();
    expect(screen.getByText(/agents seen in recent audit/)).toBeInTheDocument();
    expect(screen.queryByText(/agents active/)).not.toBeInTheDocument();
    expect(screen.getByText("New audit rows per poll (5s)")).toBeInTheDocument();
    expect(screen.queryByText("LIVE")).not.toBeInTheDocument();
    expect(apiMock.get).toHaveBeenCalledWith("/audit", expect.objectContaining({
      params: expect.objectContaining({ page: 1, per_page: 20 }),
    }));
  });

  it("makes an audit outage visible instead of presenting stale data as current", async () => {
    apiMock.get.mockRejectedValue(new Error("audit unavailable"));
    render(<Observatory />);
    expect(await screen.findByText("AUDIT DELAYED")).toBeInTheDocument();
    expect(screen.getByText("Audit data unavailable. Retrying on the next poll.")).toBeInTheDocument();
  });
});
