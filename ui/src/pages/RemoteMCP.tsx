// SPDX-License-Identifier: Apache-2.0
import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router";
import { RefreshCw, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { RemoteConnector } from "@/components/RemoteMCPToolPicker";
import api, { extractApiError } from "@/lib/api";

function Connection({ item, reload }: { item: RemoteConnector; reload: () => Promise<void> }) {
  const [reads, setReads] = useState(item.tools.filter((tool) => tool.permission === "read").map((tool) => tool.name));
  const [token, setToken] = useState("");
  const [tool, setTool] = useState("");
  const [argumentsText, setArgumentsText] = useState("{}");
  const [message, setMessage] = useState("");
  const [result, setResult] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirmArchive, setConfirmArchive] = useState(false);
  useEffect(() => { setReads(item.tools.filter((entry) => entry.permission === "read").map((entry) => entry.name)); }, [item]);
  async function action(work: () => Promise<unknown>, success: string) {
    setBusy(true); setMessage(""); setResult("");
    try { await work(); setMessage(success); await reload(); }
    catch (err: unknown) { setMessage(extractApiError(err, "Connection operation failed")); }
    finally { setBusy(false); }
  }
  return <Card>
    <CardHeader><CardTitle className="text-base break-words">{item.name}</CardTitle>
      <p className="text-xs text-muted-foreground break-all">{item.base_url}</p></CardHeader>
    <CardContent className="space-y-4">
      <div className="space-y-3">
        {item.tools.map((entry) => <div key={entry.name} className="text-sm"><label className="flex items-start gap-3">
          <input type="checkbox" className="mt-1" aria-label={`Approve read-only ${entry.name}`}
            disabled={!item.can_manage || !entry.read_only_hint || /(?:^|_)(send|reply|create|update|delete|pay|execute|remove|post)(?:_|$)/.test(entry.name.toLowerCase())}
            checked={reads.includes(entry.name)} onChange={(event) => setReads(event.target.checked ? [...reads, entry.name] : reads.filter((name) => name !== entry.name))} />
          <span className="min-w-0"><span className="font-medium break-words">{entry.name}</span>
            <span className="block text-xs text-muted-foreground">{entry.permission === "read" ? "Reviewed read" : "Write / approval required"}</span>
            <span className="block break-words">{entry.description}</span></span>
        </label><details className="ml-6 mt-1"><summary className="cursor-pointer text-xs">Input schema</summary>
          <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-all border p-2 text-xs">{JSON.stringify(entry.inputSchema, null, 2)}</pre>
        </details></div>)}
      </div>
      {item.can_manage && <>
        <Button disabled={busy} onClick={() => action(() => api.put(`/connectors/mcp/${item.id}/permissions`, {
          read_only_tools: reads, schema_hashes: Object.fromEntries(item.tools.map((entry) => [entry.name, entry.schema_hash])),
        }), "Tool review saved")}>Save read-only review</Button>
        <div className="flex flex-wrap items-end gap-2 border-t pt-4">
          <label className="flex-1 min-w-48 text-sm">Replacement bearer token (optional)
            <input type="password" autoComplete="new-password" value={token} onChange={(event) => setToken(event.target.value)} className="mt-1 w-full rounded border px-3 py-2" />
          </label>
          <Button disabled={busy} title="Refresh discovery and rotate token" aria-label={`Refresh ${item.name}`} onClick={() => action(async () => {
            await api.post(`/connectors/mcp/${item.id}/refresh`, token ? { access_token: token } : {}); setToken("");
          }, "Discovery refreshed")}><RefreshCw size={16} /></Button>
          <Button variant="outline" title="Archive connection" aria-label={`Archive ${item.name}`} disabled={busy} onClick={() => setConfirmArchive(true)}><Trash2 size={16} /></Button>
        </div>
        {confirmArchive && <div role="alert" className="flex items-center gap-3 text-sm">
          Archive this connection and prevent further calls?
          <Button disabled={busy} onClick={() => action(() => api.delete(`/connectors/${item.id}`), "Connection archived")}>Archive</Button>
          <Button variant="outline" onClick={() => setConfirmArchive(false)}>Cancel</Button>
        </div>}
        <div className="grid gap-2 border-t pt-4">
          <label className="text-sm">Read-only connection probe
            <select value={tool} onChange={(event) => setTool(event.target.value)} className="mt-1 w-full rounded border px-3 py-2">
              <option value="">Choose a reviewed tool</option>
              {item.tools.filter((entry) => entry.permission === "read").map((entry) => <option key={entry.name} value={entry.name}>{entry.name}</option>)}
            </select>
          </label>
          <label className="text-sm">Arguments (JSON)
            <textarea rows={3} value={argumentsText} onChange={(event) => setArgumentsText(event.target.value)} className="mt-1 w-full rounded border px-3 py-2 font-mono" />
          </label>
          <Button disabled={busy || !tool} onClick={() => action(async () => {
            const args: unknown = JSON.parse(argumentsText);
            if (!args || typeof args !== "object" || Array.isArray(args)) throw new Error("Arguments must be a JSON object");
            const { data } = await api.post(`/connectors/mcp/${item.id}/probe`, { tool, arguments: args });
            setResult(JSON.stringify(data.result, null, 2));
          }, "Read-only probe completed")}>Run read-only probe</Button>
        </div>
      </>}
      {message && <p role="status" className="text-sm break-words">{message}</p>}
      {result && <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words rounded border p-3 text-xs">{result}</pre>}
    </CardContent>
  </Card>;
}

export default function RemoteMCP() {
  const [items, setItems] = useState<RemoteConnector[]>([]);
  const [name, setName] = useState("mcp_");
  const [url, setUrl] = useState("");
  const [token, setToken] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function reload() { const { data } = await api.get<{ items: RemoteConnector[] }>("/connectors/mcp"); setItems(data.items); }
  useEffect(() => { reload().catch((err: unknown) => setError(extractApiError(err, "Could not load MCP connections"))); }, []);
  async function register(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    try { await api.post("/connectors/mcp", { name, url, access_token: token }); setToken(""); await reload(); }
    catch (err: unknown) { setError(extractApiError(err, "Could not register MCP server")); }
    finally { setBusy(false); }
  }
  return <div className="mx-auto max-w-5xl space-y-6">
    <div className="flex flex-wrap items-center justify-between gap-3"><h1 className="text-2xl font-semibold">Remote MCP connections</h1><Link to="/dashboard/connectors" className="text-sm underline">All connectors</Link></div>
    <form onSubmit={register} className="grid gap-4 border-b pb-6 sm:grid-cols-2">
      <label className="text-sm">Connection name<input required pattern="mcp_[a-z][a-z0-9_]{0,22}" value={name} onChange={(event) => setName(event.target.value)} className="mt-1 w-full rounded border px-3 py-2" /></label>
      <label className="text-sm">HTTPS MCP endpoint<input required type="url" value={url} placeholder="https://tools.example.com/mcp" onChange={(event) => setUrl(event.target.value)} className="mt-1 w-full rounded border px-3 py-2" /></label>
      <label className="text-sm">Bearer token<input required type="password" autoComplete="new-password" value={token} onChange={(event) => setToken(event.target.value)} className="mt-1 w-full rounded border px-3 py-2" /></label>
      <div className="flex items-end"><Button disabled={busy} type="submit">{busy ? "Discovering tools..." : "Connect and discover tools"}</Button></div>
      {error && <p role="alert" className="text-sm text-destructive sm:col-span-2">{error}</p>}
    </form>
    <div className="grid items-start gap-4 lg:grid-cols-2">{items.map((item) => <Connection key={item.id} item={item} reload={reload} />)}</div>
  </div>;
}
