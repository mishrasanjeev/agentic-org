/**
 * Bug sheet 2026-09-14 rows 17-19/52 — AgentCreate personal agents.
 *
 * Non-admin agent-creator roles create personal agents: they see a
 * personal-agent note, no visibility picker, and a domain picker locked to
 * their own domain (developers keep the full list). Admins choose
 * visibility, which is sent as `visibility` in the POST body. The backend
 * stays the authorization boundary; its 403 detail is shown verbatim.
 */
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockGet = vi.fn();
const mockPost = vi.fn();
const mockNavigate = vi.fn();

let mockUser: Record<string, unknown> = {};

vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => ({ user: mockUser, isAuthenticated: true }),
}));

vi.mock("react-router", async () => {
  const actual = await vi.importActual<typeof import("react-router")>("react-router");
  return { ...actual, useNavigate: () => mockNavigate };
});

vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  },
  agentsApi: { listAll: () => Promise.resolve([]) },
  promptTemplatesApi: { list: () => Promise.resolve({ data: [] }) },
  extractApiError: (e: unknown, fallback: string) =>
    (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? fallback,
}));

import AgentCreate from "@/pages/AgentCreate";

function setUser(role: string, domain: string) {
  mockUser = { user_id: `u-${role}`, email: `${role}@example.com`, name: role, role, domain, tenant_id: "t1" };
}

function renderPage() {
  return render(
    <MemoryRouter>
      <AgentCreate />
    </MemoryRouter>,
  );
}

async function openPersonaStep() {
  renderPage();
  fireEvent.click(screen.getByTestId("skip-to-manual"));
  return (await screen.findByTestId("agent-domain")) as HTMLSelectElement;
}

async function walkToReviewAndCreate() {
  fireEvent.change(screen.getByPlaceholderText("e.g. Priya, Arjun, Maya"), { target: { value: "Ledger Bot" } });
  fireEvent.click(screen.getByRole("button", { name: "Next" }));
  await screen.findByText("Step 2: Role");
  fireEvent.click(screen.getByRole("button", { name: "Next" }));
  await screen.findByText("Step 3: Prompt");
  fireEvent.change(screen.getByPlaceholderText(/You are the/), { target: { value: "You reconcile ledgers." } });
  fireEvent.click(screen.getByRole("button", { name: "Next" }));
  await screen.findByText("Step 4: Behavior");
  fireEvent.click(screen.getByRole("button", { name: "Next" }));
  await screen.findByText("Step 5: Review");
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Create as Shadow" }));
  });
}

describe("AgentCreate ownership (bug sheet 2026-09-14)", () => {
  beforeEach(() => {
    mockGet.mockReset();
    mockPost.mockReset();
    mockNavigate.mockReset();
    mockGet.mockImplementation((url: string) => {
      if (url === "/connectors/registry") return Promise.resolve({ data: { items: [] } });
      if (url === "/tenant-ai-settings/registry") return Promise.reject(new Error("unavailable"));
      return Promise.resolve({ data: { tools: [] } });
    });
    mockPost.mockResolvedValue({ data: { agent_id: "new-agent" } });
  });

  it("CFO: domain locked to finance, personal note shown, no visibility select", async () => {
    setUser("cfo", "finance");
    const domain = await openPersonaStep();

    expect(screen.getByTestId("personal-agent-note")).toHaveTextContent(
      "Personal agent — only you and tenant admins can see it",
    );
    expect(domain).toBeDisabled();
    expect(domain.value).toBe("finance");
    expect(Array.from(domain.options).map((o) => o.value)).toEqual(["finance"]);
    expect(screen.queryByTestId("agent-visibility")).not.toBeInTheDocument();

    await walkToReviewAndCreate();
    await waitFor(() => expect(mockPost).toHaveBeenCalledTimes(1));
    const body = mockPost.mock.calls[0][1] as Record<string, unknown>;
    expect(body.domain).toBe("finance");
    // Non-admins never send visibility; the backend makes the agent personal.
    expect(body.visibility).toBeUndefined();
  });

  it("CFO: a 403 from the backend is surfaced to the user", async () => {
    setUser("cfo", "finance");
    mockPost.mockRejectedValue({ response: { status: 403, data: { detail: "Only admins can create tenant agents" } } });
    await openPersonaStep();
    await walkToReviewAndCreate();
    expect(await screen.findByText("Only admins can create tenant agents")).toBeInTheDocument();
    expect(mockNavigate).not.toHaveBeenCalledWith("/dashboard/agents/new-agent");
  });

  it("admin: visibility select defaults to shared and the choice is sent in the POST body", async () => {
    setUser("admin", "all");
    const domain = await openPersonaStep();

    expect(screen.queryByTestId("personal-agent-note")).not.toBeInTheDocument();
    expect(domain).not.toBeDisabled();
    const visibility = screen.getByTestId("agent-visibility") as HTMLSelectElement;
    expect(visibility.value).toBe("tenant");
    fireEvent.change(visibility, { target: { value: "personal" } });

    await walkToReviewAndCreate();
    await waitFor(() => expect(mockPost).toHaveBeenCalledTimes(1));
    expect(mockPost).toHaveBeenCalledWith("/agents", expect.objectContaining({ visibility: "personal" }));
  });

  it("developer: domain is not locked and the full domain list is offered", async () => {
    setUser("developer", "");
    const domain = await openPersonaStep();

    expect(screen.getByTestId("personal-agent-note")).toHaveTextContent("You can build it in any domain.");
    expect(domain).not.toBeDisabled();
    expect(Array.from(domain.options).map((o) => o.value)).toEqual(
      expect.arrayContaining(["finance", "hr", "marketing", "ops", "backoffice", "comms"]),
    );
    expect(screen.queryByTestId("agent-visibility")).not.toBeInTheDocument();
  });

  it("domain_lead: domain locked to the domain on the account", async () => {
    setUser("domain_lead", "hr");
    const domain = await openPersonaStep();
    expect(domain).toBeDisabled();
    expect(domain.value).toBe("hr");
  });
});
