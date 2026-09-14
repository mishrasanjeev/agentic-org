/**
 * UI contract audit 2026-09-13 — AgentDetail feedback + amendments.
 *
 *  A1  ExplainerPanel takes the run_id from GET /agents/{id}/explanation/latest
 *      (the old GET /feedback?limit=1 read a non-existent `items` key, so the
 *      thumbs-up/down buttons never had a run to attach to).
 *  A3  "Dismiss" on a learned rule calls DELETE /agents/{id}/amendments/{index}
 *      and surfaces the API error instead of only filtering local state.
 */
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockGet = vi.fn();
const mockPost = vi.fn();
const mockPatch = vi.fn();
const mockDelete = vi.fn();

vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
    patch: (...args: unknown[]) => mockPatch(...args),
    delete: (...args: unknown[]) => mockDelete(...args),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  },
  agentsApi: {
    update: (...args: unknown[]) => mockPatch(...args),
    promptHistory: () => Promise.resolve({ data: [] }),
  },
  extractApiError: (_e: unknown, fallback: string) => fallback,
}));

vi.mock("@/components/KillSwitch", () => ({
  default: () => <div data-testid="kill-switch" />,
}));

vi.mock("@/components/ChatPanel", () => ({
  default: () => <div data-testid="chat-panel" />,
}));

vi.mock("react-helmet-async", () => ({
  Helmet: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

// Bug sheet 2026-09-14 ownership: the page reads the session role; an admin
// keeps every management control visible for this suite.
vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => ({
    user: { user_id: "u-admin", email: "admin@example.com", name: "Admin", role: "admin", domain: "all", tenant_id: "t1" },
    isAuthenticated: true,
  }),
}));

import AgentDetail from "@/pages/AgentDetail";

const AGENT = {
  id: "a1",
  name: "Ledger Agent",
  agent_type: "bookkeeper",
  domain: "finance",
  status: "shadow",
  confidence_floor: 0.88,
  max_retries: 3,
  authorized_tools: [],
  connector_ids: [],
  llm_config: { model: "gemini" },
  hitl_policy: { condition: "confidence < 0.88" },
  cost_controls: { monthly_cap_usd: 10, cost_current_usd: 0 },
  system_prompt_text: "You are a bookkeeper.",
  prompt_amendments: ["Always cite the ledger account"],
};

function routeGet(url: string) {
  if (url === "/agents/a1") return Promise.resolve({ data: AGENT });
  if (url === "/agents/a1/explanation/latest") {
    return Promise.resolve({
      data: { has_run: true, run_id: "run-77", status: "completed", bullets: ["Looked up ledger"], tools_cited: [], confidence: 0.9 },
    });
  }
  if (url === "/agents/a1/feedback?limit=50") return Promise.resolve({ data: { agent_id: "a1", feedback: [], count: 0 } });
  if (url === "/agents/a1/amendments") {
    return Promise.resolve({ data: { agent_id: "a1", amendments: ["Always cite the ledger account", "Never guess a GSTIN"], count: 2 } });
  }
  return Promise.resolve({ data: {} });
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/agents/a1"]}>
      <Routes>
        <Route path="/agents/:id" element={<AgentDetail />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("AgentDetail feedback + amendments contract", () => {
  beforeEach(() => {
    mockGet.mockReset();
    mockPost.mockReset();
    mockPatch.mockReset();
    mockDelete.mockReset();
    mockGet.mockImplementation((url: string) => routeGet(url));
    mockPost.mockResolvedValue({ data: {} });
  });

  it("A1: thumbs-up posts feedback with the run_id from /explanation/latest", async () => {
    renderPage();
    await screen.findByText("Ledger Agent");

    fireEvent.click(screen.getByText("Why did the agent do this?"));
    const thumbsUp = await screen.findByTitle("Thumbs up");
    await waitFor(() => expect(mockGet).toHaveBeenCalledWith("/agents/a1/explanation/latest"));
    // The dead feedback-list fetch must be gone.
    expect(mockGet).not.toHaveBeenCalledWith("/agents/a1/feedback?limit=1");

    await act(async () => {
      fireEvent.click(thumbsUp);
    });
    await waitFor(() => expect(mockPost).toHaveBeenCalledTimes(1));
    expect(mockPost).toHaveBeenCalledWith(
      "/agents/a1/feedback",
      expect.objectContaining({ run_id: "run-77", feedback_type: "thumbs_up" }),
    );
  });

  it("A3: Dismiss calls DELETE /agents/{id}/amendments/{index} and removes the row", async () => {
    mockDelete.mockResolvedValue({ data: { agent_id: "a1", removed: "Never guess a GSTIN", count: 1 } });
    renderPage();
    await screen.findByText("Ledger Agent");

    fireEvent.click(screen.getByRole("button", { name: "learning" }));
    await screen.findByText("Never guess a GSTIN");

    const dismissButtons = screen.getAllByRole("button", { name: "Dismiss" });
    await act(async () => {
      fireEvent.click(dismissButtons[1]);
    });
    await waitFor(() => expect(mockDelete).toHaveBeenCalledWith("/agents/a1/amendments/1"));
    await waitFor(() => expect(screen.queryByText("Never guess a GSTIN")).toBeNull());
    expect(screen.getByText("Always cite the ledger account")).toBeTruthy();
  });

  it("A3: a failed DELETE keeps the row and shows an error", async () => {
    mockDelete.mockRejectedValue({ response: { status: 403, data: { detail: "forbidden" } } });
    renderPage();
    await screen.findByText("Ledger Agent");

    fireEvent.click(screen.getByRole("button", { name: "learning" }));
    await screen.findByText("Never guess a GSTIN");

    await act(async () => {
      fireEvent.click(screen.getAllByRole("button", { name: "Dismiss" })[0]);
    });
    await screen.findByTestId("amendment-error");
    expect(screen.getByText("Always cite the ledger account")).toBeTruthy();
    expect(mockDelete).toHaveBeenCalledWith("/agents/a1/amendments/0");
  });
});
