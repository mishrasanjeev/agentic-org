// SPDX-License-Identifier: Apache-2.0
import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router";
import { ArrowLeft, ArrowRight, Check, Copy, KeyRound, Mail, Mic, Plug, Plus, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import api, { extractApiError } from "@/lib/api";
import { useAuth } from "@/contexts/AuthContext";
import { isAdminUser } from "@/lib/roles";

interface Provider { id: string; label: string; fields: string[]; guide: string; reply: boolean; identity_link: boolean }
interface Binding { id: string; name: string; provider: string; target_kind: string; target_id: string; enabled: boolean; auto_reply: boolean; callback_url: string }
interface Target { id: string; name: string }
interface Delivery { id: string; text: string; status: string; error_code?: string; result?: unknown; created_at: string }
const inputClass = "mt-1 w-full rounded border bg-background px-3 py-2 text-sm";
const label = (name: string) => name.replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());

export default function Channels() {
  const { user } = useAuth();
  const admin = isAdminUser(user);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [bindings, setBindings] = useState<Binding[]>([]);
  const [agents, setAgents] = useState<Target[]>([]);
  const [workflows, setWorkflows] = useState<Target[]>([]);
  const [creating, setCreating] = useState(false);
  const [step, setStep] = useState(0);
  const [providerId, setProviderId] = useState("");
  const [name, setName] = useState("");
  const [kind, setKind] = useState("agent");
  const [target, setTarget] = useState("");
  const [credentials, setCredentials] = useState<Record<string, string>>({});
  const [reply, setReply] = useState(false);
  const [rotating, setRotating] = useState(false);
  const [selected, setSelected] = useState<Binding | null>(null);
  const [deliveries, setDeliveries] = useState<Delivery[]>([]);
  const [command, setCommand] = useState("");
  const [expiresAt, setExpiresAt] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const provider = providers.find((item) => item.id === providerId);
  const selectedProvider = providers.find((item) => item.id === selected?.provider);

  async function reload() {
    const response = await api.get("/channels");
    setBindings(response.data.bindings);
    return response.data.bindings as Binding[];
  }
  useEffect(() => {
    let active = true;
    (async () => {
      try {
        const catalog = await api.get("/channels/catalog");
        const channels = await api.get("/channels");
        const agentList = await api.get("/agents", { params: { page_size: 100 } });
        const workflowList = admin ? await api.get("/workflows", { params: { page_size: 100 } }) : null;
        if (active) {
          setProviders(catalog.data.providers); setBindings(channels.data.bindings);
          setAgents(agentList.data.items || []); setWorkflows(workflowList?.data.items || []);
        }
      } catch (err) { if (active) setError(extractApiError(err, "Could not load channel setup")); }
    })();
    return () => { active = false; };
  }, [admin]);
  useEffect(() => {
    if (!selected) return;
    let active = true;
    async function load() {
      try {
        const response = await api.get(`/channels/${selected!.id}/deliveries`);
        if (active) setDeliveries(response.data.deliveries);
      } catch (err) { if (active) setError(extractApiError(err, "Could not load inbox")); }
    }
    void load();
    const interval = window.setInterval(load, 10000);
    return () => { active = false; window.clearInterval(interval); };
  }, [selected]);
  useEffect(() => {
    if (!command || !expiresAt) return;
    const timer = window.setTimeout(() => setCommand(""), Math.max(0, new Date(expiresAt).getTime() - Date.now()));
    return () => window.clearTimeout(timer);
  }, [command, expiresAt]);
  async function action(work: () => Promise<void>) {
    setBusy(true); setError(""); setNotice("");
    try { await work(); } catch (err) { setError(extractApiError(err, "Channel operation failed")); }
    finally { setBusy(false); }
  }
  async function save(event: FormEvent) {
    event.preventDefault();
    await action(async () => {
      const response = await api.post("/channels", { name, provider: providerId, target_kind: kind, target_id: target, credentials, auto_reply: reply });
      setCredentials({}); setCreating(false); setSelected(response.data); setCommand("");
      await reload(); setNotice("Channel saved. Register the callback in your provider account, then enable it.");
    });
  }
  function credentialFields(fields: string[]) {
    return fields.map((field) => <div key={field} className="text-sm"><label htmlFor={`channel-credential-${field}`}>{label(field)}</label>
      <input id={`channel-credential-${field}`} required type={["receiver_id", "api_version"].includes(field) ? "text" : "password"}
        autoComplete="new-password" maxLength={4096} className={inputClass}
        value={credentials[field] || ""} onChange={(event) => setCredentials({ ...credentials, [field]: event.target.value })} />
      {field === "webhook_secret" && <Button type="button" variant="ghost" className="mt-1"
        onClick={() => { const bytes = crypto.getRandomValues(new Uint8Array(32));
          setCredentials({ ...credentials, [field]: Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("") }); }}>
        <KeyRound size={14} className="mr-2" />Generate secret</Button>}
    </div>);
  }
  return <main className="space-y-6 min-w-0">
    <header className="flex flex-wrap items-center justify-between gap-3 border-b pb-4">
      <div><h1 className="text-2xl font-semibold">Channels</h1><a href="/docs/channel-workflows" className="text-sm underline">Account setup guide</a></div>
      <Button onClick={() => { setCreating(true); setRotating(false); setStep(0); setCredentials({}); setName(""); setProviderId(""); setTarget(""); setReply(false); }}><Plus size={16} className="mr-2" />Connect channel</Button>
    </header>
    {error && <p role="alert" className="border-l-4 border-red-600 bg-red-50 p-3 text-sm text-red-900 break-words">{error}</p>}
    {notice && <p role="status" className="border-l-4 border-green-600 bg-green-50 p-3 text-sm text-green-900">{notice}</p>}
    <nav aria-label="Related connections" className="flex flex-wrap gap-5 text-sm">
      {admin && <Link className="flex items-center gap-2 underline" to="/dashboard/voice-setup"><Mic size={16} />Voice and calls</Link>}
      <Link className="flex items-center gap-2 underline" to="/dashboard/connectors/remote-mcp"><Plug size={16} />MCP tools</Link>
      <Link className="flex items-center gap-2 underline" to="/dashboard/connectors"><Mail size={16} />Email connectors</Link>
      {admin && <Link className="flex items-center gap-2 underline" to="/dashboard/settings/ai-credentials"><KeyRound size={16} />AI credentials</Link>}
    </nav>
    {creating && <section className="border-y py-5" aria-label="Connect channel">
      <ol className="mb-5 flex flex-wrap gap-5 text-sm" aria-label="Setup progress">
        {["Channel", "Destination", "Credentials"].map((item, index) => <li key={item} aria-current={index === step ? "step" : undefined} className={index === step ? "font-semibold" : "text-muted-foreground"}>{index + 1}. {item}</li>)}
      </ol>
      <form onSubmit={save} className="max-w-xl space-y-4">
        {step === 0 && <><label className="block text-sm">Channel<select required className={inputClass} value={providerId} onChange={(event) => { setProviderId(event.target.value); setCredentials({}); setReply(false); }}><option value="">Choose a channel</option>{providers.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label>
          <label className="block text-sm">Connection name<input required maxLength={100} className={inputClass} value={name} onChange={(event) => setName(event.target.value)} /></label></>}
        {step === 1 && <><label className="block text-sm">Destination type<select className={inputClass} value={kind} onChange={(event) => { setKind(event.target.value); setTarget(""); }}><option value="agent">Agent</option>{admin && <option value="workflow">Workflow</option>}</select></label>
          <label className="block text-sm">{kind === "agent" ? "Agent" : "Workflow"}<select required className={inputClass} value={target} onChange={(event) => setTarget(event.target.value)}><option value="">Choose destination</option>{(kind === "agent" ? agents : workflows).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label></>}
        {step === 2 && provider && <><a href={provider.guide} target="_blank" rel="noreferrer" className="inline-block text-sm underline">{provider.label} account setup</a>
          {credentialFields(provider.fields)}
          {provider.reply && <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={reply} onChange={(event) => setReply(event.target.checked)} />Send task-status replies</label>}</>}
        <div className="flex flex-wrap gap-3 pt-3">
          {step > 0 && <Button type="button" variant="outline" onClick={() => setStep(step - 1)}><ArrowLeft size={16} className="mr-2" />Back</Button>}
          {step < 2 ? <Button type="button" disabled={step === 0 ? !provider || !name.trim() : !target} onClick={() => setStep(step + 1)}>Next<ArrowRight size={16} className="ml-2" /></Button> : <Button type="submit" disabled={busy}>Save connection</Button>}
          <Button type="button" variant="ghost" onClick={() => { setCreating(false); setCredentials({}); }}>Cancel</Button>
        </div>
      </form>
    </section>}
    <section aria-label="Your channel connections" className="overflow-x-auto">
      <table className="w-full text-left text-sm"><thead className="border-b"><tr><th className="py-3 pr-4">Connection</th><th className="pr-4">Channel</th><th className="pr-4">Status</th><th>Inbox</th></tr></thead>
        <tbody>{bindings.map((item) => <tr className="border-b" key={item.id}><td className="py-3 pr-4 font-medium break-words">{item.name}</td><td className="pr-4">{providers.find((entry) => entry.id === item.provider)?.label || item.provider}</td><td className="pr-4">{item.enabled ? "Enabled" : "Disabled"}</td><td><Button variant="outline" onClick={() => { setSelected(item); setCommand(""); setRotating(false); setCredentials({}); setDeliveries([]); }}>Open</Button></td></tr>)}</tbody></table>
      {!bindings.length && <p className="py-6 text-sm text-muted-foreground">No channel connections</p>}
    </section>
    {selected && <section className="space-y-4 border-t pt-5" aria-label="Channel inbox">
      <div className="flex flex-wrap items-center justify-between gap-3"><h2 className="text-lg font-semibold break-words">{selected.name}</h2><label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={selected.enabled} disabled={busy} onChange={() => action(async () => {
        const response = await api.patch(`/channels/${selected.id}`, { enabled: !selected.enabled }); setSelected(response.data); setCommand(""); await reload();
      })} />Enabled</label></div>
      <div className="flex items-start gap-2"><label className="flex-1 min-w-0 text-sm">Callback URL<input className={inputClass} readOnly value={selected.callback_url} /></label><Button className="mt-6 shrink-0" variant="outline" title="Copy callback URL" aria-label="Copy callback URL" onClick={() => action(async () => { await navigator.clipboard.writeText(selected.callback_url); setNotice("Callback copied"); })}><Copy size={16} /></Button></div>
      <div className="flex flex-wrap items-center gap-4"><Button variant="outline" disabled={busy} onClick={() => { setCreating(false); setCredentials({}); setRotating(!rotating); }}><KeyRound size={16} className="mr-2" />Replace credentials</Button>
        {selectedProvider?.reply && <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={selected.auto_reply} disabled={busy} onChange={() => action(async () => {
          const response = await api.patch(`/channels/${selected.id}`, { auto_reply: !selected.auto_reply }); setSelected(response.data); await reload();
        })} />Task-status replies</label>}</div>
      {rotating && selectedProvider && <form className="max-w-xl space-y-3 border-y py-4" aria-label="Replace channel credentials" onSubmit={(event) => { event.preventDefault(); void action(async () => {
        const response = await api.patch(`/channels/${selected.id}`, { credentials }); setSelected(response.data); setCredentials({}); setRotating(false); setCommand(""); await reload();
        setNotice("Credentials replaced. Connection disabled and sender links revoked. Update the provider callback settings before enabling again.");
      }); }}><h3 className="text-base font-semibold">New credentials</h3>{credentialFields(selectedProvider.fields)}<div className="flex gap-3"><Button type="submit" disabled={busy}>Replace and disable</Button><Button type="button" variant="ghost" onClick={() => { setRotating(false); setCredentials({}); }}>Cancel</Button></div></form>}
      {selected.enabled && selectedProvider?.identity_link && <Button disabled={busy} variant="outline" onClick={() => action(async () => { const response = await api.post(`/channels/${selected.id}/link-code`); setCommand(response.data.command); setExpiresAt(response.data.expires_at); })}><KeyRound size={16} className="mr-2" />Link my messaging account</Button>}
      {command && <div role="status" className="border-l-4 border-teal-600 bg-teal-50 p-3 text-sm text-teal-950 break-all"><p>Send this command to your bot from your private account. Expires at {new Date(expiresAt).toLocaleTimeString()}.</p><code className="block mt-2 select-all">{command}</code></div>}
      <div className="flex justify-between items-center"><h3 className="text-base font-semibold">Messages</h3><Button variant="outline" disabled={busy} title="Refresh messages" aria-label="Refresh messages" onClick={() => action(async () => { const response = await api.get(`/channels/${selected.id}/deliveries`); setDeliveries(response.data.deliveries); })}><RefreshCw size={16} /></Button></div>
      {deliveries.map((item) => <article className="border-b py-4 space-y-2" key={item.id}><div className="flex flex-wrap justify-between gap-2 text-xs text-muted-foreground"><time dateTime={item.created_at}>{new Date(item.created_at).toLocaleString()}</time><span>{label(item.status)}</span></div><p className="text-sm whitespace-pre-wrap break-words">{item.text}</p>{item.error_code && <p className="text-sm text-red-700">{label(item.error_code)}</p>}
        {item.status === "needs_review" && !item.error_code && <Button disabled={busy || !selected.enabled} variant="outline" onClick={() => action(async () => { await api.post(`/channels/${selected.id}/deliveries/${item.id}/approve`); const response = await api.get(`/channels/${selected.id}/deliveries`); setDeliveries(response.data.deliveries); })}><Check size={16} className="mr-2" />Approve this task</Button>}
        {item.result != null && <details><summary className="cursor-pointer text-sm">Result</summary><pre className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap break-all border p-3 text-xs">{JSON.stringify(item.result, null, 2)}</pre></details>}
      </article>)}
      {!deliveries.length && <p className="text-sm text-muted-foreground">No messages received</p>}
    </section>}
  </main>;
}
