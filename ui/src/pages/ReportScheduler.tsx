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

type FrequencyPreset = "daily" | "weekly" | "monthly";

const FREQUENCY_PRESETS: { value: FrequencyPreset; label: string }[] = [
  { value: "daily", label: "Daily" },
  { value: "weekly", label: "Weekly" },
  { value: "monthly", label: "Monthly" },
];

// Cron weekday convention: 0 or 7 = Sunday, 1 = Monday, ... 6 = Saturday.
const DAYS_OF_WEEK = [
  { value: "1", label: "Monday" },
  { value: "2", label: "Tuesday" },
  { value: "3", label: "Wednesday" },
  { value: "4", label: "Thursday" },
  { value: "5", label: "Friday" },
  { value: "6", label: "Saturday" },
  { value: "0", label: "Sunday" },
] as const;

const FORMAT_OPTIONS = [
  { value: "pdf", label: "PDF" },
  { value: "excel", label: "Excel" },
  { value: "both", label: "Both" },
] as const;

const API_BASE = "/api/v1";

// Must match the server-side regexes in api/v1/report_schedules.py so
// users see the same error client-side before the round-trip.
const EMAIL_RE = /^[^@\s]+@[^@\s.]+\.[^@\s]+$/;
const SLACK_RE = /^(?:[CGD][A-Z0-9]{8,10}|#[a-z0-9][a-z0-9._-]{0,78})$/;
const WHATSAPP_RE = /^\+?[1-9]\d{7,14}$/;

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

/** Compose a 5-field cron from the simple controls. */
function composeCron(
  freq: FrequencyPreset,
  time: string,
  dayOfWeek: string,
  dayOfMonth: string,
): string {
  const [hh, mm] = time.split(":");
  const minute = String(parseInt(mm, 10) || 0);
  const hour = String(parseInt(hh, 10) || 0);
  if (freq === "daily") return `${minute} ${hour} * * *`;
  if (freq === "weekly") return `${minute} ${hour} * * ${dayOfWeek}`;
  return `${minute} ${hour} ${dayOfMonth} * *`;
}

/** Try to map a stored cron back to the simple controls. Falls back to
 *  advanced mode when the pattern doesn't fit one of our three presets. */
function parseCron(cron: string): {
  advanced: boolean;
  freq: FrequencyPreset;
  time: string;
  dayOfWeek: string;
  dayOfMonth: string;
  raw: string;
} {
  const defaults = {
    advanced: false,
    freq: "daily" as FrequencyPreset,
    time: "09:00",
    dayOfWeek: "1",
    dayOfMonth: "1",
    raw: cron,
  };
  // Preset keyword — show as "Daily/Weekly/Monthly" with a default 09:00.
  if (cron === "daily" || cron === "weekly" || cron === "monthly") {
    return { ...defaults, freq: cron };
  }
  const parts = cron.trim().split(/\s+/);
  if (parts.length !== 5) return { ...defaults, advanced: true };
  const [m, h, dom, mo, dow] = parts;
  const mNum = parseInt(m, 10);
  const hNum = parseInt(h, 10);
  if (Number.isNaN(mNum) || Number.isNaN(hNum) || mo !== "*") {
    return { ...defaults, advanced: true };
  }
  const time = `${String(hNum).padStart(2, "0")}:${String(mNum).padStart(2, "0")}`;
  if (dom === "*" && dow === "*") {
    return { ...defaults, freq: "daily", time };
  }
  if (dom === "*" && /^[0-7]$/.test(dow)) {
    return { ...defaults, freq: "weekly", time, dayOfWeek: dow };
  }
  if (dow === "*" && /^([1-9]|[12]\d|3[01])$/.test(dom)) {
    return { ...defaults, freq: "monthly", time, dayOfMonth: dom };
  }
  return { ...defaults, advanced: true };
}

function validateTarget(
  type: DeliveryChannel["type"],
  target: string,
): string | null {
  const t = target.trim();
  if (!t) return `${type} recipient is required`;
  if (type === "email" && !EMAIL_RE.test(t)) {
    return "Invalid email address (expected name@domain.tld)";
  }
  if (type === "slack" && !SLACK_RE.test(t)) {
    return "Invalid Slack channel — use C01ABC23DEF or #channel-name";
  }
  if (type === "whatsapp" && !WHATSAPP_RE.test(t)) {
    return "Invalid WhatsApp number — use E.164 (e.g. +919876543210)";
  }
  return null;
}

/** Flatten a FastAPI 422 body into a one-line user-readable message. */
async function extractError(resp: Response): Promise<string> {
  try {
    const data = await resp.json();
    if (Array.isArray(data?.detail)) {
      return data.detail
        .map((d: { loc?: unknown[]; msg?: string }) => {
          const field = Array.isArray(d.loc) ? d.loc.slice(1).join(".") : "";
          return field ? `${field}: ${d.msg}` : d.msg;
        })
        .filter(Boolean)
        .join("; ");
    }
    if (typeof data?.detail === "string") return data.detail;
  } catch {
    /* fall through */
  }
  return `HTTP ${resp.status}`;
}

/* ── Component ─────────────────────────────────────────────────────── */

export default function ReportScheduler() {
  const [schedules, setSchedules] = useState<ReportSchedule[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  /* ── Form state ── */
  const [formType, setFormType] = useState("cfo_daily");
  const [formFreq, setFormFreq] = useState<FrequencyPreset>("daily");
  const [formTime, setFormTime] = useState("09:00");
  const [formDayOfWeek, setFormDayOfWeek] = useState("1");
  const [formDayOfMonth, setFormDayOfMonth] = useState("1");
  const [formAdvanced, setFormAdvanced] = useState(false);
  const [formCustomCron, setFormCustomCron] = useState("0 9 * * *");
  const [formFormat, setFormFormat] = useState("pdf");
  const [formActive, setFormActive] = useState(true);
  const [formEmailEnabled, setFormEmailEnabled] = useState(true);
  const [formEmailTarget, setFormEmailTarget] = useState("");
  const [formSlackEnabled, setFormSlackEnabled] = useState(false);
  const [formSlackTarget, setFormSlackTarget] = useState("");
  const [formWhatsappEnabled, setFormWhatsappEnabled] = useState(false);
  const [formWhatsappTarget, setFormWhatsappTarget] = useState("");
  const [formFieldErrors, setFormFieldErrors] = useState<Record<string, string>>({});

  // TC_002 (Aishwarya 2026-04-21): a stale per-channel error that was
  // set on a previous submit persisted after the user unchecked the
  // channel and switched to a different one. The next submit would
  // read ``formFieldErrors.email`` while the form no longer owned an
  // email row. Centralise toggles through setters that clear the
  // matching error and any "no channels selected" message.
  function toggleEmail(next: boolean) {
    setFormEmailEnabled(next);
    setFormFieldErrors((prev) => {
      const cleaned = { ...prev };
      if (!next) delete cleaned.email;
      delete cleaned.channels;
      return cleaned;
    });
  }
  function toggleSlack(next: boolean) {
    setFormSlackEnabled(next);
    setFormFieldErrors((prev) => {
      const cleaned = { ...prev };
      if (!next) delete cleaned.slack;
      delete cleaned.channels;
      return cleaned;
    });
  }
  function toggleWhatsapp(next: boolean) {
    setFormWhatsappEnabled(next);
    setFormFieldErrors((prev) => {
      const cleaned = { ...prev };
      if (!next) delete cleaned.whatsapp;
      delete cleaned.channels;
      return cleaned;
    });
  }

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
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Failed to load schedules";
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchSchedules();
  }, [fetchSchedules]);

  /* ── Build channels + pre-submit validation ── */
  function buildValidatedChannels(): {
    channels: DeliveryChannel[];
    errors: Record<string, string>;
  } {
    const errors: Record<string, string> = {};
    const channels: DeliveryChannel[] = [];
    if (formEmailEnabled) {
      const err = validateTarget("email", formEmailTarget);
      if (err) errors.email = err;
      else channels.push({ type: "email", target: formEmailTarget.trim() });
    }
    if (formSlackEnabled) {
      const err = validateTarget("slack", formSlackTarget);
      if (err) errors.slack = err;
      else channels.push({ type: "slack", target: formSlackTarget.trim() });
    }
    if (formWhatsappEnabled) {
      const err = validateTarget("whatsapp", formWhatsappTarget);
      if (err) errors.whatsapp = err;
      else channels.push({ type: "whatsapp", target: formWhatsappTarget.trim() });
    }
    if (channels.length === 0 && Object.keys(errors).length === 0) {
      errors.channels = "At least one delivery channel is required";
    }
    return { channels, errors };
  }

  /* ── Reset form ── */
  function resetForm() {
    setFormType("cfo_daily");
    setFormFreq("daily");
    setFormTime("09:00");
    setFormDayOfWeek("1");
    setFormDayOfMonth("1");
    setFormAdvanced(false);
    setFormCustomCron("0 9 * * *");
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
    setFormFieldErrors({});
  }

  /* ── Populate form for edit ── */
  function startEdit(s: ReportSchedule) {
    setEditingId(s.id);
    setFormType(s.report_type);
    const parsed = parseCron(s.cron_expression);
    setFormAdvanced(parsed.advanced);
    setFormFreq(parsed.freq);
    setFormTime(parsed.time);
    setFormDayOfWeek(parsed.dayOfWeek);
    setFormDayOfMonth(parsed.dayOfMonth);
    setFormCustomCron(parsed.advanced ? parsed.raw : "0 9 * * *");
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
    setFormFieldErrors({});
  }

  /* ── Create / Update ── */
  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const { channels, errors } = buildValidatedChannels();
    const cron = formAdvanced
      ? formCustomCron.trim()
      : composeCron(formFreq, formTime, formDayOfWeek, formDayOfMonth);
    if (formAdvanced && cron.split(/\s+/).length !== 5) {
      errors.cron = "Advanced cron must be a 5-field expression (min hour dom mon dow)";
    }
    if (Object.keys(errors).length > 0) {
      setFormFieldErrors(errors);
      return;
    }
    setFormFieldErrors({});
    setSubmitting(true);
    const token = localStorage.getItem("token") || "";

    try {
      const payload = {
        report_type: formType,
        cron_expression: cron,
        delivery_channels: channels,
        format: formFormat,
        is_active: formActive,
      };
      const resp = editingId
        ? await fetch(`${API_BASE}/report-schedules/${editingId}`, {
            method: "PATCH",
            headers: {
              Authorization: `Bearer ${token}`,
              "Content-Type": "application/json",
            },
            body: JSON.stringify(payload),
          })
        : await fetch(`${API_BASE}/report-schedules`, {
            method: "POST",
            headers: {
              Authorization: `Bearer ${token}`,
              "Content-Type": "application/json",
            },
            body: JSON.stringify(payload),
          });

      if (!resp.ok) {
        setError(await extractError(resp));
        return;
      }
      resetForm();
      fetchSchedules();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setSubmitting(false);
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
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Run failed");
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
              <label className="block text-sm font-medium mb-1" htmlFor="rs-report-type">
                Report Type
              </label>
              <select
                id="rs-report-type"
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

            {/* Frequency */}
            <div>
              <label className="block text-sm font-medium mb-1" htmlFor="rs-frequency">
                Frequency
              </label>
              <select
                id="rs-frequency"
                value={formFreq}
                onChange={(e) => setFormFreq(e.target.value as FrequencyPreset)}
                disabled={formAdvanced}
                className="w-full border rounded-lg px-3 py-2 text-sm bg-background disabled:opacity-50"
              >
                {FREQUENCY_PRESETS.map((p) => (
                  <option key={p.value} value={p.value}>
                    {p.label}
                  </option>
                ))}
              </select>
            </div>

            {/* Execution time (HH:MM) — visible for every frequency */}
            <div>
              <label className="block text-sm font-medium mb-1" htmlFor="rs-time">
                Execution Time (UTC)
              </label>
              <input
                id="rs-time"
                type="time"
                value={formTime}
                onChange={(e) => setFormTime(e.target.value || "09:00")}
                disabled={formAdvanced}
                className="w-full border rounded-lg px-3 py-2 text-sm bg-background disabled:opacity-50"
              />
            </div>

            {/* Day selector — weekly only */}
            {formFreq === "weekly" && !formAdvanced && (
              <div>
                <label className="block text-sm font-medium mb-1" htmlFor="rs-dow">
                  Day of Week
                </label>
                <select
                  id="rs-dow"
                  value={formDayOfWeek}
                  onChange={(e) => setFormDayOfWeek(e.target.value)}
                  className="w-full border rounded-lg px-3 py-2 text-sm bg-background"
                >
                  {DAYS_OF_WEEK.map((d) => (
                    <option key={d.value} value={d.value}>
                      {d.label}
                    </option>
                  ))}
                </select>
              </div>
            )}

            {/* Day-of-month — monthly only */}
            {formFreq === "monthly" && !formAdvanced && (
              <div>
                <label className="block text-sm font-medium mb-1" htmlFor="rs-dom">
                  Day of Month
                </label>
                <input
                  id="rs-dom"
                  type="number"
                  min={1}
                  max={31}
                  value={formDayOfMonth}
                  onChange={(e) => setFormDayOfMonth(e.target.value || "1")}
                  className="w-full border rounded-lg px-3 py-2 text-sm bg-background"
                />
              </div>
            )}

            {/* Advanced / CRON toggle */}
            <div className="sm:col-span-2">
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={formAdvanced}
                  onChange={(e) => setFormAdvanced(e.target.checked)}
                  className="rounded"
                />
                Advanced — write CRON expression directly
              </label>
              {formAdvanced && (
                <div className="mt-2">
                  <input
                    type="text"
                    value={formCustomCron}
                    onChange={(e) => setFormCustomCron(e.target.value)}
                    placeholder="0 9 * * 1-5   (every weekday at 09:00 UTC)"
                    className="w-full border rounded-lg px-3 py-2 text-sm bg-background font-mono"
                    aria-label="Custom cron expression"
                  />
                  <p className="text-xs text-muted-foreground mt-1">
                    5 fields: minute (0-59), hour (0-23), day-of-month (1-31),
                    month (1-12), day-of-week (0-7).
                  </p>
                  {formFieldErrors.cron && (
                    <p className="text-xs text-red-600 mt-1">{formFieldErrors.cron}</p>
                  )}
                </div>
              )}
            </div>

            {/* Format */}
            <div>
              <label className="block text-sm font-medium mb-1" htmlFor="rs-format">
                Output Format
              </label>
              <select
                id="rs-format"
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
              <div className="space-y-1">
                <div className="flex items-center gap-3">
                  <input
                    type="checkbox"
                    id="ch-email"
                    checked={formEmailEnabled}
                    onChange={(e) => toggleEmail(e.target.checked)}
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
                      aria-invalid={!!formFieldErrors.email}
                    />
                  )}
                </div>
                {formFieldErrors.email && (
                  <p className="text-xs text-red-600 pl-32">{formFieldErrors.email}</p>
                )}
              </div>

              {/* Slack */}
              <div className="space-y-1">
                <div className="flex items-center gap-3">
                  <input
                    type="checkbox"
                    id="ch-slack"
                    checked={formSlackEnabled}
                    onChange={(e) => toggleSlack(e.target.checked)}
                    className="rounded"
                  />
                  <label htmlFor="ch-slack" className="text-sm w-24">
                    Slack
                  </label>
                  {formSlackEnabled && (
                    <input
                      type="text"
                      placeholder="Channel ID (e.g. C01ABC23DEF) or #channel-name"
                      value={formSlackTarget}
                      onChange={(e) => setFormSlackTarget(e.target.value)}
                      className="flex-1 border rounded-lg px-3 py-1.5 text-sm bg-background"
                      aria-invalid={!!formFieldErrors.slack}
                    />
                  )}
                </div>
                {formFieldErrors.slack && (
                  <p className="text-xs text-red-600 pl-32">{formFieldErrors.slack}</p>
                )}
              </div>

              {/* WhatsApp */}
              <div className="space-y-1">
                <div className="flex items-center gap-3">
                  <input
                    type="checkbox"
                    id="ch-whatsapp"
                    checked={formWhatsappEnabled}
                    onChange={(e) => toggleWhatsapp(e.target.checked)}
                    className="rounded"
                  />
                  <label htmlFor="ch-whatsapp" className="text-sm w-24">
                    WhatsApp
                  </label>
                  {formWhatsappEnabled && (
                    <input
                      type="tel"
                      placeholder="+919876543210"
                      value={formWhatsappTarget}
                      onChange={(e) => setFormWhatsappTarget(e.target.value)}
                      className="flex-1 border rounded-lg px-3 py-1.5 text-sm bg-background"
                      aria-invalid={!!formFieldErrors.whatsapp}
                    />
                  )}
                </div>
                {formFieldErrors.whatsapp && (
                  <p className="text-xs text-red-600 pl-32">{formFieldErrors.whatsapp}</p>
                )}
              </div>

              {formFieldErrors.channels && (
                <p className="text-xs text-red-600">{formFieldErrors.channels}</p>
              )}
            </div>

            {/* Actions */}
            <div className="sm:col-span-2 flex gap-3 pt-2">
              <button
                type="submit"
                disabled={submitting}
                className="px-4 py-2 rounded-lg bg-primary text-primary-foreground text-sm font-medium hover:opacity-90 disabled:opacity-50"
              >
                {submitting ? "Saving..." : editingId ? "Update Schedule" : "Create Schedule"}
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
                  <td className="px-4 py-3 hidden sm:table-cell font-mono text-xs">
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
