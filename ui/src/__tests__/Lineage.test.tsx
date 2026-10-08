// SPDX-License-Identifier: Apache-2.0
/**
 * Lineage: find a node, trace it, describe it, and the sync sources with their runs.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { HelmetProvider } from "react-helmet-async";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockGet = vi.fn();
const mockPost = vi.fn();
let mockRole = "admin";

vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => ({ user: { role: mockRole }, logout: vi.fn(), isAuthenticated: true }),
}));

vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  },
  extractApiError: (_err: unknown, fallback: string) => fallback,
}));

import Lineage, { KIND_ORDER, layout, shortRef } from "@/pages/Lineage";

const node = (id: string, kind: string, ref: string, version = "") => ({ id, kind, ref, source: "", version, observed_at: "2026-10-07T10:00:00+00:00", attributes: {} });
const SOURCE = node("n1", "source", "https://feed.example.com/a", "sha256:1");
const DOC = node("n2", "document", "https://docs.example.com/a.pdf", "v2");
const CHUNK = node("n3", "chunk", "https://docs.example.com/a.pdf#chunk1-ab", "ab");
const TRACE = {
  root: DOC,
  direction: "both",
  hops: 4,
  nodes: [SOURCE, DOC, CHUNK],
  steps: [
    { id: "s1", from_node: "n1", to_node: "n2", step: "acquire", tool: "core.lineage.sync", params_hash: "", at: "2026-10-07T10:00:00+00:00" },
    { id: "s2", from_node: "n2", to_node: "n3", step: "chunk", tool: "core.rag.chunking", params_hash: "", at: "2026-10-07T10:00:01+00:00" },
  ],
  truncated: true,
};
const DESCRIPTION = {
  node: DOC,
  versions: [DOC, { ...DOC, id: "n9", version: "v1" }],
  sources: [{ kind: "source", ref: "https://feed.example.com/a", version: "sha256:1" }],
  history: [{ step: "acquire", tool: "core.lineage.sync", at: "2026-10-07T10:00:00+00:00", from: { kind: "source", ref: "x" }, to: { kind: "document", ref: "y" } }],
  complete: true,
  truncated: false,
};
const SYNC = { id: "src1", name: "core", kind: "feed", url: "https://feed.example.com/changes", item_kind: "document", interval_minutes: 60, enabled: true, cursor: "c1", next_run_at: null, last_run_at: null, last_status: "completed", running: false };

function answer(url: string) {
  if (url === "/lineage/status") return Promise.resolve({ data: { enabled: true, kinds: KIND_ORDER } });
  if (url === "/lineage/nodes") return Promise.resolve({ data: { nodes: [DOC], total: 1 } });
  if (url.startsWith("/lineage/trace/")) return Promise.resolve({ data: TRACE });
  if (url.startsWith("/lineage/nodes/")) return Promise.resolve({ data: DESCRIPTION });
  if (url === "/lineage/sync/sources") return Promise.resolve({ data: { sources: [SYNC], total: 1 } });
  if (url.endsWith("/runs")) return Promise.resolve({ data: { runs: [{ id: "r1", trigger: "schedule", status: "partial", started_at: "2026-10-07T09:00:00+00:00", received: 3, processed: 1, skipped: 1, failed: 1, errors: ["x: ingest_failed: empty"] }], total: 1 } });
  return Promise.reject(new Error(`unexpected ${url}`));
}

function renderPage() {
  return render(
    <HelmetProvider>
      <Lineage />
    </HelmetProvider>,
  );
}

describe("Lineage", () => {
  beforeEach(() => {
    mockGet.mockReset();
    mockPost.mockReset();
    mockGet.mockImplementation((url: string) => answer(url));
    mockRole = "admin";
  });

  it("lays kinds out from origin to use and shortens long references", () => {
    const drawn = layout([CHUNK, SOURCE, DOC] as never);
    expect(drawn.positions.n1.x).toBeLessThan(drawn.positions.n2.x);
    expect(drawn.positions.n2.x).toBeLessThan(drawn.positions.n3.x);
    expect(drawn.width).toBeGreaterThan(drawn.positions.n3.x);
    expect(layout([node("a", "mystery", "m")] as never).positions.a.x).toBe(70); // an unknown kind still gets a column
    expect(shortRef("short")).toBe("short");
    const cut = shortRef("https://docs.example.com/very/long/path/a.pdf", 12);
    expect(cut).toBe("…/path/a.pdf"); // the end of a reference tells two apart
    expect(cut.startsWith("…") && cut.endsWith("a.pdf") && cut.length === 12).toBe(true);
  });

  it("finds a node, traces it both ways and describes it", async () => {
    renderPage();
    const found = await screen.findByTestId("lineage-found-node");
    expect(mockGet).toHaveBeenCalledWith("/lineage/nodes", { params: { limit: "50" } });
    fireEvent.click(found);
    await screen.findByTestId("lineage-graph");
    expect(mockGet).toHaveBeenCalledWith(`/lineage/trace/document/${encodeURIComponent(DOC.ref)}`, { params: { direction: "both", hops: "4", version: "v2" } });
    expect(screen.getAllByTestId("lineage-node")).toHaveLength(3);
    expect(screen.getByText("The trace was cut at its bounds; more lineage exists beyond it.")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("lineage-complete")).toHaveTextContent("Traced to its source."));
    expect(mockGet).toHaveBeenCalledWith(`/lineage/nodes/document/${encodeURIComponent(DOC.ref)}`, { params: { version: "v2" } });
    expect(screen.getByTestId("lineage-history").textContent).toContain("acquire by core.lineage.sync");
    expect(screen.getByTestId("lineage-versions").querySelectorAll("li")).toHaveLength(2);
  });

  it("searches by kind and reference", async () => {
    renderPage();
    await screen.findByTestId("lineage-found-node");
    fireEvent.change(screen.getByTestId("lineage-kind"), { target: { value: "chunk" } });
    fireEvent.change(screen.getByTestId("lineage-query"), { target: { value: "a.pdf" } });
    fireEvent.click(screen.getByTestId("lineage-search"));
    await waitFor(() => expect(mockGet).toHaveBeenCalledWith("/lineage/nodes", { params: { limit: "50", kind: "chunk", q: "a.pdf" } }));
  });

  it("lists sync sources, shows their runs and runs one for a writer", async () => {
    mockPost.mockResolvedValue({ data: { id: "r2", trigger: "manual", status: "completed", received: 2, processed: 2, skipped: 0, failed: 0, errors: [] } });
    renderPage();
    await screen.findByTestId("lineage-source");
    fireEvent.click(screen.getByTestId("lineage-source-runs"));
    expect((await screen.findByTestId("lineage-runs")).textContent).toContain("partial: 3 received, 1 processed, 1 unchanged, 1 failed");
    fireEvent.click(screen.getByTestId("lineage-source-run"));
    await waitFor(() => expect(mockPost).toHaveBeenCalledWith("/lineage/sync/sources/src1/run"));
    expect(await screen.findByTestId("lineage-notice")).toHaveTextContent("core: completed, 2 processed, 0 unchanged, 0 failed.");
  });

  it("offers no run control to a read-only role and reports a failed trace", async () => {
    mockRole = "auditor";
    renderPage();
    await screen.findByTestId("lineage-source");
    expect(screen.queryByTestId("lineage-source-run")).not.toBeInTheDocument();
    mockGet.mockImplementation((url: string) => (url.startsWith("/lineage/trace/") ? Promise.reject(new Error("404")) : answer(url)));
    fireEvent.click(await screen.findByTestId("lineage-found-node"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Failed to trace the lineage.");
  });
});
