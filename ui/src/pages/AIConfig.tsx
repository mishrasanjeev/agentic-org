import { useCallback, useEffect, useState } from "react";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import api, { extractApiError } from "@/lib/api";

/**
 * Tenant AI config — per-tenant LLM + embedding model selection.
 *
 * Closes S0-08 (PR-2 of the four-PR closure plan). Admins pick:
 *   - Which LLM provider + model the agents use.
 *   - Which embedding provider + model the knowledge base uses.
 *   - Whether the platform is allowed to fall back when the tenant has
 *     no BYO credential (ai_fallback_policy).
 *
 * The pickers are populated from GET /tenant-ai-settings/registry so
 * only catalog-approved (provider, model) combinations can be chosen.
 */

interface LlmRegistryItem {
  model: string;
  context_window: number;
  max_output_tokens: number;
  supports_tools: boolean;
  supports_vision: boolean;
  notes: string;
}

interface EmbeddingRegistryItem {
  model: string;
  dimensions: number;
  max_input_tokens: number;
  notes: string;
}

interface Registry {
  llm: Record<string, LlmRegistryItem[]>;
  embedding: Record<string, EmbeddingRegistryItem[]>;
}

interface Setting {
  tenant_id: string;
  llm_provider: string | null;
  llm_model: string | null;
  llm_fallback_model: string | null;
  llm_routing_policy: string;
  max_input_tokens: number | null;
  embedding_provider: string | null;
  embedding_model: string | null;
  embedding_dimensions: number | null;
  chunk_size: number | null;
  chunk_overlap: number | null;
  ai_fallback_policy: string;
}

const ROUTING_POLICIES = ["auto", "single", "fallback_only", "disabled"];
const FALLBACK_POLICIES = [
  { value: "allow", label: "Allow platform fallback" },
  { value: "deny", label: "Deny — require BYO tokens" },
];

export default function AIConfig() {
  const [registry, setRegistry] = useState<Registry | null>(null);
  const [setting, setSetting] = useState<Setting | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const [form, setForm] = useState({
    llm_provider: "",
    llm_model: "",
    llm_fallback_model: "",
    llm_routing_policy: "auto",
    max_input_tokens: "",
    embedding_provider: "",
    embedding_model: "",
    chunk_size: "",
    chunk_overlap: "",
    ai_fallback_policy: "allow",
  });

  const fetchAll = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [regRes, setRes] = await Promise.all([
        api.get("/tenant-ai-settings/registry"),
        api.get("/tenant-ai-settings"),
      ]);
      setRegistry(regRes.data);
      setSetting(setRes.data);
      setForm({
        llm_provider: setRes.data?.llm_provider || "",
        llm_model: setRes.data?.llm_model || "",
        llm_fallback_model: setRes.data?.llm_fallback_model || "",
        llm_routing_policy: setRes.data?.llm_routing_policy || "auto",
        max_input_tokens: setRes.data?.max_input_tokens?.toString() || "",
        embedding_provider: setRes.data?.embedding_provider || "",
        embedding_model: setRes.data?.embedding_model || "",
        chunk_size: setRes.data?.chunk_size?.toString() || "",
        chunk_overlap: setRes.data?.chunk_overlap?.toString() || "",
        ai_fallback_policy: setRes.data?.ai_fallback_policy || "allow",
      });
    } catch (e) {
      setError(extractApiError(e, "Failed to load AI config"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAll();
  }, [fetchAll]);

  async function handleSave() {
    setSaving(true);
    setError(null);
    setNotice(null);
    try {
      const payload: Record<string, unknown> = {};
      if (form.llm_provider) payload.llm_provider = form.llm_provider;
      if (form.llm_model) payload.llm_model = form.llm_model;
      if (form.llm_fallback_model) payload.llm_fallback_model = form.llm_fallback_model;
      if (form.llm_routing_policy) payload.llm_routing_policy = form.llm_routing_policy;
      if (form.max_input_tokens) payload.max_input_tokens = parseInt(form.max_input_tokens, 10);
      if (form.embedding_provider) payload.embedding_provider = form.embedding_provider;
      if (form.embedding_model) payload.embedding_model = form.embedding_model;
      if (form.chunk_size) payload.chunk_size = parseInt(form.chunk_size, 10);
      if (form.chunk_overlap) payload.chunk_overlap = parseInt(form.chunk_overlap, 10);
      if (form.ai_fallback_policy) payload.ai_fallback_policy = form.ai_fallback_policy;
      await api.put("/tenant-ai-settings", payload);
      setNotice("Config saved. Resolver cache will refresh within 60 seconds.");
      await fetchAll();
    } catch (e) {
      setError(extractApiError(e, "Failed to save config"));
    } finally {
      setSaving(false);
    }
  }

  const llmModels =
    registry && form.llm_provider ? registry.llm[form.llm_provider] || [] : [];
  const embeddingModels =
    registry && form.embedding_provider
      ? registry.embedding[form.embedding_provider] || []
      : [];
  const selectedEmbedding = embeddingModels.find(
    (m) => m.model === form.embedding_model,
  );

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-bold">AI Configuration</h2>
        <p className="text-sm text-muted-foreground mt-1">
          Pick the LLM and embedding model your agents and knowledge base use.
          Paired with <a href="/dashboard/settings/ai-credentials" className="underline">AI Credentials</a> for BYO provider tokens.
        </p>
      </div>

      {notice && (
        <div className="rounded-lg border border-emerald-200 bg-emerald-50 text-emerald-900 px-3 py-2 text-sm" role="status">
          {notice}
        </div>
      )}
      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 text-red-900 px-3 py-2 text-sm" role="alert">
          {error}
        </div>
      )}

      {loading ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : (
        <>
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Large Language Model</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <div>
                  <label className="text-xs font-medium text-muted-foreground">Provider</label>
                  <select
                    className="w-full border rounded px-2 py-1.5 text-sm bg-background"
                    value={form.llm_provider}
                    onChange={(e) => setForm({ ...form, llm_provider: e.target.value, llm_model: "", llm_fallback_model: "" })}
                  >
                    <option value="">— Use platform default —</option>
                    {registry && Object.keys(registry.llm).map((p) => (
                      <option key={p} value={p}>{p}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="text-xs font-medium text-muted-foreground">Model</label>
                  <select
                    className="w-full border rounded px-2 py-1.5 text-sm bg-background"
                    value={form.llm_model}
                    onChange={(e) => setForm({ ...form, llm_model: e.target.value })}
                    disabled={!form.llm_provider}
                  >
                    <option value="">— Select —</option>
                    {llmModels.map((m) => (
                      <option key={m.model} value={m.model}>
                        {m.model} · ctx {m.context_window.toLocaleString()}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <div>
                  <label className="text-xs font-medium text-muted-foreground">Fallback model</label>
                  <select
                    className="w-full border rounded px-2 py-1.5 text-sm bg-background"
                    value={form.llm_fallback_model}
                    onChange={(e) => setForm({ ...form, llm_fallback_model: e.target.value })}
                    disabled={!form.llm_provider}
                  >
                    <option value="">— None —</option>
                    {llmModels.map((m) => (
                      <option key={m.model} value={m.model}>{m.model}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="text-xs font-medium text-muted-foreground">Routing policy</label>
                  <select
                    className="w-full border rounded px-2 py-1.5 text-sm bg-background"
                    value={form.llm_routing_policy}
                    onChange={(e) => setForm({ ...form, llm_routing_policy: e.target.value })}
                  >
                    {ROUTING_POLICIES.map((p) => (
                      <option key={p} value={p}>{p}</option>
                    ))}
                  </select>
                </div>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Embedding model</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <div>
                  <label className="text-xs font-medium text-muted-foreground">Provider</label>
                  <select
                    className="w-full border rounded px-2 py-1.5 text-sm bg-background"
                    value={form.embedding_provider}
                    onChange={(e) => setForm({ ...form, embedding_provider: e.target.value, embedding_model: "" })}
                  >
                    <option value="">— Use platform default —</option>
                    {registry && Object.keys(registry.embedding).map((p) => (
                      <option key={p} value={p}>{p}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="text-xs font-medium text-muted-foreground">Model</label>
                  <select
                    className="w-full border rounded px-2 py-1.5 text-sm bg-background"
                    value={form.embedding_model}
                    onChange={(e) => setForm({ ...form, embedding_model: e.target.value })}
                    disabled={!form.embedding_provider}
                  >
                    <option value="">— Select —</option>
                    {embeddingModels.map((m) => (
                      <option key={m.model} value={m.model}>
                        {m.model} · dims {m.dimensions}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
              {selectedEmbedding && selectedEmbedding.dimensions !== setting?.embedding_dimensions && (
                <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded px-2 py-1">
                  Warning: changing embedding dimensions from {setting?.embedding_dimensions ?? "none"} to {selectedEmbedding.dimensions} requires a backfill. Run <code>scripts/embedding_rotate.py</code> before switching models on a production index.
                </p>
              )}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <div>
                  <label className="text-xs font-medium text-muted-foreground">Chunk size (tokens)</label>
                  <input
                    type="number"
                    className="w-full border rounded px-2 py-1.5 text-sm bg-background"
                    value={form.chunk_size}
                    onChange={(e) => setForm({ ...form, chunk_size: e.target.value })}
                    min={32}
                    max={8192}
                  />
                </div>
                <div>
                  <label className="text-xs font-medium text-muted-foreground">Chunk overlap</label>
                  <input
                    type="number"
                    className="w-full border rounded px-2 py-1.5 text-sm bg-background"
                    value={form.chunk_overlap}
                    onChange={(e) => setForm({ ...form, chunk_overlap: e.target.value })}
                    min={0}
                    max={1024}
                  />
                </div>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Fallback policy</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <select
                className="w-full md:w-96 border rounded px-2 py-1.5 text-sm bg-background"
                value={form.ai_fallback_policy}
                onChange={(e) => setForm({ ...form, ai_fallback_policy: e.target.value })}
              >
                {FALLBACK_POLICIES.map((p) => (
                  <option key={p.value} value={p.value}>{p.label}</option>
                ))}
              </select>
              <p className="text-xs text-muted-foreground">
                When <strong>deny</strong>, the platform refuses to use its own provider keys for your tenant — every LLM, embedding, and RAG call must use a BYO credential. Use this for regulated deployments.
              </p>
            </CardContent>
          </Card>

          <div className="flex gap-2">
            <Button onClick={handleSave} disabled={saving}>
              {saving ? "Saving…" : "Save configuration"}
            </Button>
          </div>
        </>
      )}
    </div>
  );
}
