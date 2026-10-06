// SPDX-License-Identifier: Apache-2.0
/**
 * Evaluation datasets panel: absent while the feature is off, creating a
 * dataset, saving a new version against the version that was opened, reading
 * an earlier version, a partial run and archiving.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockGet = vi.fn();
const mockPost = vi.fn();
const mockDelete = vi.fn();

vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
    delete: (...args: unknown[]) => mockDelete(...args),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  },
  extractApiError: (_e: unknown, fallback: string) => fallback,
}));

import EvalDatasets from "@/components/evals/EvalDatasets";

const LIMITS = { datasets: 200, cases: 200, cases_per_run: 25 };
const DATASET = { id: "d1", name: "Claims answers", description: null, latest_version: 2, case_count: 2, updated_at: null };
const CASES_V2 = [
  { id: "a", input: "q1", contains: ["x"] },
  { id: "b", input: "q2", equals: "y" },
];
const CASES_V1 = [{ id: "a", input: "q1", contains: ["x"] }];
const JUDGES = ["faithfulness", "relevance", "instruction_adherence", "context_recall"];
const HISTORY = [
  {
    id: "r1",
    version: 2,
    model: "gpt-4o-mini",
    judges: ["relevance"],
    prompt_label: "claims v2",
    cases_run: 2,
    complete: true,
    pass_rate: 0.5,
    scores: { relevance: { cases: 2, mean: 0.75, errors: 0 } },
    created_at: "2026-10-06T09:30:00+00:00",
  },
];
const VERSIONS = [
  { version: 2, case_count: 2, content_hash: "h2", note: "adds b", created_at: null },
  { version: 1, case_count: 1, content_hash: "h1", note: null, created_at: null },
];

function serve({ enabled = true, datasets = [DATASET], models = ["gpt-4o-mini"] } = {}) {
  mockGet.mockImplementation((path: string) => {
    if (path === "/eval-datasets") return Promise.resolve({ data: { enabled, datasets, limits: LIMITS } });
    if (path === "/prompt-templates/compare/models")
      return Promise.resolve({ data: { enabled: models.length > 0, models: models.map((model) => ({ model })) } });
    if (path === "/eval-datasets/d1") return Promise.resolve({ data: { ...DATASET, versions: VERSIONS } });
    if (path === "/eval-datasets/d1/versions/2") return Promise.resolve({ data: { version: 2, cases: CASES_V2 } });
    if (path === "/eval-datasets/d1/versions/1") return Promise.resolve({ data: { version: 1, cases: CASES_V1 } });
    if (path === "/eval-datasets/d1/runs") return Promise.resolve({ data: { runs: HISTORY, judges: JUDGES } });
    return Promise.reject(new Error(`unexpected ${path}`));
  });
}

async function openDataset() {
  render(<EvalDatasets />);
  fireEvent.click(await screen.findByTestId("eval-dataset-open-d1"));
  await waitFor(() => expect(screen.getByTestId("eval-dataset-heading")).toHaveTextContent("Claims answers, version 2"));
}

describe("EvalDatasets", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("is absent while datasets are off or cannot be loaded", async () => {
    serve({ enabled: false });
    const { container, unmount } = render(<EvalDatasets />);
    await waitFor(() => expect(mockGet).toHaveBeenCalledWith("/eval-datasets"));
    expect(container).toBeEmptyDOMElement();
    unmount();
    mockGet.mockRejectedValue(new Error("403"));
    const second = render(<EvalDatasets />);
    await waitFor(() => expect(mockGet).toHaveBeenCalled());
    expect(second.container).toBeEmptyDOMElement();
  });

  it("creates a dataset from a name and cases", async () => {
    serve({ datasets: [] });
    mockPost.mockResolvedValue({ data: { name: "Claims answers", version: { version: 1 } } });
    render(<EvalDatasets />);
    expect(await screen.findByText("No datasets yet.")).toBeInTheDocument();
    expect(screen.getByTestId("eval-dataset-save")).toBeDisabled();
    fireEvent.change(screen.getByTestId("eval-dataset-name"), { target: { value: "Claims answers" } });
    fireEvent.change(screen.getByTestId("eval-dataset-cases"), { target: { value: JSON.stringify(CASES_V1) } });
    fireEvent.click(screen.getByTestId("eval-dataset-save"));
    await waitFor(() =>
      expect(mockPost).toHaveBeenCalledWith("/eval-datasets", { name: "Claims answers", cases: CASES_V1, note: null }),
    );
    expect(await screen.findByRole("status")).toHaveTextContent("Created Claims answers at version 1.");
  });

  it("does not send cases that are not a JSON list", async () => {
    serve({ datasets: [] });
    render(<EvalDatasets />);
    fireEvent.change(await screen.findByTestId("eval-dataset-name"), { target: { value: "x" } });
    fireEvent.change(screen.getByTestId("eval-dataset-cases"), { target: { value: "{not json" } });
    fireEvent.click(screen.getByTestId("eval-dataset-save"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Cases are not valid JSON.");
    fireEvent.change(screen.getByTestId("eval-dataset-cases"), { target: { value: "{}" } });
    fireEvent.click(screen.getByTestId("eval-dataset-save"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Cases are a non-empty JSON list.");
    expect(mockPost).not.toHaveBeenCalled();
  });

  it("saves edited cases as the next version, against the version that was opened", async () => {
    serve();
    mockPost.mockResolvedValue({ data: { version: { version: 3 } } });
    await openDataset();
    expect((screen.getByTestId("eval-dataset-cases") as HTMLTextAreaElement).value).toContain('"q2"');
    fireEvent.change(screen.getByTestId("eval-dataset-note"), { target: { value: "tightens b" } });
    fireEvent.click(screen.getByTestId("eval-dataset-save"));
    await waitFor(() =>
      expect(mockPost).toHaveBeenCalledWith("/eval-datasets/d1/versions", {
        cases: CASES_V2,
        note: "tightens b",
        expected_latest: 2,
      }),
    );
    expect(await screen.findByRole("status")).toHaveTextContent("Saved as version 3.");
  });

  it("shows an earlier version and says saving it makes a new one", async () => {
    serve();
    await openDataset();
    fireEvent.change(screen.getByTestId("eval-dataset-version"), { target: { value: "1" } });
    await waitFor(() => expect(screen.getByTestId("eval-dataset-heading")).toHaveTextContent("version 1"));
    expect((screen.getByTestId("eval-dataset-cases") as HTMLTextAreaElement).value).not.toContain('"q2"');
    expect(screen.getByText(/Saving stores these cases as version 3/)).toBeInTheDocument();
  });

  it("scores a prompt against the shown version and marks a partial run", async () => {
    serve();
    mockPost.mockResolvedValue({
      data: {
        version: 2,
        content_hash: "h2",
        cases_total: 30,
        offset: 0,
        cases_run: 25,
        complete: false,
        model: "gpt-4o-mini",
        judge_model: "gpt-4o-mini",
        judges: ["relevance"],
        passed: 23,
        failed: 1,
        errors: 1,
        pass_rate: 0.92,
        cost_usd: 0.0123,
        metrics: {
          exact_match: { cases: 10, matched: 9, rate: 0.9 },
          classification: { cases: 8, accuracy: 0.75, precision: 0.7, recall: 0.8, f1: 0.746 },
        },
        scores: { relevance: { cases: 23, mean: 0.8, errors: 1 } },
        results: [
          { id: "a", result: "passed" },
          { id: "b", result: "failed", failed_checks: ["equals"] },
          { id: "c", result: "error", error_type: "timeout" },
        ],
        reasons: { b: { relevance: "Answers a different question." } },
      },
    });
    await openDataset();
    expect(await screen.findByTestId("eval-run-history")).toHaveTextContent("v2 · gpt-4o-mini · claims v2 · 50% of 2 · relevance 75%");
    expect(screen.getByTestId("eval-run-start")).toBeDisabled();
    fireEvent.change(screen.getByTestId("eval-run-system"), { target: { value: "You answer claims questions." } });
    fireEvent.change(screen.getByTestId("eval-run-model"), { target: { value: "gpt-4o-mini" } });
    fireEvent.change(screen.getByTestId("eval-run-label"), { target: { value: "claims v3" } });
    fireEvent.click(screen.getByTestId("eval-judge-relevance"));
    // A judge needs a judge model before the run can start.
    expect(screen.getByTestId("eval-run-start")).toBeDisabled();
    fireEvent.change(screen.getByTestId("eval-judge-model"), { target: { value: "gpt-4o-mini" } });
    fireEvent.click(screen.getByTestId("eval-run-start"));
    await waitFor(() =>
      expect(mockPost).toHaveBeenCalledWith("/eval-datasets/d1/run", {
        version: 2,
        system: "You answer claims questions.",
        model: "gpt-4o-mini",
        judges: ["relevance"],
        judge_model: "gpt-4o-mini",
        prompt_label: "claims v3",
      }),
    );
    const result = await screen.findByTestId("eval-run-result");
    expect(result).toHaveTextContent("23 passed, 1 failed, 1 errors of 25 cases (92%)");
    expect(result).toHaveTextContent("Partial: cases 1 to 25 of 30.");
    expect(screen.getByTestId("eval-run-metrics")).toHaveTextContent("Exact match 90% of 10 · Labels: accuracy 75%, precision 70%, recall 80%, F1 75% · Relevance: 80% over 23 (1 unrated)");
    expect(result).toHaveTextContent("b: equals");
    expect(result).toHaveTextContent("Relevance: Answers a different question.");
    expect(result).toHaveTextContent("c: error (timeout)");
    // The history is reloaded after a run.
    await waitFor(() => expect(mockGet).toHaveBeenCalledTimes(mockGet.mock.calls.filter((call) => call[0] === "/eval-datasets/d1/runs").length > 1 ? mockGet.mock.calls.length : mockGet.mock.calls.length));
    expect(mockGet.mock.calls.filter((call) => call[0] === "/eval-datasets/d1/runs").length).toBe(2);
  });

  it("sends no judge model when no judge is chosen", async () => {
    serve();
    mockPost.mockResolvedValue({
      data: {
        version: 2, content_hash: "h2", cases_total: 2, offset: 0, cases_run: 2, complete: true, model: "gpt-4o-mini",
        judge_model: null, judges: [], passed: 2, failed: 0, errors: 0, pass_rate: 1, cost_usd: 0.001,
        metrics: { exact_match: { cases: 0, matched: 0, rate: null }, classification: null }, scores: {}, results: [],
      },
    });
    await openDataset();
    fireEvent.change(screen.getByTestId("eval-run-system"), { target: { value: "s" } });
    fireEvent.change(screen.getByTestId("eval-run-model"), { target: { value: "gpt-4o-mini" } });
    fireEvent.click(screen.getByTestId("eval-run-start"));
    await waitFor(() =>
      expect(mockPost).toHaveBeenCalledWith("/eval-datasets/d1/run", {
        version: 2, system: "s", model: "gpt-4o-mini", judges: [], judge_model: null, prompt_label: null,
      }),
    );
    expect(await screen.findByTestId("eval-run-metrics")).toHaveTextContent("Exact match n/a of 0");
  });

  it("offers no run where prompt evaluation is off", async () => {
    serve({ models: [] });
    await openDataset();
    expect(screen.queryByTestId("eval-dataset-run")).not.toBeInTheDocument();
  });

  it("archives the open dataset and reports a failure to save", async () => {
    serve();
    mockDelete.mockResolvedValue({ data: {} });
    await openDataset();
    mockPost.mockRejectedValueOnce(new Error("409"));
    fireEvent.click(screen.getByTestId("eval-dataset-save"));
    expect(await screen.findByRole("alert")).toHaveTextContent("The cases were not saved.");
    fireEvent.click(screen.getByTestId("eval-dataset-archive"));
    await waitFor(() => expect(mockDelete).toHaveBeenCalledWith("/eval-datasets/d1"));
    expect(await screen.findByRole("status")).toHaveTextContent("Archived Claims answers.");
    expect(screen.getByTestId("eval-dataset-heading")).toHaveTextContent("New dataset");
  });
});
