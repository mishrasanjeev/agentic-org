/**
 * Bug sheet 2026-09-14 — AIConfig LLM picker.
 *
 *  #37  openai_compatible's only catalog entry is the wildcard "*"; the model
 *       <select> offered literally "*". It is now a free-text input.
 *  #40  `context_window.toLocaleString()` crashed when the registry returned
 *       null; the ctx suffix is omitted instead.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockGet = vi.fn();
const mockPut = vi.fn();

vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    put: (...args: unknown[]) => mockPut(...args),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  },
  extractApiError: (_e: unknown, fallback: string) => fallback,
}));

import AIConfig from "@/pages/AIConfig";

const REGISTRY = {
  llm: {
    gemini: [
      { model: "gemini-2.5-flash", context_window: null, max_output_tokens: 8192, supports_tools: true, supports_vision: false, notes: "" },
      { model: "gemini-2.5-pro", context_window: 2097152, max_output_tokens: 8192, supports_tools: true, supports_vision: true, notes: "" },
    ],
    openai_compatible: [
      { model: "*", context_window: 128000, max_output_tokens: 16384, supports_tools: true, supports_vision: false, notes: "admin must set base_url" },
    ],
  },
  embedding: {
    local: [{ model: "BAAI/bge-small-en-v1.5", dimensions: 384, max_input_tokens: 512, notes: "" }],
  },
};

const SETTING = {
  tenant_id: "t1",
  llm_provider: null,
  llm_model: null,
  llm_fallback_model: null,
  llm_routing_policy: "auto",
  max_input_tokens: null,
  embedding_provider: null,
  embedding_model: null,
  embedding_dimensions: null,
  chunk_size: null,
  chunk_overlap: null,
  ai_fallback_policy: "allow",
};

describe("AIConfig LLM picker (bug sheet 2026-09-14)", () => {
  beforeEach(() => {
    mockGet.mockReset();
    mockPut.mockReset();
    mockGet.mockImplementation((url: string) => {
      if (url === "/tenant-ai-settings/registry") return Promise.resolve({ data: REGISTRY });
      if (url === "/tenant-ai-settings") return Promise.resolve({ data: SETTING });
      return Promise.resolve({ data: {} });
    });
    mockPut.mockResolvedValue({ data: {} });
  });

  it("#37: openai_compatible renders a free-text model input instead of a '*' option and saves the typed name", async () => {
    render(<AIConfig />);
    const provider = await screen.findByTestId("llm-provider-select");

    fireEvent.change(provider, { target: { value: "openai_compatible" } });

    const modelInput = screen.getByTestId("llm-model-input") as HTMLInputElement;
    expect(modelInput.tagName).toBe("INPUT");
    expect(screen.queryByTestId("llm-model-select")).toBeNull();
    expect(screen.queryByRole("option", { name: "*" })).toBeNull();
    expect(document.querySelector('option[value="*"]')).toBeNull();
    // Fallback model is free text for the same provider.
    expect((screen.getByTestId("llm-fallback-input") as HTMLInputElement).tagName).toBe("INPUT");

    fireEvent.change(modelInput, { target: { value: "my-local-llm" } });
    fireEvent.click(screen.getByRole("button", { name: "Save configuration" }));

    await waitFor(() => expect(mockPut).toHaveBeenCalledTimes(1));
    expect(mockPut).toHaveBeenCalledWith(
      "/tenant-ai-settings",
      expect.objectContaining({ llm_provider: "openai_compatible", llm_model: "my-local-llm" }),
    );
  });

  it("#40: a null context_window renders the model without a ctx suffix instead of crashing", async () => {
    render(<AIConfig />);
    const provider = await screen.findByTestId("llm-provider-select");

    fireEvent.change(provider, { target: { value: "gemini" } });

    const select = screen.getByTestId("llm-model-select") as HTMLSelectElement;
    const labels = Array.from(select.options).map((o) => o.textContent?.trim());
    expect(labels).toContain("gemini-2.5-flash");
    expect(labels).toContain(`gemini-2.5-pro · ctx ${(2097152).toLocaleString()}`);
    expect(labels.some((l) => l?.startsWith("gemini-2.5-flash · ctx"))).toBe(false);
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
