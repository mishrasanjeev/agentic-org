// SPDX-License-Identifier: Apache-2.0
import { useCallback, useEffect, useState } from "react";
import api, { extractApiError } from "@/lib/api";

/**
 * The business console: the rules, thresholds and routing an administrator changes per tenant. Each
 * setting shows its bounds or options, the value in force and where it came from; a value is saved
 * within the catalogue's bounds or reset so the default applies again. The backend validates and
 * records every change.
 */

export interface SettingRow {
  key: string;
  title: string;
  description: string;
  group: string;
  kind: string;
  default: unknown;
  applies: string;
  minimum: number | null;
  maximum: number | null;
  unit: string;
  options: string[];
  value: unknown;
  source: string;
  updated_by: string | null;
  updated_at: string | null;
  previous: unknown;
}

interface Group {
  key: string;
  title: string;
  settings: SettingRow[];
}

export function show(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

/** The value to send for a setting from what the person typed or picked. */
export function parseInput(setting: SettingRow, raw: string, picked?: Record<string, boolean>): unknown {
  if (setting.kind === "number") return Number(raw);
  if (setting.kind === "integer") return Math.trunc(Number(raw));
  if (setting.kind === "boolean") return raw === "true";
  if (setting.kind === "list") {
    if (picked) return setting.options.filter((o) => picked[o]);
    return raw
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
  }
  return JSON.parse(raw);
}

function SettingCard({ setting, onSaved }: { setting: SettingRow; onSaved: () => Promise<void> }) {
  const [raw, setRaw] = useState<string>(() => (setting.kind === "mapping" || setting.kind === "rules" ? JSON.stringify(setting.value, null, 2) : show(setting.value)));
  const [picked, setPicked] = useState<Record<string, boolean>>(() => {
    const out: Record<string, boolean> = {};
    if (setting.kind === "list" && Array.isArray(setting.value)) for (const v of setting.value as string[]) out[v] = true;
    return out;
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      let value: unknown;
      try {
        value = parseInput(setting, raw, setting.kind === "list" && setting.options.length > 0 ? picked : undefined);
      } catch {
        setError("The value is not valid JSON.");
        return;
      }
      await api.put(`/workbench/console/${encodeURIComponent(setting.key)}`, { value });
      await onSaved();
    } catch (err) {
      setError(extractApiError(err, "The value was not saved."));
    } finally {
      setBusy(false);
    }
  };

  const reset = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.delete(`/workbench/console/${encodeURIComponent(setting.key)}`);
      await onSaved();
    } catch (err) {
      setError(extractApiError(err, "The default was not restored."));
    } finally {
      setBusy(false);
    }
  };

  const bounds = setting.minimum !== null || setting.maximum !== null ? `${setting.minimum ?? ""} to ${setting.maximum ?? ""}${setting.unit ? ` ${setting.unit}` : ""}` : "";

  return (
    <div className="rounded-md border border-slate-200 bg-white p-3" data-testid={`console-${setting.key}`}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 className="text-sm font-semibold text-slate-900">{setting.title}</h3>
          <p className="text-xs text-slate-600">{setting.description}</p>
          <p className="text-xs text-slate-500">
            {setting.source === "set" ? `Set by ${setting.updated_by || "an administrator"}${setting.updated_at ? ` on ${new Date(setting.updated_at).toLocaleString()}` : ""}` : "Default"}
            {setting.previous !== null && setting.previous !== undefined ? ` · previously ${show(setting.previous)}` : ""}
            {bounds ? ` · ${bounds}` : ""}
          </p>
        </div>
        <div className="flex gap-2">
          <button type="button" className="rounded-md bg-indigo-600 px-2 py-1 text-xs font-medium text-white disabled:opacity-50" disabled={busy} onClick={() => void save()} data-testid={`console-save-${setting.key}`}>
            Save
          </button>
          <button type="button" className="rounded-md border border-slate-300 px-2 py-1 text-xs text-slate-700 disabled:opacity-50" disabled={busy || setting.source !== "set"} onClick={() => void reset()} data-testid={`console-reset-${setting.key}`}>
            Reset to default
          </button>
        </div>
      </div>
      {error && (
        <div role="alert" className="mt-2 rounded-md border border-red-200 bg-red-50 px-2 py-1 text-xs text-red-800">
          {error}
        </div>
      )}
      <div className="mt-2">
        {(setting.kind === "number" || setting.kind === "integer") && (
          <input type="number" className="w-40 rounded-md border border-slate-300 px-2 py-1 text-sm" value={raw} min={setting.minimum ?? undefined} max={setting.maximum ?? undefined} step={setting.kind === "integer" ? 1 : 0.01} onChange={(e) => setRaw(e.target.value)} data-testid={`console-input-${setting.key}`} />
        )}
        {setting.kind === "boolean" && (
          <label className="text-sm text-slate-700">
            <input type="checkbox" className="mr-2" checked={raw === "true"} onChange={(e) => setRaw(e.target.checked ? "true" : "false")} />
            Enabled
          </label>
        )}
        {setting.kind === "list" && setting.options.length > 0 && (
          <div className="flex flex-wrap gap-2">
            {setting.options.map((option) => (
              <label key={option} className="rounded-full border border-slate-300 px-2 py-0.5 text-xs text-slate-700">
                <input type="checkbox" className="mr-1" checked={!!picked[option]} onChange={(e) => setPicked((prev) => ({ ...prev, [option]: e.target.checked }))} data-testid={`console-option-${setting.key}-${option}`} />
                {option}
              </label>
            ))}
          </div>
        )}
        {setting.kind === "list" && setting.options.length === 0 && <input className="w-full rounded-md border border-slate-300 px-2 py-1 text-sm" value={raw} onChange={(e) => setRaw(e.target.value)} placeholder="comma-separated" />}
        {(setting.kind === "mapping" || setting.kind === "rules") && (
          <textarea className="w-full rounded-md border border-slate-300 px-2 py-1 font-mono text-xs" rows={setting.kind === "rules" ? 6 : 4} value={raw} onChange={(e) => setRaw(e.target.value)} data-testid={`console-input-${setting.key}`} />
        )}
        {setting.kind === "mapping" && setting.options.length > 0 && <p className="mt-1 text-xs text-slate-500">Keys: {setting.options.join(", ")}</p>}
        {setting.kind === "rules" && <p className="mt-1 text-xs text-slate-500">Each rule: kind (any, approval, document, draft, case), field, op (&gt;=, &lt;=, ==, contains), value, priority (critical, high, normal, low).</p>}
      </div>
    </div>
  );
}

export default function BusinessConsole() {
  const [groups, setGroups] = useState<Group[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);

  const load = useCallback(async () => {
    setError(null);
    try {
      const { data } = await api.get("/workbench/console");
      setGroups((data as { groups: Group[] }).groups);
      setVersion((v) => v + 1);
    } catch (err) {
      setError(extractApiError(err, "Failed to load the business console."));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="space-y-4" data-testid="business-console">
      {error && (
        <div role="alert" className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
          {error}
        </div>
      )}
      {groups.map((group) => (
        <section key={group.key} className="space-y-2">
          <h2 className="text-base font-semibold text-slate-900">{group.title}</h2>
          {group.settings.map((setting) => (
            <SettingCard key={`${setting.key}:${version}`} setting={setting} onSaved={load} />
          ))}
        </section>
      ))}
    </div>
  );
}
