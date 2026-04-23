import { useCallback, useEffect, useState } from "react";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import api, { extractApiError } from "@/lib/api";

/**
 * RPA Schedules — tenant-facing UI for the generic RPA framework.
 *
 * Lists every script discovered by the backend registry
 * (`GET /rpa-schedules/registry`) + lets admins schedule recurring
 * runs that drop vector-embedded chunks into the knowledge base.
 *
 * The 4.8/5 quality target is surfaced as a status pill on each
 * schedule's last-run row so operators can see at a glance when a
 * script starts producing sub-target output.
 */

interface RegistryScript {
  script_key: string;
  name: string;
  description: string | null;
  category: string | null;
  estimated_duration_s: number | null;
  target_quality: number | null;
  admin_only: boolean;
  produces_chunks: boolean;
  params_schema: Record<string, { label?: string; required?: boolean; type?: string }>;
}

interface RPASchedule {
  id: string;
  name: string;
  script_key: string;
  cron_expression: string;
  enabled: boolean;
  params: Record<string, string>;
  config: Record<string, unknown>;
  last_run_at: string | null;
  next_run_at: string | null;
  last_run_status: string | null;
  last_run_chunks_published: number | null;
  last_run_chunks_rejected: number | null;
  last_quality_avg: number | null;
}

const CRON_PRESETS = [
  { value: "every_5_minutes", label: "Every 5 minutes" },
  { value: "every_15_minutes", label: "Every 15 minutes" },
  { value: "hourly", label: "Hourly" },
  { value: "daily", label: "Daily" },
  { value: "weekly", label: "Weekly" },
  { value: "monthly", label: "Monthly" },
];

function qualityBadge(avg: number | null): { label: string; variant: "success" | "warning" | "destructive" | "secondary" } {
  if (avg == null) return { label: "—", variant: "secondary" };
  if (avg >= 4.8) return { label: `${avg.toFixed(2)}/5`, variant: "success" };
  if (avg >= 4.5) return { label: `${avg.toFixed(2)}/5 (review)`, variant: "warning" };
  return { label: `${avg.toFixed(2)}/5 (below target)`, variant: "destructive" };
}

export default function RPASchedules() {
  const [registry, setRegistry] = useState<RegistryScript[]>([]);
  const [schedules, setSchedules] = useState<RPASchedule[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // Create form state
  const [showForm, setShowForm] = useState(false);
  const [formName, setFormName] = useState("");
  const [formScript, setFormScript] = useState("");
  const [formCron, setFormCron] = useState("daily");
  const [formParams, setFormParams] = useState<Record<string, string>>({});
  const [formSubmitting, setFormSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const fetchAll = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [regRes, schedRes] = await Promise.all([
        api.get("/rpa-schedules/registry"),
        api.get("/rpa-schedules"),
      ]);
      setRegistry(Array.isArray(regRes.data?.items) ? regRes.data.items : []);
      setSchedules(Array.isArray(schedRes.data) ? schedRes.data : []);
    } catch (e) {
      setError(extractApiError(e, "Failed to load RPA schedules"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAll();
  }, [fetchAll]);

  function resetForm() {
    setFormName("");
    setFormScript("");
    setFormCron("daily");
    setFormParams({});
    setFormError(null);
    setShowForm(false);
  }

  async function handleCreate() {
    setFormError(null);
    if (!formName.trim()) {
      setFormError("Name is required");
      return;
    }
    if (!formScript) {
      setFormError("Pick an RPA script");
      return;
    }
    setFormSubmitting(true);
    try {
      await api.post("/rpa-schedules", {
        name: formName.trim(),
        script_key: formScript,
        cron_expression: formCron,
        enabled: true,
        params: formParams,
      });
      resetForm();
      setNotice("Schedule created.");
      await fetchAll();
    } catch (e) {
      setFormError(extractApiError(e, "Failed to create schedule"));
    } finally {
      setFormSubmitting(false);
    }
  }

  async function handleRunNow(s: RPASchedule) {
    setNotice(null);
    try {
      const { data } = await api.post(`/rpa-schedules/${s.id}/run-now`);
      setNotice(
        data?.task_id
          ? `Queued run for "${s.name}" (task ${String(data.task_id).slice(0, 8)}…). Refresh in a few seconds to see the result.`
          : `Queued run for "${s.name}".`,
      );
    } catch (e) {
      setError(extractApiError(e, "Failed to queue run"));
    }
  }

  async function handleToggle(s: RPASchedule) {
    try {
      await api.patch(`/rpa-schedules/${s.id}`, { enabled: !s.enabled });
      await fetchAll();
    } catch (e) {
      setError(extractApiError(e, "Failed to toggle schedule"));
    }
  }

  async function handleDelete(s: RPASchedule) {
    if (!window.confirm(`Delete the schedule "${s.name}"?`)) return;
    try {
      await api.delete(`/rpa-schedules/${s.id}`);
      await fetchAll();
    } catch (e) {
      setError(extractApiError(e, "Failed to delete schedule"));
    }
  }

  const selectedScript = registry.find((r) => r.script_key === formScript) || null;

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h2 className="text-2xl font-bold">RPA Schedules</h2>
          <p className="text-sm text-muted-foreground mt-1">
            Run browser-automation scripts on a schedule and feed the
            results into the knowledge base. Target vector-embedding
            quality: 4.8 / 5.
          </p>
        </div>
        <Button onClick={() => setShowForm((v) => !v)}>
          {showForm ? "Cancel" : "New Schedule"}
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
            <CardTitle className="text-base">Create Schedule</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <div>
                <label className="text-xs font-medium text-muted-foreground">Name</label>
                <input
                  className="w-full border rounded px-2 py-1.5 text-sm bg-background"
                  value={formName}
                  onChange={(e) => setFormName(e.target.value)}
                  placeholder="e.g. RBI daily press releases"
                />
              </div>
              <div>
                <label className="text-xs font-medium text-muted-foreground">Script</label>
                <select
                  className="w-full border rounded px-2 py-1.5 text-sm bg-background"
                  value={formScript}
                  onChange={(e) => setFormScript(e.target.value)}
                >
                  <option value="">— Select —</option>
                  {registry.map((r) => (
                    <option key={r.script_key} value={r.script_key}>
                      {r.name} ({r.category || "general"})
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="text-xs font-medium text-muted-foreground">Frequency</label>
                <select
                  className="w-full border rounded px-2 py-1.5 text-sm bg-background"
                  value={formCron}
                  onChange={(e) => setFormCron(e.target.value)}
                >
                  {CRON_PRESETS.map((p) => (
                    <option key={p.value} value={p.value}>
                      {p.label}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            {selectedScript?.description && (
              <p className="text-xs text-muted-foreground">{selectedScript.description}</p>
            )}

            {selectedScript && Object.keys(selectedScript.params_schema).length > 0 && (
              <div className="space-y-2 border rounded p-3 bg-muted/20">
                <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Script parameters</p>
                {Object.entries(selectedScript.params_schema).map(([key, def]) => (
                  <div key={key}>
                    <label className="text-xs text-muted-foreground">
                      {def.label || key}
                      {def.required ? " *" : ""}
                    </label>
                    <input
                      type={def.type === "password" ? "password" : "text"}
                      className="w-full border rounded px-2 py-1.5 text-sm bg-background"
                      value={formParams[key] || ""}
                      onChange={(e) => setFormParams((prev) => ({ ...prev, [key]: e.target.value }))}
                    />
                  </div>
                ))}
              </div>
            )}

            {formError && <p className="text-sm text-red-600">{formError}</p>}
            <div className="flex gap-2">
              <Button onClick={handleCreate} disabled={formSubmitting}>
                {formSubmitting ? "Creating…" : "Create"}
              </Button>
              <Button variant="outline" onClick={resetForm} disabled={formSubmitting}>
                Cancel
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Active schedules</CardTitle>
        </CardHeader>
        <CardContent>
          {loading ? (
            <p className="text-sm text-muted-foreground">Loading…</p>
          ) : schedules.length === 0 ? (
            <p className="text-sm text-muted-foreground">No RPA schedules yet. Create one to get started.</p>
          ) : (
            <div className="space-y-2">
              {schedules.map((s) => {
                const q = qualityBadge(s.last_quality_avg);
                return (
                  <div
                    key={s.id}
                    className="border rounded-lg p-3 flex flex-col md:flex-row md:items-center md:justify-between gap-3"
                  >
                    <div className="space-y-1">
                      <div className="flex items-center gap-2">
                        <p className="font-medium">{s.name}</p>
                        <Badge variant={s.enabled ? "default" : "secondary"}>
                          {s.enabled ? "enabled" : "disabled"}
                        </Badge>
                        <Badge variant={q.variant}>quality {q.label}</Badge>
                      </div>
                      <p className="text-xs text-muted-foreground">
                        {s.script_key} • {s.cron_expression} •{" "}
                        {s.last_run_status ? `last: ${s.last_run_status}` : "never run"}
                        {s.last_run_chunks_published != null && s.last_run_status === "success" &&
                          ` • ${s.last_run_chunks_published} published, ${s.last_run_chunks_rejected ?? 0} rejected`}
                      </p>
                    </div>
                    <div className="flex gap-2">
                      <Button size="sm" variant="outline" onClick={() => handleRunNow(s)}>
                        Run now
                      </Button>
                      <Button size="sm" variant="outline" onClick={() => handleToggle(s)}>
                        {s.enabled ? "Disable" : "Enable"}
                      </Button>
                      <Button size="sm" variant="destructive" onClick={() => handleDelete(s)}>
                        Delete
                      </Button>
                    </div>
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
