import { useState, useEffect, useCallback, useRef } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Helmet } from "react-helmet-async";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import api, { extractApiError } from "@/lib/api";
import { INDIAN_STATES } from "@/lib/indianStates";

// BUG-001 / BUG-006: industry dropdown options used to be derived from
// the loaded companies, which left the filter empty on a fresh tenant
// and (per the Ramesh report) sometimes surfaced mis-mapped values like
// the language-picker codes EN / HI when a dev accidentally reused the
// wrong option list. Anchor the filter options to the same canonical
// INDUSTRIES the onboarding wizard offers, including "Accounting / CA
// Firm" for BUG-006 coverage of the target audience.
const INDUSTRIES = [
  "Accounting / CA Firm",
  "Manufacturing",
  "IT Services",
  "Healthcare",
  "Export",
  "Retail",
  "Textile",
  "Logistics",
  "Education",
  "Construction",
  "Real Estate",
  "FMCG",
  "Agriculture",
  "Pharmaceuticals",
  "Automotive",
  "Other",
];

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface Company {
  id: string;
  name: string;
  gstin?: string;
  pan?: string;
  industry?: string;
  state?: string;
  state_code?: string;
  status?: string;
  is_active?: boolean;
  created_at?: string;
  health_score?: number;
  client_health_score?: number;
  pending_approvals?: number;
}

interface CompanySummary {
  total_clients: number;
  active_clients: number;
  total_pending_filings: number;
  total_overdue: number;
}


const INDUSTRY_COLORS: Record<string, string> = {
  Manufacturing: "from-blue-500 to-cyan-600",
  Export: "from-emerald-500 to-teal-600",
  Healthcare: "from-red-500 to-pink-600",
  "IT Services": "from-blue-500 to-cyan-600",
  Textile: "from-amber-500 to-orange-600",
  Logistics: "from-slate-500 to-slate-600",
};

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export default function CompanyDashboard() {
  const navigate = useNavigate();
  const [companies, setCompanies] = useState<Company[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [industryFilter, setIndustryFilter] = useState("");
  const [stateFilter, setStateFilter] = useState("");
  const [summary, setSummary] = useState<CompanySummary>({
    total_clients: 0,
    active_clients: 0,
    total_pending_filings: 0,
    total_overdue: 0,
  });

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [companiesRes, summaryRes] = await Promise.all([
        api.get("/companies"),
        api.get("/partner-dashboard"),
      ]);

      const data = Array.isArray(companiesRes.data)
        ? companiesRes.data
        : Array.isArray(companiesRes.data?.items)
          ? companiesRes.data.items
          : [];
      setCompanies(data);

      const summaryData = (summaryRes.data || {}) as Record<string, unknown>;
      setSummary({
        total_clients: Number(summaryData.total_clients || data.length || 0),
        active_clients: Number(summaryData.active_clients || 0),
        total_pending_filings: Number(summaryData.total_pending_filings || 0),
        total_overdue: Number(summaryData.total_overdue || 0),
      });
    } catch (err) {
      setCompanies([]);
      setSummary({
        total_clients: 0,
        active_clients: 0,
        total_pending_filings: 0,
        total_overdue: 0,
      });
      setError(extractApiError(err, "Failed to load companies."));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  // BUG-001: seed from the canonical INDUSTRIES list so the dropdown has
  // options even when the tenant has no companies yet, then union any
  // industries present on currently-loaded companies so bespoke values
  // saved before the canonical list grew are still selectable.
  // BUG-010: same pattern for the state filter — seed with all INDIAN_STATES
  // codes so the dropdown is never empty on an unpopulated tenant.
  const industries = (() => {
    const pool = new Set(INDUSTRIES);
    for (const c of companies) if (c.industry) pool.add(c.industry);
    if (industryFilter) pool.add(industryFilter);
    return [...pool].sort();
  })();
  const states = (() => {
    const pool = new Set(INDIAN_STATES.map((s) => s.code));
    for (const c of companies) {
      const v = c.state || c.state_code;
      if (v) pool.add(v);
    }
    if (stateFilter) pool.add(stateFilter);
    return [...pool].sort();
  })();

  const filtered = companies.filter((c) => {
    const matchSearch = !search || c.name.toLowerCase().includes(search.toLowerCase()) || (c.gstin || "").toLowerCase().includes(search.toLowerCase());
    const matchIndustry = !industryFilter || c.industry === industryFilter;
    const matchState = !stateFilter || (c.state || c.state_code) === stateFilter;
    return matchSearch && matchIndustry && matchState;
  });

  const isActive = (c: Company) =>
    c.is_active === false
      ? false
      : c.status === "active" || c.is_active === true || !c.status;
  const activeCount = summary.active_clients || companies.filter(isActive).length;
  const totalCount = summary.total_clients || companies.length;
  const inactiveCount = Math.max(totalCount - activeCount, 0);

  // BUG-002 / BUG-009 (Ramesh 2026-04-20): quick-actions menu on each
  // card. Archive toggles is_active via PATCH (soft delete), Hard
  // delete uses DELETE which the backend treats as a tenant-admin-only
  // destructive op. Single active menu at a time to avoid modal-like
  // overlays on dense grids.
  const [openMenuId, setOpenMenuId] = useState<string | null>(null);
  const [busyCompanyId, setBusyCompanyId] = useState<string | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const close = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setOpenMenuId(null);
      }
    };
    if (openMenuId) {
      document.addEventListener("mousedown", close);
      return () => document.removeEventListener("mousedown", close);
    }
  }, [openMenuId]);

  const handleArchive = async (company: Company) => {
    const nextActive = !isActive(company);
    const verb = nextActive ? "reactivate" : "archive";
    if (!window.confirm(`Are you sure you want to ${verb} ${company.name}?`)) return;
    setBusyCompanyId(company.id);
    setError(null);
    try {
      await api.patch(`/companies/${company.id}`, { is_active: nextActive });
      setOpenMenuId(null);
      await fetchData();
    } catch (err) {
      setError(extractApiError(err, `Failed to ${verb} ${company.name}.`));
    } finally {
      setBusyCompanyId(null);
    }
  };

  const handleDelete = async (company: Company) => {
    const confirmed = window.confirm(
      `Permanently delete ${company.name}? This cannot be undone. ` +
        "Consider Archive if you may need the data later.",
    );
    if (!confirmed) return;
    // Two-stage confirm for hard delete because it's irreversible.
    const typed = window.prompt(
      `Type ${company.name} to confirm hard delete:`,
      "",
    );
    if (typed !== company.name) {
      setError("Delete cancelled — name did not match.");
      return;
    }
    setBusyCompanyId(company.id);
    setError(null);
    try {
      await api.delete(`/companies/${company.id}`);
      setOpenMenuId(null);
      await fetchData();
    } catch (err) {
      setError(extractApiError(err, `Failed to delete ${company.name}.`));
    } finally {
      setBusyCompanyId(null);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[50vh]">
        <p className="text-muted-foreground">Loading companies...</p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <Helmet>
        <title>Companies | AgenticOrg</title>
      </Helmet>

      {error && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-700">
          {error}
        </div>
      )}

      {/* Header */}
      <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
        <div>
          <h2 className="text-2xl font-bold">Companies</h2>
          <p className="text-sm text-muted-foreground mt-1">
            {totalCount} total &middot; {activeCount} active &middot; {inactiveCount} inactive
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" onClick={fetchData}>Refresh</Button>
          <Link to="/dashboard/partner">
            <Button variant="outline">Partner View</Button>
          </Link>
          <Link to="/dashboard/companies/new">
            <Button>Add Client</Button>
          </Link>
        </div>
      </div>

      {/* Stats Bar */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <Card>
          <CardContent className="pt-4 pb-4">
            <p className="text-2xl font-bold">{totalCount}</p>
            <p className="text-xs text-muted-foreground">Total Clients</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4 pb-4">
            <p className="text-2xl font-bold text-emerald-600">{activeCount}</p>
            <p className="text-xs text-muted-foreground">Active</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4 pb-4">
            <p className="text-2xl font-bold text-amber-600">{summary.total_pending_filings}</p>
            <p className="text-xs text-muted-foreground">Pending Filings</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4 pb-4">
            <p className="text-2xl font-bold text-red-600">{summary.total_overdue}</p>
            <p className="text-xs text-muted-foreground">Overdue Filings</p>
          </CardContent>
        </Card>
      </div>

      {/* Filters */}
      <div className="flex flex-col sm:flex-row gap-3">
        <input
          type="text"
          placeholder="Search by name or GSTIN..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="flex-1 h-9 rounded-md border border-input bg-background px-3 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
        />
        <select
          value={industryFilter}
          onChange={(e) => setIndustryFilter(e.target.value)}
          className="h-9 rounded-md border border-input bg-background px-3 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
        >
          <option value="">All Industries</option>
          {industries.map((ind) => (
            <option key={ind} value={ind}>{ind}</option>
          ))}
        </select>
        <select
          value={stateFilter}
          onChange={(e) => setStateFilter(e.target.value)}
          className="h-9 rounded-md border border-input bg-background px-3 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
        >
          <option value="">All States</option>
          {states.map((st) => {
            const meta = INDIAN_STATES.find((s) => s.code === st);
            return (
              <option key={st} value={st}>
                {meta ? `${meta.code} — ${meta.name}` : st}
              </option>
            );
          })}
        </select>
      </div>

      {/* Company Cards Grid */}
      {filtered.length === 0 ? (
        <div className="text-center py-12">
          <p className="text-muted-foreground">
            {companies.length === 0 ? "No data yet. Add a client to get started." : "No companies found matching your filters."}
          </p>
        </div>
      ) : (
        <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-4">
          {filtered.map((company) => (
            <Card
              key={company.id}
              className="group relative cursor-pointer hover:shadow-md transition-shadow"
              onClick={() => navigate(`/dashboard/companies/${company.id}`)}
            >
              <CardHeader className="pb-2">
                <div className="flex items-start justify-between">
                  <div className={`w-10 h-10 rounded-lg bg-gradient-to-br ${INDUSTRY_COLORS[company.industry || ""] || "from-gray-500 to-gray-600"} flex items-center justify-center text-white font-bold text-sm`}>
                    {company.name.charAt(0)}
                  </div>
                  <div className="flex items-center gap-2">
                    <span
                      className={`w-2.5 h-2.5 rounded-full ${
                        ((company.health_score ?? company.client_health_score ?? 0)) >= 80 ? "bg-emerald-500" :
                        ((company.health_score ?? company.client_health_score ?? 0)) >= 50 ? "bg-amber-500" : "bg-red-500"
                      }`}
                      title={`Health: ${company.health_score ?? company.client_health_score ?? "N/A"}`}
                    />
                    <Badge variant={isActive(company) ? "success" : "secondary"}>
                      {isActive(company) ? "active" : (company.status || "inactive")}
                    </Badge>
                    {/*
                      BUG-009 (Ramesh 2026-04-20): kebab menu with Archive / Delete.
                      Shows on group-hover on desktop, always on mobile (md: class).
                    */}
                    <div
                      className="relative opacity-0 group-hover:opacity-100 focus-within:opacity-100 md:opacity-100 transition-opacity"
                      ref={openMenuId === company.id ? menuRef : undefined}
                    >
                      <button
                        type="button"
                        aria-label={`Open actions for ${company.name}`}
                        className="p-1 rounded hover:bg-muted disabled:opacity-50"
                        disabled={busyCompanyId === company.id}
                        onClick={(e) => {
                          e.stopPropagation();
                          setOpenMenuId((current) =>
                            current === company.id ? null : company.id,
                          );
                        }}
                      >
                        <svg
                          width="16"
                          height="16"
                          viewBox="0 0 24 24"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="2"
                          strokeLinecap="round"
                          strokeLinejoin="round"
                        >
                          <circle cx="12" cy="5" r="1.5" />
                          <circle cx="12" cy="12" r="1.5" />
                          <circle cx="12" cy="19" r="1.5" />
                        </svg>
                      </button>
                      {openMenuId === company.id && (
                        <div
                          className="absolute right-0 top-full mt-1 w-40 rounded-md border bg-popover shadow-lg z-10"
                          onClick={(e) => e.stopPropagation()}
                        >
                          <button
                            type="button"
                            className="w-full text-left px-3 py-2 text-sm hover:bg-muted"
                            onClick={(e) => {
                              e.stopPropagation();
                              setOpenMenuId(null);
                              navigate(`/dashboard/companies/${company.id}`);
                            }}
                          >
                            View details
                          </button>
                          <button
                            type="button"
                            className="w-full text-left px-3 py-2 text-sm hover:bg-muted"
                            onClick={(e) => {
                              e.stopPropagation();
                              void handleArchive(company);
                            }}
                          >
                            {isActive(company) ? "Archive" : "Reactivate"}
                          </button>
                          <button
                            type="button"
                            className="w-full text-left px-3 py-2 text-sm text-red-600 hover:bg-red-50 dark:hover:bg-red-950/30"
                            onClick={(e) => {
                              e.stopPropagation();
                              void handleDelete(company);
                            }}
                          >
                            Delete permanently
                          </button>
                        </div>
                      )}
                    </div>
                  </div>
                </div>
                <CardTitle className="text-base mt-2">{company.name}</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="space-y-1.5">
                  {company.gstin && (
                    <div className="flex items-center gap-2 text-xs">
                      <span className="text-muted-foreground w-12">GSTIN</span>
                      <span className="font-mono">{company.gstin}</span>
                    </div>
                  )}
                  {company.pan && (
                    <div className="flex items-center gap-2 text-xs">
                      <span className="text-muted-foreground w-12">PAN</span>
                      <span className="font-mono">{company.pan}</span>
                    </div>
                  )}
                  <div className="flex items-center gap-3 mt-2 pt-2 border-t">
                    {company.industry && (
                      <Badge variant="outline" className="text-[10px]">{company.industry}</Badge>
                    )}
                    {(company.state || company.state_code) && (
                      <span className="text-[10px] text-muted-foreground">{company.state || company.state_code}</span>
                    )}
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
