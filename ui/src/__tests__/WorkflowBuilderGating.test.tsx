// SPDX-License-Identifier: Apache-2.0
/**
 * The visual builder behind its server flag: the create tab and the stored-workflow graph appear only while
 * the server reports the flag on, and the detail page draws the graph the graph route answers.
 */
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

const mockGet = vi.fn();

vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    post: vi.fn(),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  },
  extractApiError: (_e: unknown, fallback: string) => fallback,
}));

import { graphToSteps, stepsToEdges } from "@/components/WorkflowBuilder";
import WorkflowCreate from "@/pages/WorkflowCreate";
import WorkflowDetail from "@/pages/WorkflowDetail";

const WF_ID = "00000000-0000-4000-8000-000000000001";
const GRAPH = {
  nodes: [
    { id: "check", type: "condition", name: "check", summary: "x > 1", on_failure: "halt" },
    { id: "post", type: "agent", name: "post", summary: "ap: post", on_failure: "fallback(manual)" },
    { id: "skip", type: "notify", name: "skip", summary: "notify", on_failure: "halt" },
    { id: "manual", type: "human_in_loop", name: "manual", summary: "ops decides: done", on_failure: "halt" },
  ],
  edges: [
    { source: "check", target: "post", kind: "true", label: "true" },
    { source: "check", target: "skip", kind: "false", label: "false" },
    { source: "check", target: "post", kind: "then", label: "" },
    { source: "check", target: "skip", kind: "then", label: "" },
    { source: "post", target: "manual", kind: "fallback", label: "on failure" },
  ],
};

beforeAll(() => {
  // ReactFlow measures its container; jsdom has no ResizeObserver or DOMMatrix.
  class RO {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = RO;
  (globalThis as unknown as { DOMMatrixReadOnly: unknown }).DOMMatrixReadOnly = class {
    m22 = 1;
    constructor() {}
  };
});

beforeEach(() => {
  mockGet.mockReset();
});

function renderCreate() {
  return render(
    <MemoryRouter>
      <WorkflowCreate />
    </MemoryRouter>,
  );
}

function renderDetail() {
  return render(
    <MemoryRouter initialEntries={[`/workflows/${WF_ID}`]}>
      <Routes>
        <Route path="/workflows/:id" element={<WorkflowDetail />} />
      </Routes>
    </MemoryRouter>,
  );
}

function detailApi(graphAnswer: () => Promise<unknown>) {
  mockGet.mockImplementation((path: string) => {
    if (path === `/workflows/${WF_ID}`) {
      return Promise.resolve({ data: { id: WF_ID, name: "Invoices", version: "1.0.0", is_active: true } });
    }
    if (path === `/workflows/${WF_ID}/runs`) return Promise.resolve({ data: { items: [] } });
    if (path === `/workflows/${WF_ID}/graph`) return graphAnswer();
    return Promise.reject(new Error(`unexpected ${path}`));
  });
}

describe("graphToSteps", () => {
  it("rebuilds dependencies, paths and the failure directive from the graph route", () => {
    const steps = graphToSteps(GRAPH);
    expect(steps.map((s) => s.id)).toEqual(["check", "post", "skip", "manual"]);
    expect(steps[0]).toMatchObject({ type: "condition", true_path: "post", false_path: "skip", depends_on: [] });
    expect(steps[1]).toMatchObject({ depends_on: ["check"], on_failure: "fallback(manual)" });
    const drawn = stepsToEdges(steps).map((e) => `${e.source}>${e.target}:${e.kind}`).sort();
    expect(drawn).toEqual(GRAPH.edges.map((e) => `${e.source}>${e.target}:${e.kind}`).sort());
    expect(graphToSteps(null)).toEqual([]);
    expect(graphToSteps({ nodes: "x", edges: null })).toEqual([]);
  });
});

describe("WorkflowCreate builder tab", () => {
  it("is hidden while the server reports the flag off", async () => {
    mockGet.mockResolvedValue({ data: { enabled: false } });
    renderCreate();
    await waitFor(() => expect(mockGet).toHaveBeenCalledWith("/workflows/builder"));
    expect(screen.getByTestId("tab-template")).toBeInTheDocument();
    expect(screen.queryByTestId("tab-build")).not.toBeInTheDocument();
  });

  it("stays hidden when the flag cannot be read", async () => {
    mockGet.mockRejectedValue(new Error("offline"));
    renderCreate();
    await waitFor(() => expect(mockGet).toHaveBeenCalledWith("/workflows/builder"));
    expect(screen.queryByTestId("tab-build")).not.toBeInTheDocument();
  });

  it("is shown while the flag is on", async () => {
    mockGet.mockResolvedValue({ data: { enabled: true } });
    renderCreate();
    expect(await screen.findByTestId("tab-build")).toBeInTheDocument();
  });
});

describe("WorkflowDetail graph", () => {
  it("draws the stored workflow from the graph route while the flag is on", async () => {
    detailApi(() => Promise.resolve({ data: { workflow_id: WF_ID, enabled: true, graph: GRAPH, errors: [] } }));
    renderDetail();
    expect(await screen.findByTestId("workflow-graph")).toBeInTheDocument();
    expect(mockGet).toHaveBeenCalledWith(`/workflows/${WF_ID}/graph`);
  });

  it("draws nothing while the flag is off or the graph cannot be read", async () => {
    detailApi(() => Promise.resolve({ data: { workflow_id: WF_ID, enabled: false, graph: GRAPH, errors: [] } }));
    const first = renderDetail();
    await waitFor(() => expect(mockGet).toHaveBeenCalledWith(`/workflows/${WF_ID}/graph`));
    await screen.findByText("Invoices");
    expect(screen.queryByTestId("workflow-graph")).not.toBeInTheDocument();
    first.unmount();

    mockGet.mockReset();
    detailApi(() => Promise.reject(new Error("forbidden")));
    renderDetail();
    await waitFor(() => expect(mockGet).toHaveBeenCalledWith(`/workflows/${WF_ID}/graph`));
    await screen.findByText("Invoices");
    expect(screen.queryByTestId("workflow-graph")).not.toBeInTheDocument();
  });
});
