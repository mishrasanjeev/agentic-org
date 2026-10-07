// SPDX-License-Identifier: Apache-2.0
/**
 * Agent catalogue: the filters it sends, the rows it shows, the templates and the off state.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { HelmetProvider } from "react-helmet-async";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockGet = vi.fn();

vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  },
  extractApiError: (_e: unknown, fallback: string) => fallback,
}));

import AgentCatalogue from "@/pages/AgentCatalogue";

const ENTRY = {
  agent_id: "a1",
  name: "Claims decider",
  agent_type: "claims",
  domain: "ops",
  status: "active",
  purpose: "Settle small claims fast",
  risk_tier: "high",
  use_case: "motor claims",
  channels: ["chat", "api"],
  state: "published",
  environment: "production",
};
const LISTS = { states: ["draft", "review", "approved", "published"], risk_tiers: ["low", "high"], channels: ["api", "chat"] };
const TEMPLATE = {
  pack: "banking",
  pack_display_name: "Banking Pack",
  installable: true,
  install_disabled_reason: "",
  agent_type: "kyc_reviewer",
  name: "Kyc Reviewer",
  domain: "ops",
  description: "Check documents against the KYC checklist.",
  model: "gpt-4o",
  tools: ["knowledge_base_search"],
  hitl_condition: "outcome != 'clear'",
  confidence_floor: 0.92,
  compliance: ["KYC_AML"],
};

function renderPage() {
  return render(
    <HelmetProvider>
      <MemoryRouter>
        <AgentCatalogue />
      </MemoryRouter>
    </HelmetProvider>,
  );
}

describe("AgentCatalogue", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("lists the entries with their card fields and sends the filters", async () => {
    mockGet.mockImplementation((path: string) =>
      path === "/agent-registry"
        ? Promise.resolve({ data: { entries: [ENTRY], ...LISTS } })
        : Promise.reject(new Error(`unexpected ${path}`)),
    );
    renderPage();
    const row = await screen.findByTestId("catalogue-row-a1");
    expect(row).toHaveTextContent("Claims decider");
    expect(row).toHaveTextContent("motor claims");
    expect(row).toHaveTextContent("chat, api");
    expect(row).toHaveTextContent("published");
    expect(row).toHaveTextContent("production");
    expect(mockGet).toHaveBeenCalledWith("/agent-registry", { params: {} });
    fireEvent.change(screen.getByTestId("catalogue-domain"), { target: { value: "finance" } });
    fireEvent.change(screen.getByTestId("catalogue-q"), { target: { value: " loans " } });
    await waitFor(() => expect(mockGet).toHaveBeenCalledWith("/agent-registry", { params: { q: "loans", domain: "finance" } }));
  });

  it("shows the templates from the packs on request", async () => {
    mockGet.mockImplementation((path: string) => {
      if (path === "/agent-registry") return Promise.resolve({ data: { entries: [], ...LISTS } });
      if (path === "/agent-registry/templates") return Promise.resolve({ data: { templates: [TEMPLATE] } });
      return Promise.reject(new Error(`unexpected ${path}`));
    });
    renderPage();
    expect(await screen.findByText("No agents match.")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("catalogue-templates-toggle"));
    const template = await screen.findByTestId("template-banking-kyc_reviewer");
    expect(template).toHaveTextContent("Kyc Reviewer");
    expect(template).toHaveTextContent("Banking Pack · ops · gpt-4o · floor 92%");
    expect(template).toHaveTextContent("Tools: knowledge_base_search");
  });

  it("says so where the registry is off", async () => {
    mockGet.mockRejectedValue({ response: { status: 409 } });
    renderPage();
    expect(await screen.findByTestId("catalogue-off")).toHaveTextContent("off in this deployment");
    expect(screen.queryByTestId("catalogue-table")).not.toBeInTheDocument();
  });
});
