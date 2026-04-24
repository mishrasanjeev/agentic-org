import { useCallback, useEffect, useState } from "react";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import api, { extractApiError } from "@/lib/api";

/**
 * Tenant BYO AI provider credentials — admin-only.
 *
 * Lists every registered provider token (LLM / embedding / RAG / STT /
 * TTS), lets an admin create/rotate/test/delete. Never displays raw
 * tokens; only prefix/suffix + status. Closes S0-09 (PR-1 of the
 * four-PR closure plan at docs/STRICT_REPO_S0_CLOSURE_PLAN_2026-04-24.md).
 */

interface Credential {
  id: string;
  provider: string;
  credential_kind: string;
  status: "active" | "inactive" | "unverified" | "failing";
  label: string | null;
  display_prefix: string | null;
  display_suffix: string | null;
  provider_config: Record<string, unknown> | null;
  last_health_check_at: string | null;
  last_health_check_error: string | null;
  last_used_at: string | null;
  rotated_at: string | null;
  created_at: string;
  updated_at: string;
}

const PROVIDERS = [
  { value: "gemini", label: "Google Gemini" },
  { value: "openai", label: "OpenAI" },
  { value: "anthropic", label: "Anthropic" },
  { value: "azure_openai", label: "Azure OpenAI" },
  { value: "openai_compatible", label: "OpenAI-compatible (self-hosted / 3rd-party)" },
  { value: "voyage", label: "Voyage AI" },
  { value: "cohere", label: "Cohere" },
  { value: "ragflow", label: "RAGFlow" },
  { value: "stt_deepgram", label: "Deepgram STT" },
  { value: "stt_azure", label: "Azure Speech STT" },
  { value: "tts_elevenlabs", label: "ElevenLabs TTS" },
  { value: "tts_azure", label: "Azure Speech TTS" },
];

const KINDS = [
  { value: "llm", label: "LLM" },
  { value: "embedding", label: "Embedding" },
  { value: "rag", label: "RAG" },
  { value: "stt", label: "STT" },
  { value: "tts", label: "TTS" },
];

function statusVariant(s: Credential["status"]): "success" | "warning" | "destructive" | "secondary" {
  if (s === "active") return "success";
  if (s === "failing") return "destructive";
  if (s === "inactive") return "secondary";
  return "warning";
}

function formatTimestamp(v: string | null): string {
  if (!v) return "—";
  try {
    return new Date(v).toLocaleString();
  } catch {
    return "—";
  }
}

export default function AICredentials() {
  const [items, setItems] = useState<Credential[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const [showForm, setShowForm] = useState(false);
  const [formProvider, setFormProvider] = useState("openai");
  const [formKind, setFormKind] = useState("llm");
  const [formApiKey, setFormApiKey] = useState("");
  const [formLabel, setFormLabel] = useState("");
  const [formBaseUrl, setFormBaseUrl] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const [rotateFor, setRotateFor] = useState<string | null>(null);
  const [rotateKey, setRotateKey] = useState("");

  const fetchItems = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const { data } = await api.get("/tenant-ai-credentials");
      setItems(Array.isArray(data) ? data : []);
    } catch (e) {
      setError(extractApiError(e, "Failed to load credentials"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchItems();
  }, [fetchItems]);

  function resetForm() {
    setFormProvider("openai");
    setFormKind("llm");
    setFormApiKey("");
    setFormLabel("");
    setFormBaseUrl("");
    setShowForm(false);
    setFormError(null);
  }

  async function handleCreate() {
    setFormError(null);
    if (!formApiKey.trim() || formApiKey.trim().length < 8) {
      setFormError("API key is required and must be at least 8 characters.");
      return;
    }
    setSubmitting(true);
    try {
      const payload: Record<string, unknown> = {
        provider: formProvider,
        credential_kind: formKind,
        api_key: formApiKey,
      };
      if (formLabel.trim()) payload.label = formLabel.trim();
      if (formBaseUrl.trim()) payload.provider_config = { base_url: formBaseUrl.trim() };
      await api.post("/tenant-ai-credentials", payload);
      setNotice("Credential saved. Click 'Test' to verify it works.");
      resetForm();
      await fetchItems();
    } catch (e) {
      setFormError(extractApiError(e, "Failed to save credential"));
    } finally {
      setSubmitting(false);
    }
  }

  async function handleTest(id: string) {
    setNotice(null);
    try {
      const { data } = await api.post(`/tenant-ai-credentials/${id}/test`);
      if (data?.status === "active") {
        setNotice("Credential is active — provider accepted the key.");
      } else {
        setError(data?.error || "Credential test failed");
      }
      await fetchItems();
    } catch (e) {
      setError(extractApiError(e, "Test failed"));
    }
  }

  async function handleRotate(id: string) {
    if (!rotateKey.trim() || rotateKey.trim().length < 8) {
      setError("New API key is required and must be at least 8 characters.");
      return;
    }
    try {
      await api.patch(`/tenant-ai-credentials/${id}`, { api_key: rotateKey });
      setNotice("Credential rotated. Click 'Test' to verify.");
      setRotateFor(null);
      setRotateKey("");
      await fetchItems();
    } catch (e) {
      setError(extractApiError(e, "Rotation failed"));
    }
  }

  async function handleDelete(item: Credential) {
    if (!window.confirm(`Delete the ${item.provider}/${item.credential_kind} credential?`)) return;
    try {
      await api.delete(`/tenant-ai-credentials/${item.id}`);
      setNotice("Credential deleted.");
      await fetchItems();
    } catch (e) {
      setError(extractApiError(e, "Delete failed"));
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h2 className="text-2xl font-bold">AI Provider Credentials</h2>
          <p className="text-sm text-muted-foreground mt-1">
            Bring-your-own LLM, embedding, RAG, STT, and TTS tokens. Tokens
            are encrypted at rest, masked on display, and never appear in
            logs or API responses.
          </p>
        </div>
        <Button onClick={() => setShowForm((v) => !v)}>
          {showForm ? "Cancel" : "New Credential"}
        </Button>
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

      {showForm && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">New Credential</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <div>
                <label className="text-xs font-medium text-muted-foreground">Provider</label>
                <select
                  className="w-full border rounded px-2 py-1.5 text-sm bg-background"
                  value={formProvider}
                  onChange={(e) => setFormProvider(e.target.value)}
                >
                  {PROVIDERS.map((p) => (
                    <option key={p.value} value={p.value}>{p.label}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="text-xs font-medium text-muted-foreground">Kind</label>
                <select
                  className="w-full border rounded px-2 py-1.5 text-sm bg-background"
                  value={formKind}
                  onChange={(e) => setFormKind(e.target.value)}
                >
                  {KINDS.map((k) => (
                    <option key={k.value} value={k.value}>{k.label}</option>
                  ))}
                </select>
              </div>
            </div>
            <div>
              <label className="text-xs font-medium text-muted-foreground">API Key *</label>
              <input
                type="password"
                className="w-full border rounded px-2 py-1.5 text-sm bg-background font-mono"
                value={formApiKey}
                onChange={(e) => setFormApiKey(e.target.value)}
                placeholder="Paste the raw provider token — stored encrypted, never displayed again"
                autoComplete="new-password"
              />
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <div>
                <label className="text-xs font-medium text-muted-foreground">Label (optional)</label>
                <input
                  type="text"
                  className="w-full border rounded px-2 py-1.5 text-sm bg-background"
                  value={formLabel}
                  onChange={(e) => setFormLabel(e.target.value)}
                  placeholder="e.g. Finance team OpenAI"
                />
              </div>
              <div>
                <label className="text-xs font-medium text-muted-foreground">Base URL (optional)</label>
                <input
                  type="text"
                  className="w-full border rounded px-2 py-1.5 text-sm bg-background"
                  value={formBaseUrl}
                  onChange={(e) => setFormBaseUrl(e.target.value)}
                  placeholder="Required for Azure OpenAI + OpenAI-compatible endpoints"
                />
              </div>
            </div>
            {formError && <p className="text-sm text-red-600">{formError}</p>}
            <div className="flex gap-2">
              <Button onClick={handleCreate} disabled={submitting}>
                {submitting ? "Saving…" : "Save credential"}
              </Button>
              <Button variant="outline" onClick={resetForm} disabled={submitting}>
                Cancel
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Stored credentials</CardTitle>
        </CardHeader>
        <CardContent>
          {loading ? (
            <p className="text-sm text-muted-foreground">Loading…</p>
          ) : items.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No BYO credentials registered yet. The platform will use its own
              fallback tokens until you add one.
            </p>
          ) : (
            <div className="space-y-2">
              {items.map((it) => {
                const display = it.display_prefix || it.display_suffix
                  ? `${it.display_prefix || ""}...${it.display_suffix || ""}`
                  : "***";
                return (
                  <div
                    key={it.id}
                    className="border rounded-lg p-3 flex flex-col md:flex-row md:items-center md:justify-between gap-3"
                  >
                    <div className="space-y-1 min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <p className="font-medium">
                          {it.provider} / {it.credential_kind}
                        </p>
                        <Badge variant={statusVariant(it.status)}>{it.status}</Badge>
                        <span className="font-mono text-xs text-muted-foreground">{display}</span>
                        {it.label && <span className="text-xs text-muted-foreground">· {it.label}</span>}
                      </div>
                      <p className="text-xs text-muted-foreground">
                        Last tested: {formatTimestamp(it.last_health_check_at)}
                        {it.last_health_check_error && (
                          <span className="text-red-600 ml-2">
                            ({it.last_health_check_error})
                          </span>
                        )}
                        {it.last_used_at && <> · Last used: {formatTimestamp(it.last_used_at)}</>}
                        {it.rotated_at && <> · Rotated: {formatTimestamp(it.rotated_at)}</>}
                      </p>
                    </div>
                    <div className="flex gap-2">
                      <Button size="sm" variant="outline" onClick={() => handleTest(it.id)}>
                        Test
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => {
                          setRotateFor(rotateFor === it.id ? null : it.id);
                          setRotateKey("");
                        }}
                      >
                        {rotateFor === it.id ? "Cancel rotate" : "Rotate"}
                      </Button>
                      <Button size="sm" variant="destructive" onClick={() => handleDelete(it)}>
                        Delete
                      </Button>
                    </div>
                    {rotateFor === it.id && (
                      <div className="md:col-span-2 md:basis-full">
                        <div className="flex gap-2 items-center">
                          <input
                            type="password"
                            className="flex-1 border rounded px-2 py-1.5 text-sm bg-background font-mono"
                            value={rotateKey}
                            onChange={(e) => setRotateKey(e.target.value)}
                            placeholder="Paste the new API key"
                            autoComplete="new-password"
                          />
                          <Button size="sm" onClick={() => handleRotate(it.id)}>
                            Confirm rotation
                          </Button>
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
