/**
 * Shared LLM provider/model registry contract (bug sheet 2026-09-14 #34/#37/#40).
 *
 * AIConfig, AgentCreate and AgentDetail all render provider/model pickers.
 * They used to drift: AgentCreate/AgentDetail hardcoded model ids that do
 * not exist in `core/ai_providers/catalog.py`, so a saved agent could point
 * at a model the backend rejects or maps to a different provider. Every
 * picker now reads GET /tenant-ai-settings/registry through this module and
 * sends `llm.provider` alongside `llm.model`.
 */
import { useEffect, useState } from "react";
import api from "@/lib/api";

export interface LlmRegistryItem {
  model: string;
  /** Sheet #40: nullable — pass-through entries carry no window. */
  context_window: number | null;
  max_output_tokens: number | null;
  supports_tools: boolean;
  supports_vision: boolean;
  notes: string;
}

export type LlmRegistry = Record<string, LlmRegistryItem[]>;

export const OPENAI_COMPATIBLE_PROVIDER = "openai_compatible";

/**
 * Sheet #37: openai_compatible's only catalog entry is the wildcard "*" —
 * the admin's endpoint owns the model list, so the model is free text.
 */
export function isFreeTextModelProvider(provider: string | null | undefined): boolean {
  return provider === OPENAI_COMPATIBLE_PROVIDER;
}

/** Sheet #40: omit the ctx suffix when the registry has no window. */
export function formatLlmOption(item: LlmRegistryItem): string {
  return item.context_window != null
    ? `${item.model} · ctx ${item.context_window.toLocaleString()}`
    : item.model;
}

/**
 * Providers an agent can be pinned to. Mirrors
 * `AGENT_RUNTIME_LLM_PROVIDERS` in core/ai_providers/catalog.py:
 * `azure_openai` is catalogued for tenant settings only and the agent API
 * rejects it with 422, so the agent pickers never offer it.
 */
export const AGENT_RUNTIME_LLM_PROVIDERS: readonly string[] = ["gemini", "openai", "anthropic", "openai_compatible"];

export function agentLlmProviders(registry: LlmRegistry | null): string[] {
  if (!registry) return [];
  return Object.keys(registry).filter((p) => AGENT_RUNTIME_LLM_PROVIDERS.includes(p));
}

/** Models that can be offered in a <select> for `provider` (never the "*" wildcard). */
export function selectableModels(registry: LlmRegistry | null, provider: string): LlmRegistryItem[] {
  if (!registry || !provider || isFreeTextModelProvider(provider)) return [];
  return (registry[provider] || []).filter((m) => m.model !== "*");
}

/** Legacy agents carry llm_model but no llm_provider: find the provider that lists it. */
export function inferProviderForModel(registry: LlmRegistry | null, model: string | null | undefined): string {
  if (!registry || !model) return "";
  for (const provider of agentLlmProviders(registry)) {
    if ((registry[provider] || []).some((m) => m.model === model)) return provider;
  }
  return "";
}

export interface LlmRegistryState {
  registry: LlmRegistry | null;
  loading: boolean;
  /** True when the registry could not be loaded — callers fall back to text inputs. */
  failed: boolean;
}

export function useLlmRegistry(): LlmRegistryState {
  const [registry, setRegistry] = useState<LlmRegistry | null>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await api.get("/tenant-ai-settings/registry");
        const llm = res?.data?.llm;
        if (cancelled) return;
        if (llm && typeof llm === "object" && Object.keys(llm).length > 0) {
          setRegistry(llm as LlmRegistry);
        } else {
          setFailed(true);
        }
      } catch {
        if (!cancelled) setFailed(true);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return { registry, loading, failed };
}
