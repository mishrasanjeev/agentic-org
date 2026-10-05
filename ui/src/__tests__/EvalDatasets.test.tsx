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
        passed: 23,
        failed: 1,
        errors: 1,
        pass_rate: 0.92,
        cost_usd: 0.0123,
        results: [
          { id: "a", result: "passed" },
          { id: "b", result: "failed", failed_checks: ["equals"] },
          { id: "c", result: "error", error_type: "timeout" },
        ],
      },
    });
    await openDataset();
    expect(screen.getByTestId("eval-run-start")).toBeDisabled();
    fireEvent.change(screen.getByTestId("eval-run-system"), { target: { value: "You answer claims questions." } });
    fireEvent.change(screen.getByTestId("eval-run-model"), { target: { value: "gpt-4o-mini" } });
    fireEvent.click(screen.getByTestId("eval-run-start"));
    await waitFor(() =>
      expect(mockPost).toHaveBeenCalledWith("/eval-datasets/d1/run", {
        version: 2,
        system: "You answer claims questions.",
        model: "gpt-4o-mini",
      }),
    );
    const result = await screen.findByTestId("eval-run-result");
    expect(result).toHaveTextContent("23 passed, 1 failed, 1 errors of 25 cases (92%)");
    expect(result).toHaveTextContent("Partial: cases 1 to 25 of 30.");
    expect(result).toHaveTextContent("b: equals");
    expect(result).toHaveTextContent("c: error (timeout)");
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
