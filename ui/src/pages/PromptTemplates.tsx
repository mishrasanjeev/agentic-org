import { useState, useEffect } from "react";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { promptTemplatesApi } from "@/lib/api";
import type { PromptTemplate } from "@/types";

function humanize(s: string) {
  return s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export default function PromptTemplates() {
  const [templates, setTemplates] = useState<PromptTemplate[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [domainFilter, setDomainFilter] = useState("");
  const [creating, setCreating] = useState(false);

  // Create form state
  const [newName, setNewName] = useState("");
  const [newType, setNewType] = useState("");
  const [newDomain, setNewDomain] = useState("finance");
  const [newText, setNewText] = useState("");
  const [newDesc, setNewDesc] = useState("");

  useEffect(() => { fetchTemplates(); }, [domainFilter]);

  async function fetchTemplates() {
    setLoading(true);
    try {
      const params: Record<string, string> = {};
      if (domainFilter) params.domain = domainFilter;
      const { data } = await promptTemplatesApi.list(params);
      setTemplates(Array.isArray(data) ? data : data.items || []);
    } catch { setTemplates([]); }
    finally { setLoading(false); }
  }

  async function handleCreate() {
    try {
      await promptTemplatesApi.create({
        name: newName.trim(), agent_type: newType.trim(), domain: newDomain,
        template_text: newText, description: newDesc.trim() || undefined,
      });
      setCreating(false);
      setNewName(""); setNewType(""); setNewText(""); setNewDesc("");
      fetchTemplates();
    } catch { /* ignore */ }
  }

  const selected = templates.find((t) => t.id === selectedId);

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <h2 className="text-2xl font-bold">Prompt Templates</h2>
        <Button onClick={() => setCreating(!creating)}>{creating ? "Cancel" : "Create Template"}</Button>
      </div>

      {/* Create Form */}
      {creating && (
        <Card>
          <CardHeader><CardTitle>New Prompt Template</CardTitle></CardHeader>
          <CardContent className="space-y-3">
            <div className="grid grid-cols-3 gap-3">
              <div>
                <label className="text-sm font-medium">Name *</label>
                <input type="text" value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="e.g. custom_ap" className="border rounded px-3 py-2 text-sm w-full mt-1" />
              </div>
              <div>
                <label className="text-sm font-medium">Agent Type *</label>
                <input type="text" value={newType} onChange={(e) => setNewType(e.target.value)} placeholder="e.g. ap_processor" className="border rounded px-3 py-2 text-sm w-full mt-1" />
              </div>
              <div>
                <label className="text-sm font-medium">Domain</label>
                <select value={newDomain} onChange={(e) => setNewDomain(e.target.value)} className="border rounded px-3 py-2 text-sm w-full mt-1">
                  {["finance", "hr", "marketing", "ops", "backoffice"].map((d) => <option key={d} value={d}>{humanize(d)}</option>)}
                </select>
              </div>
            </div>
            <div>
              <label className="text-sm font-medium">Description</label>
              <input type="text" value={newDesc} onChange={(e) => setNewDesc(e.target.value)} placeholder="Brief description" className="border rounded px-3 py-2 text-sm w-full mt-1" />
            </div>
            <div>
              <label className="text-sm font-medium">Template Text *</label>
              <textarea value={newText} onChange={(e) => setNewText(e.target.value)} placeholder="You are the {{role}} Agent for {{org_name}}..." className="border rounded px-3 py-2 text-sm w-full mt-1 font-mono" rows={10} />
            </div>
            <Button onClick={handleCreate} disabled={!newName.trim() || !newType.trim() || !newText.trim()}>Create Template</Button>
          </CardContent>
        </Card>
      )}

      {/* Filters */}
      <div className="flex gap-3">
        <select value={domainFilter} onChange={(e) => setDomainFilter(e.target.value)} className="border rounded px-3 py-2 text-sm">
          <option value="">All Domains</option>
          {["finance", "hr", "marketing", "ops", "backoffice"].map((d) => <option key={d} value={d}>{humanize(d)}</option>)}
        </select>
        <span className="text-sm text-muted-foreground self-center">{templates.length} templates</span>
      </div>

      {/* Template List */}
      {loading ? (
        <p className="text-muted-foreground">Loading...</p>
      ) : (
        <div className="grid grid-cols-1 gap-3">
          {templates.map((t) => (
            <Card key={t.id} className={`cursor-pointer hover:shadow-md transition-shadow ${selectedId === t.id ? "ring-2 ring-primary" : ""}`} onClick={() => setSelectedId(selectedId === t.id ? null : t.id)}>
              <CardContent className="pt-4">
                <div className="flex justify-between items-center">
                  <div>
                    <p className="font-medium">{humanize(t.name)}</p>
                    <p className="text-xs text-muted-foreground">{humanize(t.agent_type)} | {humanize(t.domain)}</p>
                  </div>
                  <div className="flex gap-2">
                    {t.is_builtin && <Badge variant="outline">Built-in</Badge>}
                    <Badge variant="secondary">{t.template_text.length} chars</Badge>
                  </div>
                </div>
                {t.description && <p className="text-sm text-muted-foreground mt-1">{t.description}</p>}
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {/* Selected Template Preview */}
      {selected && (
        <Card>
          <CardHeader>
            <div className="flex justify-between items-center">
              <CardTitle>{humanize(selected.name)}</CardTitle>
              {selected.is_builtin && <Badge variant="outline">Built-in (read-only)</Badge>}
            </div>
          </CardHeader>
          <CardContent>
            <pre className="bg-muted rounded p-4 text-xs font-mono whitespace-pre-wrap max-h-96 overflow-auto">
              {selected.template_text}
            </pre>
            {selected.variables && selected.variables.length > 0 && (
              <div className="mt-3">
                <p className="text-sm font-medium">Variables</p>
                <div className="flex gap-2 mt-1 flex-wrap">
                  {selected.variables.map((v) => (
                    <Badge key={v.name} variant="outline">{`{{${v.name}}}`}</Badge>
                  ))}
                </div>
              </div>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
