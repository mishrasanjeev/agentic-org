import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import api, { extractApiError, promptTemplatesApi, agentsApi } from "@/lib/api";
import type { Agent, PromptTemplate } from "@/types";

const DOMAINS = ["finance", "hr", "marketing", "ops", "backoffice", "comms"];
const AGENT_TYPES: Record<string, string[]> = {
  finance: ["ap_processor", "ar_collections", "recon_agent", "tax_compliance", "close_agent", "fpa_agent"],
  hr: ["talent_acquisition", "onboarding_agent", "payroll_engine", "performance_coach", "ld_coordinator", "offboarding_agent"],
  marketing: ["content_factory", "campaign_pilot", "seo_strategist", "crm_intelligence", "brand_monitor"],
  ops: ["support_triage", "vendor_manager", "contract_intelligence", "compliance_guard", "it_operations"],
  backoffice: ["legal_ops", "risk_sentinel", "facilities_agent"],
  comms: ["email_agent", "notification_agent", "chat_agent"],
};

const STEPS = ["Persona", "Role", "Prompt", "Behavior", "Review"];

function humanize(s: string) {
  return s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

type PermissionLevel = "READ" | "WRITE" | "DELETE" | "ADMIN";

function getToolPermission(toolName: string): PermissionLevel {
  if (/^(get_|fetch_|list_|query|search_)/.test(toolName)) return "READ";
  if (/^(create_|update_|send_|post_)/.test(toolName)) return "WRITE";
  if (/^(delete_|remove_)/.test(toolName)) return "DELETE";
  if (/^(bulk_|reset_|admin_)/.test(toolName)) return "ADMIN";
  return "READ";
}

const PERMISSION_COLORS: Record<PermissionLevel, string> = {
  READ: "bg-green-100 text-green-700 border-green-300",
  WRITE: "bg-blue-100 text-blue-700 border-blue-300",
  DELETE: "bg-red-100 text-red-700 border-red-300",
  ADMIN: "bg-purple-100 text-purple-700 border-purple-300",
};

function PermissionBadge({ tool }: { tool: string }) {
  const perm = getToolPermission(tool);
  return (
    <span className={`inline-block text-[10px] font-semibold px-1.5 py-0 rounded border ml-1 ${PERMISSION_COLORS[perm]}`}>
      {perm}
    </span>
  );
}

export default function AgentCreate() {
  const navigate = useNavigate();
  const [step, setStep] = useState(0);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  // Step 1: Persona
  const [employeeName, setEmployeeName] = useState("");
  const [designation, setDesignation] = useState("");
  const [avatarUrl, setAvatarUrl] = useState("");
  const [domain, setDomain] = useState("finance");

  // Step 2: Role
  const [agentType, setAgentType] = useState(AGENT_TYPES.finance[0]);
  const [customType, setCustomType] = useState("");
  const [useCustomType, setUseCustomType] = useState(false);
  const [specialization, setSpecialization] = useState("");
  const [routingFilters, setRoutingFilters] = useState<Array<{ key: string; value: string }>>([]);

  // Step 3: Prompt
  const [templates, setTemplates] = useState<PromptTemplate[]>([]);
  const [selectedTemplateId, setSelectedTemplateId] = useState("");
  const [promptText, setPromptText] = useState("");
  const [promptVars, setPromptVars] = useState<Record<string, string>>({});

  // Step 2 (cont): Org Chart
  const [parentAgentId, setParentAgentId] = useState("");
  const [reportingTo, setReportingTo] = useState("");
  const [availableParents, setAvailableParents] = useState<Agent[]>([]);

  // Step 4: Behavior
  const [confidenceFloor, setConfidenceFloor] = useState(0.88);
  const [hitlCondition, setHitlCondition] = useState("confidence < 0.88");
  const [maxRetries, setMaxRetries] = useState(3);
  const [llmModel, setLlmModel] = useState("gemini-2.5-flash");
  const [authorizedTools, setAuthorizedTools] = useState<string[]>([]);
  const [availableTools, setAvailableTools] = useState<string[]>([]);

  // Load available parent agents when domain changes
  useEffect(() => {
    agentsApi.list({ domain, status: "active" }).then(({ data }) => {
      const items = Array.isArray(data) ? data : data.items || [];
      setAvailableParents(items);
    }).catch(() => setAvailableParents([]));
  }, [domain]);

  // Load default tools when agent type changes
  useEffect(() => {
    api.get("/mcp/tools").then(({ data }) => {
      const allTools = (data.tools || []).map((t: { name: string }) => t.name);
      setAvailableTools(allTools);
    }).catch(() => setAvailableTools([]));
  }, []);

  // Auto-assign default tools when agent type changes
  useEffect(() => {
    const type = useCustomType ? customType : agentType;
    api.get(`/agents/default-tools/${type}`).then(({ data }) => {
      setAuthorizedTools(data.tools || []);
    }).catch(() => {
      // Fallback: use first 5 tools from available
      setAuthorizedTools(availableTools.slice(0, 5));
    });
  }, [agentType, useCustomType, customType]); // eslint-disable-line react-hooks/exhaustive-deps

  // Load templates when domain changes
  useEffect(() => {
    promptTemplatesApi.list({ domain }).then(({ data }) => {
      const items = Array.isArray(data) ? data : data.items || [];
      setTemplates(items);
    }).catch(() => setTemplates([]));
  }, [domain]);

  // When template is selected, load its text and variables
  useEffect(() => {
    if (!selectedTemplateId) return;
    const t = templates.find((t) => t.id === selectedTemplateId);
    if (t) {
      setPromptText(t.template_text);
      const vars: Record<string, string> = {};
      (t.variables || []).forEach((v) => { vars[v.name] = v.default || ""; });
      setPromptVars(vars);
    }
  }, [selectedTemplateId, templates]);

  function resolvedPrompt() {
    let text = promptText;
    Object.entries(promptVars).forEach(([k, v]) => {
      text = text.split(`{{${k}}}`).join(v || `{{${k}}}`);
    });
    return text;
  }

  const finalType = useCustomType ? customType : agentType;
  const routingFilter: Record<string, string> = {};
  routingFilters.forEach(({ key, value }) => { if (key && value) routingFilter[key] = value; });

  function canNext() {
    if (step === 0) return employeeName.trim().length > 0;
    if (step === 1) return finalType.trim().length > 0;
    if (step === 2) return promptText.trim().length > 0;
    if (step === 3) return maxRetries >= 1 && llmModel.trim().length > 0;
    return true;
  }

  async function handleCreate() {
    setSubmitting(true);
    setError("");
    try {
      const { data } = await api.post("/agents", {
        name: employeeName.trim(),
        employee_name: employeeName.trim(),
        designation: designation.trim() || undefined,
        avatar_url: avatarUrl.trim() || undefined,
        domain,
        agent_type: finalType,
        specialization: specialization.trim() || undefined,
        routing_filter: Object.keys(routingFilter).length > 0 ? routingFilter : {},
        system_prompt_text: resolvedPrompt(),
        system_prompt: "",
        prompt_variables: promptVars,
        confidence_floor: confidenceFloor,
        hitl_policy: { condition: hitlCondition },
        max_retries: maxRetries,
        initial_status: "shadow",
        llm: { model: llmModel, fallback_model: "gemini-2.5-flash-preview-05-20" },
        parent_agent_id: parentAgentId || undefined,
        reporting_to: reportingTo || undefined,
        authorized_tools: authorizedTools.length > 0 ? authorizedTools : undefined,
      });
      import("@/components/Analytics").then(m => m.trackEvent("agent_create", { agent_type: agentType, domain })).catch(() => {});
      navigate(`/dashboard/agents/${data.agent_id || ""}`);
    } catch (e: any) {
      setError(extractApiError(e, "Failed to create agent. Please try again."));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="space-y-6 max-w-3xl mx-auto">
      <div className="flex justify-between items-center">
        <h2 className="text-2xl font-bold">Create Virtual Employee</h2>
        <Button variant="outline" onClick={() => navigate("/dashboard/agents")}>Back</Button>
      </div>

      {/* Progress bar */}
      <div className="flex gap-1">
        {STEPS.map((s, i) => (
          <div key={s} className="flex-1 text-center">
            <div className={`h-2 rounded-full ${i <= step ? "bg-primary" : "bg-muted"}`} />
            <p className={`text-xs mt-1 ${i === step ? "font-semibold text-primary" : "text-muted-foreground"}`}>{s}</p>
          </div>
        ))}
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Step {step + 1}: {STEPS[step]}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">

          {/* Step 1: Persona */}
          {step === 0 && (
            <>
              <div>
                <label className="text-sm font-medium">Employee Name *</label>
                <input type="text" value={employeeName} onChange={(e) => setEmployeeName(e.target.value)} placeholder="e.g. Priya, Arjun, Maya" className="border rounded px-3 py-2 text-sm w-full mt-1" />
                <p className="text-xs text-muted-foreground mt-1">The virtual employee's identity — how they'll appear across the platform.</p>
              </div>
              <div>
                <label className="text-sm font-medium">Designation</label>
                <input type="text" value={designation} onChange={(e) => setDesignation(e.target.value)} placeholder="e.g. Senior AP Analyst - Mumbai Office" className="border rounded px-3 py-2 text-sm w-full mt-1" />
              </div>
              <div>
                <label className="text-sm font-medium">Avatar URL</label>
                <input type="text" value={avatarUrl} onChange={(e) => setAvatarUrl(e.target.value)} placeholder="https://example.com/avatar.jpg" className="border rounded px-3 py-2 text-sm w-full mt-1" />
              </div>
              <div>
                <label className="text-sm font-medium">Domain *</label>
                <select value={domain} onChange={(e) => { setDomain(e.target.value); setAgentType(AGENT_TYPES[e.target.value][0]); }} className="border rounded px-3 py-2 text-sm w-full mt-1">
                  {DOMAINS.map((d) => <option key={d} value={d}>{humanize(d)}</option>)}
                </select>
              </div>
            </>
          )}

          {/* Step 2: Role */}
          {step === 1 && (
            <>
              <div>
                <label className="flex items-center gap-2 text-sm font-medium mb-2">
                  <input type="checkbox" checked={useCustomType} onChange={(e) => setUseCustomType(e.target.checked)} />
                  Create custom agent type
                </label>
                {useCustomType ? (
                  <input type="text" value={customType} onChange={(e) => setCustomType(e.target.value)} placeholder="e.g. customer_success" className="border rounded px-3 py-2 text-sm w-full" />
                ) : (
                  <select value={agentType} onChange={(e) => setAgentType(e.target.value)} className="border rounded px-3 py-2 text-sm w-full">
                    {AGENT_TYPES[domain].map((t) => <option key={t} value={t}>{humanize(t)}</option>)}
                  </select>
                )}
              </div>
              <div>
                <label className="text-sm font-medium">Specialization</label>
                <textarea value={specialization} onChange={(e) => setSpecialization(e.target.value)} placeholder="e.g. Import invoices above 10L, ICEGATE compliance" className="border rounded px-3 py-2 text-sm w-full mt-1" rows={2} />
              </div>
              <div>
                <label className="text-sm font-medium">Routing Filters</label>
                <p className="text-xs text-muted-foreground mb-2">When multiple agents share this type, routing filters decide who gets the task.</p>
                {routingFilters.map((f, i) => (
                  <div key={i} className="flex gap-2 mb-2">
                    <input type="text" value={f.key} onChange={(e) => { const n = [...routingFilters]; n[i].key = e.target.value; setRoutingFilters(n); }} placeholder="Key (e.g. region)" className="border rounded px-2 py-1 text-sm flex-1" />
                    <input type="text" value={f.value} onChange={(e) => { const n = [...routingFilters]; n[i].value = e.target.value; setRoutingFilters(n); }} placeholder="Value (e.g. APAC)" className="border rounded px-2 py-1 text-sm flex-1" />
                    <Button variant="outline" size="sm" onClick={() => setRoutingFilters(routingFilters.filter((_, j) => j !== i))}>Remove</Button>
                  </div>
                ))}
                <Button variant="outline" size="sm" onClick={() => setRoutingFilters([...routingFilters, { key: "", value: "" }])}>+ Add Filter</Button>
              </div>
              <div>
                <label className="text-sm font-medium">Reports To (Org Chart)</label>
                <p className="text-xs text-muted-foreground mb-2">Select a parent agent for escalation hierarchy. Leave empty for no parent.</p>
                <select value={parentAgentId} onChange={(e) => {
                  setParentAgentId(e.target.value);
                  const parent = availableParents.find((a) => a.id === e.target.value);
                  setReportingTo(parent ? (parent.employee_name || parent.name) : "");
                }} className="border rounded px-3 py-2 text-sm w-full">
                  <option value="">— No parent (escalates to human) —</option>
                  {availableParents.map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.employee_name || a.name} ({humanize(a.agent_type)}) — {humanize(a.domain)}
                    </option>
                  ))}
                </select>
              </div>
            </>
          )}

          {/* Step 3: Prompt */}
          {step === 2 && (
            <>
              <div>
                <label className="text-sm font-medium">Select Template</label>
                <select value={selectedTemplateId} onChange={(e) => setSelectedTemplateId(e.target.value)} className="border rounded px-3 py-2 text-sm w-full mt-1">
                  <option value="">— Write custom prompt —</option>
                  {templates.map((t) => (
                    <option key={t.id} value={t.id}>
                      {humanize(t.name)} {t.is_builtin ? "(built-in)" : ""}
                    </option>
                  ))}
                </select>
              </div>
              {Object.keys(promptVars).length > 0 && (
                <div className="bg-muted/50 rounded p-3 space-y-2">
                  <p className="text-sm font-medium">Template Variables</p>
                  {Object.entries(promptVars).map(([k, v]) => (
                    <div key={k} className="flex items-center gap-2">
                      <code className="text-xs bg-muted px-1 rounded whitespace-nowrap">{`{{${k}}}`}</code>
                      <input type="text" value={v} onChange={(e) => setPromptVars({ ...promptVars, [k]: e.target.value })} placeholder={`Value for ${k}`} className="border rounded px-2 py-1 text-sm flex-1" />
                    </div>
                  ))}
                </div>
              )}
              <div>
                <label className="text-sm font-medium">Prompt Text *</label>
                <textarea value={promptText} onChange={(e) => setPromptText(e.target.value)} placeholder="You are the {{role}} Agent for {{org_name}}..." className="border rounded px-3 py-2 text-sm w-full mt-1 font-mono" rows={12} />
                <p className="text-xs text-muted-foreground mt-1">{promptText.length} characters</p>
              </div>
            </>
          )}

          {/* Step 4: Behavior */}
          {step === 3 && (
            <>
              <div>
                <label className="text-sm font-medium">LLM Model</label>
                <select value={llmModel} onChange={(e) => setLlmModel(e.target.value)} className="border rounded px-3 py-2 text-sm w-full mt-1">
                  <option value="gemini-2.5-flash">Gemini 2.5 Flash (default)</option>
                  <option value="gemini-2.5-pro">Gemini 2.5 Pro</option>
                  <option value="claude-3-5-sonnet-20241022">Claude 3.5 Sonnet (requires API key)</option>
                  <option value="claude-opus-4-20250514">Claude Opus 4 (requires API key)</option>
                  <option value="gpt-4o">GPT-4o (requires API key)</option>
                  <option value="gpt-4o-mini">GPT-4o Mini (requires API key)</option>
                </select>
                <p className="text-xs text-muted-foreground mt-1">
                  {llmModel.includes("claude") || llmModel.includes("gpt")
                    ? "This model requires an API key. If not configured, the agent will fall back to Gemini."
                    : "Gemini is always available — no additional API key needed."}
                </p>
              </div>
              <div>
                <label className="text-sm font-medium">Confidence Floor: {(confidenceFloor * 100).toFixed(0)}%</label>
                <input type="range" min={0.5} max={0.99} step={0.01} value={confidenceFloor} onChange={(e) => setConfidenceFloor(Number(e.target.value))} className="w-full mt-1" />
                <p className="text-xs text-muted-foreground mt-1">Agent escalates to HITL when confidence drops below this threshold.</p>
              </div>
              <div>
                <label className="text-sm font-medium">HITL Condition</label>
                <input type="text" value={hitlCondition} onChange={(e) => setHitlCondition(e.target.value)} placeholder="confidence < 0.88 OR amount > 500000" className="border rounded px-3 py-2 text-sm w-full mt-1" />
              </div>
              <div>
                <label className="text-sm font-medium">Max Retries</label>
                <input type="number" min={1} max={10} value={maxRetries} onChange={(e) => setMaxRetries(Number(e.target.value))} className="border rounded px-3 py-2 text-sm w-24 mt-1" />
              </div>
              {/* Authorized Tools */}
              <div>
                <label className="text-sm font-medium">Authorized Tools</label>
                <p className="text-xs text-muted-foreground mb-2">
                  Tools this agent can call. Auto-populated based on agent type — add or remove as needed.
                </p>
                {authorizedTools.length > 0 ? (
                  <div className="space-y-2 mb-2">
                    <div className="flex flex-wrap gap-2">
                      {authorizedTools.map((tool) => (
                        <span key={tool} className="inline-flex items-center gap-1 bg-primary/10 text-primary rounded-full px-3 py-1 text-xs font-medium">
                          {humanize(tool)}
                          <PermissionBadge tool={tool} />
                          <span className="text-[9px] text-muted-foreground ml-1 font-mono">
                            tool:{domain}:{getToolPermission(tool).toLowerCase()}:{tool}
                          </span>
                          <button
                            type="button"
                            onClick={() => setAuthorizedTools(authorizedTools.filter((t) => t !== tool))}
                            className="ml-1 text-primary/60 hover:text-primary"
                            aria-label={`Remove ${tool}`}
                          >
                            &times;
                          </button>
                        </span>
                      ))}
                    </div>
                    {/* Minimal scope set summary */}
                    <div className="bg-muted/40 rounded p-2">
                      <p className="text-[10px] font-medium text-muted-foreground uppercase tracking-wide mb-1">Minimal Scope Set ({[...new Set(authorizedTools.map((t) => `tool:${domain}:${getToolPermission(t).toLowerCase()}:*`))].length} scopes)</p>
                      <div className="flex flex-wrap gap-1">
                        {[...new Set(authorizedTools.map((t) => `tool:${domain}:${getToolPermission(t).toLowerCase()}:*`))].map((scope) => (
                          <code key={scope} className="text-[10px] bg-muted px-1.5 py-0.5 rounded font-mono">{scope}</code>
                        ))}
                      </div>
                    </div>
                  </div>
                ) : (
                  <p className="text-xs text-amber-600 mb-2">No tools selected. Default tools will be assigned based on agent type.</p>
                )}
                <select
                  onChange={(e) => {
                    const tool = e.target.value;
                    if (tool && !authorizedTools.includes(tool)) {
                      setAuthorizedTools([...authorizedTools, tool]);
                    }
                    e.target.value = "";
                  }}
                  className="border rounded px-3 py-2 text-sm w-full"
                  defaultValue=""
                >
                  <option value="">+ Add a tool...</option>
                  {availableTools.filter((t) => !authorizedTools.includes(t)).map((t) => (
                    <option key={t} value={t}>{humanize(t)}</option>
                  ))}
                </select>
              </div>

              {authorizedTools.length === 0 && (
                <div className="rounded-lg bg-amber-50 border border-amber-200 px-4 py-3 text-sm text-amber-800">
                  <p className="font-medium">No tools selected</p>
                  <p className="text-xs mt-1">This agent won't be able to call any tools. Default tools will be assigned based on agent type, but you may want to explicitly select tools for better control.</p>
                </div>
              )}

              {authorizedTools.some((t) => getToolPermission(t) === "DELETE" || getToolPermission(t) === "ADMIN") && (
                <div className="rounded-lg bg-yellow-50 border border-yellow-300 px-4 py-3 text-sm text-yellow-800">
                  <p className="font-medium">Elevated permissions detected</p>
                  <p className="text-xs mt-1">
                    This agent has tools with <strong>DELETE</strong> or <strong>ADMIN</strong> permission levels.
                    These scopes allow destructive or privileged operations. Ensure this is intentional and review carefully before deploying.
                  </p>
                  <div className="flex flex-wrap gap-1.5 mt-2">
                    {authorizedTools
                      .filter((t) => getToolPermission(t) === "DELETE" || getToolPermission(t) === "ADMIN")
                      .map((t) => (
                        <span key={t} className="inline-flex items-center gap-1 text-xs">
                          {humanize(t)} <PermissionBadge tool={t} />
                        </span>
                      ))}
                  </div>
                </div>
              )}

              <div className="bg-muted/50 rounded-lg p-4 text-sm text-muted-foreground">
                <p>New agents start in <strong>Shadow Mode</strong>. They observe and produce outputs without taking actions. Promote to Active after validation.</p>
                <p className="mt-2">The agent will auto-register on <strong>Grantex</strong> with a unique DID and scoped token for A2A/MCP external access.</p>
              </div>
            </>
          )}

          {/* Step 5: Review */}
          {step === 4 && (
            <div className="space-y-3">
              <div className="flex items-center gap-4 bg-muted/30 rounded-lg p-4">
                {avatarUrl && /^https:\/\//i.test(avatarUrl) ? (
                  <img src={encodeURI(avatarUrl)} alt={employeeName} className="w-16 h-16 rounded-full object-cover" />
                ) : (
                  <div className="w-16 h-16 rounded-full bg-primary/10 flex items-center justify-center text-2xl font-bold text-primary">
                    {employeeName.charAt(0).toUpperCase()}
                  </div>
                )}
                <div>
                  <h3 className="text-lg font-semibold">{employeeName}</h3>
                  {designation && <p className="text-sm text-muted-foreground">{designation}</p>}
                  <div className="flex gap-2 mt-1">
                    <Badge>{humanize(domain)}</Badge>
                    <Badge variant="outline">{humanize(finalType)}</Badge>
                    <Badge variant="secondary">Shadow</Badge>
                  </div>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3 text-sm">
                <div><span className="text-muted-foreground">Agent Type:</span> {humanize(finalType)}</div>
                <div><span className="text-muted-foreground">Confidence Floor:</span> {(confidenceFloor * 100).toFixed(0)}%</div>
                <div><span className="text-muted-foreground">HITL Condition:</span> {hitlCondition}</div>
                <div><span className="text-muted-foreground">Max Retries:</span> {maxRetries}</div>
                <div><span className="text-muted-foreground">LLM Model:</span> {llmModel}</div>
                {reportingTo && <div><span className="text-muted-foreground">Reports To:</span> {reportingTo}</div>}
                {specialization && <div className="col-span-2"><span className="text-muted-foreground">Specialization:</span> {specialization}</div>}
                {Object.keys(routingFilter).length > 0 && (
                  <div className="col-span-2"><span className="text-muted-foreground">Routing:</span> {Object.entries(routingFilter).map(([k, v]) => `${k}=${v}`).join(", ")}</div>
                )}
              </div>

              {/* Authorized Tools Preview */}
              {authorizedTools.length > 0 && (
                <div>
                  <p className="text-sm font-medium mb-1">Authorized Tools ({authorizedTools.length})</p>
                  <div className="flex flex-wrap gap-1.5">
                    {authorizedTools.map((t) => (
                      <Badge key={t} variant="outline" className="text-xs inline-flex items-center gap-1">
                        {humanize(t)} <PermissionBadge tool={t} />
                      </Badge>
                    ))}
                  </div>
                </div>
              )}

              <div>
                <p className="text-sm font-medium mb-1">Prompt Preview ({promptText.length} chars)</p>
                <pre className="bg-muted rounded p-3 text-xs max-h-40 overflow-auto whitespace-pre-wrap">{resolvedPrompt().slice(0, 500)}{resolvedPrompt().length > 500 ? "..." : ""}</pre>
              </div>
            </div>
          )}

          {error && <p className="text-sm text-destructive">{error}</p>}

          {/* Navigation */}
          <div className="flex justify-between pt-4 border-t">
            <Button variant="outline" onClick={() => step > 0 ? setStep(step - 1) : navigate("/dashboard/agents")}>
              {step === 0 ? "Cancel" : "Back"}
            </Button>
            {step < 4 ? (
              <Button onClick={() => setStep(step + 1)} disabled={!canNext()}>
                Next
              </Button>
            ) : (
              <Button onClick={handleCreate} disabled={submitting}>
                {submitting ? "Creating..." : "Create as Shadow"}
              </Button>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
