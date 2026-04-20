/**
 * ABM (Account-Based Marketing) Dashboard
 *
 * Executive view of target accounts, intent scores, campaigns, and
 * pipeline influence.  Supports CSV upload, per-account intent drill-down,
 * and one-click campaign launch.
 */

import { useEffect, useState, useCallback, useRef } from "react";
import { abmApi } from "../lib/api";

/* ── Types ──────────────────────────────────────────────────────────── */

interface ABMAccount {
  id: string;
  company_name: string;
  domain: string;
  tier: string;
  industry: string;
  revenue: string;
  intent_score: number;
  intent_data: Record<string, unknown> | null;
  campaigns: unknown[];
  created_at: string;
  updated_at: string;
}

interface DashboardSummary {
  total_accounts: number;
  by_tier: Record<string, number>;
  avg_intent_score: number;
  top_10_by_intent: {
    id: string;
    company_name: string;
    domain: string;
    tier: string;
    intent_score: number;
  }[];
  pipeline_influenced_usd: number;
  total_campaigns: number;
}

/* ── Intent score color coding ──────────────────────────────────────── */

function intentColor(score: number): string {
  if (score >= 81) return "bg-red-100 text-red-800 border-red-300";
  if (score >= 61) return "bg-orange-100 text-orange-800 border-orange-300";
  if (score >= 31) return "bg-yellow-100 text-yellow-800 border-yellow-300";
  return "bg-gray-100 text-gray-600 border-gray-300";
}

function intentLabel(score: number): string {
  if (score >= 81) return "Hot";
  if (score >= 61) return "Warm";
  if (score >= 31) return "Medium";
  return "Low";
}

/* ── Metric card ────────────────────────────────────────────────────── */

function MetricCard({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
  return (
    <div className="rounded-xl border bg-card p-5 shadow-sm">
      <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">{label}</p>
      <p className="mt-1 text-2xl font-bold">{value}</p>
      {sub && <p className="mt-0.5 text-xs text-muted-foreground">{sub}</p>}
    </div>
  );
}

/* ── Main component ─────────────────────────────────────────────────── */

export default function ABMDashboard() {
  const [accounts, setAccounts] = useState<ABMAccount[]>([]);
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // Filters
  const [filterTier, setFilterTier] = useState("");
  const [filterIndustry, setFilterIndustry] = useState("");
  const [filterMinIntent, setFilterMinIntent] = useState("");

  // Upload
  const fileRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);

  // Campaign modal
  const [campaignAccountId, setCampaignAccountId] = useState<string | null>(null);
  const [campaignName, setCampaignName] = useState("");
  const [campaignChannel, setCampaignChannel] = useState("linkedin");

  /* ── Fetch data ───────────────────────────────────────────────────── */

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const params: Record<string, string> = {};
      if (filterTier) params.tier = filterTier;
      if (filterIndustry) params.industry = filterIndustry;
      if (filterMinIntent) params.min_intent_score = filterMinIntent;

      const [acctRes, dashRes] = await Promise.all([
        abmApi.listAccounts(params),
        abmApi.dashboard(),
      ]);
      setAccounts(acctRes.data.accounts || []);
      setSummary(dashRes.data);
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || "Failed to load ABM data";
      setError(typeof msg === "string" ? msg : "Failed to load ABM data");
    } finally {
      setLoading(false);
    }
  }, [filterTier, filterIndustry, filterMinIntent]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  /* ── CSV upload ───────────────────────────────────────────────────── */

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    setError("");
    try {
      const res = await abmApi.uploadCsv(file);
      const r = res.data as {
        created?: number;
        skipped?: number;
        dedup_skipped?: { row: string; domain: string; reason: string }[];
        row_errors?: { row: number; domain: string; reason: string }[];
      };
      const parts: string[] = [];
      if (r.created) parts.push(`${r.created} added`);
      if (r.dedup_skipped?.length) {
        parts.push(`${r.dedup_skipped.length} duplicate(s) skipped`);
      }
      if (r.row_errors?.length) {
        // Show the first three concrete reasons so the user can act.
        const preview = r.row_errors
          .slice(0, 3)
          .map((e) => `row ${e.row}: ${e.reason}`)
          .join("; ");
        const more = r.row_errors.length > 3
          ? ` (+${r.row_errors.length - 3} more)`
          : "";
        parts.push(`${r.row_errors.length} row error(s): ${preview}${more}`);
      }
      if (parts.length === 0) {
        setError("CSV processed but no rows were imported");
      } else if (r.dedup_skipped?.length || r.row_errors?.length) {
        // Report via the same banner — non-fatal but user should see it.
        setError(parts.join(" · "));
      }
      await fetchData();
    } catch (err: unknown) {
      const detail = (err as {
        response?: { data?: { detail?: string } };
      })?.response?.data?.detail;
      setError(
        typeof detail === "string"
          ? `CSV upload rejected: ${detail}`
          : "CSV upload failed",
      );
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  /* ── Intent drill-down ────────────────────────────────────────────── */

  const handleViewIntent = async (accountId: string) => {
    try {
      const res = await abmApi.getIntent(accountId);
      const intent = res.data;
      alert(
        `Intent for ${intent.domain}\n\n` +
        `Composite: ${intent.composite_score}\n` +
        `Bombora Surge: ${intent.bombora_surge}\n` +
        `G2 Signals: ${intent.g2_signals}\n` +
        `TrustRadius: ${intent.trustradius_intent}\n` +
        `Topics: ${(intent.topics || []).join(", ") || "none"}`
      );
      await fetchData();
    } catch {
      setError("Failed to fetch intent data");
    }
  };

  /* ── Launch campaign ──────────────────────────────────────────────── */

  const handleLaunchCampaign = async () => {
    if (!campaignAccountId || !campaignName) return;
    try {
      await abmApi.launchCampaign(campaignAccountId, {
        campaign_name: campaignName,
        channel: campaignChannel,
      });
      setCampaignAccountId(null);
      setCampaignName("");
      await fetchData();
    } catch {
      setError("Failed to launch campaign");
    }
  };

  /* ── Unique industries for filter ─────────────────────────────────── */

  const industries = [...new Set(accounts.map((a) => a.industry).filter(Boolean))].sort();

  /* ── Render ───────────────────────────────────────────────────────── */

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold">Account-Based Marketing</h1>
          <p className="text-sm text-muted-foreground">
            Target accounts, intent signals, and campaign management
          </p>
        </div>
        <div className="flex items-center gap-2">
          <input
            ref={fileRef}
            type="file"
            accept=".csv"
            onChange={handleUpload}
            className="hidden"
            id="csv-upload"
          />
          <label
            htmlFor="csv-upload"
            className="cursor-pointer inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 transition-colors"
          >
            {uploading ? "Uploading..." : "Upload CSV"}
          </label>
        </div>
      </div>

      {error && (
        <div className="rounded-lg bg-destructive/10 text-destructive px-4 py-3 text-sm">
          {error}
        </div>
      )}

      {/* Metric cards */}
      {summary && (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <MetricCard label="Total Accounts" value={summary.total_accounts} />
          <MetricCard
            label="Tier 1 Accounts"
            value={summary.by_tier["1"] || 0}
            sub={`${summary.by_tier["2"] || 0} Tier 2 / ${summary.by_tier["3"] || 0} Tier 3`}
          />
          <MetricCard
            label="Avg Intent Score"
            value={summary.avg_intent_score}
            sub="0-100 composite"
          />
          <MetricCard
            label="Pipeline Influenced"
            value={`$${summary.pipeline_influenced_usd.toLocaleString()}`}
            sub={`${summary.total_campaigns} campaigns`}
          />
        </div>
      )}

      <div className="flex flex-col lg:flex-row gap-6">
        {/* Sidebar filters */}
        <aside className="w-full lg:w-56 space-y-4 flex-shrink-0">
          <h3 className="text-sm font-semibold">Filters</h3>

          <div>
            <label className="text-xs font-medium text-muted-foreground">Tier</label>
            <select
              value={filterTier}
              onChange={(e) => setFilterTier(e.target.value)}
              className="mt-1 w-full rounded-md border bg-background px-3 py-1.5 text-sm"
            >
              <option value="">All Tiers</option>
              <option value="1">Tier 1</option>
              <option value="2">Tier 2</option>
              <option value="3">Tier 3</option>
            </select>
          </div>

          <div>
            <label className="text-xs font-medium text-muted-foreground">Industry</label>
            <select
              value={filterIndustry}
              onChange={(e) => setFilterIndustry(e.target.value)}
              className="mt-1 w-full rounded-md border bg-background px-3 py-1.5 text-sm"
            >
              <option value="">All Industries</option>
              {industries.map((ind) => (
                <option key={ind} value={ind}>{ind}</option>
              ))}
            </select>
          </div>

          <div>
            <label className="text-xs font-medium text-muted-foreground">Min Intent Score</label>
            <input
              type="number"
              min={0}
              max={100}
              value={filterMinIntent}
              onChange={(e) => setFilterMinIntent(e.target.value)}
              placeholder="0"
              className="mt-1 w-full rounded-md border bg-background px-3 py-1.5 text-sm"
            />
          </div>

          <button
            onClick={fetchData}
            className="w-full px-3 py-1.5 rounded-md bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 transition-colors"
          >
            Apply Filters
          </button>
        </aside>

        {/* Accounts table */}
        <div className="flex-1 min-w-0">
          {loading ? (
            <div className="flex items-center justify-center py-20">
              <div className="w-6 h-6 rounded-md bg-gradient-to-br from-blue-500 to-teal-500 animate-pulse" />
            </div>
          ) : accounts.length === 0 ? (
            <div className="text-center py-20 text-muted-foreground">
              <p className="text-lg font-medium">No target accounts yet</p>
              <p className="text-sm mt-1">Upload a CSV or add accounts manually to get started.</p>
            </div>
          ) : (
            <div className="overflow-x-auto rounded-xl border">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b bg-muted/40">
                    <th className="text-left px-4 py-3 font-medium">Company</th>
                    <th className="text-left px-4 py-3 font-medium">Domain</th>
                    <th className="text-left px-4 py-3 font-medium">Tier</th>
                    <th className="text-left px-4 py-3 font-medium">Industry</th>
                    <th className="text-left px-4 py-3 font-medium">Intent Score</th>
                    <th className="text-left px-4 py-3 font-medium">Last Activity</th>
                    <th className="text-right px-4 py-3 font-medium">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {accounts.map((acct) => (
                    <tr key={acct.id} className="border-b hover:bg-muted/20 transition-colors">
                      <td className="px-4 py-3 font-medium">{acct.company_name}</td>
                      <td className="px-4 py-3 text-muted-foreground">{acct.domain}</td>
                      <td className="px-4 py-3">
                        <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-semibold bg-primary/10 text-primary">
                          T{acct.tier}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-muted-foreground">{acct.industry || "--"}</td>
                      <td className="px-4 py-3">
                        <span
                          className={`inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-semibold border ${intentColor(acct.intent_score)}`}
                        >
                          {acct.intent_score}
                          <span className="text-[10px] font-normal">
                            {intentLabel(acct.intent_score)}
                          </span>
                        </span>
                      </td>
                      <td className="px-4 py-3 text-muted-foreground text-xs">
                        {new Date(acct.updated_at).toLocaleDateString()}
                      </td>
                      <td className="px-4 py-3 text-right">
                        <div className="flex items-center justify-end gap-1.5">
                          <button
                            onClick={() => handleViewIntent(acct.id)}
                            className="px-2.5 py-1 rounded text-xs font-medium bg-blue-50 text-blue-700 hover:bg-blue-100 transition-colors"
                          >
                            View Intent
                          </button>
                          <button
                            onClick={() => {
                              setCampaignAccountId(acct.id);
                              setCampaignName(`Campaign for ${acct.company_name}`);
                            }}
                            className="px-2.5 py-1 rounded text-xs font-medium bg-green-50 text-green-700 hover:bg-green-100 transition-colors"
                          >
                            Launch Campaign
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
      </div>

      {/* Campaign launch modal */}
      {campaignAccountId && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/60">
          <div className="bg-background rounded-xl border shadow-xl w-full max-w-md p-6 space-y-4">
            <h2 className="text-lg font-bold">Launch Campaign</h2>

            <div>
              <label className="text-xs font-medium text-muted-foreground">Campaign Name</label>
              <input
                type="text"
                value={campaignName}
                onChange={(e) => setCampaignName(e.target.value)}
                className="mt-1 w-full rounded-md border bg-background px-3 py-2 text-sm"
              />
            </div>

            <div>
              <label className="text-xs font-medium text-muted-foreground">Channel</label>
              <select
                value={campaignChannel}
                onChange={(e) => setCampaignChannel(e.target.value)}
                className="mt-1 w-full rounded-md border bg-background px-3 py-2 text-sm"
              >
                <option value="linkedin">LinkedIn</option>
                <option value="email">Email</option>
                <option value="display">Display</option>
                <option value="multi">Multi-Channel</option>
              </select>
            </div>

            <div className="flex justify-end gap-2 pt-2">
              <button
                onClick={() => setCampaignAccountId(null)}
                className="px-4 py-2 rounded-lg text-sm font-medium border hover:bg-muted transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleLaunchCampaign}
                className="px-4 py-2 rounded-lg text-sm font-medium bg-primary text-primary-foreground hover:bg-primary/90 transition-colors"
              >
                Launch
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
