import { useState } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import api from "@/lib/api";

const TRIGGER_TYPES = ["manual", "schedule", "webhook", "api_event", "email_received"];

const COMMON_ENGLISH_WORDS = new Set([
  "the", "a", "an", "and", "or", "for", "to", "in", "of", "on",
  "with", "from", "by", "at", "when", "if", "then", "after", "before",
  "create", "process", "send", "check", "verify", "approve", "review",
  "generate", "update", "notify", "calculate", "run", "start", "stop",
  "all", "each", "every", "new", "should", "will", "can", "must",
  "is", "are", "was", "be", "have", "has", "do", "does", "not",
  "this", "that", "it", "they", "we", "my", "our",
]);
const DOMAINS = ["finance", "hr", "marketing", "ops", "backoffice"];

// CRON expression validator (5-field: minute hour day month weekday)
const CRON_REGEX = /^(\*|[0-9,\-\/]+)\s+(\*|[0-9,\-\/]+)\s+(\*|[0-9,\-\/]+)\s+(\*|[0-9,\-\/]+)\s+(\*|[0-9,\-\/]+)$/;
function isValidCron(expr: string): boolean {
  return CRON_REGEX.test(expr.trim());
}

const STEP_TEMPLATE = JSON.stringify([
  {
    step: 1,
    name: "Step 1",
    agent_type: "ap_processor",
    action: "process",
    inputs: {},
    on_success: "next",
    on_failure: "halt",
  },
], null, 2);

type TabMode = "describe" | "template";

interface GeneratedStep {
  id: string;
  type: string;
  title?: string;
  agent_type?: string;
  condition?: string;
  depends_on?: string[];
}

interface GeneratedWorkflow {
  name: string;
  description?: string;
  domain?: string;
  trigger_type?: string;
  trigger_config?: Record<string, unknown>;
  steps: GeneratedStep[];
}

export default function WorkflowCreate() {
  const navigate = useNavigate();
  const location = useLocation();
  const templateState = location.state as Record<string, string | number> | null;

  // Tab state
  const [activeTab, setActiveTab] = useState<TabMode>(templateState?.templateId ? "template" : "describe");

  // Template form state (existing) — pre-fill from template navigation
  const [name, setName] = useState(templateState?.templateName as string || "");
  const [version, setVersion] = useState("1.0.0");
  const [domain, setDomain] = useState(templateState?.templateDomain as string || "finance");
  const [triggerType, setTriggerType] = useState(templateState?.templateTrigger as string || "manual");
  const [stepsJson, setStepsJson] = useState(STEP_TEMPLATE);
  const [cronSchedule, setCronSchedule] = useState("0 9 * * 1-5");
  const [replanOnFailure, setReplanOnFailure] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [cronError, setCronError] = useState("");

  // NL generation state
  const [nlDescription, setNlDescription] = useState("");
  const [generating, setGenerating] = useState(false);
  const [generatedWorkflow, setGeneratedWorkflow] = useState<GeneratedWorkflow | null>(null);
  const [deploying, setDeploying] = useState(false);
  const [nlError, setNlError] = useState("");

  function validateSteps(): { valid: boolean; parsed: any[] } {
    try {
      const parsed = JSON.parse(stepsJson);
      if (!Array.isArray(parsed)) return { valid: false, parsed: [] };
      return { valid: true, parsed };
    } catch {
      return { valid: false, parsed: [] };
    }
  }

  // ── Template form submit ──
  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) { setError("Workflow name is required"); return; }
    const { valid, parsed } = validateSteps();
    if (!valid) { setError("Steps must be valid JSON array. Check syntax and try again."); return; }
    if (triggerType === "schedule" && !isValidCron(cronSchedule)) {
      setCronError("Invalid CRON expression. Use 5 fields: minute hour day month weekday (e.g. 0 9 * * 1-5)");
      return;
    }
    setCronError("");
    setSubmitting(true);
    setError("");
    try {
      const { data } = await api.post("/workflows", {
        name: name.trim(),
        version,
        domain,
        trigger_type: triggerType,
        ...(triggerType === "schedule" ? { cron_schedule: cronSchedule } : {}),
        definition: { steps: parsed },
        replan_on_failure: replanOnFailure,
      });
      navigate(`/dashboard/workflows/${data.workflow_id || data.id || ""}`);
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Failed to create workflow. Please try again.");
    } finally {
      setSubmitting(false);
    }
  }

  // ── NL validation helper ──
  function validateNlInput(text: string): string | null {
    const trimmed = text.trim();
    if (trimmed.length < 20) {
      return "Please provide a more detailed description (at least 20 characters).";
    }
    const words = trimmed.split(/\s+/);
    if (words.length < 3) {
      return "Please use at least 3 words to describe your workflow.";
    }
    const matchedCommon = words.filter((w) => COMMON_ENGLISH_WORDS.has(w.toLowerCase()));
    if (matchedCommon.length < 2) {
      return "Your input does not appear to be a valid workflow description. Please describe the process in plain English.";
    }
    return null;
  }

  // ── NL generation ──
  async function handleGenerate() {
    if (!nlDescription.trim()) {
      setNlError("Please describe the workflow you want to create.");
      return;
    }
    const validationError = validateNlInput(nlDescription);
    if (validationError) {
      setNlError(validationError);
      return;
    }
    setGenerating(true);
    setNlError("");
    setGeneratedWorkflow(null);
    try {
      const { data } = await api.post("/workflows/generate", {
        description: nlDescription.trim(),
        deploy: false,
        ...(triggerType === "schedule" ? { cron_schedule: cronSchedule } : {}),
      });
      setGeneratedWorkflow(data.workflow as GeneratedWorkflow);
    } catch (e: any) {
      setNlError(
        e?.response?.data?.detail || "Failed to generate workflow. Try rephrasing or use a template."
      );
    } finally {
      setGenerating(false);
    }
  }

  // ── Deploy generated workflow ──
  async function handleDeploy() {
    if (!generatedWorkflow) return;
    setDeploying(true);
    setNlError("");
    try {
      const { data } = await api.post("/workflows/generate", {
        description: nlDescription.trim(),
        deploy: true,
        ...(triggerType === "schedule" ? { cron_schedule: cronSchedule } : {}),
      });
      if (data.workflow_id) {
        navigate(`/dashboard/workflows/${data.workflow_id}`);
      }
    } catch (e: any) {
      setNlError(e?.response?.data?.detail || "Failed to deploy workflow.");
    } finally {
      setDeploying(false);
    }
  }

  // ── Switch to template editor with pre-filled data from generated workflow ──
  function handleEditInTemplate() {
    if (!generatedWorkflow) return;
    setActiveTab("template");
    setName(generatedWorkflow.name || "");
    setDomain(generatedWorkflow.domain || "finance");
    setTriggerType(generatedWorkflow.trigger_type || "manual");
    setStepsJson(JSON.stringify(generatedWorkflow.steps, null, 2));
  }

  const stepsValid = validateSteps().valid;

  // Collaboration step state
  const [collabAgents, setCollabAgents] = useState<string[]>([]);
  const [collabAggregation, setCollabAggregation] = useState<"merge" | "vote" | "first_complete">("merge");
  const [collabTimeout, setCollabTimeout] = useState(10);

  // ── Step type badge color ──
  function stepTypeBadge(stepType: string): string {
    const colors: Record<string, string> = {
      agent: "bg-blue-100 text-blue-800",
      condition: "bg-yellow-100 text-yellow-800",
      human_in_loop: "bg-purple-100 text-purple-800",
      parallel: "bg-green-100 text-green-800",
      wait: "bg-gray-100 text-gray-800",
      wait_for_event: "bg-orange-100 text-orange-800",
      notify: "bg-pink-100 text-pink-800",
      transform: "bg-indigo-100 text-indigo-800",
      collaboration: "bg-teal-100 text-teal-800",
    };
    return colors[stepType] || "bg-gray-100 text-gray-800";
  }

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <h2 className="text-2xl font-bold">Create Workflow</h2>
        <Button variant="outline" onClick={() => navigate("/dashboard/workflows")}>Back to Workflows</Button>
      </div>

      {/* Tab switcher */}
      <div className="flex border-b" data-testid="workflow-tabs">
        <button
          className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${activeTab === "describe" ? "border-primary text-primary" : "border-transparent text-muted-foreground hover:text-foreground"}`}
          onClick={() => setActiveTab("describe")}
          data-testid="tab-describe"
        >
          Describe in English
        </button>
        <button
          className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${activeTab === "template" ? "border-primary text-primary" : "border-transparent text-muted-foreground hover:text-foreground"}`}
          onClick={() => setActiveTab("template")}
          data-testid="tab-template"
        >
          Use Template
        </button>
      </div>

      {/* ── Describe in English tab ── */}
      {activeTab === "describe" && (
        <Card>
          <CardHeader>
            <CardTitle>Describe Your Workflow</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div>
              <label className="text-sm font-medium" htmlFor="nl-description">
                What should this workflow do?
              </label>
              <textarea
                id="nl-description"
                data-testid="nl-description"
                value={nlDescription}
                onChange={(e) => setNlDescription(e.target.value)}
                placeholder={
                  "Examples:\n" +
                  "- Automate invoice approval when amount > 5L, route to CFO for amounts > 10L\n" +
                  "- When a new employee joins, create accounts in Slack, Gmail, and Jira in parallel\n" +
                  "- Monitor GST filings weekly and alert compliance team if overdue\n" +
                  "- Process expense reports: validate receipts, check policy, approve or escalate"
                }
                className="border rounded px-3 py-2 text-sm w-full mt-1 min-h-[120px]"
                rows={5}
                maxLength={5000}
              />
              <div className="flex justify-between mt-1">
                <p className="text-xs text-muted-foreground">
                  Describe your business process in plain English. We will generate the workflow steps automatically.
                </p>
                <span className="text-xs text-muted-foreground whitespace-nowrap ml-2">
                  {nlDescription.length} / 5000
                </span>
              </div>
            </div>

            <div className="flex gap-3">
              <Button
                onClick={handleGenerate}
                disabled={generating || !nlDescription.trim()}
                data-testid="btn-generate"
              >
                {generating ? (
                  <span className="flex items-center gap-2">
                    <span className="animate-spin inline-block w-4 h-4 border-2 border-current border-t-transparent rounded-full" />
                    Generating...
                  </span>
                ) : (
                  "Generate Workflow"
                )}
              </Button>
            </div>

            {nlError && (
              <p className="text-sm text-destructive" data-testid="nl-error">{nlError}</p>
            )}

            {/* ── Generated workflow preview ── */}
            {generatedWorkflow && (
              <div className="border rounded-lg p-4 space-y-4" data-testid="workflow-preview">
                <div className="flex justify-between items-start">
                  <div>
                    <h3 className="font-semibold text-lg">{generatedWorkflow.name}</h3>
                    {generatedWorkflow.description && (
                      <p className="text-sm text-muted-foreground mt-1">{generatedWorkflow.description}</p>
                    )}
                  </div>
                  <div className="flex gap-2 text-xs">
                    {generatedWorkflow.domain && (
                      <span className="bg-muted px-2 py-1 rounded">{generatedWorkflow.domain}</span>
                    )}
                    {generatedWorkflow.trigger_type && (
                      <span className="bg-muted px-2 py-1 rounded">
                        {generatedWorkflow.trigger_type.replace(/_/g, " ")}
                      </span>
                    )}
                  </div>
                </div>

                {/* Step list */}
                <div className="space-y-2">
                  <h4 className="text-sm font-medium">Steps ({generatedWorkflow.steps.length})</h4>
                  <div className="space-y-1">
                    {generatedWorkflow.steps.map((step, idx) => (
                      <div
                        key={step.id || idx}
                        className="flex items-center gap-3 p-2 rounded bg-muted/50 text-sm"
                        data-testid={`preview-step-${idx}`}
                      >
                        <span className="text-muted-foreground font-mono text-xs w-6">
                          {idx + 1}.
                        </span>
                        <span className={`px-2 py-0.5 rounded text-xs font-medium ${stepTypeBadge(step.type)}`}>
                          {step.type}
                        </span>
                        <span className="font-medium">{step.title || step.id}</span>
                        {step.agent_type && (
                          <span className="text-xs text-muted-foreground">({step.agent_type})</span>
                        )}
                        {step.condition && (
                          <span className="text-xs text-muted-foreground italic">
                            if: {step.condition}
                          </span>
                        )}
                      </div>
                    ))}
                  </div>
                </div>

                {/* Action buttons */}
                <div className="flex gap-3 pt-2">
                  <Button
                    onClick={handleDeploy}
                    disabled={deploying}
                    data-testid="btn-deploy"
                  >
                    {deploying ? "Deploying..." : "Deploy Workflow"}
                  </Button>
                  <Button
                    variant="outline"
                    onClick={handleEditInTemplate}
                    data-testid="btn-edit"
                  >
                    Edit in Template
                  </Button>
                </div>
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {/* ── Use Template tab (existing) ── */}
      {activeTab === "template" && (
        <Card>
          <CardHeader><CardTitle>Workflow Configuration</CardTitle></CardHeader>
          <CardContent>
            <form onSubmit={handleSubmit} className="space-y-4">
              <div>
                <label className="text-sm font-medium">Workflow Name *</label>
                <input type="text" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Invoice Processing Pipeline" className="border rounded px-3 py-2 text-sm w-full mt-1" />
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                <div>
                  <label className="text-sm font-medium">Version</label>
                  <input type="text" value={version} onChange={(e) => setVersion(e.target.value)} placeholder="1.0.0" className="border rounded px-3 py-2 text-sm w-full mt-1" />
                </div>
                <div>
                  <label className="text-sm font-medium">Domain</label>
                  <select value={domain} onChange={(e) => setDomain(e.target.value)} className="border rounded px-3 py-2 text-sm w-full mt-1">
                    {DOMAINS.map((d) => <option key={d} value={d}>{d.charAt(0).toUpperCase() + d.slice(1)}</option>)}
                  </select>
                </div>
                <div>
                  <label className="text-sm font-medium">Trigger Type</label>
                  <select value={triggerType} onChange={(e) => setTriggerType(e.target.value)} className="border rounded px-3 py-2 text-sm w-full mt-1">
                    {TRIGGER_TYPES.map((t) => <option key={t} value={t}>{t.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())}</option>)}
                  </select>
                </div>
              </div>

              {triggerType === "schedule" && (
                <div className="mt-4" data-testid="cron-schedule-section">
                  <label className="text-sm font-medium">Cron Schedule</label>
                  <input
                    type="text"
                    value={cronSchedule}
                    onChange={(e) => {
                      setCronSchedule(e.target.value);
                      if (cronError && isValidCron(e.target.value)) setCronError("");
                    }}
                    onBlur={() => {
                      if (cronSchedule.trim() && !isValidCron(cronSchedule)) {
                        setCronError("Invalid CRON expression. Use 5 fields: minute hour day month weekday (e.g. 0 9 * * 1-5)");
                      } else {
                        setCronError("");
                      }
                    }}
                    placeholder="0 9 * * 1-5 (weekdays at 9 AM)"
                    className={`border rounded px-3 py-2 text-sm w-full mt-1 ${cronError ? "border-destructive" : ""}`}
                    data-testid="cron-schedule-input"
                  />
                  <p className="text-xs text-gray-500 mt-1">Format: minute hour day month weekday</p>
                  {cronError && <p className="text-xs text-destructive mt-1">{cronError}</p>}
                </div>
              )}

              <div className="flex items-center gap-3">
                <input
                  type="checkbox"
                  id="replan-on-failure"
                  data-testid="replan-toggle"
                  checked={replanOnFailure}
                  onChange={(e) => setReplanOnFailure(e.target.checked)}
                  className="h-4 w-4 rounded border-gray-300"
                />
                <label htmlFor="replan-on-failure" className="text-sm font-medium cursor-pointer">
                  Enable adaptive replanning
                </label>
                <span className="text-xs text-muted-foreground">
                  When a step fails, the AI will attempt to re-plan the remaining steps (max 3 attempts).
                </span>
              </div>

              {/* Step Type Quick-Add */}
              <div>
                <label className="text-sm font-medium">Add Step Type</label>
                <select
                  data-testid="step-type-select"
                  onChange={(e) => {
                    const type = e.target.value;
                    if (!type) return;
                    if (type === "collaboration") {
                      // Will be configured below
                    }
                    e.target.value = "";
                  }}
                  defaultValue=""
                  className="border rounded px-3 py-2 text-sm w-full mt-1"
                >
                  <option value="">Select step type...</option>
                  <option value="agent">Agent</option>
                  <option value="condition">Condition</option>
                  <option value="human_in_loop">Human in Loop</option>
                  <option value="parallel">Parallel</option>
                  <option value="wait">Wait</option>
                  <option value="wait_for_event">Wait for Event</option>
                  <option value="notify">Notify</option>
                  <option value="transform">Transform</option>
                  <option value="collaboration">Collaboration</option>
                </select>
              </div>

              {/* Collaboration Step Config */}
              <div className="border rounded-lg p-4 space-y-3 bg-teal-50/50" data-testid="collaboration-config">
                <h4 className="text-sm font-semibold text-teal-800">Collaboration Step</h4>
                <p className="text-xs text-muted-foreground">Configure agents to run in parallel with an aggregation strategy.</p>

                <div>
                  <label className="text-sm font-medium">Agents (select 2+ to run in parallel)</label>
                  <select
                    multiple
                    value={collabAgents}
                    onChange={(e) => {
                      const selected = Array.from(e.target.selectedOptions, (o) => o.value);
                      setCollabAgents(selected);
                    }}
                    className="border rounded px-3 py-2 text-sm w-full mt-1 min-h-[100px]"
                    data-testid="collab-agents"
                  >
                    <option value="ap_processor">AP Processor</option>
                    <option value="ar_collections">AR Collections</option>
                    <option value="recon_agent">Recon Agent</option>
                    <option value="support_triage">Support Triage</option>
                    <option value="content_factory">Content Factory</option>
                    <option value="seo_strategist">SEO Strategist</option>
                    <option value="talent_acquisition">Talent Acquisition</option>
                    <option value="compliance_guard">Compliance Guard</option>
                  </select>
                  {collabAgents.length > 0 && collabAgents.length < 2 && (
                    <p className="text-xs text-amber-600 mt-1">Select at least 2 agents for collaboration.</p>
                  )}
                </div>

                <div>
                  <label className="text-sm font-medium">Aggregation Strategy</label>
                  <select
                    value={collabAggregation}
                    onChange={(e) => setCollabAggregation(e.target.value as "merge" | "vote" | "first_complete")}
                    className="border rounded px-3 py-2 text-sm w-full mt-1"
                    data-testid="collab-aggregation"
                  >
                    <option value="merge">Merge (combine all outputs)</option>
                    <option value="vote">Vote (majority wins)</option>
                    <option value="first_complete">First Complete (fastest agent wins)</option>
                  </select>
                </div>

                <div>
                  <label className="text-sm font-medium">Timeout (minutes)</label>
                  <input
                    type="number"
                    min={1}
                    max={60}
                    value={collabTimeout}
                    onChange={(e) => setCollabTimeout(Number(e.target.value))}
                    className="border rounded px-3 py-2 text-sm w-24 mt-1"
                    data-testid="collab-timeout"
                  />
                </div>

                {collabAgents.length >= 2 && (
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => {
                      try {
                        const current = JSON.parse(stepsJson);
                        const nextStep = Array.isArray(current) ? current.length + 1 : 1;
                        const collabStep = {
                          step: nextStep,
                          name: `Collaboration Step ${nextStep}`,
                          type: "collaboration",
                          agents: collabAgents,
                          aggregation: collabAggregation,
                          timeout_minutes: collabTimeout,
                          on_success: "next",
                          on_failure: "halt",
                        };
                        const updated = Array.isArray(current) ? [...current, collabStep] : [collabStep];
                        setStepsJson(JSON.stringify(updated, null, 2));
                      } catch {
                        const collabStep = [{
                          step: 1,
                          name: "Collaboration Step 1",
                          type: "collaboration",
                          agents: collabAgents,
                          aggregation: collabAggregation,
                          timeout_minutes: collabTimeout,
                          on_success: "next",
                          on_failure: "halt",
                        }];
                        setStepsJson(JSON.stringify(collabStep, null, 2));
                      }
                    }}
                  >
                    Add Collaboration Step to JSON
                  </Button>
                )}
              </div>

              <div>
                <div className="flex items-center justify-between mb-1">
                  <label className="text-sm font-medium">Define Steps (JSON) *</label>
                  {!stepsValid && stepsJson.trim() && (
                    <span className="text-xs text-destructive">Invalid JSON</span>
                  )}
                </div>
                <textarea
                  value={stepsJson}
                  onChange={(e) => setStepsJson(e.target.value)}
                  placeholder='[{"step": 1, "name": "Step 1", "agent_type": "ap_processor", "action": "process", "inputs": {}}]'
                  className={`border rounded px-3 py-2 text-sm w-full mt-1 font-mono ${!stepsValid && stepsJson.trim() ? "border-destructive" : ""}`}
                  rows={10}
                />
                <p className="text-xs text-muted-foreground mt-1">
                  Define workflow steps as a JSON array. Each step should have: step (number), name, agent_type, action, inputs, on_success, on_failure. Collaboration steps also need: agents, aggregation, timeout_minutes.
                </p>
              </div>

              <div className="bg-muted/50 rounded-lg p-4 text-sm text-muted-foreground">
                <p>After creation, you can use the visual Workflow Builder to modify steps and configure agent orchestration.</p>
              </div>

              {error && <p className="text-sm text-destructive">{error}</p>}

              <div className="flex gap-3">
                <Button type="submit" disabled={submitting}>{submitting ? "Creating..." : "Create Workflow"}</Button>
                <Button type="button" variant="outline" onClick={() => navigate("/dashboard/workflows")}>Cancel</Button>
              </div>
            </form>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
