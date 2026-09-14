/**
 * Bug sheet 2026-09-14 #34 — AgentDetail Config tab provider/model picker.
 *
 * The tab hardcoded model ids ("claude-opus-4-5", "claude-sonnet-4-5", ...)
 * that do not exist in the backend catalog. It now reads the shared
 * registry, filters models by the chosen provider, sends `llm.provider`
 * with the model, and falls back to text inputs when the registry fails.
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

const REGISTRY = {
  llm: {
    anthropic: [
      { model: "claude-sonnet-4-5-20250929", context_window: 200000, max_output_tokens: 64000, supports_tools: true, supports_vision: true, notes: "" },
    ],
    gemini: [
      { model: "gemini-2.5-flash", context_window: 1048576, max_output_tokens: 8192, supports_tools: true, supports_vision: false, notes: "" },
    ],
    openai: [
      { model: "gpt-4o", context_window: 128000, max_output_tokens: 16384, supports_tools: true, supports_vision: true, notes: "" },
    ],
    openai_compatible: [
      { model: "*", context_window: 128000, max_output_tokens: 16384, supports_tools: true, supports_vision: false, notes: "" },
    ],
    // Tenant-settings-only provider: the agent API rejects it, so it must not be offered.
    azure_openai: [
      { model: "deployment:gpt-4o", context_window: 128000, max_output_tokens: 16384, supports_tools: true, supports_vision: true, notes: "" },
    ],
  },
  embedding: {},
};

// Legacy row: model saved before agents.llm_provider existed.
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
  llm_model: "gpt-4o",
  llm_provider: null,
  llm_config: { model: "gpt-4o" },
  hitl_condition: "confidence < 0.88",
  cost_controls: { monthly_cap_usd: 10, cost_current_usd: 0 },
  system_prompt_text: "You are a bookkeeper.",
  created_at: "2026-09-14T00:00:00Z",
};

function routeGet(url: string, registryOk: boolean) {
  if (url === "/agents/a1") return Promise.resolve({ data: AGENT });
  if (url === "/tenant-ai-settings/registry") {
    return registryOk ? Promise.resolve({ data: REGISTRY }) : Promise.reject(new Error("403"));
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

async function openConfigEditor() {
  renderPage();
  await screen.findByText("Ledger Agent");
  fireEvent.click(screen.getByRole("button", { name: "config" }));
  await screen.findByText("Agent Configuration");
  await waitFor(() => expect(mockGet).toHaveBeenCalledWith("/tenant-ai-settings/registry"));
  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
}

describe("AgentDetail Config tab LLM picker (bug sheet 2026-09-14 #34)", () => {
  beforeEach(() => {
    mockGet.mockReset();
    mockPost.mockReset();
    mockPatch.mockReset();
    mockDelete.mockReset();
    mockPatch.mockResolvedValue({ data: {} });
    mockPost.mockResolvedValue({ data: {} });
  });

  it("drives provider + model from the registry, filters by provider and sends llm.provider", async () => {
    mockGet.mockImplementation((url: string) => routeGet(url, true));
    await openConfigEditor();

    // Legacy row: provider inferred from the catalog entry that lists gpt-4o.
    const provider = (await screen.findByTestId("llm-provider")) as HTMLSelectElement;
    expect(provider.tagName).toBe("SELECT");
    expect(provider.value).toBe("openai");
    expect(Array.from(provider.options).map((o) => o.value)).toEqual(
      expect.arrayContaining(["anthropic", "gemini", "openai", "openai_compatible"]),
    );
    expect(Array.from(provider.options).map((o) => o.value)).not.toContain("azure_openai");

    fireEvent.change(provider, { target: { value: "anthropic" } });

    const model = screen.getByTestId("llm-model") as HTMLSelectElement;
    expect(model.tagName).toBe("SELECT");
    const modelIds = Array.from(model.options).map((o) => o.value);
    expect(modelIds).toEqual(["claude-sonnet-4-5-20250929"]);
    // The old hardcoded, non-catalog ids are gone.
    expect(modelIds).not.toContain("claude-opus-4-5");
    expect(modelIds).not.toContain("claude-sonnet-4-5");
    expect(model.value).toBe("claude-sonnet-4-5-20250929");

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Save Config" }));
    });
    await waitFor(() => expect(mockPatch).toHaveBeenCalledTimes(1));
    expect(mockPatch).toHaveBeenCalledWith(
      "/agents/a1",
      expect.objectContaining({ llm: { model: "claude-sonnet-4-5-20250929", provider: "anthropic" } }),
    );
  });

  it("openai_compatible switches the model to a free-text input", async () => {
    mockGet.mockImplementation((url: string) => routeGet(url, true));
    await openConfigEditor();

    const provider = (await screen.findByTestId("llm-provider")) as HTMLSelectElement;
    fireEvent.change(provider, { target: { value: "openai_compatible" } });

    const model = screen.getByTestId("llm-model") as HTMLInputElement;
    expect(model.tagName).toBe("INPUT");
    expect(document.querySelector('option[value="*"]')).toBeNull();
    fireEvent.change(model, { target: { value: "my-local-llm" } });

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Save Config" }));
    });
    await waitFor(() => expect(mockPatch).toHaveBeenCalledTimes(1));
    expect(mockPatch).toHaveBeenCalledWith(
      "/agents/a1",
      expect.objectContaining({ llm: { model: "my-local-llm", provider: "openai_compatible" } }),
    );
  });

  it("falls back to text inputs (never an empty select) when the registry call fails", async () => {
    mockGet.mockImplementation((url: string) => routeGet(url, false));
    await openConfigEditor();

    const provider = (await screen.findByTestId("llm-provider")) as HTMLInputElement;
    expect(provider.tagName).toBe("INPUT");
    const model = screen.getByTestId("llm-model") as HTMLInputElement;
    expect(model.tagName).toBe("INPUT");
    expect(model.value).toBe("gpt-4o");
    expect(document.querySelectorAll("select[data-testid='llm-model']").length).toBe(0);
  });
});
