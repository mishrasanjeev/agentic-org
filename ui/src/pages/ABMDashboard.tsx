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

// TC_016: backend stores tier as "1"/"2"/"3" but the product docs
// (and the QA plan) expect the semantic labels the sales team actually
// uses: Strategic / Enterprise / Growth. Mapping at the display layer
// keeps the API contract stable while fixing the dropdown + card text.
const TIER_LABEL: Record<string, string> = {
  "1": "Strategic",
  "2": "Enterprise",
  "3": "Growth",
};

// TC_013 (Aishwarya 2026-04-21): table displayed "Tier Strategic" /
// "Tier Enterprise" / "Tier Growth" instead of plain semantic labels.
// Root cause: tierLabel's fallback was ``Tier ${key}`` so anything
// that wasn't exactly "1"/"2"/"3" got the "Tier " prefix slapped on.
// If the backend (or a fixture) returns tier already as "Strategic",
// the fallback produced "Tier Strategic" — which also broke the
// Strategic/Enterprise/Growth filter because the dropdown value
// ("1") no longer matches the rendered text. Make tierLabel
// idempotent so known semantic labels round-trip cleanly.
const _SEMANTIC_TIERS = new Set(Object.values(TIER_LABEL));

function tierLabel(tier: string | number | null | undefined): string {
  const key = String(tier ?? "").trim();
  if (!key) return "—";
  if (TIER_LABEL[key]) return TIER_LABEL[key];
  if (_SEMANTIC_TIERS.has(key)) return key;
  // Preserve unknown values without the confusing "Tier " prefix.
  return key;
}

// TC_015: the table used `new Date(acct.updated_at).toLocaleDateString()`
// and rendered 01/01/1970 for every account whose updated_at came back
// null (every newly-uploaded row does). Formalise the null-safe
// formatter so we never render an epoch date again.
function formatActivityDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime()) || d.getUTCFullYear() < 2000) return "—";
  return d.toLocaleDateString();
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
  // TC_008 (Aishwarya 2026-04-21): the CSV upload banner used the same
  // `error` state as `fetchData`, and because `fetchData` clears
  // `error` on entry, the "N duplicates skipped" message vanished the
  // moment the table refreshed. Separate notice state outlives the
  // refetch so dedup feedback is actually visible.
  const [uploadNotice, setUploadNotice] = useState("");

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
    setUploadNotice("");
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
        const preview = r.row_errors
          .slice(0, 3)
          .map((e) => `row ${e.row}: ${e.reason}`)
          .join("; ");
        const more = r.row_errors.length > 3
          ? ` (+${r.row_errors.length - 3} more)`
          : "";
        parts.push(`${r.row_errors.length} row error(s): ${preview}${more}`);
      }
      // TC_008: route the notice through a state that fetchData doesn't
      // touch so duplicate-skipped / row-error feedback outlives the
      // refresh that follows.
      if (parts.length === 0) {
        setUploadNotice("CSV processed but no rows were imported.");
      } else {
        setUploadNotice(parts.join(" · "));
      }
      await fetchData();
    } catch (err: unknown) {
      const detail = (err as {
        response?: { data?: { detail?: string } };
      })?.response?.data?.detail;
      setUploadNotice(
        typeof detail === "string"
          ? `CSV upload rejected: ${detail}`
          : "CSV upload failed. Please check the file and try again.",
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
      // TC_012 (Aishwarya 2026-04-21): when the tenant hasn't
      // configured Bombora / G2 / TrustRadius connectors the backend
      // now returns source="seeded" with a note pointing the user at
      // Settings. Surface that note in the popup so the zeros don't
      // look like silent failures, and clearly label the provider
      // signals as "not configured" instead of "0".
      const isSeeded = intent.source === "seeded";
      const note = typeof intent.note === "string" ? intent.note : "";
      const lines = [
        `Intent for ${intent.domain}`,
        "",
        `Composite: ${intent.composite_score}` +
          (isSeeded ? "  (seeded placeholder — see note)" : ""),
        `Bombora Surge: ${isSeeded ? "not configured" : intent.bombora_surge}`,
        `G2 Signals: ${isSeeded ? "not configured" : intent.g2_signals}`,
        `TrustRadius: ${isSeeded ? "not configured" : intent.trustradius_intent}`,
        `Topics: ${(intent.topics || []).join(", ") || "none"}`,
      ];
      if (note) {
        lines.push("", note);
      }
      alert(lines.join("\n"));
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

  /* ── Unique industries for filter (TC_021) ────────────────────────── */
  //
  // The old code derived `industries` from the current `accounts` on
  // every render. When the filter was applied and the list shrank, the
  // industry dropdown's `<option>` list shrank with it — so the already
  // selected value often fell out of the new options, and the dropdown
  // appeared "stuck" on a ghost value with no way to pick something
  // else. We now keep an all-time industries state that only grows as
  // new accounts arrive, and we always ensure the currently-selected
  // filter value is in the option list even if it's no longer in the
  // filtered accounts.
  const [knownIndustries, setKnownIndustries] = useState<string[]>([]);

  useEffect(() => {
    setKnownIndustries((prev) => {
      const merged = new Set(prev);
      for (const a of accounts) {
        if (a.industry) merged.add(a.industry);
      }
      return [...merged].sort();
    });
  }, [accounts]);

  const industries = (() => {
    const pool = new Set(knownIndustries);
    if (filterIndustry) pool.add(filterIndustry);
    return [...pool].sort();
  })();

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

      {/*
        TC_008 upload notice — amber (informational) banner separate
        from the destructive error banner so dedup/row-error feedback
        is visible even when the upload partially succeeds.
      */}
      {uploadNotice && (
        <div className="rounded-lg bg-amber-50 dark:bg-amber-950/30 border border-amber-300 text-amber-900 dark:text-amber-100 px-4 py-3 text-sm flex items-start gap-3">
          <span className="flex-1">{uploadNotice}</span>
          <button
            type="button"
            onClick={() => setUploadNotice("")}
            aria-label="Dismiss upload notice"
            className="text-amber-800 hover:text-amber-950"
          >
            ×
          </button>
        </div>
      )}

      {/* Metric cards */}
      {summary && (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <MetricCard label="Total Accounts" value={summary.total_accounts} />
          <MetricCard
            label={`${tierLabel("1")} Accounts`}
            value={summary.by_tier["1"] || 0}
            sub={`${summary.by_tier["2"] || 0} ${tierLabel("2")} / ${
              summary.by_tier["3"] || 0
            } ${tierLabel("3")}`}
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
              <option value="1">{tierLabel("1")}</option>
              <option value="2">{tierLabel("2")}</option>
              <option value="3">{tierLabel("3")}</option>
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
                      <td
                        className="px-4 py-3 font-medium max-w-[260px] truncate"
                        title={acct.company_name}
                      >
                        {acct.company_name}
                      </td>
                      <td
                        className="px-4 py-3 text-muted-foreground max-w-[200px] truncate"
                        title={acct.domain}
                      >
                        {acct.domain}
                      </td>
                      <td className="px-4 py-3">
                        <span
                          className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-semibold bg-primary/10 text-primary whitespace-nowrap"
                        >
                          {tierLabel(acct.tier)}
                        </span>
                      </td>
                      <td
                        className="px-4 py-3 text-muted-foreground max-w-[160px] truncate"
                        title={acct.industry || undefined}
                      >
                        {acct.industry || "—"}
                      </td>
                      <td className="px-4 py-3">
                        <span
                          className={`inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-semibold border whitespace-nowrap ${intentColor(acct.intent_score)}`}
                        >
                          {acct.intent_score}
                          <span className="text-[10px] font-normal">
                            {intentLabel(acct.intent_score)}
                          </span>
                        </span>
                      </td>
                      <td className="px-4 py-3 text-muted-foreground text-xs whitespace-nowrap">
                        {formatActivityDate(acct.updated_at)}
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
