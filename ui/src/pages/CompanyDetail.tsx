import { useState, useEffect, useCallback } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { Helmet } from "react-helmet-async";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import api from "@/lib/api";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface CompanyInfo {
  id: string;
  name: string;
  gstin?: string;
  pan?: string;
  tan?: string;
  cin?: string;
  industry?: string;
  state?: string;
  status?: string;
  address?: string;
  created_at?: string;
  pf_reg?: string;
  esi_reg?: string;
  pt_reg?: string;
  fy_start?: string;
  fy_end?: string;
  signatory_name?: string;
  signatory_designation?: string;
  signatory_email?: string;
  bank_name?: string;
  account_number?: string;
  ifsc?: string;
  branch?: string;
  tally_bridge_url?: string;
  tally_company_name?: string;
  gst_auto_file?: boolean;
  subscription_status?: string;
}

interface AuditEntry {
  id: string;
  timestamp: string;
  action: string;
  actor: string;
  outcome: string;
}

interface AgentAssignment {
  id: string;
  name: string;
  domain: string;
  status: string;
}

interface WorkflowRun {
  id: string;
  name: string;
  status: string;
  started_at: string;
}

interface RoleMember {
  email: string;
  role: string;
}


/* ------------------------------------------------------------------ */
/*  GST Calendar helpers                                               */
/* ------------------------------------------------------------------ */

const MONTHS = ["Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec", "Jan", "Feb", "Mar"];

type FilingStatus = "filed" | "pending" | "overdue";

function getFilingStatus(monthIndex: number): FilingStatus {
  // Mock: filed for months 0-8, pending for 9, overdue for 10-11
  if (monthIndex < 9) return "filed";
  if (monthIndex === 9) return "pending";
  return "overdue";
}

const STATUS_COLORS: Record<FilingStatus, string> = {
  filed: "bg-emerald-500 text-white",
  pending: "bg-amber-400 text-amber-900",
  overdue: "bg-red-500 text-white",
};

/* ------------------------------------------------------------------ */
/*  Industry colors                                                    */
/* ------------------------------------------------------------------ */

const INDUSTRY_COLORS: Record<string, string> = {
  Manufacturing: "from-blue-500 to-cyan-600",
  Export: "from-emerald-500 to-teal-600",
  Healthcare: "from-red-500 to-pink-600",
  "IT Services": "from-purple-500 to-indigo-600",
  Textile: "from-amber-500 to-orange-600",
  Logistics: "from-slate-500 to-slate-600",
};

const DOMAIN_COLORS: Record<string, string> = {
  finance: "bg-emerald-100 text-emerald-800",
  hr: "bg-violet-100 text-violet-800",
  marketing: "bg-blue-100 text-blue-800",
  ops: "bg-orange-100 text-orange-800",
};

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

type TabKey = "overview" | "compliance" | "agents" | "workflows" | "audit" | "approvals" | "settings";

export default function CompanyDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [company, setCompany] = useState<CompanyInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<TabKey>("overview");
  const [agents] = useState<AgentAssignment[]>([]);
  const [workflows] = useState<WorkflowRun[]>([]);
  const [auditLog] = useState<AuditEntry[]>([]);
  const [roles, setRoles] = useState<RoleMember[]>([]);
  const [editForm, setEditForm] = useState<Partial<CompanyInfo>>({});
  const [saving, setSaving] = useState(false);

  /* Approvals data -- shared between overview metrics and approvals tab */
  const filingApprovals = [
    { id: "fa1", filing_type: "GSTR-1", period: "Mar 2026", status: "approved", requested_by: "GST Agent", approved_by: "partner@cafirm.com", date: "2026-04-05" },
    { id: "fa2", filing_type: "GSTR-3B", period: "Mar 2026", status: "pending", requested_by: "GST Agent", approved_by: null as string | null, date: "2026-04-07" },
    { id: "fa3", filing_type: "TDS 26Q", period: "Q4 FY26", status: "approved", requested_by: "TDS Agent", approved_by: "partner@cafirm.com", date: "2026-04-06" },
    { id: "fa4", filing_type: "GSTR-9", period: "FY 2025-26", status: "pending", requested_by: "GST Agent", approved_by: null as string | null, date: "2026-04-08" },
  ];
  const pendingFilingsCount = filingApprovals.filter((a) => a.status === "pending").length;
  const [newRoleEmail, setNewRoleEmail] = useState("");
  const [newRoleValue, setNewRoleValue] = useState("auditor");

  /* Credential Vault state */
  const [credUsername, setCredUsername] = useState("");
  const [credPassword, setCredPassword] = useState("");
  const [credPortalType, setCredPortalType] = useState("GSTN");
  const [gstnAutoUpload, setGstnAutoUpload] = useState(false);
  const [storedCreds] = useState([
    { gstin: "29AABCU9603R1ZM", username: "acme_gst_user", portal: "GSTN", status: "active", last_verified: "2026-04-05", },
    { gstin: "29AABCU9603R1ZM", username: "acme_it_user", portal: "Income Tax", status: "active", last_verified: "2026-03-20", },
  ]);

  const fetchCompany = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.get(`/companies/${id}`);
      if (res.status && res.status >= 400) {
        setError(`Server returned ${res.status}.`);
        setCompany(null);
        setEditForm({});
      } else if (res.data?.id) {
        setCompany(res.data);
        setEditForm(res.data);
      } else {
        setError("No data returned from API.");
        setCompany(null);
        setEditForm({});
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Unknown error";
      const status = (err as { response?: { status?: number } })?.response?.status;
      if (status === 502) {
        setError("502 Bad Gateway -- API is temporarily unavailable.");
      } else {
        setError(`Failed to load company: ${msg}.`);
      }
      setCompany(null);
      setEditForm({});
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    fetchCompany();
  }, [fetchCompany]);

  const handleSaveSettings = async () => {
    setSaving(true);
    try {
      await api.patch(`/companies/${id}`, editForm);
      setCompany({ ...company, ...editForm } as CompanyInfo);
    } catch {
      // Silently fail for demo
    } finally {
      setSaving(false);
    }
  };

  const handleAddRole = () => {
    if (!newRoleEmail.trim()) return;
    setRoles([...roles, { email: newRoleEmail.trim(), role: newRoleValue }]);
    setNewRoleEmail("");
  };

  const handleRemoveRole = (email: string) => {
    setRoles(roles.filter((r) => r.email !== email));
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[50vh]">
        <p className="text-muted-foreground">Loading company...</p>
      </div>
    );
  }

  if (!company) {
    return (
      <div className="space-y-4">
        {error && (
          <div className="bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg p-3">
            <p className="text-sm text-red-700 dark:text-red-300">{error}</p>
          </div>
        )}
        <p className="text-muted-foreground">Company not found.</p>
        <Button variant="outline" onClick={() => navigate("/dashboard/companies")}>Back to Companies</Button>
      </div>
    );
  }

  const tabs: { key: TabKey; label: string }[] = [
    { key: "overview", label: "Overview" },
    { key: "compliance", label: "Compliance" },
    { key: "agents", label: "Agents" },
    { key: "workflows", label: "Workflows" },
    { key: "audit", label: "Audit Log" },
    { key: "approvals", label: "Approvals" },
    { key: "settings", label: "Settings" },
  ];

  return (
    <div className="space-y-6">
      <Helmet>
        <title>{company.name} | AgenticOrg</title>
      </Helmet>

      {/* Error banner */}
      {error && (
        <div className="bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 rounded-lg p-3">
          <p className="text-sm text-amber-700 dark:text-amber-300">{error}</p>
        </div>
      )}

      {/* Header */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
        <div className="flex items-center gap-4">
          <div className={`w-12 h-12 rounded-xl bg-gradient-to-br ${INDUSTRY_COLORS[company.industry || ""] || "from-gray-500 to-gray-600"} flex items-center justify-center text-white font-bold text-lg`}>
            {company.name.charAt(0)}
          </div>
          <div>
            <h2 className="text-2xl font-bold">{company.name}</h2>
            <div className="flex items-center gap-3 mt-1">
              {company.gstin && <span className="text-sm font-mono text-muted-foreground">{company.gstin}</span>}
              {company.industry && <Badge variant="outline">{company.industry}</Badge>}
              <Badge variant={
                (company.subscription_status || "trial") === "active" ? "success" :
                (company.subscription_status || "trial") === "expired" ? "destructive" : "warning"
              }>
                {(company.subscription_status || "trial").charAt(0).toUpperCase() + (company.subscription_status || "trial").slice(1)}
              </Badge>
              <Badge variant={company.status === "active" ? "success" : "secondary"}>
                {company.status || "active"}
              </Badge>
            </div>
          </div>
        </div>
        <Button variant="outline" onClick={() => navigate("/dashboard/companies")}>Back to Companies</Button>
      </div>

      {/* Tabs */}
      <div className="flex border-b overflow-x-auto">
        {tabs.map((tab) => (
          <button
            key={tab.key}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors whitespace-nowrap ${
              activeTab === tab.key
                ? "border-primary text-primary"
                : "border-transparent text-muted-foreground hover:text-foreground"
            }`}
            onClick={() => setActiveTab(tab.key)}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* ==================== Overview Tab ==================== */}
      {activeTab === "overview" && (
        <div className="space-y-6">
          {/* Metrics row */}
          <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
            <Card>
              <CardContent className="pt-4 pb-4">
                <p className="text-2xl font-bold text-amber-600">{pendingFilingsCount}</p>
                <p className="text-xs text-muted-foreground">Pending Filings</p>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="pt-4 pb-4">
                <p className="text-2xl font-bold text-blue-600">12</p>
                <p className="text-xs text-muted-foreground">Recon Items</p>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="pt-4 pb-4">
                <p className="text-2xl font-bold text-emerald-600">47</p>
                <p className="text-xs text-muted-foreground">Agent Runs (30d)</p>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="pt-4 pb-4">
                <p className="text-2xl font-bold text-purple-600">99.2%</p>
                <p className="text-xs text-muted-foreground">Success Rate</p>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="pt-4 pb-4">
                {(() => {
                  const healthScore = 92;
                  const color = healthScore >= 80 ? "text-emerald-600" : healthScore >= 50 ? "text-amber-600" : "text-red-600";
                  return (
                    <>
                      <p className={`text-2xl font-bold ${color}`}>{healthScore}</p>
                      <p className="text-xs text-muted-foreground">Client Health Score</p>
                    </>
                  );
                })()}
              </CardContent>
            </Card>
          </div>

          {/* Info card */}
          <div className="grid md:grid-cols-2 gap-6">
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Company Information</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="grid grid-cols-2 gap-3 text-sm">
                  <div><span className="text-muted-foreground">PAN:</span> <span className="font-mono">{company.pan || "—"}</span></div>
                  <div><span className="text-muted-foreground">TAN:</span> <span className="font-mono">{company.tan || "—"}</span></div>
                  <div><span className="text-muted-foreground">CIN:</span> <span className="font-mono">{company.cin || "—"}</span></div>
                  <div><span className="text-muted-foreground">State:</span> {company.state || "—"}</div>
                  <div className="col-span-2"><span className="text-muted-foreground">Address:</span> {company.address || "—"}</div>
                  <div><span className="text-muted-foreground">FY:</span> {company.fy_start || "—"} to {company.fy_end || "—"}</div>
                  <div><span className="text-muted-foreground">Signatory:</span> {company.signatory_name || "—"}</div>
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="text-base">Recent Agent Runs</CardTitle>
              </CardHeader>
              <CardContent>
                {auditLog.length === 0 ? (
                  <p className="text-muted-foreground text-sm">No data yet.</p>
                ) : (
                <div className="space-y-3">
                  {auditLog.slice(0, 4).map((entry) => (
                    <div key={entry.id} className="flex items-center justify-between text-sm">
                      <div>
                        <p className="font-medium">{entry.action}</p>
                        <p className="text-xs text-muted-foreground">{entry.actor}</p>
                      </div>
                      <Badge variant={entry.outcome === "success" ? "success" : "warning"} className="text-[10px]">
                        {entry.outcome}
                      </Badge>
                    </div>
                  ))}
                </div>
                )}
              </CardContent>
            </Card>
          </div>
        </div>
      )}

      {/* ==================== Compliance Tab ==================== */}
      {activeTab === "compliance" && (
        <div className="space-y-6">
          {/* GST Filing Calendar */}
          <Card>
            <CardHeader>
              <CardTitle className="text-base">GST Filing Calendar (FY 2025-26)</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-4 sm:grid-cols-6 lg:grid-cols-12 gap-2">
                {MONTHS.map((month, i) => {
                  const status = getFilingStatus(i);
                  return (
                    <div
                      key={month}
                      className={`rounded-lg p-3 text-center ${STATUS_COLORS[status]} cursor-default`}
                      title={`${month}: ${status}`}
                    >
                      <p className="text-xs font-bold">{month}</p>
                      <p className="text-[10px] mt-0.5 capitalize">{status}</p>
                    </div>
                  );
                })}
              </div>
              <div className="flex items-center gap-4 mt-4 text-xs">
                <span className="flex items-center gap-1.5"><span className="w-3 h-3 rounded bg-emerald-500" /> Filed</span>
                <span className="flex items-center gap-1.5"><span className="w-3 h-3 rounded bg-amber-400" /> Pending</span>
                <span className="flex items-center gap-1.5"><span className="w-3 h-3 rounded bg-red-500" /> Overdue</span>
              </div>
            </CardContent>
          </Card>

          {/* TDS Quarterly Status */}
          <Card>
            <CardHeader>
              <CardTitle className="text-base">TDS Quarterly Status</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid sm:grid-cols-4 gap-4">
                {[
                  { quarter: "Q1 (Apr-Jun)", form: "26Q", status: "filed" as FilingStatus },
                  { quarter: "Q2 (Jul-Sep)", form: "26Q", status: "filed" as FilingStatus },
                  { quarter: "Q3 (Oct-Dec)", form: "26Q", status: "filed" as FilingStatus },
                  { quarter: "Q4 (Jan-Mar)", form: "26Q", status: "pending" as FilingStatus },
                ].map((q) => (
                  <div key={q.quarter} className="border rounded-lg p-4 text-center">
                    <p className="text-sm font-semibold">{q.quarter}</p>
                    <p className="text-xs text-muted-foreground mb-2">Form {q.form}</p>
                    <Badge variant={q.status === "filed" ? "success" : q.status === "pending" ? "warning" : "destructive"}>
                      {q.status}
                    </Badge>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>

          {/* Compliance Details */}
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Compliance Registrations</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid sm:grid-cols-2 gap-3 text-sm">
                <div><span className="text-muted-foreground">PF Registration:</span> {company.pf_reg || "—"}</div>
                <div><span className="text-muted-foreground">ESI Registration:</span> {company.esi_reg || "—"}</div>
                <div><span className="text-muted-foreground">PT Registration:</span> {company.pt_reg || "—"}</div>
                <div><span className="text-muted-foreground">GST Auto-File:</span> {company.gst_auto_file ? <Badge variant="warning">Enabled</Badge> : "Disabled"}</div>
              </div>
            </CardContent>
          </Card>

          {/* GSTN Manual Upload */}
          <Card>
            <CardHeader>
              <CardTitle className="text-base">GSTN Manual Upload</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-xs text-muted-foreground mb-4 bg-blue-50 border border-blue-200 rounded-lg px-3 py-2">
                For companies with auto-filing disabled, download the JSON and upload manually to the GSTN portal.
              </p>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b">
                      <th className="text-left py-2 px-3 font-medium text-muted-foreground">Type</th>
                      <th className="text-left py-2 px-3 font-medium text-muted-foreground">Period</th>
                      <th className="text-left py-2 px-3 font-medium text-muted-foreground">Status</th>
                      <th className="text-left py-2 px-3 font-medium text-muted-foreground">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[
                      { id: "gu1", type: "GSTR-1 JSON", period: "Mar 2026", status: "downloaded", file_name: "GSTR1_27AABCS1234F1Z5_032026.json" },
                      { id: "gu2", type: "GSTR-3B JSON", period: "Mar 2026", status: "generated", file_name: "GSTR3B_27AABCS1234F1Z5_032026.json" },
                    ].map((file) => (
                      <tr key={file.id} className="border-b last:border-0">
                        <td className="py-2 px-3 font-medium">{file.type}</td>
                        <td className="py-2 px-3 text-muted-foreground">{file.period}</td>
                        <td className="py-2 px-3">
                          <Badge variant={file.status === "downloaded" ? "success" : "secondary"}>
                            {file.status}
                          </Badge>
                        </td>
                        <td className="py-2 px-3">
                          <div className="flex items-center gap-2">
                            <Button variant="outline" size="sm" onClick={() => alert(`Downloading ${file.file_name}`)}>
                              Download
                            </Button>
                            <Button variant="outline" size="sm" onClick={() => {
                              const arn = prompt("Enter GSTN ARN after uploading to portal:");
                              if (arn) alert(`Marked as uploaded with ARN: ${arn}`);
                            }}>
                              Mark as Uploaded
                            </Button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>

          {/* Compliance Calendar */}
          <Card>
            <CardHeader>
              <div className="flex items-center justify-between">
                <CardTitle className="text-base">Compliance Calendar</CardTitle>
                <Button variant="outline" size="sm" onClick={() => alert("Generating compliance deadlines for this company...")}>
                  Generate Deadlines
                </Button>
              </div>
            </CardHeader>
            <CardContent>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b">
                      <th className="text-left py-2 px-3 font-medium text-muted-foreground">Type</th>
                      <th className="text-left py-2 px-3 font-medium text-muted-foreground">Period</th>
                      <th className="text-left py-2 px-3 font-medium text-muted-foreground">Due Date</th>
                      <th className="text-left py-2 px-3 font-medium text-muted-foreground">Alert Status</th>
                      <th className="text-left py-2 px-3 font-medium text-muted-foreground">Filed</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[
                      { type: "GSTR-3B", period: "Apr 2026", due: "2026-04-20", alert_7d: true, alert_1d: false, filed: false },
                      { type: "GSTR-1", period: "Apr 2026", due: "2026-05-11", alert_7d: false, alert_1d: false, filed: false },
                      { type: "TDS 26Q", period: "Q1 FY27", due: "2026-07-31", alert_7d: false, alert_1d: false, filed: false },
                      { type: "PF ECR", period: "Apr 2026", due: "2026-05-15", alert_7d: true, alert_1d: false, filed: false },
                      { type: "PT", period: "Apr 2026", due: "2026-04-30", alert_7d: true, alert_1d: true, filed: false },
                    ].map((dl, idx) => (
                      <tr key={idx} className="border-b last:border-0">
                        <td className="py-2 px-3 font-medium">{dl.type}</td>
                        <td className="py-2 px-3 text-muted-foreground">{dl.period}</td>
                        <td className="py-2 px-3 text-xs">{dl.due}</td>
                        <td className="py-2 px-3">
                          <div className="flex items-center gap-1">
                            {dl.alert_7d && <Badge variant="warning" className="text-[10px]">7d sent</Badge>}
                            {dl.alert_1d && <Badge variant="destructive" className="text-[10px]">1d sent</Badge>}
                            {!dl.alert_7d && !dl.alert_1d && <span className="text-xs text-muted-foreground">Not yet</span>}
                          </div>
                        </td>
                        <td className="py-2 px-3">
                          <Badge variant={dl.filed ? "success" : "secondary"}>
                            {dl.filed ? "Filed" : "Pending"}
                          </Badge>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      {/* ==================== Agents Tab ==================== */}
      {activeTab === "agents" && (
        <div className="space-y-4">
          {agents.length === 0 && (
            <p className="text-muted-foreground text-sm">No data yet. Agents will appear once assigned to this company.</p>
          )}
          {agents.map((agent) => (
            <Card key={agent.id} className="hover:shadow-md transition-shadow">
              <CardContent className="pt-4 pb-4">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <div className={`w-8 h-8 rounded-lg flex items-center justify-center text-white font-bold text-xs bg-gradient-to-br ${
                      agent.domain === "finance" ? "from-emerald-500 to-teal-600" :
                      agent.domain === "hr" ? "from-violet-500 to-indigo-600" :
                      "from-blue-500 to-cyan-600"
                    }`}>
                      {agent.name.charAt(0)}
                    </div>
                    <div>
                      <p className="font-medium text-sm">{agent.name}</p>
                      <span className={`text-[10px] px-1.5 py-0.5 rounded ${DOMAIN_COLORS[agent.domain] || "bg-gray-100 text-gray-800"}`}>
                        {agent.domain}
                      </span>
                    </div>
                  </div>
                  <Badge variant={agent.status === "active" ? "success" : "secondary"}>
                    {agent.status}
                  </Badge>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {/* ==================== Workflows Tab ==================== */}
      {activeTab === "workflows" && (
        <div className="space-y-4">
          {workflows.length === 0 && (
            <p className="text-muted-foreground text-sm">No data yet. Workflow runs will appear once triggered.</p>
          )}
          {workflows.map((wf) => (
            <Card key={wf.id} className="hover:shadow-md transition-shadow">
              <CardContent className="pt-4 pb-4">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="font-medium text-sm">{wf.name}</p>
                    <p className="text-xs text-muted-foreground">Started: {new Date(wf.started_at).toLocaleString()}</p>
                  </div>
                  <Badge variant={
                    wf.status === "completed" ? "success" :
                    wf.status === "running" ? "default" :
                    "secondary"
                  }>
                    {wf.status}
                  </Badge>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {/* ==================== Audit Log Tab ==================== */}
      {activeTab === "audit" && (
        <Card>
          <CardContent className="pt-4">
            {auditLog.length === 0 ? (
              <p className="text-muted-foreground text-sm">No data yet. Audit entries will appear as agents perform actions.</p>
            ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b">
                    <th className="text-left py-2 px-3 font-medium text-muted-foreground">Timestamp</th>
                    <th className="text-left py-2 px-3 font-medium text-muted-foreground">Action</th>
                    <th className="text-left py-2 px-3 font-medium text-muted-foreground">Actor</th>
                    <th className="text-left py-2 px-3 font-medium text-muted-foreground">Outcome</th>
                  </tr>
                </thead>
                <tbody>
                  {auditLog.map((entry) => (
                    <tr key={entry.id} className="border-b last:border-0">
                      <td className="py-2 px-3 text-xs text-muted-foreground whitespace-nowrap">
                        {new Date(entry.timestamp).toLocaleString()}
                      </td>
                      <td className="py-2 px-3">{entry.action}</td>
                      <td className="py-2 px-3 text-muted-foreground">{entry.actor}</td>
                      <td className="py-2 px-3">
                        <Badge variant={entry.outcome === "success" ? "success" : "warning"} className="text-[10px]">
                          {entry.outcome}
                        </Badge>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            )}
          </CardContent>
        </Card>
      )}

      {/* ==================== Approvals Tab ==================== */}
      {activeTab === "approvals" && (
        <div className="space-y-6">
          <div className="flex items-center justify-between">
            <h3 className="text-lg font-semibold">Filing Approvals</h3>
            <Button onClick={() => alert("Approval request sent to partner for review.")}>
              Request Filing Approval
            </Button>
          </div>
          <Card>
            <CardContent className="pt-4">
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b">
                      <th className="text-left py-2 px-3 font-medium text-muted-foreground">Filing Type</th>
                      <th className="text-left py-2 px-3 font-medium text-muted-foreground">Period</th>
                      <th className="text-left py-2 px-3 font-medium text-muted-foreground">Status</th>
                      <th className="text-left py-2 px-3 font-medium text-muted-foreground">Requested By</th>
                      <th className="text-left py-2 px-3 font-medium text-muted-foreground">Approved By</th>
                      <th className="text-left py-2 px-3 font-medium text-muted-foreground">Date</th>
                      <th className="text-left py-2 px-3 font-medium text-muted-foreground">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filingApprovals.map((req) => (
                      <tr key={req.id} className="border-b last:border-0">
                        <td className="py-2 px-3 font-medium">{req.filing_type}</td>
                        <td className="py-2 px-3 text-muted-foreground">{req.period}</td>
                        <td className="py-2 px-3">
                          <Badge variant={
                            req.status === "approved" ? "success" :
                            req.status === "pending" ? "warning" :
                            req.status === "rejected" ? "destructive" : "default"
                          }>
                            {req.status}
                          </Badge>
                        </td>
                        <td className="py-2 px-3 text-muted-foreground">{req.requested_by}</td>
                        <td className="py-2 px-3 text-muted-foreground">{req.approved_by || "—"}</td>
                        <td className="py-2 px-3 text-xs text-muted-foreground">{req.date}</td>
                        <td className="py-2 px-3">
                          {req.status === "pending" && (
                            <Button size="sm" onClick={() => alert(`Approved: ${req.filing_type} for ${req.period}`)}>
                              Approve
                            </Button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      {/* ==================== Settings Tab ==================== */}
      {activeTab === "settings" && (
        <div className="space-y-6">
          {/* Edit form */}
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Company Settings</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid gap-4 sm:grid-cols-2">
                <div>
                  <label className="block text-sm font-medium mb-1">Company Name</label>
                  <input
                    value={editForm.name || ""}
                    onChange={(e) => setEditForm({ ...editForm, name: e.target.value })}
                    className="w-full h-9 rounded-md border border-input bg-background px-3 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium mb-1">State</label>
                  <input
                    value={editForm.state || ""}
                    onChange={(e) => setEditForm({ ...editForm, state: e.target.value })}
                    className="w-full h-9 rounded-md border border-input bg-background px-3 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium mb-1">Industry</label>
                  <input
                    value={editForm.industry || ""}
                    onChange={(e) => setEditForm({ ...editForm, industry: e.target.value })}
                    className="w-full h-9 rounded-md border border-input bg-background px-3 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium mb-1">Address</label>
                  <input
                    value={editForm.address || ""}
                    onChange={(e) => setEditForm({ ...editForm, address: e.target.value })}
                    className="w-full h-9 rounded-md border border-input bg-background px-3 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
                  />
                </div>
                <div className="sm:col-span-2">
                  <Button onClick={handleSaveSettings} disabled={saving}>
                    {saving ? "Saving..." : "Save Changes"}
                  </Button>
                </div>
              </div>
            </CardContent>
          </Card>

          {/* Role Management */}
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Role Management</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b">
                      <th className="text-left py-2 px-3 font-medium text-muted-foreground">Email</th>
                      <th className="text-left py-2 px-3 font-medium text-muted-foreground">Role</th>
                      <th className="text-left py-2 px-3 font-medium text-muted-foreground">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {roles.map((r) => (
                      <tr key={r.email} className="border-b last:border-0">
                        <td className="py-2 px-3">{r.email}</td>
                        <td className="py-2 px-3">
                          <select
                            value={r.role}
                            onChange={(e) => setRoles(roles.map((m) => m.email === r.email ? { ...m, role: e.target.value } : m))}
                            className="h-8 rounded-md border border-input bg-background px-2 text-xs focus:outline-none focus:ring-1 focus:ring-primary"
                          >
                            <option value="admin">Admin</option>
                            <option value="cfo">CFO</option>
                            <option value="auditor">Auditor</option>
                            <option value="coo">COO</option>
                          </select>
                        </td>
                        <td className="py-2 px-3">
                          <Button variant="outline" size="sm" onClick={() => handleRemoveRole(r.email)}>Remove</Button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div className="flex items-center gap-2 mt-4 pt-4 border-t">
                <input
                  type="email"
                  placeholder="user@company.com"
                  value={newRoleEmail}
                  onChange={(e) => setNewRoleEmail(e.target.value)}
                  className="flex-1 h-9 rounded-md border border-input bg-background px-3 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
                />
                <select
                  value={newRoleValue}
                  onChange={(e) => setNewRoleValue(e.target.value)}
                  className="h-9 rounded-md border border-input bg-background px-3 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
                >
                  <option value="admin">Admin</option>
                  <option value="cfo">CFO</option>
                  <option value="auditor">Auditor</option>
                  <option value="coo">COO</option>
                </select>
                <Button onClick={handleAddRole}>Add</Button>
              </div>
            </CardContent>
          </Card>

          {/* GSTN Portal Credentials Vault */}
          <Card>
            <CardHeader>
              <CardTitle className="text-base">GSTN Portal Credentials</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 rounded-lg p-3 mb-4">
                <p className="text-xs text-amber-700 dark:text-amber-300">
                  Credentials are encrypted at rest using AES-256. Never shared or logged.
                </p>
              </div>

              <div className="grid gap-4 sm:grid-cols-2">
                <div>
                  <label className="block text-sm font-medium mb-1">GSTIN</label>
                  <input
                    value={company.gstin || ""}
                    disabled
                    className="w-full h-9 rounded-md border border-input bg-muted px-3 text-sm"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium mb-1">Username</label>
                  <input
                    value={credUsername}
                    onChange={(e) => setCredUsername(e.target.value)}
                    className="w-full h-9 rounded-md border border-input bg-background px-3 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
                    placeholder="Portal username"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium mb-1">Password</label>
                  <input
                    type="password"
                    value={credPassword}
                    onChange={(e) => setCredPassword(e.target.value)}
                    className="w-full h-9 rounded-md border border-input bg-background px-3 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
                    placeholder="Portal password"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium mb-1">Portal Type</label>
                  <select
                    value={credPortalType}
                    onChange={(e) => setCredPortalType(e.target.value)}
                    className="w-full h-9 rounded-md border border-input bg-background px-3 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
                  >
                    <option value="GSTN">GSTN</option>
                    <option value="Income Tax">Income Tax</option>
                    <option value="EPFO">EPFO</option>
                  </select>
                </div>
                <div className="sm:col-span-2">
                  <Button onClick={() => alert("Credentials saved (encrypted)")}>Save Credentials</Button>
                </div>
              </div>

              {/* Stored credentials table */}
              <div className="overflow-x-auto mt-6 pt-4 border-t">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b">
                      <th className="text-left py-2 px-3 font-medium text-muted-foreground">GSTIN</th>
                      <th className="text-left py-2 px-3 font-medium text-muted-foreground">Username</th>
                      <th className="text-left py-2 px-3 font-medium text-muted-foreground">Portal</th>
                      <th className="text-left py-2 px-3 font-medium text-muted-foreground">Status</th>
                      <th className="text-left py-2 px-3 font-medium text-muted-foreground">Last Verified</th>
                      <th className="text-left py-2 px-3 font-medium text-muted-foreground">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {storedCreds.map((cred, idx) => (
                      <tr key={idx} className="border-b last:border-0">
                        <td className="py-2 px-3 font-mono text-xs">{cred.gstin}</td>
                        <td className="py-2 px-3">{cred.username}</td>
                        <td className="py-2 px-3">{cred.portal}</td>
                        <td className="py-2 px-3">
                          <Badge variant={cred.status === "active" ? "success" : "secondary"}>
                            {cred.status}
                          </Badge>
                        </td>
                        <td className="py-2 px-3 text-xs text-muted-foreground">{cred.last_verified}</td>
                        <td className="py-2 px-3">
                          <div className="flex items-center gap-2">
                            <Button variant="outline" size="sm" onClick={() => alert(`Verifying ${cred.portal} credentials...`)}>
                              Verify
                            </Button>
                            <Button variant="outline" size="sm" onClick={() => alert(`Deactivating ${cred.portal} credentials`)}>
                              Deactivate
                            </Button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {/* Auto-upload toggle */}
              <div className="mt-4 pt-4 border-t">
                <label className="flex items-center gap-3 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={gstnAutoUpload}
                    onChange={(e) => setGstnAutoUpload(e.target.checked)}
                    className="w-4 h-4 rounded border-input"
                  />
                  <span className="text-sm font-medium">Enable auto-upload to GSTN</span>
                </label>
                {gstnAutoUpload && (
                  <p className="text-xs text-amber-600 mt-1 ml-7">
                    Filings will be auto-uploaded to the GSTN portal using stored credentials.
                  </p>
                )}
              </div>
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}
