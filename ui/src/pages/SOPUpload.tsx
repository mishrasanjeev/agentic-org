import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import api, { extractApiError } from "@/lib/api";

const DOMAINS = ["finance", "hr", "marketing", "ops", "backoffice"];

interface ParsedStep {
  step_number: number;
  name: string;
  description: string;
  required_tools: string[];
  hitl_required: boolean;
  hitl_condition: string;
}

interface ParsedConfig {
  agent_name: string;
  agent_type: string;
  domain: string;
  description: string;
  steps: ParsedStep[];
  required_tools: string[];
  hitl_conditions: string[];
  confidence_floor: number;
  escalation_chain: string[];
  suggested_prompt: string;
  _unknown_tools?: string[];
  _parse_status?: string;
  parse_error?: string;
}

export default function SOPUpload() {
  const navigate = useNavigate();
  const [mode, setMode] = useState<"upload" | "paste">("upload");
  const [file, setFile] = useState<File | null>(null);
  const [pastedText, setPastedText] = useState("");
  const [domainHint, setDomainHint] = useState("");
  const [parsing, setParsing] = useState(false);
  const [deploying, setDeploying] = useState(false);
  const [error, setError] = useState("");
  const [parsedConfig, setParsedConfig] = useState<ParsedConfig | null>(null);
  const [editMode, setEditMode] = useState(false);

  async function handleParse() {
    setError("");
    setParsing(true);
    setParsedConfig(null);
    try {
      let response;
      if (mode === "upload" && file) {
        const formData = new FormData();
        formData.append("file", file);
        if (domainHint) formData.append("domain_hint", domainHint);
        response = await api.post("/sop/upload", formData, {
          headers: { "Content-Type": "multipart/form-data" },
        });
      } else if (mode === "paste" && pastedText.trim()) {
        response = await api.post("/sop/parse-text", {
          text: pastedText,
          domain_hint: domainHint,
        });
      } else {
        setError(mode === "upload" ? "Please select a file" : "Please enter SOP text");
        return;
      }
      setParsedConfig(response.data.config);
    } catch (e: unknown) {
      setError(extractApiError(e, "Failed to parse document. Please try again."));
    } finally {
      setParsing(false);
    }
  }

  async function handleDeploy() {
    if (!parsedConfig) return;
    setError("");
    setDeploying(true);
    try {
      const { data } = await api.post("/sop/deploy", { config: parsedConfig });
      navigate(`/dashboard/agents/${data.agent_id}`);
    } catch (e: unknown) {
      setError(extractApiError(e, "Failed to deploy agent."));
    } finally {
      setDeploying(false);
    }
  }

  return (
    <div className="space-y-6 max-w-4xl mx-auto">
      <div className="flex justify-between items-center">
        <div>
          <h2 className="text-2xl font-bold">Create Agent from SOP</h2>
          <p className="text-sm text-muted-foreground mt-1">
            Upload a business process document and we'll generate an AI agent configuration
          </p>
        </div>
        <Button variant="outline" onClick={() => navigate("/dashboard/agents")}>Back to Agents</Button>
      </div>

      {/* Step 1: Input */}
      {!parsedConfig && (
        <Card>
          <CardHeader>
            <CardTitle>Step 1: Provide SOP Document</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex gap-2 border-b pb-2">
              <button onClick={() => setMode("upload")} className={`px-3 py-1 text-sm font-medium ${mode === "upload" ? "border-b-2 border-primary" : "text-muted-foreground"}`}>
                Upload File
              </button>
              <button onClick={() => setMode("paste")} className={`px-3 py-1 text-sm font-medium ${mode === "paste" ? "border-b-2 border-primary" : "text-muted-foreground"}`}>
                Paste Text
              </button>
            </div>

            {mode === "upload" ? (
              <div>
                <label className="text-sm font-medium">Document (PDF, Markdown, or Text)</label>
                <input
                  type="file"
                  accept=".pdf,.md,.txt,.markdown"
                  onChange={(e) => setFile(e.target.files?.[0] || null)}
                  className="border rounded px-3 py-2 text-sm w-full mt-1"
                />
                {file && <p className="text-xs text-muted-foreground mt-1">{file.name} ({(file.size / 1024).toFixed(0)} KB)</p>}
              </div>
            ) : (
              <div>
                <label className="text-sm font-medium">SOP Text</label>
                <textarea
                  value={pastedText}
                  onChange={(e) => setPastedText(e.target.value)}
                  placeholder="Paste your Standard Operating Procedure here..."
                  className="border rounded px-3 py-2 text-sm w-full mt-1 font-mono"
                  rows={12}
                />
                <p className="text-xs text-muted-foreground mt-1">{pastedText.length} characters</p>
              </div>
            )}

            <div>
              <label className="text-sm font-medium">Domain Hint (optional)</label>
              <select value={domainHint} onChange={(e) => setDomainHint(e.target.value)} className="border rounded px-3 py-2 text-sm w-full mt-1">
                <option value="">Auto-detect</option>
                {DOMAINS.map((d) => <option key={d} value={d}>{d.charAt(0).toUpperCase() + d.slice(1)}</option>)}
              </select>
            </div>

            {error && <p className="text-sm text-destructive">{error}</p>}

            <Button onClick={handleParse} disabled={parsing}>
              {parsing ? "Parsing document..." : "Parse SOP"}
            </Button>
          </CardContent>
        </Card>
      )}

      {/* Step 2: Review */}
      {parsedConfig && (
        <>
          <Card>
            <CardHeader>
              <div className="flex justify-between items-center">
                <CardTitle>Step 2: Review Agent Configuration</CardTitle>
                <div className="flex gap-2">
                  <Badge variant={parsedConfig.parse_error ? "destructive" : "default"}>
                    {parsedConfig.parse_error ? "Needs Editing" : "Ready for Review"}
                  </Badge>
                  <Button variant="outline" size="sm" onClick={() => setEditMode(!editMode)}>
                    {editMode ? "Done Editing" : "Edit"}
                  </Button>
                </div>
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              {parsedConfig.parse_error && (
                <div className="rounded-lg bg-amber-50 border border-amber-200 px-4 py-3 text-sm text-amber-800">
                  {parsedConfig.parse_error}
                </div>
              )}

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="text-xs text-muted-foreground">Agent Name</label>
                  {editMode ? (
                    <input type="text" value={parsedConfig.agent_name} onChange={(e) => setParsedConfig({...parsedConfig, agent_name: e.target.value})} className="border rounded px-2 py-1 text-sm w-full" />
                  ) : (
                    <p className="font-medium">{parsedConfig.agent_name}</p>
                  )}
                </div>
                <div>
                  <label className="text-xs text-muted-foreground">Agent Type</label>
                  {editMode ? (
                    <input type="text" value={parsedConfig.agent_type} onChange={(e) => setParsedConfig({...parsedConfig, agent_type: e.target.value})} className="border rounded px-2 py-1 text-sm w-full" />
                  ) : (
                    <p className="font-medium font-mono">{parsedConfig.agent_type}</p>
                  )}
                </div>
                <div>
                  <label className="text-xs text-muted-foreground">Domain</label>
                  {editMode ? (
                    <select value={parsedConfig.domain} onChange={(e) => setParsedConfig({...parsedConfig, domain: e.target.value})} className="border rounded px-2 py-1 text-sm w-full">
                      {DOMAINS.map((d) => <option key={d} value={d}>{d}</option>)}
                    </select>
                  ) : (
                    <p className="font-medium">{parsedConfig.domain}</p>
                  )}
                </div>
                <div>
                  <label className="text-xs text-muted-foreground">Confidence Floor</label>
                  {editMode ? (
                    <input type="number" step="0.01" min="0.5" max="0.99" value={parsedConfig.confidence_floor} onChange={(e) => setParsedConfig({...parsedConfig, confidence_floor: Number(e.target.value)})} className="border rounded px-2 py-1 text-sm w-full" />
                  ) : (
                    <p className="font-medium">{(parsedConfig.confidence_floor * 100).toFixed(0)}%</p>
                  )}
                </div>
              </div>

              <div>
                <label className="text-xs text-muted-foreground">Description</label>
                <p className="text-sm">{parsedConfig.description}</p>
              </div>

              {/* Steps */}
              <div>
                <label className="text-xs text-muted-foreground uppercase tracking-wide">Process Steps ({parsedConfig.steps.length})</label>
                <div className="space-y-2 mt-2">
                  {parsedConfig.steps.map((step) => (
                    <div key={step.step_number} className="border rounded-lg p-3 text-sm">
                      <div className="flex items-center gap-2 mb-1">
                        <span className="bg-primary text-primary-foreground rounded-full w-6 h-6 flex items-center justify-center text-xs font-bold">{step.step_number}</span>
                        <span className="font-medium">{step.name}</span>
                        {step.hitl_required && <Badge variant="warning">HITL Required</Badge>}
                      </div>
                      <p className="text-muted-foreground ml-8">{step.description}</p>
                      {step.required_tools.length > 0 && (
                        <div className="flex flex-wrap gap-1 ml-8 mt-1">
                          {step.required_tools.map((t) => (
                            <Badge key={t} variant="outline" className="text-xs">{t}</Badge>
                          ))}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </div>

              {/* Tools */}
              <div>
                <label className="text-xs text-muted-foreground uppercase tracking-wide">Required Tools ({parsedConfig.required_tools.length})</label>
                <div className="flex flex-wrap gap-2 mt-2">
                  {parsedConfig.required_tools.map((t) => (
                    <Badge key={t} variant={parsedConfig._unknown_tools?.includes(t) ? "destructive" : "default"}>{t}</Badge>
                  ))}
                </div>
                {parsedConfig._unknown_tools && parsedConfig._unknown_tools.length > 0 && (
                  <p className="text-xs text-destructive mt-1">
                    {parsedConfig._unknown_tools.length} tool(s) not found in connector registry
                  </p>
                )}
              </div>

              {/* HITL Conditions */}
              {parsedConfig.hitl_conditions.length > 0 && (
                <div>
                  <label className="text-xs text-muted-foreground uppercase tracking-wide">HITL Conditions</label>
                  <ul className="list-disc list-inside text-sm mt-1">
                    {parsedConfig.hitl_conditions.map((c, i) => <li key={i}>{c}</li>)}
                  </ul>
                </div>
              )}

              {/* Escalation */}
              {parsedConfig.escalation_chain.length > 0 && (
                <div>
                  <label className="text-xs text-muted-foreground uppercase tracking-wide">Escalation Chain</label>
                  <p className="text-sm mt-1">{parsedConfig.escalation_chain.join(" -> ")}</p>
                </div>
              )}

              {/* Prompt Preview */}
              <div>
                <label className="text-xs text-muted-foreground uppercase tracking-wide">Generated Prompt</label>
                {editMode ? (
                  <textarea
                    value={parsedConfig.suggested_prompt}
                    onChange={(e) => setParsedConfig({...parsedConfig, suggested_prompt: e.target.value})}
                    className="border rounded px-3 py-2 text-sm w-full mt-1 font-mono"
                    rows={10}
                  />
                ) : (
                  <pre className="bg-muted rounded p-3 text-xs max-h-48 overflow-auto mt-1 whitespace-pre-wrap">
                    {parsedConfig.suggested_prompt.slice(0, 1000)}{parsedConfig.suggested_prompt.length > 1000 ? "..." : ""}
                  </pre>
                )}
              </div>
            </CardContent>
          </Card>

          {error && <p className="text-sm text-destructive">{error}</p>}

          <div className="flex gap-3">
            <Button onClick={handleDeploy} disabled={deploying}>
              {deploying ? "Deploying..." : "Deploy as Shadow Agent"}
            </Button>
            <Button variant="outline" onClick={() => { setParsedConfig(null); setError(""); }}>
              Start Over
            </Button>
          </div>
        </>
      )}
    </div>
  );
}
