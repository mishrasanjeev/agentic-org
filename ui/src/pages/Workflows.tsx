import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import api from "@/lib/api";
import type { Workflow } from "@/types";

interface WorkflowTemplate {
  id: string;
  name: string;
  description: string;
  domain: string;
  steps: number;
  trigger: string;
}

const WORKFLOW_TEMPLATES: WorkflowTemplate[] = [
  { id: "tpl-invoice-processing", name: "Invoice Processing", description: "Automatically extract, validate, and route invoices for approval based on amount thresholds.", domain: "finance", steps: 5, trigger: "api_event" },
  { id: "tpl-bank-reconciliation", name: "Bank Reconciliation", description: "Match bank statement entries with ledger transactions and flag discrepancies for review.", domain: "finance", steps: 4, trigger: "schedule" },
  { id: "tpl-month-end-close", name: "Month-End Close", description: "Orchestrate journal entries, accruals, reconciliations, and reporting for month-end close.", domain: "finance", steps: 8, trigger: "schedule" },
  { id: "tpl-gst-filing", name: "GST Filing", description: "Collect sales and purchase data, compute GST liability, and prepare GSTR-1/3B filings.", domain: "finance", steps: 6, trigger: "schedule" },
  { id: "tpl-expense-approval", name: "Expense Approval", description: "Route expense reports through policy checks and multi-level approval chains.", domain: "finance", steps: 4, trigger: "api_event" },
  { id: "tpl-payroll-processing", name: "Payroll Processing", description: "Calculate salaries, deductions, taxes, and generate payslips for all employees.", domain: "hr", steps: 6, trigger: "schedule" },
  { id: "tpl-employee-onboarding", name: "Employee Onboarding", description: "Provision accounts, assign equipment, schedule orientation, and notify stakeholders in parallel.", domain: "hr", steps: 7, trigger: "api_event" },
  { id: "tpl-leave-approval", name: "Leave Approval", description: "Validate leave balance, check team coverage, and route to manager for approval.", domain: "hr", steps: 3, trigger: "api_event" },
  { id: "tpl-performance-review", name: "Performance Review Cycle", description: "Initiate self-assessments, collect manager ratings, calibrate scores, and finalize reviews.", domain: "hr", steps: 6, trigger: "schedule" },
  { id: "tpl-talent-screening", name: "Talent Screening", description: "Parse resumes, score candidates against job requirements, and shortlist for interviews.", domain: "hr", steps: 5, trigger: "api_event" },
  { id: "tpl-campaign-launch", name: "Campaign Launch", description: "Coordinate creative assets, audience targeting, channel setup, and launch across platforms.", domain: "marketing", steps: 6, trigger: "manual" },
  { id: "tpl-lead-scoring", name: "Lead Scoring", description: "Evaluate inbound leads using firmographic, behavioral, and engagement signals.", domain: "marketing", steps: 4, trigger: "api_event" },
  { id: "tpl-content-publishing", name: "Content Publishing", description: "Draft, review, optimize for SEO, and publish content across blog and social channels.", domain: "marketing", steps: 5, trigger: "manual" },
  { id: "tpl-social-media-calendar", name: "Social Media Calendar", description: "Plan, schedule, and auto-publish posts across social media platforms on a weekly cadence.", domain: "marketing", steps: 4, trigger: "schedule" },
  { id: "tpl-email-drip-campaign", name: "Email Drip Campaign", description: "Enroll contacts, send sequenced emails, track engagement, and branch based on actions.", domain: "marketing", steps: 5, trigger: "api_event" },
  { id: "tpl-support-ticket-triage", name: "Support Ticket Triage", description: "Classify incoming tickets by urgency and topic, then route to the appropriate support tier.", domain: "ops", steps: 4, trigger: "api_event" },
  { id: "tpl-it-asset-provisioning", name: "IT Asset Provisioning", description: "Allocate laptops, software licenses, and cloud accounts for new hires or role changes.", domain: "ops", steps: 5, trigger: "api_event" },
  { id: "tpl-vendor-onboarding", name: "Vendor Onboarding", description: "Collect vendor documents, verify compliance, set up payment details, and approve registration.", domain: "ops", steps: 5, trigger: "manual" },
  { id: "tpl-contract-renewal", name: "Contract Renewal", description: "Track contract expiry dates, notify stakeholders, negotiate terms, and execute renewals.", domain: "ops", steps: 5, trigger: "schedule" },
  { id: "tpl-compliance-audit", name: "Compliance Audit", description: "Gather evidence, run control checks, flag exceptions, and generate audit reports.", domain: "ops", steps: 6, trigger: "schedule" },
  { id: "tpl-report-generation", name: "Report Generation", description: "Aggregate data from multiple sources, generate formatted reports, and distribute to stakeholders.", domain: "ops", steps: 4, trigger: "schedule" },
];

type WorkflowsTab = "my-workflows" | "templates";

export default function Workflows() {
  const navigate = useNavigate();
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<WorkflowsTab>("my-workflows");

  useEffect(() => {
    fetchWorkflows();
  }, []);

  async function fetchWorkflows() {
    setLoading(true);
    try {
      const { data } = await api.get("/workflows");
      const items = Array.isArray(data) ? data : Array.isArray(data?.items) ? data.items : [];
      setWorkflows(items);
    } catch {
      setWorkflows([]);
    } finally {
      setLoading(false);
    }
  }

  async function triggerRun(wfId: string) {
    setError(null);
    try {
      const { data } = await api.post(`/workflows/${wfId}/run`, {});
      if (data.run_id) {
        navigate(`/dashboard/workflows/${wfId}/runs/${data.run_id}`);
      }
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Failed to trigger workflow run");
    }
  }

  function useTemplate(template: WorkflowTemplate) {
    navigate("/dashboard/workflows/new", {
      state: {
        templateId: template.id,
        templateName: template.name,
        templateDescription: template.description,
        templateDomain: template.domain,
        templateSteps: template.steps,
        templateTrigger: template.trigger,
      },
    });
  }

  const domainColors: Record<string, string> = {
    finance: "bg-emerald-100 text-emerald-800",
    hr: "bg-violet-100 text-violet-800",
    marketing: "bg-blue-100 text-blue-800",
    ops: "bg-orange-100 text-orange-800",
  };

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <h2 className="text-2xl font-bold">Workflows</h2>
        <Button onClick={() => navigate("/dashboard/workflows/new")}>Create Workflow</Button>
      </div>

      {/* Tab switcher */}
      <div className="flex border-b" data-testid="workflows-tabs">
        <button
          className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${activeTab === "my-workflows" ? "border-primary text-primary" : "border-transparent text-muted-foreground hover:text-foreground"}`}
          onClick={() => setActiveTab("my-workflows")}
          data-testid="tab-my-workflows"
        >
          My Workflows
        </button>
        <button
          className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${activeTab === "templates" ? "border-primary text-primary" : "border-transparent text-muted-foreground hover:text-foreground"}`}
          onClick={() => setActiveTab("templates")}
          data-testid="tab-templates"
        >
          Templates
        </button>
      </div>

      {error && (
        <div className="rounded-lg bg-red-50 text-red-800 border border-red-200 px-4 py-3 text-sm">{error}</div>
      )}

      {/* My Workflows tab */}
      {activeTab === "my-workflows" && (
        <>
          {loading ? (
            <p className="text-muted-foreground">Loading workflows...</p>
          ) : workflows.length === 0 ? (
            <p className="text-muted-foreground">No workflows configured yet.</p>
          ) : (
            <div className="space-y-4">
              {workflows.map((wf) => (
                <Card key={wf.id} className="hover:shadow-md transition-shadow">
                  <CardHeader>
                    <div className="flex justify-between items-center">
                      <CardTitle className="text-base">{wf.name}</CardTitle>
                      <div className="flex items-center gap-2">
                        <Badge variant={wf.is_active ? "success" as any : "secondary"}>{wf.is_active ? "Active" : "Inactive"}</Badge>
                        <span className="text-sm text-muted-foreground">v{wf.version}</span>
                      </div>
                    </div>
                  </CardHeader>
                  <CardContent>
                    <div className="flex justify-between items-center">
                      <div className="text-sm text-muted-foreground">
                        Trigger: <span className="font-medium">{wf.trigger_type || "manual"}</span>
                        {" | "}Created: <span className="font-medium">{new Date(wf.created_at).toLocaleDateString()}</span>
                      </div>
                      <div className="flex gap-2">
                        <Button variant="outline" size="sm" onClick={() => navigate(`/dashboard/workflows/${wf.id}`)}>View</Button>
                        <Button size="sm" onClick={() => triggerRun(wf.id)}>Run Now</Button>
                      </div>
                    </div>
                  </CardContent>
                </Card>
              ))}
            </div>
          )}
        </>
      )}

      {/* Templates tab */}
      {activeTab === "templates" && (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4" data-testid="templates-grid">
          {WORKFLOW_TEMPLATES.map((tpl) => (
            <Card key={tpl.id} className="hover:shadow-md transition-shadow flex flex-col">
              <CardHeader className="pb-2">
                <div className="flex justify-between items-start">
                  <CardTitle className="text-base">{tpl.name}</CardTitle>
                  <span className={`text-xs px-2 py-0.5 rounded font-medium ${domainColors[tpl.domain] || "bg-gray-100 text-gray-800"}`}>
                    {tpl.domain.charAt(0).toUpperCase() + tpl.domain.slice(1)}
                  </span>
                </div>
              </CardHeader>
              <CardContent className="flex flex-col flex-1">
                <p className="text-sm text-muted-foreground mb-3 flex-1">{tpl.description}</p>
                <div className="flex justify-between items-center">
                  <div className="text-xs text-muted-foreground">
                    {tpl.steps} steps | {tpl.trigger.replace(/_/g, " ")}
                  </div>
                  <Button size="sm" onClick={() => useTemplate(tpl)} data-testid={`use-template-${tpl.id}`}>
                    Use Template
                  </Button>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
