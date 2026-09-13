import { useState, useEffect } from "react";
import { useNavigate } from "react-router";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import api, { extractApiError } from "@/lib/api";
import type { Workflow } from "@/types";

/**
 * Workflow template catalog item â€” shape returned by
 * `GET /api/v1/workflows/templates` (Enterprise Readiness P7.2 / PR-C3).
 * Pre-PR-C3 the UI embedded a 21-entry hardcoded array that drifted
 * away from the backend. The catalog is now backend-served so adding
 * / renaming a template doesn't require a UI code change.
 */
interface WorkflowTemplate {
  id: string;
  name: string;
  description: string;
  domain: string;
  steps: number;
  trigger: string;
}

type WorkflowsTab = "my-workflows" | "templates";

// Server caps per_page at 100; default (20) silently hid everything past
// the first page.
const WORKFLOWS_PER_PAGE = 50;

export default function Workflows() {
  const navigate = useNavigate();
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [page, setPage] = useState(1);
  const [pages, setPages] = useState(1);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<WorkflowsTab>("my-workflows");

  // PR-C3: template catalog now sourced from GET /workflows/templates.
  const [templates, setTemplates] = useState<WorkflowTemplate[]>([]);
  const [templatesLoading, setTemplatesLoading] = useState(true);

  useEffect(() => {
    fetchTemplates();
  }, []);

  useEffect(() => {
    fetchWorkflows(page);
  }, [page]);

  async function fetchWorkflows(pageNum: number) {
    setLoading(true);
    setListError(null);
    try {
      const { data } = await api.get("/workflows", {
        params: { page: pageNum, per_page: WORKFLOWS_PER_PAGE },
      });
      const items: Workflow[] = Array.isArray(data) ? data : Array.isArray(data?.items) ? data.items : [];
      setWorkflows(items);
      setTotal(typeof data?.total === "number" ? data.total : items.length);
      setPages(typeof data?.pages === "number" && data.pages > 0 ? data.pages : 1);
    } catch (e: unknown) {
      setWorkflows([]);
      setTotal(0);
      setPages(1);
      setListError(extractApiError(e, "Failed to load workflows"));
    } finally {
      setLoading(false);
    }
  }

  async function fetchTemplates() {
    setTemplatesLoading(true);
    try {
      const { data } = await api.get<{ items: WorkflowTemplate[]; total: number }>(
        "/workflows/templates",
      );
      setTemplates(Array.isArray(data?.items) ? data.items : []);
    } catch {
      setTemplates([]);
    } finally {
      setTemplatesLoading(false);
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

  function applyTemplate(template: WorkflowTemplate) {
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
          ) : listError ? (
            <div
              className="rounded-lg bg-red-50 text-red-800 border border-red-200 px-4 py-3 text-sm flex items-center justify-between"
              data-testid="workflows-error"
            >
              <span>Failed to load workflows: {listError}</span>
              <Button variant="outline" size="sm" onClick={() => fetchWorkflows(page)}>Retry</Button>
            </div>
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
          {!loading && !listError && pages > 1 && (
            <div className="flex items-center justify-between text-sm text-muted-foreground" data-testid="workflows-pagination">
              <span>Showing {workflows.length} of {total} on page {page} of {pages}</span>
              <div className="flex gap-2">
                <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage((p) => Math.max(1, p - 1))}>
                  Previous
                </Button>
                <Button variant="outline" size="sm" disabled={page >= pages} onClick={() => setPage((p) => p + 1)}>
                  Next
                </Button>
              </div>
            </div>
          )}
        </>
      )}

      {/* Templates tab */}
      {activeTab === "templates" && templatesLoading && templates.length === 0 && (
        <p className="text-sm text-muted-foreground" data-testid="templates-loading">
          Loading templatesâ€¦
        </p>
      )}
      {activeTab === "templates" && !templatesLoading && templates.length === 0 && (
        <p className="text-sm text-muted-foreground">
          No templates available. Check that /api/v1/workflows/templates is reachable.
        </p>
      )}
      {activeTab === "templates" && templates.length > 0 && (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4" data-testid="templates-grid">
          {templates.map((tpl) => (
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
                  <Button size="sm" onClick={() => applyTemplate(tpl)} data-testid={`use-template-${tpl.id}`}>
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
