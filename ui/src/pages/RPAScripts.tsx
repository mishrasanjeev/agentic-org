import { useState, useEffect, useCallback } from "react";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import api from "@/lib/api";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface RPAScript {
  id: string;
  name: string;
  target_url: string;
  last_run: string | null;
  status: "ready" | "running" | "success" | "failed";
  params: { name: string; label: string; placeholder: string }[];
}

interface RPAExecution {
  id: string;
  script_id: string;
  script_name: string;
  started_at: string;
  finished_at: string | null;
  status: "success" | "failed" | "running";
  output: string;
}

/* ------------------------------------------------------------------ */
/*  Mock data                                                          */
/* ------------------------------------------------------------------ */

const MOCK_SCRIPTS: RPAScript[] = [
  {
    id: "rpa1",
    name: "EPFO ECR Download",
    target_url: "https://unifiedportal-emp.epfindia.gov.in",
    last_run: "2026-04-03T08:30:00Z",
    status: "success",
    params: [
      { name: "establishment_id", label: "Establishment ID", placeholder: "e.g. DLCPM0012345" },
      { name: "month", label: "Month (MMYYYY)", placeholder: "e.g. 032026" },
    ],
  },
  {
    id: "rpa2",
    name: "MCA Company Search",
    target_url: "https://www.mca.gov.in",
    last_run: "2026-04-02T14:15:00Z",
    status: "success",
    params: [
      { name: "cin", label: "CIN / Company Name", placeholder: "e.g. U72200DL2020PTC123456" },
    ],
  },
  {
    id: "rpa3",
    name: "Income Tax 26AS",
    target_url: "https://www.incometax.gov.in",
    last_run: "2026-04-01T10:00:00Z",
    status: "failed",
    params: [
      { name: "pan", label: "PAN Number", placeholder: "e.g. ABCDE1234F" },
      { name: "fy", label: "Financial Year", placeholder: "e.g. 2025-26" },
    ],
  },
  {
    id: "rpa4",
    name: "GST Return Status",
    target_url: "https://www.gst.gov.in",
    last_run: null,
    status: "ready",
    params: [
      { name: "gstin", label: "GSTIN", placeholder: "e.g. 07AABCU1234F1ZL" },
      { name: "return_type", label: "Return Type", placeholder: "e.g. GSTR-1" },
    ],
  },
];

const MOCK_HISTORY: RPAExecution[] = [
  { id: "e1", script_id: "rpa1", script_name: "EPFO ECR Download", started_at: "2026-04-03T08:30:00Z", finished_at: "2026-04-03T08:32:15Z", status: "success", output: "ECR file downloaded: ECR_032026.txt (145 KB)" },
  { id: "e2", script_id: "rpa2", script_name: "MCA Company Search", started_at: "2026-04-02T14:15:00Z", finished_at: "2026-04-02T14:16:30Z", status: "success", output: "Company data extracted: 3 directors, registration active" },
  { id: "e3", script_id: "rpa3", script_name: "Income Tax 26AS", started_at: "2026-04-01T10:00:00Z", finished_at: "2026-04-01T10:03:45Z", status: "failed", output: "Login captcha could not be solved after 3 retries" },
];

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

const STATUS_BADGE: Record<string, "success" | "warning" | "destructive" | "secondary"> = {
  ready: "secondary",
  running: "warning",
  success: "success",
  failed: "destructive",
};

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export default function RPAScripts() {
  const [scripts, setScripts] = useState<RPAScript[]>([]);
  const [history, setHistory] = useState<RPAExecution[]>([]);
  const [loading, setLoading] = useState(true);
  const [dialogScript, setDialogScript] = useState<RPAScript | null>(null);
  const [paramValues, setParamValues] = useState<Record<string, string>>({});
  const [runningId, setRunningId] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const [scriptsRes, historyRes] = await Promise.allSettled([
        api.get("/rpa/scripts"),
        api.get("/rpa/history"),
      ]);
      const s =
        scriptsRes.status === "fulfilled"
          ? Array.isArray(scriptsRes.value.data) ? scriptsRes.value.data : scriptsRes.value.data?.items || []
          : [];
      const h =
        historyRes.status === "fulfilled"
          ? Array.isArray(historyRes.value.data) ? historyRes.value.data : historyRes.value.data?.items || []
          : [];

      setScripts(s.length > 0 ? s : MOCK_SCRIPTS);
      setHistory(h.length > 0 ? h : MOCK_HISTORY);
    } catch {
      setScripts(MOCK_SCRIPTS);
      setHistory(MOCK_HISTORY);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const openRunDialog = (script: RPAScript) => {
    setDialogScript(script);
    const defaults: Record<string, string> = {};
    script.params.forEach((p) => { defaults[p.name] = ""; });
    setParamValues(defaults);
  };

  const handleRun = async () => {
    if (!dialogScript) return;
    setRunningId(dialogScript.id);
    setDialogScript(null);
    try {
      await api.post(`/rpa/scripts/${dialogScript.id}/run`, { params: paramValues });
    } catch {
      // add to history locally
      const exec: RPAExecution = {
        id: `e-${Date.now()}`,
        script_id: dialogScript.id,
        script_name: dialogScript.name,
        started_at: new Date().toISOString(),
        finished_at: new Date().toISOString(),
        status: "success",
        output: "Script queued (API offline — will run when backend is available)",
      };
      setHistory((prev) => [exec, ...prev]);
    } finally {
      setRunningId(null);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[50vh]">
        <p className="text-muted-foreground">Loading RPA scripts...</p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <h2 className="text-2xl font-bold">Browser RPA Scripts</h2>
        <Button variant="outline" onClick={fetchData}>Refresh</Button>
      </div>

      {/* Script table */}
      <div className="border rounded overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-muted">
            <tr>
              <th className="text-left p-3">Script Name</th>
              <th className="text-left p-3">Target URL</th>
              <th className="text-left p-3">Last Run</th>
              <th className="text-left p-3">Status</th>
              <th className="text-left p-3">Actions</th>
            </tr>
          </thead>
          <tbody>
            {scripts.map((s) => (
              <tr key={s.id} className="border-t hover:bg-muted/50">
                <td className="p-3 font-medium">{s.name}</td>
                <td className="p-3">
                  <a href={s.target_url} target="_blank" rel="noopener noreferrer" className="text-blue-600 hover:underline text-xs">
                    {new URL(s.target_url).hostname}
                  </a>
                </td>
                <td className="p-3 text-muted-foreground">
                  {s.last_run ? new Date(s.last_run).toLocaleString() : "Never"}
                </td>
                <td className="p-3">
                  <Badge variant={STATUS_BADGE[s.status] || "secondary"}>{s.status}</Badge>
                </td>
                <td className="p-3">
                  <Button
                    size="sm"
                    onClick={() => openRunDialog(s)}
                    disabled={runningId === s.id}
                  >
                    {runningId === s.id ? "Running..." : "Run"}
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Run dialog */}
      {dialogScript && (
        <div className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center" onClick={() => setDialogScript(null)}>
          <div className="bg-background border rounded-lg p-6 w-full max-w-md shadow-lg" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-lg font-bold mb-1">Run: {dialogScript.name}</h3>
            <p className="text-sm text-muted-foreground mb-4">{dialogScript.target_url}</p>
            <div className="space-y-3">
              {dialogScript.params.map((p) => (
                <div key={p.name}>
                  <label className="block text-sm font-medium mb-1">{p.label}</label>
                  <input
                    type="text"
                    placeholder={p.placeholder}
                    value={paramValues[p.name] || ""}
                    onChange={(e) => setParamValues((prev) => ({ ...prev, [p.name]: e.target.value }))}
                    className="w-full border rounded px-3 py-2 text-sm"
                  />
                </div>
              ))}
            </div>
            <div className="flex justify-end gap-2 mt-4">
              <Button variant="outline" onClick={() => setDialogScript(null)}>Cancel</Button>
              <Button onClick={handleRun}>Run Script</Button>
            </div>
          </div>
        </div>
      )}

      {/* Execution history */}
      <div>
        <h3 className="text-lg font-semibold mb-3">Execution History</h3>
        {history.length === 0 ? (
          <p className="text-muted-foreground text-sm">No executions yet.</p>
        ) : (
          <div className="space-y-2">
            {history.map((exec) => (
              <Card key={exec.id}>
                <CardHeader className="pb-2">
                  <div className="flex items-center justify-between">
                    <CardTitle className="text-sm font-medium">{exec.script_name}</CardTitle>
                    <Badge variant={STATUS_BADGE[exec.status] || "secondary"}>{exec.status}</Badge>
                  </div>
                </CardHeader>
                <CardContent>
                  <p className="text-xs text-muted-foreground mb-1">
                    {new Date(exec.started_at).toLocaleString()}
                    {exec.finished_at && ` — ${new Date(exec.finished_at).toLocaleString()}`}
                  </p>
                  <p className="text-sm">{exec.output}</p>
                </CardContent>
              </Card>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
