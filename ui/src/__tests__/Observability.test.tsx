// SPDX-License-Identifier: Apache-2.0
/**
 * Observability page: the stored runs, the waterfall of one run, and the live workload.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { HelmetProvider } from "react-helmet-async";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockGet = vi.fn();

vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    post: vi.fn(),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  },
  extractApiError: (_err: unknown, fallback: string) => fallback,
}));

import Observability from "@/pages/Observability";

const TRACE = "4bf92f3577b34da6a3ce929d0e0e4736";
const RUN = "00f067aa0ba902b7";

const RUNS = {
  enabled: false,
  tracing: true,
  runs: [
    {
      trace_id: TRACE,
      run_id: RUN,
      span_id: RUN,
      name: "agenticorg.agent.run",
      agent_id: "a1",
      status: "unset",
      run_status: "completed",
      started_at: "2026-10-02T10:00:00+00:00",
      duration_ms: 2400,
      provider: "openai",
      model: "gpt-4o",
      tokens: 321,
      correlation_id: "req-1",
    },
  ],
};

const DETAIL = {
  run_id: RUN,
  trace_id: TRACE,
  started_at: "2026-10-02T10:00:00+00:00",
  duration_ms: 2400,
  spans: [
    {
      span_id: "00f067aa0ba902b7",
      parent_span_id: null,
      name: "agenticorg.agent.run",
      kind: "internal",
      status: "unset",
      agent_id: "a1",
      offset_ms: 0,
      duration_ms: 2400,
      attributes: { "agent.run.status": "completed", "llm.provider": "openai", "llm.model": "gpt-4o" },
      events: [{ name: "model_gateway.decision", offset_ms: 1, attributes: { provider: "openai", model: "gpt-4o", reason: "policy" } }],
    },
    {
      span_id: "1111111111111111",
      parent_span_id: "00f067aa0ba902b7",
      name: "agenticorg.agent.reason",
      kind: "client",
      status: "unset",
      agent_id: "a1",
      offset_ms: 100,
      duration_ms: 1200,
      attributes: { "llm.provider": "openai", "llm.model": "gpt-4o", "llm.input_tokens": 200, "llm.output_tokens": 121 },
      events: [{ name: "guardrail.outcome", offset_ms: 5, attributes: { stage: "input", detector: "injection", action: "flag", applied: true } }],
    },
    {
      span_id: "2222222222222222",
      parent_span_id: "00f067aa0ba902b7",
      name: "agenticorg.tool.call",
      kind: "client",
      status: "error",
      agent_id: "a1",
      offset_ms: 1400,
      duration_ms: 600,
      attributes: { "tool.name": "list_ledgers", "connector.id": "tally", "tool.outcome": "error" },
      events: [],
    },
  ],
};

const WORKLOAD = {
  generated_at: "2026-10-02T10:05:00+00:00",
  tracing_enabled: true,
  timeline_enabled: false,
  reviews: { pending: 4, overdue: 1, soonest_due_at: "2026-10-02T10:07:05+00:00", soonest_seconds_left: 125, error: null },
  runs: { window_hours: 1, runs: 7, by_status: { completed: 6, guardrail_blocked: 1 }, p50_duration_ms: 1800, error: null },
  model_calls: { window_hours: 1, calls: 12, failed: 1, p50_latency_ms: 900, error: null },
  guardrails: { window_hours: 1, blocked: null, transformed: null, error: "ProgrammingError" },
};

function renderPage() {
  return render(
    <HelmetProvider>
      <MemoryRouter>
        <Observability />
      </MemoryRouter>
    </HelmetProvider>,
  );
}

beforeEach(() => {
  mockGet.mockReset();
  mockGet.mockImplementation((url: string) => {
    if (url === "/observability/runs") return Promise.resolve({ data: RUNS });
    if (url === `/observability/runs/${RUN}`) return Promise.resolve({ data: DETAIL });
    if (url === "/observability/workload") return Promise.resolve({ data: WORKLOAD });
    return Promise.reject(new Error(`unexpected ${url}`));
  });
});

describe("Observability page", () => {
  it("lists the stored runs and says when recording is off", async () => {
    renderPage();
    await screen.findByTestId(`trace-row-${RUN}`);
    expect(screen.getByTestId("timeline-off").textContent).toContain("AGENTICORG_TRACING_TIMELINE_ENABLED");
    const row = screen.getByTestId(`trace-row-${RUN}`);
    expect(row.textContent).toContain("completed");
    expect(row.textContent).toContain("openai/gpt-4o");
    expect(row.textContent).toContain("321");
    expect(row.textContent).toContain("2.40 s");
    expect(mockGet).toHaveBeenCalledWith("/observability/runs", { params: { limit: "50" } });
  });

  it("opens the debugging console from a run whose root span names its thread", async () => {
    const thread = "tenant:22222222-2222-4222-8222-222222222222:run:abc";
    const detail = {
      ...DETAIL,
      spans: [{ ...DETAIL.spans[0], attributes: { ...DETAIL.spans[0].attributes, "agent.id": "a1", "agent.thread_id": thread } }, DETAIL.spans[1]],
    };
    const steps = { thread_id: thread, total: 1, paused: false, next: [], pseudonymised: false, steps: [] };
    mockGet.mockImplementation((url: string) => {
      if (url.includes("/debug/threads/")) return Promise.resolve({ data: steps });
      if (url.startsWith("/observability/runs/")) return Promise.resolve({ data: detail });
      if (url === "/observability/runs") return Promise.resolve({ data: RUNS });
      return Promise.resolve({ data: WORKLOAD });
    });
    render(<Observability />);
    fireEvent.click(await screen.findByTestId(`trace-row-${RUN}`));
    fireEvent.click(await screen.findByTestId("debugger-open"));
    await screen.findByTestId("run-debugger");
    expect(mockGet).toHaveBeenCalledWith(`/agents/a1/debug/threads/${encodeURIComponent(thread)}`);
    expect(screen.getByTestId("debugger-thread").textContent).toBe(thread);
  });

  it("shows the waterfall of the selected run with nested spans, bars and events", async () => {
    renderPage();
    fireEvent.click(await screen.findByTestId(`trace-row-${RUN}`));
    await screen.findByTestId("waterfall");
    expect(screen.getByTestId("waterfall").textContent).toContain(`run ${RUN}`);
    const reason = screen.getByTestId("span-row-1111111111111111");
    expect(reason.textContent).toContain("agent.reason");
    expect(reason.textContent).toContain("200 in / 121 out");
    expect(reason.textContent).toContain("1.20 s");
    expect(reason.querySelector("[data-testid='span-bar']")?.getAttribute("style")).toContain("width: 50%");
    expect(reason.querySelector("[style*='padding-left: 12px']")).not.toBeNull();
    const tool = screen.getByTestId("span-row-2222222222222222");
    expect(tool.textContent).toContain("tally:list_ledgers error");
    const events = screen.getAllByTestId("span-event").map((e) => e.textContent);
    expect(events).toContain("gateway openai/gpt-4o policy");
    expect(events).toContain("guardrail input injection flag applied");
  });

  it("filters the list by agent id", async () => {
    renderPage();
    await screen.findByTestId("traces-table");
    fireEvent.change(screen.getByTestId("trace-agent-filter"), { target: { value: "a1" } });
    fireEvent.click(screen.getByTestId("traces-refresh"));
    await waitFor(() => expect(mockGet).toHaveBeenCalledWith("/observability/runs", { params: { limit: "50", agent_id: "a1" } }));
  });

  it("shows the workload with the review countdown and a part that could not be read", async () => {
    renderPage();
    fireEvent.click(screen.getByTestId("tab-workload"));
    await screen.findByTestId("workload-reviews");
    expect(screen.queryByTestId("workload-queues")).toBeNull();
    expect(screen.getByTestId("reviews-pending").textContent).toBe("4");
    expect(screen.getByTestId("reviews-overdue").textContent).toBe("1");
    expect(screen.getByTestId("reviews-countdown").textContent).toBe("2m 05s");
    expect(screen.getByTestId("runs-total").textContent).toBe("7");
    expect(screen.getByTestId("model-calls-total").textContent).toBe("12");
    expect(screen.getByTestId("workload-guardrails").textContent).toContain("Unavailable (ProgrammingError)");
    expect(screen.getByTestId("workload-runs").textContent).toContain("Run timelines are off");
  });

  it("reports a failed load instead of an empty page", async () => {
    mockGet.mockRejectedValue(new Error("boom"));
    renderPage();
    expect((await screen.findByRole("alert")).textContent).toContain("Failed to load run timelines.");
  });
});

describe("Observability page: the Checks tab", () => {
  it("is absent while synthetic checks are off or unreadable", async () => {
    renderPage();
    await screen.findByTestId(`trace-row-${RUN}`);
    expect(screen.queryByTestId("tab-checks")).not.toBeInTheDocument();
    mockGet.mockImplementation((url: string) => {
      if (url === "/observability/runs") return Promise.resolve({ data: RUNS });
      if (url === "/observability/checks") {
        return Promise.resolve({ data: { enabled: false, kinds: [], limit: 20, checks: [] } });
      }
      return Promise.reject(new Error(`unexpected ${url}`));
    });
    renderPage();
    await waitFor(() => expect(mockGet).toHaveBeenCalledWith("/observability/checks"));
    expect(screen.queryByTestId("tab-checks")).not.toBeInTheDocument();
  });

  it("appears where synthetic checks are switched on", async () => {
    mockGet.mockImplementation((url: string) => {
      if (url === "/observability/runs") return Promise.resolve({ data: RUNS });
      if (url === "/observability/checks") {
        return Promise.resolve({ data: { enabled: true, kinds: ["model"], limit: 20, checks: [] } });
      }
      return Promise.reject(new Error(`unexpected ${url}`));
    });
    renderPage();
    fireEvent.click(await screen.findByTestId("tab-checks"));
    expect(await screen.findByTestId("checks-panel")).toBeInTheDocument();
  });
});
