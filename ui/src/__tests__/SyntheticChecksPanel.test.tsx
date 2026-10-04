// SPDX-License-Identifier: Apache-2.0
/**
 * Synthetic checks panel: the list, the scheduled-runs notice, run now, results, toggle and add.
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
  extractApiError: (_err: unknown, fallback: string) => fallback,
}));

import SyntheticChecksPanel from "@/components/observability/SyntheticChecksPanel";

const CHECK = {
  id: "c1",
  name: "model answers",
  kind: "model",
  config: { prompt: "Reply with ready." },
  interval_minutes: 30,
  enabled: true,
  last_run_at: "2026-10-04T10:00:00+00:00",
  last_status: "failed",
};

const RESULT = {
  id: "r1",
  check_id: "c1",
  status: "failed",
  latency_ms: 812,
  reasons: ["answer_missing_expected_text"],
  detail: { model: "m", tokens: 4 },
  trigger: "manual",
  started_at: "2026-10-04T10:00:00+00:00",
};

function route(checks = [CHECK], enabled = false) {
  mockGet.mockImplementation((url: string) => {
    if (url === "/observability/checks") {
      return Promise.resolve({ data: { enabled, kinds: ["model", "knowledge", "guardrail", "audit_chain"], limit: 20, checks } });
    }
    if (url === "/observability/checks/c1/results") return Promise.resolve({ data: { results: [RESULT] } });
    return Promise.reject(new Error(`unexpected ${url}`));
  });
}

describe("SyntheticChecksPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockPost.mockResolvedValue({ data: RESULT });
    mockPatch.mockResolvedValue({ data: CHECK });
    mockDelete.mockResolvedValue({ data: null });
  });

  it("lists the checks and says scheduled runs are off", async () => {
    route();
    render(<SyntheticChecksPanel />);
    expect(await screen.findByTestId("check-row-c1")).toHaveTextContent("model answers");
    expect(screen.getByTestId("check-row-c1")).toHaveTextContent("failed");
    expect(screen.getByTestId("checks-off")).toBeInTheDocument();
  });

  it("shows no notice when scheduled runs are on and an empty state without checks", async () => {
    route([], true);
    render(<SyntheticChecksPanel />);
    expect(await screen.findByTestId("checks-empty")).toBeInTheDocument();
    expect(screen.queryByTestId("checks-off")).not.toBeInTheDocument();
  });

  it("runs a check now and shows its results with the reasons", async () => {
    route();
    render(<SyntheticChecksPanel />);
    fireEvent.click(await screen.findByTestId("check-run-c1"));
    await waitFor(() => expect(mockPost).toHaveBeenCalledWith("/observability/checks/c1/run"));
    const results = await screen.findByTestId("check-results");
    expect(results).toHaveTextContent("answer_missing_expected_text");
    expect(results).toHaveTextContent("812 ms");
    expect(results).not.toHaveTextContent("Reply with ready.");
  });

  it("disables a check", async () => {
    route();
    render(<SyntheticChecksPanel />);
    fireEvent.click(await screen.findByTestId("check-toggle-c1"));
    await waitFor(() => expect(mockPatch).toHaveBeenCalledWith("/observability/checks/c1", { enabled: false }));
  });

  it("adds a check with the kind's example configuration", async () => {
    route([]);
    render(<SyntheticChecksPanel />);
    await screen.findByTestId("checks-empty");
    expect(screen.getByTestId("check-create")).toBeDisabled();
    fireEvent.change(screen.getByTestId("check-name"), { target: { value: "chain holds" } });
    fireEvent.change(screen.getByTestId("check-kind"), { target: { value: "audit_chain" } });
    fireEvent.change(screen.getByTestId("check-interval"), { target: { value: "15" } });
    fireEvent.click(screen.getByTestId("check-create"));
    await waitFor(() =>
      expect(mockPost).toHaveBeenCalledWith("/observability/checks", {
        name: "chain holds",
        kind: "audit_chain",
        config: { recent: 1000 },
        interval_minutes: 15,
        enabled: true,
      }),
    );
  });

  it("refuses a configuration that is not JSON without calling the API", async () => {
    route([]);
    render(<SyntheticChecksPanel />);
    await screen.findByTestId("checks-empty");
    fireEvent.change(screen.getByTestId("check-name"), { target: { value: "broken" } });
    fireEvent.change(screen.getByTestId("check-config"), { target: { value: "{not json" } });
    fireEvent.click(screen.getByTestId("check-create"));
    expect(await screen.findByRole("alert")).toHaveTextContent("not valid JSON");
    expect(mockPost).not.toHaveBeenCalled();
  });
});
