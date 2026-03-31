import { useState, useEffect } from "react";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import api from "@/lib/api";

interface FleetLimits {
  max_active_agents: number;
  max_agents_per_domain: Record<string, number>;
  max_shadow_agents: number;
  max_replicas_per_type: number;
}

const DEFAULT_LIMITS: FleetLimits = {
  max_active_agents: 35,
  max_agents_per_domain: { finance: 20, hr: 20, marketing: 20, ops: 20, backoffice: 20 },
  max_shadow_agents: 10,
  max_replicas_per_type: 20,
};

export default function Settings() {
  const [limits, setLimits] = useState<FleetLimits>(DEFAULT_LIMITS);
  const [piiMasking, setPiiMasking] = useState(true);
  const [dataRegion, setDataRegion] = useState("IN");
  const [auditRetention, setAuditRetention] = useState(7);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  useEffect(() => {
    fetchSettings();
  }, []);

  async function fetchSettings() {
    try {
      const { data } = await api.get("/config/fleet_limits");
      if (data.max_active_agents) setLimits(data);
    } catch {
      // Use defaults
    }
  }

  async function saveSettings() {
    setSaving(true);
    setSaved(false);
    setSaveError(null);
    try {
      await api.put("/config/fleet_limits", limits);
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    } catch (e: any) {
      setSaveError(e?.response?.data?.detail || "Failed to save settings. Please try again.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-6">
      <h2 className="text-2xl font-bold">Settings</h2>

      <Card>
        <CardHeader><CardTitle>Fleet Governance Limits</CardTitle></CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="text-sm font-medium">Max Active Agents</label>
              <input type="number" value={limits.max_active_agents} onChange={(e) => setLimits({ ...limits, max_active_agents: Number(e.target.value) })} className="border rounded px-3 py-2 text-sm w-full mt-1" />
            </div>
            <div>
              <label className="text-sm font-medium">Max Shadow Agents</label>
              <input type="number" value={limits.max_shadow_agents} onChange={(e) => setLimits({ ...limits, max_shadow_agents: Number(e.target.value) })} className="border rounded px-3 py-2 text-sm w-full mt-1" />
            </div>
            <div>
              <label className="text-sm font-medium">Max Replicas Per Agent Type</label>
              <input type="number" value={limits.max_replicas_per_type} onChange={(e) => setLimits({ ...limits, max_replicas_per_type: Number(e.target.value) })} className="border rounded px-3 py-2 text-sm w-full mt-1" />
            </div>
          </div>
          <div>
            <label className="text-sm font-medium">Max Agents Per Domain</label>
            <div className="grid grid-cols-5 gap-2 mt-1">
              {Object.entries(limits.max_agents_per_domain).map(([domain, count]) => (
                <div key={domain}>
                  <label className="text-xs text-muted-foreground capitalize">{domain}</label>
                  <input type="number" value={count} onChange={(e) => setLimits({ ...limits, max_agents_per_domain: { ...limits.max_agents_per_domain, [domain]: Number(e.target.value) } })} className="border rounded px-2 py-1 text-sm w-full" />
                </div>
              ))}
            </div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>Compliance & Data</CardTitle></CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-3 gap-4">
            <div>
              <label className="text-sm font-medium">PII Masking</label>
              <select value={piiMasking ? "enabled" : "disabled"} onChange={(e) => setPiiMasking(e.target.value === "enabled")} className="border rounded px-3 py-2 text-sm w-full mt-1">
                <option value="enabled">Enabled (required for production)</option>
                <option value="disabled">Disabled (dev only)</option>
              </select>
            </div>
            <div>
              <label className="text-sm font-medium">Data Region</label>
              <select value={dataRegion} onChange={(e) => setDataRegion(e.target.value)} className="border rounded px-3 py-2 text-sm w-full mt-1">
                <option value="IN">India (asia-south1)</option>
                <option value="EU">EU (europe-west1)</option>
                <option value="US">US (us-central1)</option>
              </select>
            </div>
            <div>
              <label className="text-sm font-medium">Audit Retention (years)</label>
              <input type="number" value={auditRetention} onChange={(e) => setAuditRetention(Number(e.target.value))} min={1} max={10} className="border rounded px-3 py-2 text-sm w-full mt-1" />
            </div>
          </div>
        </CardContent>
      </Card>

      <div className="flex gap-4 items-center">
        <Button onClick={saveSettings} disabled={saving}>{saving ? "Saving..." : "Save Settings"}</Button>
        {saved && <span className="text-sm text-green-600">Settings saved successfully.</span>}
        {saveError && <span className="text-sm text-red-600">{saveError}</span>}
      </div>
    </div>
  );
}
