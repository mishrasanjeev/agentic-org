import { useCallback, useEffect, useMemo, useState } from "react";
import { Helmet } from "react-helmet-async";
import { useNavigate, useParams } from "react-router-dom";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import api, { extractApiError } from "@/lib/api";
import { stateNameFromCode } from "@/lib/indianStates";

interface CompanyInfo {
  id: string;
  name: string;
  gstin?: string | null;
  pan?: string | null;
  tan?: string | null;
  cin?: string | null;
  state_code?: string | null;
  industry?: string | null;
  registered_address?: string | null;
  signatory_name?: string | null;
  signatory_designation?: string | null;
  signatory_email?: string | null;
  compliance_email?: string | null;
  pf_registration?: string | null;
  esi_registration?: string | null;
  pt_registration?: string | null;
  bank_name?: string | null;
  bank_account_number?: string | null;
  bank_ifsc?: string | null;
  bank_branch?: string | null;
  gst_auto_file?: boolean;
  is_active?: boolean;
  subscription_status?: string;
  client_health_score?: number | null;
  document_vault_enabled?: boolean;
  created_at?: string;
  updated_at?: string | null;
  // BUG-003 (Ramesh 2026-04-20): the Settings tab lacked a Tally
  // Connection section, so users who'd finished onboarding had no way
  // to update the ngrok URL or view the bridge_id. The new panel
  // needs these fields from the existing CompanyOut response.
  tally_config?: {
    bridge_url?: string;
    bridge_id?: string;
    company_name?: string;
    bridge_issued_at?: string;
  } | null;
}

interface FilingApproval {
  id: string;
  company_id: string;
  filing_type: string;
  filing_period: string;
  filing_data?: Record<string, unknown>;
  status: string;
  requested_by: string;
  approved_by?: string | null;
  approved_at?: string | null;
  rejection_reason?: string | null;
  auto_approved?: boolean;
  created_at: string;
  updated_at?: string | null;
}

interface ComplianceDeadline {
  id: string;
  company_id: string;
  deadline_type: string;
  filing_period: string;
  due_date: string;
  filed: boolean;
  filed_at?: string | null;
  created_at: string;
}

interface GSTNUpload {
  id: string;
  company_id: string;
  upload_type: string;
  filing_period: string;
  file_name: string;
  file_path?: string | null;
  file_size_bytes?: number | null;
  status: string;
  gstn_arn?: string | null;
  uploaded_at?: string | null;
  uploaded_by?: string | null;
  created_at: string;
}

interface RoleEntry {
  user_id: string;
  role: string;
}

interface Credential {
  id: string;
  company_id: string;
  gstin: string;
  username: string;
  portal_type: string;
  is_active: boolean;
  last_verified_at?: string | null;
  created_at: string;
}

interface AutomationAgent {
  id: string;
  name: string;
  domain: string;
  status: string;
  designation?: string | null;
}

interface AutomationWorkflow {
  id: string;
  name: string;
  description?: string | null;
  is_active: boolean;
  created_at?: string | null;
}

interface ActivityEntry {
  id: string;
  timestamp: string;
  action: string;
  actor: string;
  outcome: string;
}

interface RoleResponse {
  roles?: RoleEntry[];
  valid_roles?: string[];
}

type TabKey =
  | "overview"
  | "compliance"
  | "agents"
  | "workflows"
  | "audit"
  | "approvals"
  | "settings";

function itemsFromResponse<T>(data: unknown): T[] {
  if (Array.isArray(data)) return data as T[];
  if (data && typeof data === "object" && Array.isArray((data as { items?: unknown[] }).items)) {
    return (data as { items: T[] }).items;
  }
  return [];
}

function formatDate(value?: string | null): string {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
}

function formatDateTime(value?: string | null): string {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("en-IN", {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function statusVariant(status: string): "success" | "warning" | "destructive" | "secondary" {
  const normalized = status.toLowerCase();
  if (["approved", "active", "filed", "uploaded", "acknowledged", "success"].includes(normalized)) {
    return "success";
  }
  if (["pending", "trial", "downloaded", "generated"].includes(normalized)) {
    return "warning";
  }
  if (["rejected", "overdue", "inactive", "failed", "expired", "cancelled"].includes(normalized)) {
    return "destructive";
  }
  return "secondary";
}

function healthColor(score: number): string {
  if (score >= 80) return "text-emerald-600";
  if (score >= 50) return "text-amber-600";
  return "text-red-600";
}

function deadlineStatus(deadline: ComplianceDeadline): string {
  if (deadline.filed) return "filed";
  const due = new Date(deadline.due_date);
  const today = new Date();
  return due.getTime() < today.getTime() ? "overdue" : "pending";
}

function deadlineSortValue(deadline: ComplianceDeadline): number {
  return new Date(deadline.due_date).getTime();
}

export default function CompanyDetail() {
  const { id } = useParams();
  const navigate = useNavigate();

  const [company, setCompany] = useState<CompanyInfo | null>(null);
  const [approvals, setApprovals] = useState<FilingApproval[]>([]);
  const [deadlines, setDeadlines] = useState<ComplianceDeadline[]>([]);
  const [uploads, setUploads] = useState<GSTNUpload[]>([]);
  const [roles, setRoles] = useState<RoleEntry[]>([]);
  const [validRoles, setValidRoles] = useState<string[]>(["partner", "manager", "associate", "audit_reviewer"]);
  const [credentials, setCredentials] = useState<Credential[]>([]);
  const [agents, setAgents] = useState<AutomationAgent[]>([]);
  const [workflows, setWorkflows] = useState<AutomationWorkflow[]>([]);
  const [auditEntries, setAuditEntries] = useState<ActivityEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<TabKey>("overview");
  const [savingCompany, setSavingCompany] = useState(false);

  // BUG-003 / BUG-011 (Ramesh 2026-04-20): Tally Connection section
  // for post-onboard Settings. Keep state local to the card so the
  // existing Company Settings form stays untouched.
  const [tallyBridgeUrlDraft, setTallyBridgeUrlDraft] = useState("");
  const [tallyCompanyDraft, setTallyCompanyDraft] = useState("");
  const [tallySaving, setTallySaving] = useState(false);
  const [tallyTesting, setTallyTesting] = useState(false);
  const [tallyTestResult, setTallyTestResult] = useState<"success" | "error" | null>(null);
  const [tallyTestMessage, setTallyTestMessage] = useState("");
  const [bridgeGenerating, setBridgeGenerating] = useState(false);
  const [bridgeTokenOnce, setBridgeTokenOnce] = useState<string | null>(null);
  const [savingRoles, setSavingRoles] = useState(false);
  const [approvalBusyId, setApprovalBusyId] = useState<string | null>(null);
  const [deadlineBusyId, setDeadlineBusyId] = useState<string | null>(null);
  const [credentialBusyId, setCredentialBusyId] = useState<string | null>(null);
  const [creatingApproval, setCreatingApproval] = useState(false);
  const [creatingCredential, setCreatingCredential] = useState(false);

  // Rejection dialog state (replaces window.prompt for embedded browser safety)
  const [rejectDialogId, setRejectDialogId] = useState<string | null>(null);
  const [rejectReason, setRejectReason] = useState("");

  // Agent and workflow detail modals
  const [agentDetail, setAgentDetail] = useState<AutomationAgent | null>(null);
  const [workflowDetail, setWorkflowDetail] = useState<AutomationWorkflow | null>(null);

  // Audit log filter / export controls
  const [auditQuery, setAuditQuery] = useState("");
  const [auditOutcome, setAuditOutcome] = useState<string>("all");

  const [editForm, setEditForm] = useState({
    name: "",
    state_code: "",
    industry: "",
    registered_address: "",
    signatory_name: "",
    signatory_designation: "",
    signatory_email: "",
    compliance_email: "",
    gst_auto_file: false,
  });

  const [newApproval, setNewApproval] = useState({
    filing_type: "gstr3b",
    filing_period: "",
  });
  const [newRoleUserId, setNewRoleUserId] = useState("");
  const [newRoleValue, setNewRoleValue] = useState("associate");
  const [credentialForm, setCredentialForm] = useState({
    gstin: "",
    username: "",
    password: "",
    portal_type: "gstn",
  });

  const fetchData = useCallback(async () => {
    if (!id) {
      setCompany(null);
      setError("Company id is missing.");
      setLoading(false);
      return;
    }

    setLoading(true);
    setError(null);

    const [
      companyResult,
      approvalsResult,
      deadlinesResult,
      uploadsResult,
      rolesResult,
      credentialsResult,
      agentsResult,
      workflowsResult,
      auditResult,
    ] = await Promise.allSettled([
      api.get(`/companies/${id}`),
      api.get(`/companies/${id}/approvals`),
      api.get(`/companies/${id}/deadlines`),
      api.get(`/companies/${id}/gstn-uploads`),
      api.get(`/companies/${id}/roles`),
      api.get(`/companies/${id}/credentials`),
      api.get("/agents", { params: { page: 1, per_page: 200, domain: "finance", company_id: id } }),
      api.get("/workflows", { params: { page: 1, per_page: 200, company_id: id } }),
      api.get("/audit", { params: { page: 1, per_page: 200, company_id: id } }),
    ]);

    if (companyResult.status === "rejected") {
      setCompany(null);
      setError(extractApiError(companyResult.reason, "Failed to load company."));
      setLoading(false);
      return;
    }

    const companyData = companyResult.value.data as CompanyInfo;
    setCompany(companyData);
    setEditForm({
      name: companyData.name || "",
      state_code: companyData.state_code || "",
      industry: companyData.industry || "",
      registered_address: companyData.registered_address || "",
      signatory_name: companyData.signatory_name || "",
      signatory_designation: companyData.signatory_designation || "",
      signatory_email: companyData.signatory_email || "",
      compliance_email: companyData.compliance_email || "",
      gst_auto_file: Boolean(companyData.gst_auto_file),
    });
    setCredentialForm((current) => ({
      ...current,
      gstin: companyData.gstin || current.gstin,
    }));

    // BUG-003: seed the Tally Connection draft fields on load so the
    // user can edit bridge_url / company_name without re-entering
    // everything.
    setTallyBridgeUrlDraft(companyData.tally_config?.bridge_url || "");
    setTallyCompanyDraft(companyData.tally_config?.company_name || "");

    setApprovals(
      approvalsResult.status === "fulfilled"
        ? itemsFromResponse<FilingApproval>(approvalsResult.value.data)
        : []
    );
    setDeadlines(
      deadlinesResult.status === "fulfilled"
        ? itemsFromResponse<ComplianceDeadline>(deadlinesResult.value.data).sort(
            (left, right) => deadlineSortValue(left) - deadlineSortValue(right),
          )
        : []
    );
    setUploads(
      uploadsResult.status === "fulfilled"
        ? itemsFromResponse<GSTNUpload>(uploadsResult.value.data)
        : []
    );

    if (rolesResult.status === "fulfilled") {
      const payload = (rolesResult.value.data || {}) as RoleResponse;
      setRoles(Array.isArray(payload.roles) ? payload.roles : []);
      setValidRoles(
        Array.isArray(payload.valid_roles) && payload.valid_roles.length > 0
          ? payload.valid_roles
          : ["partner", "manager", "associate", "audit_reviewer"]
      );
    } else {
      setRoles([]);
    }

    setCredentials(
      credentialsResult.status === "fulfilled"
        ? itemsFromResponse<Credential>(credentialsResult.value.data)
        : []
    );

    if (agentsResult.status === "fulfilled") {
      const agentItems = itemsFromResponse<Record<string, unknown>>(agentsResult.value.data);
      setAgents(
        agentItems
          .map((record) => ({
            id: String(record.id || ""),
            name: String(record.employee_name || record.name || ""),
            domain: String(record.domain || "finance"),
            status: String(record.status || "shadow"),
            designation: String(record.designation || ""),
          }))
      );
    } else {
      setAgents([]);
    }

    if (workflowsResult.status === "fulfilled") {
      const workflowItems = itemsFromResponse<Record<string, unknown>>(workflowsResult.value.data);
      setWorkflows(
        workflowItems
          .map((record) => ({
            id: String(record.id || ""),
            name: String(record.name || ""),
            description: String(record.description || ""),
            is_active: Boolean(record.is_active),
            created_at: String(record.created_at || ""),
          }))
      );
    } else {
      setWorkflows([]);
    }

    if (auditResult.status === "fulfilled") {
      const auditItems = itemsFromResponse<Record<string, unknown>>(auditResult.value.data);
      setAuditEntries(
        auditItems.map((record) => ({
          id: `audit-${String(record.id || crypto.randomUUID())}`,
          timestamp: String(record.created_at || record.timestamp || ""),
          action: String(record.action || record.event_type || "Audit event"),
          actor: String(record.actor_id || record.actor_type || "system"),
          outcome: String(record.outcome || "recorded"),
        })),
      );
    } else {
      setAuditEntries([]);
    }

    setLoading(false);
  }, [id]);

  useEffect(() => {
    void fetchData();
  }, [fetchData]);

  const pendingApprovals = approvals.filter((approval) => approval.status === "pending").length;
  const overdueDeadlines = deadlines.filter((deadline) => deadlineStatus(deadline) === "overdue").length;
  const upcomingDeadlines = deadlines.filter((deadline) => deadlineStatus(deadline) === "pending").length;
  const activeCredentials = credentials.filter((credential) => credential.is_active).length;
  const healthScore = company?.client_health_score ?? 0;

  const activities = useMemo<ActivityEntry[]>(() => {
    const approvalActivity = approvals.map((approval) => ({
      id: `approval-${approval.id}`,
      timestamp: approval.approved_at || approval.updated_at || approval.created_at,
      action: `${approval.filing_type} ${approval.filing_period}`,
      actor: approval.approved_by || approval.requested_by || "system",
      outcome: approval.status,
    }));

    const deadlineActivity = deadlines.map((deadline) => ({
      id: `deadline-${deadline.id}`,
      timestamp: deadline.filed_at || deadline.created_at || deadline.due_date,
      action: `${deadline.deadline_type} ${deadline.filing_period}`,
      actor: deadline.filed ? "compliance filing" : "compliance calendar",
      outcome: deadlineStatus(deadline),
    }));

    const uploadActivity = uploads.map((upload) => ({
      id: `upload-${upload.id}`,
      timestamp: upload.uploaded_at || upload.created_at,
      action: `${upload.upload_type} ${upload.filing_period}`,
      actor: upload.uploaded_by || "manual upload",
      outcome: upload.status,
    }));

    const merged = [...auditEntries, ...approvalActivity, ...deadlineActivity, ...uploadActivity];
    const deduped = merged.filter((entry, index, items) => items.findIndex((candidate) => candidate.id === entry.id) === index);
    return deduped.sort(
      (left, right) => new Date(right.timestamp).getTime() - new Date(left.timestamp).getTime(),
    );
  }, [approvals, auditEntries, deadlines, uploads]);

  const tabs: { key: TabKey; label: string }[] = [
    { key: "overview", label: "Overview" },
    { key: "compliance", label: "Compliance" },
    { key: "agents", label: "Agents" },
    { key: "workflows", label: "Workflows" },
    { key: "audit", label: "Audit Log" },
    { key: "approvals", label: "Approvals" },
    { key: "settings", label: "Settings" },
  ];

  const refreshWithNotice = async (message: string) => {
    setNotice(message);
    await fetchData();
  };

  const handleSaveCompany = async () => {
    if (!id) return;
    setSavingCompany(true);
    setError(null);
    try {
      await api.patch(`/companies/${id}`, editForm);
      await refreshWithNotice("Company settings updated.");
    } catch (err) {
      setError(extractApiError(err, "Failed to save company settings."));
    } finally {
      setSavingCompany(false);
    }
  };

  // ── BUG-003 / BUG-008 / BUG-011: Tally Connection handlers ────────
  const handleSaveTally = async () => {
    if (!id) return;
    setTallySaving(true);
    setError(null);
    try {
      // Preserve existing bridge_id (don't let Save clobber an
      // already-generated credential).
      const existing = company?.tally_config || {};
      await api.patch(`/companies/${id}`, {
        tally_config: {
          ...existing,
          bridge_url: tallyBridgeUrlDraft.trim() || null,
          company_name: tallyCompanyDraft.trim() || null,
        },
      });
      await refreshWithNotice("Tally connection updated.");
    } catch (err) {
      setError(extractApiError(err, "Failed to save Tally connection."));
    } finally {
      setTallySaving(false);
    }
  };

  const handleTestTally = async () => {
    setTallyTesting(true);
    setTallyTestResult(null);
    setTallyTestMessage("");
    try {
      const res = await api.post("/companies/test-tally", {
        bridge_url: tallyBridgeUrlDraft,
        bridge_id: company?.tally_config?.bridge_id || undefined,
        company_name: tallyCompanyDraft || undefined,
      });
      const data = res.data as { success?: boolean; message?: string; bridge_version?: string };
      if (data?.success) {
        setTallyTestResult("success");
        setTallyTestMessage(
          data.bridge_version
            ? `Bridge reachable (version ${data.bridge_version})`
            : data.message || "Bridge reachable",
        );
      } else {
        setTallyTestResult("error");
        setTallyTestMessage(
          data?.message ||
            "Connection failed. Check the bridge URL and ensure Tally is running.",
        );
      }
    } catch (err: unknown) {
      setTallyTestResult("error");
      setTallyTestMessage(
        extractApiError(err, "Could not reach the Tally bridge."),
      );
    } finally {
      setTallyTesting(false);
    }
  };

  const handleGenerateBridge = async () => {
    if (!id) return;
    const existingId = company?.tally_config?.bridge_id;
    if (existingId) {
      const confirmed = window.confirm(
        "Generating new credentials will invalidate the current bridge " +
          `(${existingId}) and the old on-prem Tally bridge will need the new token. Continue?`,
      );
      if (!confirmed) return;
    }
    setBridgeGenerating(true);
    setError(null);
    try {
      const res = await api.post(`/companies/${id}/bridge/generate`);
      const data = res.data as { bridge_id: string; bridge_token: string };
      setBridgeTokenOnce(data.bridge_token);
      await refreshWithNotice(
        "Bridge credentials generated. Copy the token below — it is shown only once.",
      );
    } catch (err) {
      setError(extractApiError(err, "Failed to generate bridge credentials."));
    } finally {
      setBridgeGenerating(false);
    }
  };

  const handleCopyToClipboard = async (value: string, label: string) => {
    try {
      await navigator.clipboard.writeText(value);
      setNotice(`${label} copied to clipboard.`);
    } catch {
      // Fallback for older browsers — select the text for the user.
      setNotice(
        `Copy not supported in this browser — select the ${label} manually.`,
      );
    }
  };

  const handleCreateApproval = async () => {
    if (!id || !newApproval.filing_type.trim() || !newApproval.filing_period.trim()) return;
    setCreatingApproval(true);
    setError(null);
    try {
      await api.post(`/companies/${id}/approvals`, {
        filing_type: newApproval.filing_type.trim(),
        filing_period: newApproval.filing_period.trim(),
        filing_data: {},
      });
      setNewApproval({ filing_type: "", filing_period: "" });
      await refreshWithNotice("Approval request created.");
    } catch (err) {
      setError(extractApiError(err, "Failed to create filing approval."));
    } finally {
      setCreatingApproval(false);
    }
  };

  const handleApprove = async (approvalId: string) => {
    if (!id) return;
    setApprovalBusyId(approvalId);
    setError(null);
    try {
      await api.post(`/companies/${id}/approvals/${approvalId}/approve`);
      await refreshWithNotice("Approval marked approved.");
    } catch (err) {
      setError(extractApiError(err, "Failed to approve filing."));
    } finally {
      setApprovalBusyId(null);
    }
  };

  const openRejectDialog = (approvalId: string) => {
    setRejectDialogId(approvalId);
    setRejectReason("");
  };

  const confirmReject = async () => {
    if (!id || !rejectDialogId) return;
    setApprovalBusyId(rejectDialogId);
    setError(null);
    setRejectDialogId(null);
    try {
      await api.post(`/companies/${id}/approvals/${rejectDialogId}/reject`, null, {
        params: { reason: rejectReason },
      });
      await refreshWithNotice("Approval rejected.");
    } catch (err) {
      setError(extractApiError(err, "Failed to reject filing."));
    } finally {
      setApprovalBusyId(null);
    }
  };

  const handleMarkFiled = async (deadlineId: string) => {
    if (!id) return;
    setDeadlineBusyId(deadlineId);
    setError(null);
    try {
      await api.patch(`/companies/${id}/deadlines/${deadlineId}/filed`);
      await refreshWithNotice("Deadline marked filed.");
    } catch (err) {
      setError(extractApiError(err, "Failed to mark deadline filed."));
    } finally {
      setDeadlineBusyId(null);
    }
  };

  const handleAddRole = () => {
    const userId = newRoleUserId.trim();
    if (!userId) return;
    setRoles((current) => {
      const existingIndex = current.findIndex((role) => role.user_id === userId);
      if (existingIndex >= 0) {
        return current.map((role) => (
          role.user_id === userId ? { ...role, role: newRoleValue } : role
        ));
      }
      return [...current, { user_id: userId, role: newRoleValue }];
    });
    setNewRoleUserId("");
  };

  const handleSaveRoles = async () => {
    if (!id) return;
    setSavingRoles(true);
    setError(null);
    try {
      await api.put(`/companies/${id}/roles`, { roles });
      await refreshWithNotice("Company roles updated.");
    } catch (err) {
      setError(extractApiError(err, "Failed to update company roles."));
    } finally {
      setSavingRoles(false);
    }
  };

  const handleCreateCredential = async () => {
    if (!id) return;
    if (!credentialForm.gstin.trim() || !credentialForm.username.trim() || !credentialForm.password.trim()) {
      return;
    }
    setCreatingCredential(true);
    setError(null);
    try {
      await api.post(`/companies/${id}/credentials`, credentialForm);
      setCredentialForm((current) => ({
        ...current,
        username: "",
        password: "",
      }));
      await refreshWithNotice("Credential stored successfully.");
    } catch (err) {
      setError(extractApiError(err, "Failed to store credential."));
    } finally {
      setCreatingCredential(false);
    }
  };

  const handleVerifyCredential = async (credentialId: string) => {
    if (!id) return;
    setCredentialBusyId(credentialId);
    setError(null);
    try {
      const response = await api.post(`/companies/${id}/credentials/${credentialId}/verify`);
      const verified = Boolean(response.data?.verified);
      await refreshWithNotice(verified ? "Credential verified." : "Credential verification failed.");
    } catch (err) {
      setError(extractApiError(err, "Failed to verify credential."));
    } finally {
      setCredentialBusyId(null);
    }
  };

  const handleDeactivateCredential = async (credentialId: string) => {
    if (!id) return;
    setCredentialBusyId(credentialId);
    setError(null);
    try {
      await api.delete(`/companies/${id}/credentials/${credentialId}`);
      await refreshWithNotice("Credential deactivated.");
    } catch (err) {
      setError(extractApiError(err, "Failed to deactivate credential."));
    } finally {
      setCredentialBusyId(null);
    }
  };

  if (loading) {
    return (
      <div className="flex min-h-[50vh] items-center justify-center">
        <p className="text-muted-foreground">Loading company...</p>
      </div>
    );
  }

  if (!company) {
    return (
      <div className="space-y-4">
        {error && (
          <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
            {error}
          </div>
        )}
        <p className="text-muted-foreground">Company not found.</p>
        <Button variant="outline" onClick={() => navigate("/dashboard/companies")}>
          Back to Companies
        </Button>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <Helmet>
        <title>{company.name} | AgenticOrg</title>
      </Helmet>

      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          {error}
        </div>
      )}
      {notice && (
        <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-700">
          {notice}
        </div>
      )}

      <div className="flex flex-col items-start justify-between gap-4 sm:flex-row sm:items-center">
        <div>
          <h2 className="text-2xl font-bold">{company.name}</h2>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
            {company.gstin && <span className="font-mono">{company.gstin}</span>}
            {company.industry && <Badge variant="outline">{company.industry}</Badge>}
            <Badge variant={company.is_active ? "success" : "secondary"}>
              {company.is_active ? "active" : "inactive"}
            </Badge>
            <Badge variant={statusVariant(company.subscription_status || "trial")}>
              {company.subscription_status || "trial"}
            </Badge>
          </div>
        </div>
        <Button variant="outline" onClick={() => navigate("/dashboard/companies")}>
          Back to Companies
        </Button>
      </div>

      <div className="flex overflow-x-auto border-b">
        {tabs.map((tab) => (
          <button
            key={tab.key}
            className={`whitespace-nowrap border-b-2 px-4 py-2 text-sm font-medium transition-colors ${
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

      {activeTab === "overview" && (
        <div className="space-y-6">
          <div className="grid grid-cols-2 gap-4 md:grid-cols-5">
            <Card>
              <CardContent className="pt-4 pb-4">
                <p className="text-2xl font-bold text-amber-600">{pendingApprovals}</p>
                <p className="text-xs text-muted-foreground">Pending Approvals</p>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="pt-4 pb-4">
                <p className="text-2xl font-bold text-red-600">{overdueDeadlines}</p>
                <p className="text-xs text-muted-foreground">Overdue Deadlines</p>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="pt-4 pb-4">
                <p className="text-2xl font-bold text-blue-600">{upcomingDeadlines}</p>
                <p className="text-xs text-muted-foreground">Upcoming Deadlines</p>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="pt-4 pb-4">
                <p className="text-2xl font-bold text-emerald-600">{activeCredentials}</p>
                <p className="text-xs text-muted-foreground">Active Credentials</p>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="pt-4 pb-4">
                <p className={`text-2xl font-bold ${healthColor(healthScore)}`}>{healthScore}</p>
                <p className="text-xs text-muted-foreground">Client Health Score</p>
              </CardContent>
            </Card>
          </div>

          <div className="grid gap-6 md:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Company Information</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="grid grid-cols-2 gap-3 text-sm">
                  <div><span className="text-muted-foreground">PAN:</span> <span className="font-mono">{company.pan || "-"}</span></div>
                  <div><span className="text-muted-foreground">TAN:</span> <span className="font-mono">{company.tan || "-"}</span></div>
                  <div><span className="text-muted-foreground">CIN:</span> <span className="font-mono">{company.cin || "-"}</span></div>
                  <div><span className="text-muted-foreground">State:</span> {stateNameFromCode(company.state_code) || "-"}</div>
                  <div className="col-span-2"><span className="text-muted-foreground">Address:</span> {company.registered_address || "-"}</div>
                  <div><span className="text-muted-foreground">Signatory:</span> {company.signatory_name || "-"}</div>
                  <div><span className="text-muted-foreground">Compliance Email:</span> {company.compliance_email || "-"}</div>
                  <div><span className="text-muted-foreground">Created:</span> {formatDate(company.created_at)}</div>
                  <div><span className="text-muted-foreground">Updated:</span> {formatDate(company.updated_at)}</div>
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="text-base">Recent Audit & Activity</CardTitle>
              </CardHeader>
              <CardContent>
                {activities.length === 0 ? (
                  <p className="text-sm text-muted-foreground">No company audit events recorded yet.</p>
                ) : (
                  <div className="space-y-3">
                    {activities.slice(0, 5).map((activity) => (
                      <div key={activity.id} className="flex items-center justify-between text-sm">
                        <div>
                          <p className="font-medium">{activity.action}</p>
                          <p className="text-xs text-muted-foreground">
                            {activity.actor} - {formatDateTime(activity.timestamp)}
                          </p>
                        </div>
                        <Badge variant={statusVariant(activity.outcome)}>{activity.outcome}</Badge>
                      </div>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>
          </div>
        </div>
      )}

      {activeTab === "compliance" && (
        <div className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Compliance Registrations</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid gap-3 text-sm sm:grid-cols-2">
                <div><span className="text-muted-foreground">PF Registration:</span> {company.pf_registration || "-"}</div>
                <div><span className="text-muted-foreground">ESI Registration:</span> {company.esi_registration || "-"}</div>
                <div><span className="text-muted-foreground">PT Registration:</span> {company.pt_registration || "-"}</div>
                <div>
                  <span className="text-muted-foreground">GST Auto-File:</span>{" "}
                  <Badge variant={company.gst_auto_file ? "warning" : "secondary"}>
                    {company.gst_auto_file ? "enabled" : "disabled"}
                  </Badge>
                </div>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Compliance Deadlines</CardTitle>
            </CardHeader>
            <CardContent>
              {deadlines.length === 0 ? (
                <p className="text-sm text-muted-foreground">No deadlines generated yet.</p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b">
                        <th className="px-3 py-2 text-left font-medium text-muted-foreground">Type</th>
                        <th className="px-3 py-2 text-left font-medium text-muted-foreground">Period</th>
                        <th className="px-3 py-2 text-left font-medium text-muted-foreground">Due Date</th>
                        <th className="px-3 py-2 text-left font-medium text-muted-foreground">Status</th>
                        <th className="px-3 py-2 text-left font-medium text-muted-foreground">Action</th>
                      </tr>
                    </thead>
                    <tbody>
                      {deadlines.map((deadline) => {
                        const status = deadlineStatus(deadline);
                        return (
                          <tr key={deadline.id} className="border-b last:border-0">
                            <td className="px-3 py-2 font-medium">{deadline.deadline_type}</td>
                            <td className="px-3 py-2 text-muted-foreground">{deadline.filing_period}</td>
                            <td className="px-3 py-2">{formatDate(deadline.due_date)}</td>
                            <td className="px-3 py-2">
                              <Badge variant={statusVariant(status)}>{status}</Badge>
                            </td>
                            <td className="px-3 py-2">
                              {!deadline.filed && (
                                <Button
                                  size="sm"
                                  variant="outline"
                                  disabled={deadlineBusyId === deadline.id}
                                  onClick={() => void handleMarkFiled(deadline.id)}
                                >
                                  {deadlineBusyId === deadline.id ? "Saving..." : "Mark Filed"}
                                </Button>
                              )}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">GSTN Upload History</CardTitle>
            </CardHeader>
            <CardContent>
              {uploads.length === 0 ? (
                <p className="text-sm text-muted-foreground">No GSTN upload records yet.</p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b">
                        <th className="px-3 py-2 text-left font-medium text-muted-foreground">Upload Type</th>
                        <th className="px-3 py-2 text-left font-medium text-muted-foreground">Period</th>
                        <th className="px-3 py-2 text-left font-medium text-muted-foreground">File</th>
                        <th className="px-3 py-2 text-left font-medium text-muted-foreground">Status</th>
                        <th className="px-3 py-2 text-left font-medium text-muted-foreground">Updated</th>
                      </tr>
                    </thead>
                    <tbody>
                      {uploads.map((upload) => (
                        <tr key={upload.id} className="border-b last:border-0">
                          <td className="px-3 py-2 font-medium">{upload.upload_type}</td>
                          <td className="px-3 py-2 text-muted-foreground">{upload.filing_period}</td>
                          <td className="px-3 py-2">{upload.file_name}</td>
                          <td className="px-3 py-2">
                            <Badge variant={statusVariant(upload.status)}>{upload.status}</Badge>
                          </td>
                          <td className="px-3 py-2 text-muted-foreground">
                            {formatDateTime(upload.uploaded_at || upload.created_at)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      )}

      {activeTab === "agents" && (
        <div className="space-y-4">
          {agents.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No CA pack agents are provisioned for this tenant yet. Install the Chartered Accountant Firm Pack to create live firm automations.
            </p>
          ) : (
            agents.map((agent) => (
              <Card
                key={agent.id}
                role="button"
                tabIndex={0}
                className="cursor-pointer hover:bg-muted/40 transition-colors"
                onClick={() => setAgentDetail(agent)}
                onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setAgentDetail(agent); } }}
              >
                <CardContent className="pt-4 pb-4">
                  <div className="flex items-center justify-between">
                    <div>
                      <p className="font-medium">{agent.name}</p>
                      <p className="text-xs text-muted-foreground">
                        {agent.designation || agent.domain}
                      </p>
                    </div>
                    <Badge variant={statusVariant(agent.status)}>{agent.status}</Badge>
                  </div>
                </CardContent>
              </Card>
            ))
          )}
        </div>
      )}

      {activeTab === "workflows" && (
        <div className="space-y-4">
          {workflows.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No CA pack workflows are provisioned for this tenant yet. Installed packs now create live workflow definitions, but this tenant does not have CA workflows provisioned yet.
            </p>
          ) : (
            workflows.map((workflow) => (
              <Card
                key={workflow.id}
                role="button"
                tabIndex={0}
                className="cursor-pointer hover:bg-muted/40 transition-colors"
                onClick={() => setWorkflowDetail(workflow)}
                onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setWorkflowDetail(workflow); } }}
              >
                <CardContent className="pt-4 pb-4">
                  <div className="flex items-center justify-between gap-4">
                    <div>
                      <p className="font-medium">{workflow.name}</p>
                      <p className="text-xs text-muted-foreground">{workflow.description || "No description."}</p>
                    </div>
                    <Badge variant={workflow.is_active ? "success" : "secondary"}>
                      {workflow.is_active ? "active" : "inactive"}
                    </Badge>
                  </div>
                </CardContent>
              </Card>
            ))
          )}
        </div>
      )}

        {activeTab === "audit" && (() => {
          const auditOutcomes = Array.from(new Set(activities.map((a) => a.outcome))).sort();
          const q = auditQuery.trim().toLowerCase();
          const filteredActivities = activities.filter((a) => {
            if (auditOutcome !== "all" && a.outcome !== auditOutcome) return false;
            if (!q) return true;
            return (
              a.action.toLowerCase().includes(q) ||
              a.actor.toLowerCase().includes(q) ||
              a.outcome.toLowerCase().includes(q)
            );
          });
          const exportCsv = () => {
            const header = ["Timestamp", "Action", "Actor", "Outcome"];
            // Neutralize CSV formula injection: any cell whose first
            // character can be interpreted as a spreadsheet formula
            // (=, +, -, @, CR, LF, TAB) is prefixed with a single quote
            // so Excel/Sheets render it as text. Action and Actor fields
            // carry user-controlled strings (filing types, emails), so
            // without this an exported audit log could execute formulas
            // on the reviewer's machine.
            const csvEscape = (v: string) => {
              const guarded = /^[=+\-@\r\n\t]/.test(v) ? `'${v}` : v;
              return `"${guarded.replace(/"/g, '""')}"`;
            };
            const rows = filteredActivities.map((a) => [
              a.timestamp,
              a.action,
              a.actor,
              a.outcome,
            ].map(csvEscape).join(","));
            const csv = [header.join(","), ...rows].join("\n");
            const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
            const url = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = url;
            a.download = `audit-${company.name}-${new Date().toISOString().slice(0, 10)}.csv`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
          };
          return (
          <Card>
            <CardContent className="pt-4">
              <div className="mb-3 flex flex-col gap-2 sm:flex-row sm:items-center">
                <input
                  type="text"
                  value={auditQuery}
                  onChange={(e) => setAuditQuery(e.target.value)}
                  placeholder="Filter by action, actor, or outcome"
                  className="h-9 flex-1 rounded-md border border-input bg-background px-3 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
                />
                <select
                  value={auditOutcome}
                  onChange={(e) => setAuditOutcome(e.target.value)}
                  className="h-9 rounded-md border border-input bg-background px-3 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
                >
                  <option value="all">All outcomes</option>
                  {auditOutcomes.map((o) => <option key={o} value={o}>{o}</option>)}
                </select>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={exportCsv}
                  disabled={filteredActivities.length === 0}
                >
                  Export CSV
                </Button>
              </div>
              {filteredActivities.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  {activities.length === 0
                    ? "No audit events recorded yet."
                    : "No audit events match the current filter."}
                </p>
              ) : (
                <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b">
                      <th className="px-3 py-2 text-left font-medium text-muted-foreground">Timestamp</th>
                      <th className="px-3 py-2 text-left font-medium text-muted-foreground">Action</th>
                      <th className="px-3 py-2 text-left font-medium text-muted-foreground">Actor</th>
                      <th className="px-3 py-2 text-left font-medium text-muted-foreground">Outcome</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredActivities.map((activity) => (
                      <tr key={activity.id} className="border-b last:border-0">
                        <td className="px-3 py-2 text-xs text-muted-foreground">{formatDateTime(activity.timestamp)}</td>
                        <td className="px-3 py-2 font-medium">{activity.action}</td>
                        <td className="px-3 py-2 text-muted-foreground">{activity.actor}</td>
                        <td className="px-3 py-2">
                          <Badge variant={statusVariant(activity.outcome)}>{activity.outcome}</Badge>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </CardContent>
        </Card>
          );
        })()}

      {activeTab === "approvals" && (
        <div className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Request New Filing Approval</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid gap-4 sm:grid-cols-3">
                <div>
                  <label className="mb-1 block text-sm font-medium">Filing Type</label>
                  <input
                    value={newApproval.filing_type}
                    onChange={(event) => setNewApproval((current) => ({ ...current, filing_type: event.target.value }))}
                    className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-sm font-medium">Filing Period</label>
                  <input
                    value={newApproval.filing_period}
                    onChange={(event) => setNewApproval((current) => ({ ...current, filing_period: event.target.value }))}
                    placeholder="2026-04 or Q4 FY26"
                    className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
                  />
                </div>
                <div className="flex items-end">
                  <Button disabled={creatingApproval} onClick={() => void handleCreateApproval()}>
                    {creatingApproval ? "Creating..." : "Request Approval"}
                  </Button>
                </div>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Filing Approvals</CardTitle>
            </CardHeader>
            <CardContent>
              {approvals.length === 0 ? (
                <p className="text-sm text-muted-foreground">No filing approvals for this company yet.</p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b">
                        <th className="px-3 py-2 text-left font-medium text-muted-foreground">Filing Type</th>
                        <th className="px-3 py-2 text-left font-medium text-muted-foreground">Period</th>
                        <th className="px-3 py-2 text-left font-medium text-muted-foreground">Requested By</th>
                        <th className="px-3 py-2 text-left font-medium text-muted-foreground">Status</th>
                        <th className="px-3 py-2 text-left font-medium text-muted-foreground">Updated</th>
                        <th className="px-3 py-2 text-left font-medium text-muted-foreground">Action</th>
                      </tr>
                    </thead>
                    <tbody>
                      {approvals.map((approval) => (
                        <tr key={approval.id} className="border-b last:border-0">
                          <td className="px-3 py-2 font-medium">{approval.filing_type}</td>
                          <td className="px-3 py-2 text-muted-foreground">{approval.filing_period}</td>
                          <td className="px-3 py-2 text-muted-foreground">{approval.requested_by}</td>
                          <td className="px-3 py-2">
                            <Badge variant={statusVariant(approval.status)}>{approval.status}</Badge>
                          </td>
                          <td className="px-3 py-2 text-muted-foreground">
                            {formatDateTime(approval.approved_at || approval.updated_at || approval.created_at)}
                          </td>
                          <td className="px-3 py-2">
                            {approval.status === "pending" ? (
                              <div className="flex gap-2">
                                <Button
                                  size="sm"
                                  disabled={approvalBusyId === approval.id}
                                  onClick={() => void handleApprove(approval.id)}
                                >
                                  {approvalBusyId === approval.id ? "Saving..." : "Approve"}
                                </Button>
                                <Button
                                  size="sm"
                                  variant="outline"
                                  disabled={approvalBusyId === approval.id}
                                  onClick={() => openRejectDialog(approval.id)}
                                >
                                  Reject
                                </Button>
                              </div>
                            ) : (
                              <span className="text-xs text-muted-foreground">
                                {approval.approved_by || approval.rejection_reason || "-"}
                              </span>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      )}

      {activeTab === "settings" && (
        <div className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Company Settings</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid gap-4 sm:grid-cols-2">
                <div>
                  <label className="mb-1 block text-sm font-medium">Company Name</label>
                  <input
                    value={editForm.name}
                    onChange={(event) => setEditForm((current) => ({ ...current, name: event.target.value }))}
                    className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-sm font-medium">State Code</label>
                  <input
                    value={editForm.state_code}
                    onChange={(event) => setEditForm((current) => ({ ...current, state_code: event.target.value }))}
                    className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-sm font-medium">Industry</label>
                  <input
                    value={editForm.industry}
                    onChange={(event) => setEditForm((current) => ({ ...current, industry: event.target.value }))}
                    className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-sm font-medium">Compliance Email</label>
                  <input
                    value={editForm.compliance_email}
                    onChange={(event) => setEditForm((current) => ({ ...current, compliance_email: event.target.value }))}
                    className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-sm font-medium">Signatory Name</label>
                  <input
                    value={editForm.signatory_name}
                    onChange={(event) => setEditForm((current) => ({ ...current, signatory_name: event.target.value }))}
                    className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-sm font-medium">Signatory Designation</label>
                  <input
                    value={editForm.signatory_designation}
                    onChange={(event) => setEditForm((current) => ({ ...current, signatory_designation: event.target.value }))}
                    className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
                  />
                </div>
                <div className="sm:col-span-2">
                  <label className="mb-1 block text-sm font-medium">Registered Address</label>
                  <textarea
                    value={editForm.registered_address}
                    onChange={(event) => setEditForm((current) => ({ ...current, registered_address: event.target.value }))}
                    rows={3}
                    className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
                  />
                </div>
                <div className="sm:col-span-2">
                  <label className="flex items-center gap-3 text-sm font-medium">
                    <input
                      type="checkbox"
                      checked={editForm.gst_auto_file}
                      onChange={(event) => setEditForm((current) => ({ ...current, gst_auto_file: event.target.checked }))}
                      className="h-4 w-4 rounded border-input"
                    />
                    Enable GST auto-file
                  </label>
                </div>
                <div className="sm:col-span-2">
                  <Button disabled={savingCompany} onClick={() => void handleSaveCompany()}>
                    {savingCompany ? "Saving..." : "Save Company Settings"}
                  </Button>
                </div>
              </div>
            </CardContent>
          </Card>

          {/*
            BUG-003 / BUG-011 Tally Connection panel (Ramesh 2026-04-20).
            Post-onboard editing of bridge URL + company name, Test
            Connection with backend-specific error messages (BUG-008),
            and first-time / regenerate issuance of bridge credentials
            (BUG-011). The bridge_token is returned only once and
            surfaced in a disclosure block the user can copy.
          */}
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Tally Connection</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <p className="text-xs text-muted-foreground">
                Update the ngrok URL that AgenticOrg uses to reach the on-prem
                AgenticOrg Tally Bridge, and issue or rotate its credentials.
              </p>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div>
                  <label className="block text-sm font-medium mb-1" htmlFor="tally-url">
                    Bridge URL (ngrok)
                  </label>
                  <input
                    id="tally-url"
                    type="url"
                    placeholder="https://abc123.ngrok-free.app"
                    value={tallyBridgeUrlDraft}
                    onChange={(e) => setTallyBridgeUrlDraft(e.target.value)}
                    className="w-full h-9 rounded-md border border-input bg-background px-3 text-sm"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium mb-1" htmlFor="tally-company">
                    Tally Company Name (optional)
                  </label>
                  <input
                    id="tally-company"
                    type="text"
                    placeholder="Company name as in Tally Prime"
                    value={tallyCompanyDraft}
                    onChange={(e) => setTallyCompanyDraft(e.target.value)}
                    className="w-full h-9 rounded-md border border-input bg-background px-3 text-sm"
                  />
                </div>
              </div>

              <div className="flex items-center gap-2 flex-wrap">
                <Button
                  variant="outline"
                  onClick={() => void handleTestTally()}
                  disabled={tallyTesting || !tallyBridgeUrlDraft.trim()}
                >
                  {tallyTesting ? "Testing..." : "Test Connection"}
                </Button>
                <Button
                  onClick={() => void handleSaveTally()}
                  disabled={tallySaving}
                >
                  {tallySaving ? "Saving..." : "Save Tally Connection"}
                </Button>
              </div>

              {tallyTestResult === "success" && (
                <p className="text-xs text-emerald-600">
                  {tallyTestMessage || "Connection successful."}
                </p>
              )}
              {tallyTestResult === "error" && (
                <p className="text-xs text-red-500">
                  {tallyTestMessage ||
                    "Connection failed. Check the bridge URL and ensure Tally is running."}
                </p>
              )}

              <div className="border-t pt-4 space-y-2">
                <p className="text-sm font-medium">Bridge Credentials</p>
                {company?.tally_config?.bridge_id ? (
                  <div className="flex items-center gap-2 flex-wrap text-sm">
                    <span className="text-muted-foreground">Bridge ID:</span>
                    <code className="font-mono text-xs bg-muted px-2 py-1 rounded break-all">
                      {company.tally_config.bridge_id}
                    </code>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() =>
                        void handleCopyToClipboard(
                          company.tally_config?.bridge_id || "",
                          "Bridge ID",
                        )
                      }
                    >
                      Copy
                    </Button>
                  </div>
                ) : (
                  <p className="text-xs text-muted-foreground">
                    No bridge credentials yet. Generate a pair below to connect
                    the on-prem AgenticOrg Tally Bridge.
                  </p>
                )}

                <Button
                  variant={company?.tally_config?.bridge_id ? "outline" : "default"}
                  onClick={() => void handleGenerateBridge()}
                  disabled={bridgeGenerating}
                >
                  {bridgeGenerating
                    ? "Generating..."
                    : company?.tally_config?.bridge_id
                      ? "Regenerate Bridge Credentials"
                      : "Generate Bridge Credentials"}
                </Button>

                {bridgeTokenOnce && (
                  <div className="mt-3 rounded-md border border-amber-400 bg-amber-50 dark:bg-amber-950/30 p-3 space-y-2">
                    <p className="text-xs font-medium text-amber-900 dark:text-amber-100">
                      Save this bridge token now — it cannot be retrieved later.
                    </p>
                    <div className="flex items-center gap-2 flex-wrap">
                      <code className="font-mono text-xs bg-background border px-2 py-1 rounded break-all flex-1 min-w-0">
                        {bridgeTokenOnce}
                      </code>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() =>
                          void handleCopyToClipboard(bridgeTokenOnce, "Bridge token")
                        }
                      >
                        Copy
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setBridgeTokenOnce(null)}
                      >
                        Dismiss
                      </Button>
                    </div>
                  </div>
                )}
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Company Role Mapping</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="mb-4 text-xs text-muted-foreground">
                The current API supports role add and role update. Role removal is not supported yet, so this screen only edits or adds mappings.
              </p>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b">
                      <th className="px-3 py-2 text-left font-medium text-muted-foreground">User Email / ID</th>
                      <th className="px-3 py-2 text-left font-medium text-muted-foreground">Role</th>
                    </tr>
                  </thead>
                  <tbody>
                    {roles.map((role) => (
                      <tr key={role.user_id} className="border-b last:border-0">
                        <td className="px-3 py-2">{role.user_id}</td>
                        <td className="px-3 py-2">
                          <select
                            value={role.role}
                            onChange={(event) => setRoles((current) => current.map((entry) => (
                              entry.user_id === role.user_id ? { ...entry, role: event.target.value } : entry
                            )))}
                            className="h-8 rounded-md border border-input bg-background px-2 text-xs focus:outline-none focus:ring-1 focus:ring-primary"
                          >
                            {validRoles.map((value) => (
                              <option key={value} value={value}>{value}</option>
                            ))}
                          </select>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div className="mt-4 flex flex-col gap-2 border-t pt-4 sm:flex-row">
                <input
                  type="text"
                  placeholder="user@company.com or user id"
                  value={newRoleUserId}
                  onChange={(event) => setNewRoleUserId(event.target.value)}
                  className="h-9 flex-1 rounded-md border border-input bg-background px-3 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
                />
                <select
                  value={newRoleValue}
                  onChange={(event) => setNewRoleValue(event.target.value)}
                  className="h-9 rounded-md border border-input bg-background px-3 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
                >
                  {validRoles.map((value) => (
                    <option key={value} value={value}>{value}</option>
                  ))}
                </select>
                <Button variant="outline" onClick={handleAddRole}>Add / Update</Button>
                <Button disabled={savingRoles} onClick={() => void handleSaveRoles()}>
                  {savingRoles ? "Saving..." : "Save Roles"}
                </Button>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">GSTN Portal Credentials</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700">
                Credentials are stored through the backend credential vault. Passwords are never returned to the UI.
              </div>

              <div className="grid gap-4 sm:grid-cols-2">
                <div>
                  <label className="mb-1 block text-sm font-medium">GSTIN</label>
                  <input
                    value={credentialForm.gstin}
                    onChange={(event) => setCredentialForm((current) => ({ ...current, gstin: event.target.value }))}
                    className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-sm font-medium">Portal Type</label>
                  <select
                    value={credentialForm.portal_type}
                    onChange={(event) => setCredentialForm((current) => ({ ...current, portal_type: event.target.value }))}
                    className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
                  >
                    <option value="gstn">gstn</option>
                    <option value="income_tax">income_tax</option>
                    <option value="epfo">epfo</option>
                  </select>
                </div>
                <div>
                  <label className="mb-1 block text-sm font-medium">Username</label>
                  <input
                    value={credentialForm.username}
                    onChange={(event) => setCredentialForm((current) => ({ ...current, username: event.target.value }))}
                    className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-sm font-medium">Password</label>
                  <input
                    type="password"
                    value={credentialForm.password}
                    onChange={(event) => setCredentialForm((current) => ({ ...current, password: event.target.value }))}
                    className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
                  />
                </div>
                <div className="sm:col-span-2">
                  <Button disabled={creatingCredential} onClick={() => void handleCreateCredential()}>
                    {creatingCredential ? "Saving..." : "Save Credential"}
                  </Button>
                </div>
              </div>

              <div className="mt-6 overflow-x-auto border-t pt-4">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b">
                      <th className="px-3 py-2 text-left font-medium text-muted-foreground">GSTIN</th>
                      <th className="px-3 py-2 text-left font-medium text-muted-foreground">Username</th>
                      <th className="px-3 py-2 text-left font-medium text-muted-foreground">Portal</th>
                      <th className="px-3 py-2 text-left font-medium text-muted-foreground">Status</th>
                      <th className="px-3 py-2 text-left font-medium text-muted-foreground">Last Verified</th>
                      <th className="px-3 py-2 text-left font-medium text-muted-foreground">Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {credentials.map((credential) => (
                      <tr key={credential.id} className="border-b last:border-0">
                        <td className="px-3 py-2 font-mono text-xs">{credential.gstin}</td>
                        <td className="px-3 py-2">{credential.username}</td>
                        <td className="px-3 py-2">{credential.portal_type}</td>
                        <td className="px-3 py-2">
                          <Badge variant={credential.is_active ? "success" : "secondary"}>
                            {credential.is_active ? "active" : "inactive"}
                          </Badge>
                        </td>
                        <td className="px-3 py-2 text-muted-foreground">{formatDateTime(credential.last_verified_at)}</td>
                        <td className="px-3 py-2">
                          <div className="flex gap-2">
                            <Button
                              size="sm"
                              variant="outline"
                              disabled={credentialBusyId === credential.id}
                              onClick={() => void handleVerifyCredential(credential.id)}
                            >
                              Verify
                            </Button>
                            <Button
                              size="sm"
                              variant="outline"
                              disabled={credentialBusyId === credential.id || !credential.is_active}
                              onClick={() => void handleDeactivateCredential(credential.id)}
                            >
                              Deactivate
                            </Button>
                          </div>
                        </td>
                      </tr>
                    ))}
                    {credentials.length === 0 && (
                      <tr>
                        <td colSpan={6} className="px-3 py-4 text-sm text-muted-foreground">
                          No GSTN credentials stored for this company.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>
        </div>
      )}
      {/* Agent detail modal */}
      {agentDetail && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/60"
          onClick={() => setAgentDetail(null)}
        >
          <div
            className="w-full max-w-md rounded-lg border bg-background p-6 shadow-lg"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="text-lg font-semibold mb-3">{agentDetail.name}</h3>
            <div className="space-y-2 text-sm">
              <div><span className="text-muted-foreground">Designation:</span> {agentDetail.designation || "-"}</div>
              <div><span className="text-muted-foreground">Domain:</span> {agentDetail.domain}</div>
              <div>
                <span className="text-muted-foreground">Status:</span>{" "}
                <Badge variant={statusVariant(agentDetail.status)}>{agentDetail.status}</Badge>
              </div>
            </div>
            <div className="flex justify-end gap-2 mt-4">
              <Button variant="outline" size="sm" onClick={() => setAgentDetail(null)}>Close</Button>
              <Button
                size="sm"
                onClick={() => {
                  const aid = agentDetail.id;
                  setAgentDetail(null);
                  navigate(`/dashboard/agents/${aid}`);
                }}
              >
                Manage Agent
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* Workflow detail modal */}
      {workflowDetail && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/60"
          onClick={() => setWorkflowDetail(null)}
        >
          <div
            className="w-full max-w-md rounded-lg border bg-background p-6 shadow-lg"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="text-lg font-semibold mb-3">{workflowDetail.name}</h3>
            <div className="space-y-2 text-sm">
              <div><span className="text-muted-foreground">Description:</span> {workflowDetail.description || "No description."}</div>
              <div>
                <span className="text-muted-foreground">Status:</span>{" "}
                <Badge variant={workflowDetail.is_active ? "success" : "secondary"}>
                  {workflowDetail.is_active ? "active" : "inactive"}
                </Badge>
              </div>
              <div><span className="text-muted-foreground">Created:</span> {formatDate(workflowDetail.created_at)}</div>
            </div>
            <div className="flex justify-end gap-2 mt-4">
              <Button variant="outline" size="sm" onClick={() => setWorkflowDetail(null)}>Close</Button>
              <Button
                size="sm"
                onClick={() => {
                  const wid = workflowDetail.id;
                  setWorkflowDetail(null);
                  navigate(`/dashboard/workflows/${wid}`);
                }}
              >
                Open Workflow
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* Rejection reason dialog (replaces window.prompt) */}
      {rejectDialogId && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/60">
          <div className="w-full max-w-md rounded-lg border bg-background p-6 shadow-lg">
            <h3 className="text-lg font-semibold mb-3">Rejection Reason</h3>
            <textarea
              value={rejectReason}
              onChange={(e) => setRejectReason(e.target.value)}
              placeholder="Enter the reason for rejection..."
              className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm min-h-[100px] focus:outline-none focus:ring-1 focus:ring-primary"
              autoFocus
            />
            <div className="flex justify-end gap-2 mt-4">
              <Button variant="outline" size="sm" onClick={() => setRejectDialogId(null)}>Cancel</Button>
              <Button variant="destructive" size="sm" onClick={() => void confirmReject()}>Reject</Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
