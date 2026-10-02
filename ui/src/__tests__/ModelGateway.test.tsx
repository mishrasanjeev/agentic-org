// SPDX-License-Identifier: Apache-2.0
/**
 * Model gateway console: the lists render from the admin endpoints, a policy
 * is created with the payload the API expects, enable and delete act on the
 * right rows, the dry run reports the decision, and the records and cost tabs
 * load on demand.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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
  extractApiError: (_e: unknown, fallback: string) => fallback,
}));

import ModelGateway from "@/pages/ModelGateway";

const STATUS = { enabled: true, active_policies: [{}], active_access_policies: [], active_limits: [{}] };
const POLICIES = [
  {
    id: "p1", name: "finance-in-house", priority: 10, enabled: true, use_case: null, sensitivity: null, agent_id: null,
    business_unit: "finance", language: null, provider: "openai_compatible", model: null, tier: null, targets: null,
    allowed_providers: ["openai_compatible", "ollama"], in_region_only: false, cost_aware: false, max_failure_rate: null, reason: "",
  },
  {
    id: "p2", name: "drafting-cheapest", priority: 30, enabled: false, use_case: "agent_run", sensitivity: null, agent_id: null,
    business_unit: null, language: null, provider: null, model: null, tier: null,
    targets: [{ provider: "openai", model: "gpt-4o-mini", weight: 1 }, { provider: "gemini", model: "gemini-2.5-flash", weight: 1 }],
    allowed_providers: null, in_region_only: true, cost_aware: true, max_failure_rate: 0.02, reason: "",
  },
];
const ACCESS = [
  {
    id: "a1", name: "frontier-denied", priority: 20, enabled: true, use_case: null, sensitivity: null, agent_id: null, business_unit: null,
    application: null, principal: null, provider: null, model: "gpt-4o", effect: "deny", allowed_providers: null, allowed_models: null, reason: "",
  },
];
const LIMITS = [{ id: "l1", provider: "openai", model: "gpt-4o", enabled: true, max_concurrency: 8, requests_per_minute: null, reason: "" }];
const RECORDS = [
  {
    id: "r1", correlation_id: "req-1", use_case: "agent_run", agent_id: "a1", provider: "gemini", model: "gemini-2.5-flash",
    requested_model: "gpt-4o", fallback_from: null, outcome: "completed", error_type: null, latency_ms: 120, tokens: 15,
    cost_usd: 0.0001, signed: true, created_at: "2026-10-02T12:00:00Z",
  },
  {
    id: "r2", correlation_id: "req-2", use_case: "completion", agent_id: null, provider: "openai", model: "gpt-4o",
    requested_model: "gpt-4o", fallback_from: null, outcome: "failed", error_type: "TimeoutError", latency_ms: 9000, tokens: 0,
    cost_usd: 0, signed: false, created_at: "2026-10-02T12:01:00Z",
  },
];
const COSTS = {
  window_hours: 24,
  models: [
    { provider: "gemini", model: "gemini-2.5-flash", list_price: { input_per_million: 0.075, output_per_million: 0.3, source: "list" }, blended_per_million_usd: 0.1313, observed: { calls: 10, failures: 1, failure_rate: 0.1, avg_latency_ms: 300, avg_cost_usd: 0.001, total_cost_usd: 0.01 } },
    { provider: "openai_compatible", model: "in-house", list_price: null, blended_per_million_usd: null, observed: null },
  ],
};

function mockRoutes() {
  mockGet.mockImplementation((url: string) => {
    if (url === "/model-gateway/status") return Promise.resolve({ data: STATUS });
    if (url === "/model-gateway/policies") return Promise.resolve({ data: POLICIES });
    if (url === "/model-gateway/access-policies") return Promise.resolve({ data: ACCESS });
    if (url === "/model-gateway/limits") return Promise.resolve({ data: LIMITS });
    if (url === "/model-gateway/records") return Promise.resolve({ data: RECORDS });
    if (url === "/model-gateway/costs") return Promise.resolve({ data: COSTS });
    return Promise.resolve({ data: {} });
  });
}

describe("Model gateway console", () => {
  beforeEach(() => {
    mockGet.mockReset();
    mockPost.mockReset();
    mockPatch.mockReset();
    mockDelete.mockReset();
    mockRoutes();
    mockPost.mockResolvedValue({ data: {} });
    mockPatch.mockResolvedValue({ data: {} });
    mockDelete.mockResolvedValue({ data: {} });
    vi.spyOn(window, "confirm").mockReturnValue(true);
  });

  it("renders the status and the routing policies with their match and route summaries", async () => {
    render(<ModelGateway />);
    expect(await screen.findByText("Gateway on")).toBeTruthy();
    expect(screen.getByTestId("gateway-status").textContent).toContain("1 routing, 0 access, 1 limits active");
    const row = screen.getByTestId("policy-finance-in-house");
    expect(row.textContent).toContain("unit finance");
    expect(row.textContent).toContain("openai_compatible");
    expect(row.textContent).toContain("openai_compatible, ollama");
    const split = screen.getByTestId("policy-drafting-cheapest");
    expect(split.textContent).toContain("cheapest healthy of openai/gpt-4o-mini×1, gemini/gemini-2.5-flash×1");
    expect(split.textContent).toContain("disabled");
    expect(split.textContent).toContain("in region");
  });

  it("creates a routing policy with the payload the API expects", async () => {
    render(<ModelGateway />);
    await screen.findByTestId("policies-table");
    fireEvent.change(screen.getByTestId("policy-name"), { target: { value: "restricted-in-region" } });
    fireEvent.change(screen.getByTestId("policy-priority"), { target: { value: "20" } });
    fireEvent.change(screen.getByTestId("policy-sensitivity"), { target: { value: "restricted" } });
    fireEvent.change(screen.getByTestId("policy-allowed-providers"), { target: { value: "ollama, vllm" } });
    fireEvent.click(screen.getByTestId("policy-in-region"));
    fireEvent.change(screen.getByTestId("policy-reason"), { target: { value: "restricted data never leaves the region" } });
    fireEvent.click(screen.getByTestId("policy-create"));
    await waitFor(() => expect(mockPost).toHaveBeenCalled());
    const [url, payload] = mockPost.mock.calls[0];
    expect(url).toBe("/model-gateway/policies");
    expect(payload).toMatchObject({
      name: "restricted-in-region",
      priority: 20,
      sensitivity: "restricted",
      allowed_providers: ["ollama", "vllm"],
      in_region_only: true,
      cost_aware: false,
      reason: "restricted data never leaves the region",
    });
    expect(payload.provider).toBeUndefined();
    expect(payload.targets).toBeUndefined();
    expect(await screen.findByRole("status")).toBeTruthy();
  });

  it("keeps a priority of zero and refuses a priority that is not a whole number", async () => {
    render(<ModelGateway />);
    await screen.findByTestId("policies-table");
    fireEvent.change(screen.getByTestId("policy-name"), { target: { value: "first" } });
    fireEvent.change(screen.getByTestId("policy-priority"), { target: { value: "0" } });
    fireEvent.change(screen.getByTestId("policy-provider"), { target: { value: "openai" } });
    fireEvent.click(screen.getByTestId("policy-create"));
    await waitFor(() => expect(mockPost).toHaveBeenCalled());
    expect(mockPost.mock.calls[0][1].priority).toBe(0);
    mockPost.mockClear();
    fireEvent.change(screen.getByTestId("policy-name"), { target: { value: "second" } });
    fireEvent.change(screen.getByTestId("policy-priority"), { target: { value: "ten" } });
    fireEvent.click(screen.getByTestId("policy-create"));
    expect((await screen.findByRole("alert")).textContent).toContain("whole number");
    expect(mockPost).not.toHaveBeenCalled();
    fireEvent.change(screen.getByTestId("policy-name"), { target: { value: "third" } });
    fireEvent.change(screen.getByTestId("policy-priority"), { target: { value: "" } });
    fireEvent.click(screen.getByTestId("policy-create"));
    await waitFor(() => expect(mockPost).toHaveBeenCalled());
    expect(mockPost.mock.calls[0][1].priority).toBe(100);
  });

  it("refuses targets that are not JSON without calling the API", async () => {
    render(<ModelGateway />);
    await screen.findByTestId("policies-table");
    fireEvent.change(screen.getByTestId("policy-name"), { target: { value: "split" } });
    fireEvent.change(screen.getByTestId("policy-targets"), { target: { value: "not json" } });
    fireEvent.click(screen.getByTestId("policy-create"));
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(mockPost).not.toHaveBeenCalled();
  });

  it("uses concrete backend resource routes for every toggle and delete", async () => {
    render(<ModelGateway />);
    await screen.findByTestId("policies-table");
    const row = screen.getByTestId("policy-drafting-cheapest");
    const [enable] = Array.from(row.querySelectorAll("button"));
    expect(enable.textContent).toBe("Enable");
    fireEvent.click(enable);
    await waitFor(() => expect(mockPatch).toHaveBeenCalledWith("/model-gateway/policies/p2", { enabled: true }));
    fireEvent.click(row.querySelectorAll("button")[1]);
    await waitFor(() => expect(mockDelete).toHaveBeenCalledWith("/model-gateway/policies/p2"));

    fireEvent.click(screen.getByTestId("tab-access"));
    const accessRow = await screen.findByTestId("access-frontier-denied");
    fireEvent.click(accessRow.querySelectorAll("button")[0]);
    await waitFor(() => expect(mockPatch).toHaveBeenCalledWith("/model-gateway/access-policies/a1", { enabled: false }));
    fireEvent.click(accessRow.querySelectorAll("button")[1]);
    await waitFor(() => expect(mockDelete).toHaveBeenCalledWith("/model-gateway/access-policies/a1"));

    fireEvent.click(screen.getByTestId("tab-limits"));
    const limitRow = await screen.findByTestId("limit-openai-gpt-4o");
    fireEvent.click(limitRow.querySelectorAll("button")[0]);
    await waitFor(() => expect(mockPatch).toHaveBeenCalledWith("/model-gateway/limits/l1", { enabled: false }));
    fireEvent.click(limitRow.querySelectorAll("button")[1]);
    await waitFor(() => expect(mockDelete).toHaveBeenCalledWith("/model-gateway/limits/l1"));
  });

  it("creates an access policy and a limit", async () => {
    render(<ModelGateway />);
    await screen.findByTestId("policies-table");
    fireEvent.click(screen.getByTestId("tab-access"));
    expect((await screen.findByTestId("access-frontier-denied")).textContent).toContain("deny");
    fireEvent.change(screen.getByTestId("access-name"), { target: { value: "advisory-ok" } });
    fireEvent.change(screen.getByTestId("access-application"), { target: { value: "advisory-app" } });
    fireEvent.change(screen.getByTestId("access-model"), { target: { value: "gpt-4o" } });
    fireEvent.change(screen.getByTestId("access-language"), { target: { value: "hi" } });
    fireEvent.click(screen.getByTestId("access-create"));
    await waitFor(() => expect(mockPost).toHaveBeenCalledWith("/model-gateway/access-policies", expect.objectContaining({ name: "advisory-ok", application: "advisory-app", model: "gpt-4o", effect: "allow", language: "hi", priority: 100 })));
    fireEvent.click(screen.getByTestId("tab-limits"));
    fireEvent.change(await screen.findByTestId("limit-provider"), { target: { value: "ollama" } });
    fireEvent.change(screen.getByTestId("limit-concurrency"), { target: { value: "4" } });
    fireEvent.click(screen.getByTestId("limit-create"));
    await waitFor(() => expect(mockPost).toHaveBeenCalledWith("/model-gateway/limits", { provider: "ollama", model: undefined, max_concurrency: 4, requests_per_minute: undefined, reason: "" }));
  });

  it("runs a dry run and shows the decision", async () => {
    mockPost.mockResolvedValueOnce({ data: { refused: false, enabled: true, decision: { provider: "gemini", model: "gemini-2.5-flash", reason: "policy finance" } } });
    render(<ModelGateway />);
    await screen.findByTestId("policies-table");
    fireEvent.click(screen.getByTestId("tab-dryrun"));
    fireEvent.change(await screen.findByTestId("dryrun-model"), { target: { value: "gpt-4o" } });
    fireEvent.change(screen.getByTestId("dryrun-business-unit"), { target: { value: "finance" } });
    fireEvent.change(screen.getByTestId("dryrun-language"), { target: { value: "hi" } });
    fireEvent.click(screen.getByTestId("dryrun-run"));
    const result = await screen.findByTestId("dryrun-result");
    expect(mockPost).toHaveBeenCalledWith("/model-gateway/evaluate", { use_case: "agent_run", requested_model: "gpt-4o", business_unit: "finance", language: "hi" });
    expect(result.textContent).toContain("routed");
    expect(result.textContent).toContain("gemini-2.5-flash");
  });

  it("loads the records on demand, filters by correlation id and shows the signature check", async () => {
    render(<ModelGateway />);
    await screen.findByTestId("policies-table");
    fireEvent.click(screen.getByTestId("tab-records"));
    const table = await screen.findByTestId("records-table");
    expect(table.textContent).toContain("req-1");
    expect(screen.getByTestId("record-req-1").textContent).toContain("signed");
    expect(screen.getByTestId("record-req-2").textContent).toContain("tampered");
    expect(screen.getByTestId("record-req-2").textContent).toContain("TimeoutError");
    fireEvent.change(screen.getByTestId("records-filter"), { target: { value: "req-1" } });
    fireEvent.click(screen.getByTestId("records-search"));
    await waitFor(() => expect(mockGet).toHaveBeenCalledWith("/model-gateway/records", { params: { limit: "100", correlation_id: "req-1" } }));
  });

  it("loads the cost comparison on demand", async () => {
    render(<ModelGateway />);
    await screen.findByTestId("policies-table");
    fireEvent.click(screen.getByTestId("tab-costs"));
    const table = await screen.findByTestId("costs-table");
    expect(table.textContent).toContain("gemini-2.5-flash");
    expect(screen.getByTestId("cost-gemini-gemini-2.5-flash").textContent).toContain("10.0%");
    expect(screen.getByTestId("cost-openai_compatible-in-house").textContent).toContain("unpriced");
  });

  it("shows the load error", async () => {
    mockGet.mockImplementation(() => Promise.reject(new Error("down")));
    render(<ModelGateway />);
    expect((await screen.findByRole("alert")).textContent).toContain("Failed to load the model gateway");
  });
});
