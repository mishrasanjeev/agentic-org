// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
/** The run debugger moved from Observability to the Insights run page: administrators, when the run names its thread. */
import { fireEvent, render, screen } from "@testing-library/react";
import { HelmetProvider } from "react-helmet-async";
import { MemoryRouter, Route, Routes } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockGet = vi.fn();
vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  },
}));
const roleState = vi.hoisted(() => ({ role: "admin" }));
vi.mock("@/contexts/AuthContext", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/contexts/AuthContext")>();
  return { ...actual, useOptionalRole: () => roleState.role };
});
vi.mock("@/components/RunDebugger", () => ({
  default: ({ agentId, threadId }: { agentId: string; threadId: string }) => (
    <div data-testid="run-debugger">{`${agentId}/${threadId}`}</div>
  ),
}));

import i18n from "@/i18n";
import InsightsRun from "@/pages/InsightsRun";

function evidence(debug: unknown) {
  return {
    run: { id: "r1", agent: "Invoice matcher", agent_id: "a1", status: "failed", started_at: "2026-11-01T03:00:00+00:00" },
    gateway_records: [],
    failure: null,
    spans: [],
    debug,
    approvals: [],
    audit: [],
    links: {},
    limits: { records: 50, spans: 200 },
  };
}

function renderRun() {
  return render(
    <HelmetProvider>
      <MemoryRouter initialEntries={["/dashboard/insights/runs/r1?at=2026-11-01T03%3A00%3A00%2B00%3A00"]}>
        <Routes>
          <Route path="/dashboard/insights/runs/:runId" element={<InsightsRun />} />
        </Routes>
      </MemoryRouter>
    </HelmetProvider>,
  );
}

describe("Insights run debugger", () => {
  beforeEach(async () => {
    mockGet.mockReset();
    await i18n.changeLanguage("en");
  });

  it("opens the debugger on the run's own thread for an administrator", async () => {
    roleState.role = "admin";
    mockGet.mockResolvedValue({ data: evidence({ agent_id: "a1", thread_id: "thread-9" }) });
    renderRun();
    fireEvent.click(await screen.findByTestId("run-debug-toggle"));
    expect(screen.getByTestId("run-debugger")).toHaveTextContent("a1/thread-9");
  });

  it("is not offered to other roles, or when the run names no thread", async () => {
    roleState.role = "cfo";
    mockGet.mockResolvedValue({ data: evidence({ agent_id: "a1", thread_id: "thread-9" }) });
    const first = renderRun();
    await screen.findByText("Invoice matcher");
    expect(screen.queryByTestId("run-debug-toggle")).not.toBeInTheDocument();
    first.unmount();
    roleState.role = "admin";
    mockGet.mockResolvedValue({ data: evidence(null) });
    renderRun();
    await screen.findByText("Invoice matcher");
    expect(screen.queryByTestId("run-debug-toggle")).not.toBeInTheDocument();
  });
});
