// SPDX-License-Identifier: Apache-2.0
/**
 * Prompt comparison panel: absent while comparison is off, the model limit,
 * the request it sends, and the side-by-side result including a failed model.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockGet = vi.fn();
const mockPost = vi.fn();

vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  },
  extractApiError: (_e: unknown, fallback: string) => fallback,
}));

import PromptCompare from "@/components/prompts/PromptCompare";

const MODELS = [
  { provider: "openai", model: "gpt-4o-mini" },
  { provider: "gemini", model: "gemini-2.5-flash" },
  { provider: "anthropic", model: "claude-sonnet-4-5-20250929" },
];
const PARAMETERS = [{ name: "org" }, { name: "max_words", type: "integer", required: false, default: 120 }];

function options(enabled: boolean, limit = 2) {
  mockGet.mockResolvedValue({ data: { enabled, models: MODELS, limits: { models: limit, max_tokens: 2048 } } });
}

describe("PromptCompare", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("is absent while comparison is off or cannot be loaded", async () => {
    options(false);
    const { container, unmount } = render(<PromptCompare templateId="t1" parameters={PARAMETERS} />);
    await waitFor(() => expect(mockGet).toHaveBeenCalledWith("/prompt-templates/compare/models"));
    expect(container).toBeEmptyDOMElement();
    unmount();
    mockGet.mockRejectedValue(new Error("403"));
    const second = render(<PromptCompare templateId="t1" parameters={PARAMETERS} />);
    await waitFor(() => expect(mockGet).toHaveBeenCalledTimes(2));
    expect(second.container).toBeEmptyDOMElement();
  });

  it("holds the choice of models to the limit", async () => {
    options(true, 2);
    render(<PromptCompare templateId="t1" parameters={PARAMETERS} />);
    fireEvent.click(await screen.findByTestId("compare-model-gpt-4o-mini"));
    fireEvent.click(screen.getByTestId("compare-model-gemini-2.5-flash"));
    expect(screen.getByTestId("compare-model-claude-sonnet-4-5-20250929")).toBeDisabled();
    expect(screen.getByTestId("compare-run")).toBeDisabled();
  });

  it("sends the template, the typed values, the input and the models, and shows the answers side by side", async () => {
    options(true, 4);
    mockPost.mockResolvedValue({
      data: {
        total_cost_usd: 0.0005,
        results: [
          { model: "gpt-4o-mini", ok: true, output: "It earns 3.5% a year.", served_model: "gpt-4o-mini", latency_ms: 420, tokens: 30, cost_usd: 0.0005, error_type: null },
          { model: "gemini-2.5-flash", ok: false, output: "", served_model: null, latency_ms: 90, tokens: 0, cost_usd: 0, error_type: "TimeoutError" },
        ],
      },
    });
    render(<PromptCompare templateId="t1" parameters={PARAMETERS} />);
    fireEvent.click(await screen.findByTestId("compare-model-gpt-4o-mini"));
    fireEvent.click(screen.getByTestId("compare-model-gemini-2.5-flash"));
    fireEvent.change(screen.getByTestId("compare-value-org"), { target: { value: "Northwind" } });
    fireEvent.change(screen.getByTestId("compare-input"), { target: { value: "What does it earn?" } });
    fireEvent.click(screen.getByTestId("compare-run"));
    await waitFor(() =>
      expect(mockPost).toHaveBeenCalledWith("/prompt-templates/compare", {
        template_id: "t1",
        values: { org: "Northwind" },
        input: "What does it earn?",
        models: ["gpt-4o-mini", "gemini-2.5-flash"],
      }),
    );
    const first = await screen.findByTestId("compare-column-gpt-4o-mini");
    expect(first).toHaveTextContent("It earns 3.5% a year.");
    expect(first).toHaveTextContent("420 ms");
    expect(first).toHaveTextContent("$0.0005");
    expect(screen.getByTestId("compare-column-gemini-2.5-flash")).toHaveTextContent("Failed (TimeoutError)");
    expect(screen.getByTestId("compare-result")).toHaveTextContent("Total cost of this run: $0.0005");
  });

  it("says when the comparison did not run", async () => {
    options(true, 4);
    mockPost.mockRejectedValue(new Error("422"));
    render(<PromptCompare templateId="t1" parameters={[]} />);
    fireEvent.click(await screen.findByTestId("compare-model-gpt-4o-mini"));
    fireEvent.change(screen.getByTestId("compare-input"), { target: { value: "q" } });
    fireEvent.click(screen.getByTestId("compare-run"));
    expect(await screen.findByRole("alert")).toHaveTextContent("The comparison did not run.");
  });
});
