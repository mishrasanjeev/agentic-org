import { useState, useEffect, useCallback } from "react";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import api from "@/lib/api";

/* ------------------------------------------------------------------ */
/*  Types — aligned with the backend GET /rpa/scripts response         */
/* ------------------------------------------------------------------ */

interface ParamSchema {
  type: string;
  label: string;
  required?: boolean;
}

interface RPAScript {
  id: string;
  name: string;
  description: string;
  category: string;
  script_key: string;
  params_schema: Record<string, ParamSchema>;
  estimated_duration_s: number;
  is_builtin: boolean;
}

interface RPAExecution {
  id: string;
  script_key: string;
  script_name: string;
  status: "running" | "completed" | "failed";
  started_at: string;
  completed_at: string | null;
  elapsed_ms: number;
  success: boolean;
  error: string | null;
}

const STATUS_BADGE: Record<string, "success" | "warning" | "destructive" | "secondary"> = {
  ready: "secondary",
  running: "warning",
  completed: "success",
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
          ? Array.isArray(scriptsRes.value.data)
            ? scriptsRes.value.data
            : scriptsRes.value.data?.items || []
          : [];
      const h =
        historyRes.status === "fulfilled"
          ? Array.isArray(historyRes.value.data)
            ? historyRes.value.data
            : historyRes.value.data?.items || []
          : [];

      setScripts(s);
      setHistory(h);
    } catch {
      setScripts([]);
      setHistory([]);
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
    Object.keys(script.params_schema || {}).forEach((key) => {
      defaults[key] = "";
    });
    setParamValues(defaults);
  };

  const handleRun = async () => {
    if (!dialogScript) return;
    setRunningId(dialogScript.id);
    setDialogScript(null);
    try {
      await api.post(`/rpa/scripts/${dialogScript.id}/run`, { params: paramValues });
      await fetchData();
    } catch {
      await fetchData();
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
        <Button variant="outline" onClick={fetchData}>
          Refresh
        </Button>
      </div>

      {/* Script cards */}
      {scripts.length === 0 ? (
        <p className="text-muted-foreground text-sm">
          No RPA scripts available. Scripts are discovered from the
          <code className="mx-1 bg-muted px-1 rounded">rpa/scripts/</code>
          directory on the server.
        </p>
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {scripts.map((s) => (
            <Card key={s.id}>
              <CardHeader className="pb-2">
                <div className="flex items-start justify-between">
                  <div>
                    <CardTitle className="text-base">{s.name}</CardTitle>
                    <p className="text-xs text-muted-foreground mt-1">
                      {s.description}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    <Badge variant="secondary">{s.category}</Badge>
                    {s.is_builtin && (
                      <Badge variant="outline" className="text-[10px]">
                        built-in
                      </Badge>
                    )}
                  </div>
                </div>
              </CardHeader>
              <CardContent>
                <div className="flex items-center justify-between">
                  <span className="text-xs text-muted-foreground">
                    ~{s.estimated_duration_s}s •{" "}
                    {Object.keys(s.params_schema || {}).length} params
                  </span>
                  <Button
                    size="sm"
                    onClick={() => openRunDialog(s)}
                    disabled={runningId === s.id}
                  >
                    {runningId === s.id ? "Running..." : "Run"}
                  </Button>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {/* Run dialog */}
      {dialogScript && (
        <div
          className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center"
          onClick={() => setDialogScript(null)}
        >
          <div
            className="bg-background border rounded-lg p-6 w-full max-w-md shadow-lg"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="text-lg font-bold mb-1">Run: {dialogScript.name}</h3>
            <p className="text-sm text-muted-foreground mb-4">
              {dialogScript.description}
            </p>
            <div className="space-y-3">
              {Object.entries(dialogScript.params_schema || {}).map(
                ([key, schema]) => (
                  <div key={key}>
                    <label className="block text-sm font-medium mb-1">
                      {schema.label || key}
                      {schema.required && (
                        <span className="text-destructive ml-1">*</span>
                      )}
                    </label>
                    <input
                      type={schema.type === "password" ? "password" : "text"}
                      placeholder={schema.label || key}
                      value={paramValues[key] || ""}
                      onChange={(e) =>
                        setParamValues((prev) => ({
                          ...prev,
                          [key]: e.target.value,
                        }))
                      }
                      className="w-full border rounded px-3 py-2 text-sm"
                    />
                  </div>
                )
              )}
            </div>
            <div className="flex justify-end gap-2 mt-4">
              <Button variant="outline" onClick={() => setDialogScript(null)}>
                Cancel
              </Button>
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
                    <CardTitle className="text-sm font-medium">
                      {exec.script_name}
                    </CardTitle>
                    <div className="flex items-center gap-2">
                      <Badge variant={STATUS_BADGE[exec.status] || "secondary"}>
                        {exec.status}
                      </Badge>
                      {exec.elapsed_ms > 0 && (
                        <span className="text-xs text-muted-foreground">
                          {(exec.elapsed_ms / 1000).toFixed(1)}s
                        </span>
                      )}
                    </div>
                  </div>
                </CardHeader>
                <CardContent>
                  <p className="text-xs text-muted-foreground">
                    {new Date(exec.started_at).toLocaleString()}
                    {exec.completed_at &&
                      ` — ${new Date(exec.completed_at).toLocaleString()}`}
                  </p>
                  {exec.error && (
                    <p className="text-sm text-destructive mt-1">{exec.error}</p>
                  )}
                </CardContent>
              </Card>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
