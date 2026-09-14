/**
 * Bug sheet 2026-09-14 rows 17-19/52 — AgentDetail ownership controls.
 *
 * Mutations (pause/resume/promote/rollback/retest/delete, config, prompt,
 * amendments, parent) are allowed for admins and the owner of a personal
 * agent. Other viewers keep Run and Chat. Only admins get the visibility
 * control, which PATCHes `visibility`.
 */
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockGet = vi.fn();
const mockPost = vi.fn();
const mockPatch = vi.fn();
const mockDelete = vi.fn();

let mockUser: Record<string, unknown> = {};

vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => ({ user: mockUser, isAuthenticated: true }),
}));

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
    listAll: () => Promise.resolve([]),
  },
  extractApiError: (_e: unknown, fallback: string) => fallback,
}));

vi.mock("@/components/KillSwitch", () => ({
  default: () => <button type="button">Kill Switch</button>,
}));

vi.mock("@/components/ChatPanel", () => ({
  default: () => <div data-testid="chat-panel" />,
}));

vi.mock("react-helmet-async", () => ({
  Helmet: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

import AgentDetail from "@/pages/AgentDetail";

const OWNER_ID = "u-cfo-owner";

const PERSONAL_AGENT = {
  id: "a1",
  name: "Ledger Agent",
  agent_type: "bookkeeper",
  domain: "finance",
  status: "shadow",
  visibility: "personal",
  owner_user_id: OWNER_ID,
  version: "1",
  confidence_floor: 0.88,
  shadow_sample_count: 3,
  shadow_accuracy_current: 0.9,
  max_retries: 3,
  authorized_tools: [],
  connector_ids: [],
  llm_model: "gemini-2.5-flash",
  hitl_condition: "confidence < 0.88",
  cost_controls: { monthly_cap_usd: 10, cost_current_usd: 0 },
  system_prompt_text: "You are a bookkeeper.",
  created_at: "2026-09-14T00:00:00Z",
};

let currentAgent: Record<string, unknown> = PERSONAL_AGENT;

function setUser(userId: string, role: string, domain: string) {
  mockUser = { user_id: userId, email: `${role}@example.com`, name: role, role, domain, tenant_id: "t1" };
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

async function openTab(name: string) {
  fireEvent.click(screen.getByRole("button", { name }));
}

describe("AgentDetail ownership controls (bug sheet 2026-09-14)", () => {
  beforeEach(() => {
    mockGet.mockReset();
    mockPost.mockReset();
    mockPatch.mockReset();
    mockDelete.mockReset();
    currentAgent = PERSONAL_AGENT;
    mockGet.mockImplementation((url: string) => {
      if (url === "/agents/a1") return Promise.resolve({ data: currentAgent });
      if (url === "/agents/a1/amendments") return Promise.resolve({ data: { amendments: ["Always cite the invoice id"] } });
      if (url.startsWith("/agents/a1/feedback")) return Promise.resolve({ data: { feedback: [] } });
      return Promise.resolve({ data: {} });
    });
    mockPatch.mockResolvedValue({ data: {} });
    mockPost.mockResolvedValue({ data: {} });
    vi.spyOn(window, "confirm").mockReturnValue(true);
  });

  it("non-owner viewer of a personal agent: mutation controls hidden, run and chat kept", async () => {
    setUser("u-someone-else", "cfo", "finance");
    renderPage();
    await screen.findByText("Ledger Agent");

    expect(screen.getByTestId("agent-visibility-badge")).toHaveTextContent(/^Personal$/);
    expect(screen.getByTestId("agent-readonly-note")).toBeInTheDocument();
    for (const label of ["Promote", "Rollback", "Kill Switch", "Resume", "Delete Agent"]) {
      expect(screen.queryByRole("button", { name: label })).not.toBeInTheDocument();
    }
    expect(screen.queryByTestId("agent-visibility-toggle")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run Agent" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Chat with Agent" })).toBeInTheDocument();

    // Overview: parent edit hidden.
    expect(screen.queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();

    await openTab("config");
    await screen.findByText("Agent Configuration");
    expect(screen.queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();

    await openTab("prompt");
    await screen.findByText("System Prompt");
    expect(screen.getByText("View only")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();

    await openTab("shadow");
    await screen.findByText("Shadow Sample Progress");
    expect(screen.queryByRole("button", { name: "Retest" })).not.toBeInTheDocument();

    await openTab("learning");
    await screen.findByText("Always cite the invoice id");
    expect(screen.queryByRole("button", { name: "Analyze Feedback" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Dismiss" })).not.toBeInTheDocument();
  });

  it("owner: management controls present, domain locked in config, no visibility control", async () => {
    setUser(OWNER_ID, "cfo", "finance");
    renderPage();
    await screen.findByText("Ledger Agent");

    expect(screen.getByTestId("agent-visibility-badge")).toHaveTextContent("Personal · owned by you");
    expect(screen.queryByTestId("agent-readonly-note")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Promote" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Rollback" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Kill Switch" })).toBeInTheDocument();
    expect(screen.queryByTestId("agent-visibility-toggle")).not.toBeInTheDocument();

    await openTab("config");
    await screen.findByText("Agent Configuration");
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    const domain = screen.getByTestId("config-domain") as HTMLSelectElement;
    expect(domain).toBeDisabled();
    expect(Array.from(domain.options).map((o) => o.value)).toEqual(["finance"]);

    await openTab("shadow");
    expect(await screen.findByRole("button", { name: "Retest" })).toBeInTheDocument();

    await openTab("learning");
    await screen.findByText("Always cite the invoice id");
    expect(screen.getByRole("button", { name: "Analyze Feedback" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Dismiss" })).toBeInTheDocument();
  });

  it("admin: visibility control PATCHes visibility and the config domain is editable", async () => {
    setUser("u-admin", "admin", "all");
    renderPage();
    await screen.findByText("Ledger Agent");

    expect(screen.getByTestId("agent-visibility-badge")).toHaveTextContent(/^Personal$/);
    const toggle = screen.getByTestId("agent-visibility-toggle");
    expect(toggle).toHaveTextContent("Share with tenant");
    await act(async () => {
      fireEvent.click(toggle);
    });
    await waitFor(() => expect(mockPatch).toHaveBeenCalledWith("/agents/a1", { visibility: "tenant" }));

    await openTab("config");
    await screen.findByText("Agent Configuration");
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    const domain = screen.getByTestId("config-domain") as HTMLSelectElement;
    expect(domain).not.toBeDisabled();
    fireEvent.change(domain, { target: { value: "hr" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Save Config" }));
    });
    await waitFor(() =>
      expect(mockPatch).toHaveBeenCalledWith("/agents/a1", expect.objectContaining({ domain: "hr" })),
    );
  });

  it("admin on a shared agent is offered Make personal", async () => {
    setUser("u-admin", "admin", "all");
    currentAgent = { ...PERSONAL_AGENT, visibility: "tenant", owner_user_id: null };
    renderPage();
    await screen.findByText("Ledger Agent");
    expect(screen.getByTestId("agent-visibility-badge")).toHaveTextContent("Shared");
    expect(screen.getByTestId("agent-visibility-toggle")).toHaveTextContent("Make personal");
  });
});
