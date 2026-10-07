// SPDX-License-Identifier: Apache-2.0
/**
 * Transactions: findings with disposition, the fund-flow graph drawn by hop, node expansion, the paths, the export.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { HelmetProvider } from "react-helmet-async";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockGet = vi.fn();
const mockPost = vi.fn();

vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  },
  extractApiError: (_err: unknown, fallback: string) => fallback,
}));

import Transactions, { graphHeight, layout, money } from "@/pages/Transactions";

const FINDING = {
  id: "f1",
  kind: "structuring",
  entity_kind: "account",
  entity_ref: "A1",
  severity: "high",
  status: "open",
  summary: "3 cash deposits under 1,000,000 on account A1",
  facts: {},
  record_refs: ["s1", "s2", "s3"],
  detected_at: null,
  disposition: {},
  case_ref: null,
};
const GRAPH = {
  root: { kind: "account", ref: "A1", accounts: ["A1"] },
  hops: 2,
  nodes: [
    { id: "A1", kind: "account", label: "A1", hop: 0, in: 1000, out: 900, records: 3, root: true, findings: [{ id: "f1", kind: "structuring", severity: "high", status: "open" }] },
    { id: "Y1", kind: "counterparty", label: "Y1", hop: 1, in: 900, out: 0, records: 1, findings: [] },
    { id: "X1", kind: "counterparty", label: "X1", hop: 1, in: 0, out: 1000, records: 1, findings: [] },
  ],
  edges: [
    { from: "X1", to: "A1", amount: 1000, count: 1, first_at: null, last_at: null, channels: ["transfer"] },
    { from: "A1", to: "Y1", amount: 900, count: 1, first_at: null, last_at: null, channels: ["upi"] },
  ],
  paths: [{ hops: ["Y1"], start: "A1", carried: 900, steps: [{ from: "A1", to: "Y1", amount: 900 }] }],
  truncated: false,
  totals: { nodes: 3, edges: 2, records: 2 },
};

function answer(url: string) {
  if (url === "/txn/findings") return Promise.resolve({ data: { findings: [FINDING], total: 1 } });
  if (url.startsWith("/txn/graph/")) return Promise.resolve({ data: GRAPH });
  return Promise.reject(new Error(`unexpected ${url}`));
}

function renderPage() {
  return render(
    <HelmetProvider>
      <Transactions />
    </HelmetProvider>,
  );
}

describe("Transactions", () => {
  beforeEach(() => {
    mockGet.mockReset();
    mockPost.mockReset();
    mockGet.mockImplementation((url: string) => answer(url));
  });

  it("lays nodes out by hop and formats money", () => {
    const positions = layout(GRAPH.nodes as never);
    expect(positions.A1.x).toBeLessThan(positions.Y1.x);
    expect(positions.Y1.x).toBe(positions.X1.x);
    expect(positions.Y1.y).not.toBe(positions.X1.y);
    expect(graphHeight(GRAPH.nodes as never)).toBeGreaterThan(100);
    expect(money(1250000)).toBe("12,50,000");
    expect(money(null)).toBe("—");
  });

  it("lists findings, records a disposition and shows the fund flow of a finding", async () => {
    mockPost.mockResolvedValue({ data: { status: "confirmed" } });
    renderPage();
    const row = await screen.findByTestId("txn-finding");
    expect(row.textContent).toContain("structuring");
    fireEvent.click(row);
    await screen.findByTestId("txn-finding-detail");
    fireEvent.click(screen.getByTestId("txn-finding-graph"));
    await screen.findByTestId("txn-graph");
    expect(mockGet).toHaveBeenCalledWith("/txn/graph/account/A1", { params: { hops: "2" } });
    expect(screen.getAllByTestId("txn-node")).toHaveLength(3);
    expect(screen.getByTestId("txn-paths").textContent).toContain("A1 → Y1");
    expect(screen.getByTestId("txn-export").getAttribute("href")).toContain("/api/v1/txn/graph/account/A1/export?format=csv&hops=2");
    fireEvent.click(screen.getByText("Confirm"));
    await waitFor(() => expect(mockPost).toHaveBeenCalledWith("/txn/findings/f1/disposition", { outcome: "confirm", notes: "" }));
    expect(await screen.findByTestId("txn-notice")).toHaveTextContent("Finding confirmed.");
  });

  it("builds a graph from the form and expands a node on click", async () => {
    renderPage();
    await screen.findByTestId("txn-finding");
    fireEvent.change(screen.getByTestId("txn-ref"), { target: { value: "A1" } });
    fireEvent.change(screen.getByTestId("txn-hops"), { target: { value: "3" } });
    fireEvent.click(screen.getByTestId("txn-build"));
    await screen.findByTestId("txn-graph");
    expect(mockGet).toHaveBeenCalledWith("/txn/graph/account/A1", { params: { hops: "3" } });
    fireEvent.click(screen.getAllByTestId("txn-node")[1]);
    await waitFor(() => expect(mockGet).toHaveBeenCalledWith("/txn/graph/counterparty/Y1", { params: { hops: "3" } }));
  });

  it("requires a reason to dismiss and reports a failed graph", async () => {
    renderPage();
    fireEvent.click(await screen.findByTestId("txn-finding"));
    await screen.findByTestId("txn-finding-detail");
    expect((screen.getByTestId("txn-dismiss") as HTMLButtonElement).disabled).toBe(true);
    mockGet.mockImplementation((url: string) => (url === "/txn/findings" ? answer(url) : Promise.reject(new Error("500"))));
    fireEvent.change(screen.getByTestId("txn-ref"), { target: { value: "Z9" } });
    fireEvent.click(screen.getByTestId("txn-build"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Failed to build the fund-flow graph.");
  });
});
