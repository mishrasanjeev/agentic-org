import { useState, useEffect, useCallback } from "react";

/* ── Types ─────────────────────────────────────────────────────────── */

interface DeliveryChannel {
  type: "email" | "slack" | "whatsapp";
  target: string;
}

interface ReportSchedule {
  id: string;
  report_type: string;
  cron_expression: string;
  delivery_channels: DeliveryChannel[];
  format: string;
  is_active: boolean;
  company_id: string;
  params: Record<string, unknown>;
  tenant_id: string;
  last_run_at: string | null;
  next_run_at: string | null;
  created_at: string;
}

/* ── Constants ─────────────────────────────────────────────────────── */

const REPORT_TYPES = [
  { value: "cfo_daily", label: "CFO Daily Briefing" },
  { value: "cmo_weekly", label: "CMO Weekly Report" },
  { value: "pnl_report", label: "P&L Monthly" },
  { value: "aging_report", label: "AR/AP Aging" },
  { value: "campaign_report", label: "Campaign Performance" },
] as const;

const SCHEDULE_PRESETS = [
  { value: "daily", label: "Daily" },
  { value: "weekly", label: "Weekly" },
  { value: "monthly", label: "Monthly" },
] as const;

const FORMAT_OPTIONS = [
  { value: "pdf", label: "PDF" },
  { value: "excel", label: "Excel" },
  { value: "both", label: "Both" },
] as const;

const API_BASE = "/api/v1";

/* ── Helpers ───────────────────────────────────────────────────────── */

function reportTypeLabel(rt: string): string {
  return REPORT_TYPES.find((t) => t.value === rt)?.label ?? rt;
}

function formatDate(iso: string | null): string {
  if (!iso) return "--";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

function statusBadge(active: boolean) {
  return active ? (
    <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-800">
      Active
    </span>
  ) : (
    <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-slate-100 text-slate-600">
      Paused
    </span>
  );
}

/* ── Component ─────────────────────────────────────────────────────── */

export default function ReportScheduler() {
  const [schedules, setSchedules] = useState<ReportSchedule[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  /* ── Form state ── */
  const [formType, setFormType] = useState("cfo_daily");
  const [formCron, setFormCron] = useState("daily");
  const [formFormat, setFormFormat] = useState("pdf");
  const [formActive, setFormActive] = useState(true);
  const [formEmailEnabled, setFormEmailEnabled] = useState(true);
  const [formEmailTarget, setFormEmailTarget] = useState("");
  const [formSlackEnabled, setFormSlackEnabled] = useState(false);
  const [formSlackTarget, setFormSlackTarget] = useState("");
  const [formWhatsappEnabled, setFormWhatsappEnabled] = useState(false);
  const [formWhatsappTarget, setFormWhatsappTarget] = useState("");

  /* ── Fetch schedules ── */
  const fetchSchedules = useCallback(async () => {
    try {
      setLoading(true);
      const token = localStorage.getItem("token") || "";
      const resp = await fetch(`${API_BASE}/report-schedules`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data: ReportSchedule[] = await resp.json();
      setSchedules(data);
      setError(null);
    } catch (e: any) {
      setError(e.message ?? "Failed to load schedules");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchSchedules();
  }, [fetchSchedules]);

  /* ── Build channels from form ── */
  function buildChannels(): DeliveryChannel[] {
    const channels: DeliveryChannel[] = [];
    if (formEmailEnabled && formEmailTarget.trim()) {
      channels.push({ type: "email", target: formEmailTarget.trim() });
    }
    if (formSlackEnabled && formSlackTarget.trim()) {
      channels.push({ type: "slack", target: formSlackTarget.trim() });
    }
    if (formWhatsappEnabled && formWhatsappTarget.trim()) {
      channels.push({ type: "whatsapp", target: formWhatsappTarget.trim() });
    }
    return channels;
  }

  /* ── Reset form ── */
  function resetForm() {
    setFormType("cfo_daily");
    setFormCron("daily");
    setFormFormat("pdf");
    setFormActive(true);
    setFormEmailEnabled(true);
    setFormEmailTarget("");
    setFormSlackEnabled(false);
    setFormSlackTarget("");
    setFormWhatsappEnabled(false);
    setFormWhatsappTarget("");
    setEditingId(null);
    setShowForm(false);
  }

  /* ── Populate form for edit ── */
  function startEdit(s: ReportSchedule) {
    setEditingId(s.id);
    setFormType(s.report_type);
    setFormCron(s.cron_expression);
    setFormFormat(s.format);
    setFormActive(s.is_active);

    const email = s.delivery_channels.find((c) => c.type === "email");
    const slack = s.delivery_channels.find((c) => c.type === "slack");
    const wa = s.delivery_channels.find((c) => c.type === "whatsapp");

    setFormEmailEnabled(!!email);
    setFormEmailTarget(email?.target ?? "");
    setFormSlackEnabled(!!slack);
    setFormSlackTarget(slack?.target ?? "");
    setFormWhatsappEnabled(!!wa);
    setFormWhatsappTarget(wa?.target ?? "");

    setShowForm(true);
  }

  /* ── Create / Update ── */
  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const token = localStorage.getItem("token") || "";
    const channels = buildChannels();

    try {
      if (editingId) {
        await fetch(`${API_BASE}/report-schedules/${editingId}`, {
          method: "PATCH",
          headers: {
            Authorization: `Bearer ${token}`,
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            report_type: formType,
            cron_expression: formCron,
            delivery_channels: channels,
            format: formFormat,
            is_active: formActive,
          }),
        });
      } else {
        await fetch(`${API_BASE}/report-schedules`, {
          method: "POST",
          headers: {
            Authorization: `Bearer ${token}`,
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            report_type: formType,
            cron_expression: formCron,
            delivery_channels: channels,
            format: formFormat,
            is_active: formActive,
          }),
        });
      }
      resetForm();
      fetchSchedules();
    } catch (err: any) {
      setError(err.message ?? "Save failed");
    }
  }

  /* ── Delete ── */
  async function handleDelete(id: string) {
    if (!confirm("Delete this schedule?")) return;
    const token = localStorage.getItem("token") || "";
    await fetch(`${API_BASE}/report-schedules/${id}`, {
      method: "DELETE",
      headers: { Authorization: `Bearer ${token}` },
    });
    fetchSchedules();
  }

  /* ── Toggle active ── */
  async function handleToggle(s: ReportSchedule) {
    const token = localStorage.getItem("token") || "";
    await fetch(`${API_BASE}/report-schedules/${s.id}`, {
      method: "PATCH",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ is_active: !s.is_active }),
    });
    fetchSchedules();
  }

  /* ── Run now ── */
  async function handleRunNow(id: string) {
    setActionLoading(id);
    const token = localStorage.getItem("token") || "";
    try {
      const resp = await fetch(
        `${API_BASE}/report-schedules/${id}/run-now`,
        {
          method: "POST",
          headers: { Authorization: `Bearer ${token}` },
        }
      );
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      alert(`Report triggered. Task ID: ${data.task_id}`);
      fetchSchedules();
    } catch (err: any) {
      setError(err.message ?? "Run failed");
    } finally {
      setActionLoading(null);
    }
  }

  /* ── Render ──────────────────────────────────────────────────────── */

  return (
    <div className="max-w-6xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold">Report Schedules</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Configure automated report generation and delivery
          </p>
        </div>
        <button
          onClick={() => {
            resetForm();
            setShowForm(true);
          }}
          className="px-4 py-2 rounded-lg bg-primary text-primary-foreground text-sm font-medium hover:opacity-90 transition-opacity"
        >
          + New Schedule
        </button>
      </div>

      {/* Error banner */}
      {error && (
        <div className="mb-4 px-4 py-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
          {error}
          <button
            onClick={() => setError(null)}
            className="ml-2 underline"
          >
            Dismiss
          </button>
        </div>
      )}

      {/* ── Create / Edit form ───────────────────────────────────── */}
      {showForm && (
        <div className="mb-6 border rounded-lg p-5 bg-card">
          <h2 className="text-lg font-semibold mb-4">
            {editingId ? "Edit Schedule" : "Create Schedule"}
          </h2>
          <form onSubmit={handleSubmit} className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            {/* Report type */}
            <div>
              <label className="block text-sm font-medium mb-1">
                Report Type
              </label>
              <select
                value={formType}
                onChange={(e) => setFormType(e.target.value)}
                className="w-full border rounded-lg px-3 py-2 text-sm bg-background"
              >
                {REPORT_TYPES.map((t) => (
                  <option key={t.value} value={t.value}>
                    {t.label}
                  </option>
                ))}
              </select>
            </div>

            {/* Schedule */}
            <div>
              <label className="block text-sm font-medium mb-1">
                Schedule
              </label>
              <select
                value={formCron}
                onChange={(e) => setFormCron(e.target.value)}
                className="w-full border rounded-lg px-3 py-2 text-sm bg-background"
              >
                {SCHEDULE_PRESETS.map((p) => (
                  <option key={p.value} value={p.value}>
                    {p.label}
                  </option>
                ))}
              </select>
            </div>

            {/* Format */}
            <div>
              <label className="block text-sm font-medium mb-1">
                Output Format
              </label>
              <select
                value={formFormat}
                onChange={(e) => setFormFormat(e.target.value)}
                className="w-full border rounded-lg px-3 py-2 text-sm bg-background"
              >
                {FORMAT_OPTIONS.map((f) => (
                  <option key={f.value} value={f.value}>
                    {f.label}
                  </option>
                ))}
              </select>
            </div>

            {/* Active toggle */}
            <div className="flex items-center gap-2 pt-6">
              <input
                type="checkbox"
                id="active-toggle"
                checked={formActive}
                onChange={(e) => setFormActive(e.target.checked)}
                className="rounded"
              />
              <label htmlFor="active-toggle" className="text-sm">
                Schedule active
              </label>
            </div>

            {/* Delivery channels */}
            <div className="sm:col-span-2 space-y-3">
              <p className="text-sm font-medium">Delivery Channels</p>

              {/* Email */}
              <div className="flex items-center gap-3">
                <input
                  type="checkbox"
                  id="ch-email"
                  checked={formEmailEnabled}
                  onChange={(e) => setFormEmailEnabled(e.target.checked)}
                  className="rounded"
                />
                <label htmlFor="ch-email" className="text-sm w-24">
                  Email
                </label>
                {formEmailEnabled && (
                  <input
                    type="email"
                    placeholder="recipient@company.com"
                    value={formEmailTarget}
                    onChange={(e) => setFormEmailTarget(e.target.value)}
                    className="flex-1 border rounded-lg px-3 py-1.5 text-sm bg-background"
                  />
                )}
              </div>

              {/* Slack */}
              <div className="flex items-center gap-3">
                <input
                  type="checkbox"
                  id="ch-slack"
                  checked={formSlackEnabled}
                  onChange={(e) => setFormSlackEnabled(e.target.checked)}
                  className="rounded"
                />
                <label htmlFor="ch-slack" className="text-sm w-24">
                  Slack
                </label>
                {formSlackEnabled && (
                  <input
                    type="text"
                    placeholder="Channel ID (e.g. C01ABC23DEF)"
                    value={formSlackTarget}
                    onChange={(e) => setFormSlackTarget(e.target.value)}
                    className="flex-1 border rounded-lg px-3 py-1.5 text-sm bg-background"
                  />
                )}
              </div>

              {/* WhatsApp */}
              <div className="flex items-center gap-3">
                <input
                  type="checkbox"
                  id="ch-whatsapp"
                  checked={formWhatsappEnabled}
                  onChange={(e) => setFormWhatsappEnabled(e.target.checked)}
                  className="rounded"
                />
                <label htmlFor="ch-whatsapp" className="text-sm w-24">
                  WhatsApp
                </label>
                {formWhatsappEnabled && (
                  <input
                    type="tel"
                    placeholder="+91 98765 43210"
                    value={formWhatsappTarget}
                    onChange={(e) => setFormWhatsappTarget(e.target.value)}
                    className="flex-1 border rounded-lg px-3 py-1.5 text-sm bg-background"
                  />
                )}
              </div>
            </div>

            {/* Actions */}
            <div className="sm:col-span-2 flex gap-3 pt-2">
              <button
                type="submit"
                className="px-4 py-2 rounded-lg bg-primary text-primary-foreground text-sm font-medium hover:opacity-90"
              >
                {editingId ? "Update Schedule" : "Create Schedule"}
              </button>
              <button
                type="button"
                onClick={resetForm}
                className="px-4 py-2 rounded-lg border text-sm hover:bg-muted"
              >
                Cancel
              </button>
            </div>
          </form>
        </div>
      )}

      {/* ── Schedule list ────────────────────────────────────────── */}
      {loading ? (
        <div className="text-center py-12 text-sm text-muted-foreground">
          Loading schedules...
        </div>
      ) : schedules.length === 0 ? (
        <div className="text-center py-12 border rounded-lg bg-card">
          <p className="text-muted-foreground">
            No report schedules yet. Click "New Schedule" to get started.
          </p>
        </div>
      ) : (
        <div className="border rounded-lg overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-muted/50 border-b">
                <th className="text-left px-4 py-3 font-medium">Report</th>
                <th className="text-left px-4 py-3 font-medium hidden sm:table-cell">
                  Schedule
                </th>
                <th className="text-left px-4 py-3 font-medium hidden md:table-cell">
                  Next Run
                </th>
                <th className="text-left px-4 py-3 font-medium hidden md:table-cell">
                  Last Run
                </th>
                <th className="text-left px-4 py-3 font-medium">Status</th>
                <th className="text-left px-4 py-3 font-medium hidden sm:table-cell">
                  Format
                </th>
                <th className="text-right px-4 py-3 font-medium">Actions</th>
              </tr>
            </thead>
            <tbody>
              {schedules.map((s) => (
                <tr key={s.id} className="border-b last:border-0 hover:bg-muted/30">
                  <td className="px-4 py-3 font-medium">
                    {reportTypeLabel(s.report_type)}
                    <div className="text-xs text-muted-foreground mt-0.5">
                      {s.delivery_channels
                        .map((c) => c.type)
                        .join(", ") || "No delivery"}
                    </div>
                  </td>
                  <td className="px-4 py-3 hidden sm:table-cell capitalize">
                    {s.cron_expression}
                  </td>
                  <td className="px-4 py-3 hidden md:table-cell text-muted-foreground">
                    {formatDate(s.next_run_at)}
                  </td>
                  <td className="px-4 py-3 hidden md:table-cell text-muted-foreground">
                    {formatDate(s.last_run_at)}
                  </td>
                  <td className="px-4 py-3">
                    <button onClick={() => handleToggle(s)} title="Toggle active">
                      {statusBadge(s.is_active)}
                    </button>
                  </td>
                  <td className="px-4 py-3 hidden sm:table-cell uppercase text-xs">
                    {s.format}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex justify-end gap-2">
                      <button
                        onClick={() => handleRunNow(s.id)}
                        disabled={actionLoading === s.id}
                        className="px-2.5 py-1 rounded border text-xs hover:bg-muted disabled:opacity-50"
                        title="Run now"
                      >
                        {actionLoading === s.id ? "..." : "Run"}
                      </button>
                      <button
                        onClick={() => startEdit(s)}
                        className="px-2.5 py-1 rounded border text-xs hover:bg-muted"
                        title="Edit"
                      >
                        Edit
                      </button>
                      <button
                        onClick={() => handleDelete(s.id)}
                        className="px-2.5 py-1 rounded border text-xs text-red-600 hover:bg-red-50"
                        title="Delete"
                      >
                        Del
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
