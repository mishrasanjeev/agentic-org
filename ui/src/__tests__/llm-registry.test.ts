/**
 * Bug sheet 2026-09-14 — shared LLM registry helpers used by AIConfig,
 * AgentCreate and AgentDetail (#34 drift, #37 wildcard, #40 null ctx).
 */
import { describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({ default: { get: vi.fn() } }));

import {
  agentLlmProviders,
  formatLlmOption,
  inferProviderForModel,
  isFreeTextModelProvider,
  selectableModels,
  type LlmRegistry,
} from "@/lib/llm-registry";

const REGISTRY: LlmRegistry = {
  gemini: [
    { model: "gemini-2.5-flash", context_window: null, max_output_tokens: 8192, supports_tools: true, supports_vision: false, notes: "" },
  ],
  openai: [
    { model: "gpt-4o", context_window: 128000, max_output_tokens: 16384, supports_tools: true, supports_vision: true, notes: "" },
  ],
  openai_compatible: [
    { model: "*", context_window: 128000, max_output_tokens: 16384, supports_tools: true, supports_vision: false, notes: "" },
  ],
};

describe("llm-registry helpers", () => {
  it("#40: formatLlmOption omits the ctx suffix when context_window is null", () => {
    expect(formatLlmOption(REGISTRY.gemini[0])).toBe("gemini-2.5-flash");
    expect(formatLlmOption(REGISTRY.openai[0])).toBe(`gpt-4o · ctx ${(128000).toLocaleString()}`);
  });

  it("#37: openai_compatible is free text and never yields the '*' wildcard as an option", () => {
    expect(isFreeTextModelProvider("openai_compatible")).toBe(true);
    expect(isFreeTextModelProvider("openai")).toBe(false);
    expect(selectableModels(REGISTRY, "openai_compatible")).toEqual([]);
    expect(selectableModels(REGISTRY, "openai").map((m) => m.model)).toEqual(["gpt-4o"]);
    expect(selectableModels(null, "openai")).toEqual([]);
    expect(selectableModels(REGISTRY, "")).toEqual([]);
  });

  it("#34: agent pickers never offer azure_openai (the agent API rejects it)", () => {
    const withAzure: LlmRegistry = {
      ...REGISTRY,
      azure_openai: [
        { model: "deployment:gpt-4o", context_window: 128000, max_output_tokens: 16384, supports_tools: true, supports_vision: true, notes: "" },
      ],
    };
    expect(agentLlmProviders(withAzure)).toEqual(["gemini", "openai", "openai_compatible"]);
    expect(agentLlmProviders(null)).toEqual([]);
    expect(inferProviderForModel(withAzure, "deployment:gpt-4o")).toBe("");
  });

  it("#34: inferProviderForModel resolves a legacy model to its catalog provider", () => {
    expect(inferProviderForModel(REGISTRY, "gpt-4o")).toBe("openai");
    expect(inferProviderForModel(REGISTRY, "claude-opus-4-5")).toBe("");
    expect(inferProviderForModel(null, "gpt-4o")).toBe("");
    expect(inferProviderForModel(REGISTRY, null)).toBe("");
  });
});
