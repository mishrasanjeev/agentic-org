// SPDX-License-Identifier: Apache-2.0
import { useCallback, useEffect, useState } from "react";
import { Check, Copy, Globe, RefreshCw, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { customDomainsApi, extractApiError } from "../lib/api";

type StepState = "done" | "current" | "error" | "pending";

interface DnsRecord {
  purpose: string;
  type: string;
  name: string;
  value: string;
  note?: string;
}

interface DomainView {
  id: string;
  host: string;
  status: string;
  steps: { key: string; state: StepState }[];
  records: DnsRecord[];
  ownership_verified: boolean;
  search_console: { account: string | null; property: string; instructions: string; confirmed: boolean };
  error: { code: string; message: string; detail?: string | null } | null;
  can_retry: boolean;
  profile_url: string;
  last_checked_at: string | null;
  live_at: string | null;
}

interface QueueItem {
  id: string;
  tenant_id: string;
  merchant_id: string;
  host: string;
  status: string;
  action?: "create_mapping" | "delete_mapping";
  mapping_in_use?: boolean;
  search_console_owner_confirmed: boolean;
  commands: { create: string; describe: string; delete: string };
  error: { code: string; detail?: string | null } | null;
  mapping_requested_at: string | null;
}

export const APEX_GUIDE_URL =
  "https://github.com/mishrasanjeev/agentic-org/blob/main/docs/oacp/merchant-apex-domain.md";

const STEP_LABELS: Record<string, string> = {
  dns: "Publish DNS records",
  ownership: "Verify ownership",
  mapping: "Connect domain",
  certificate: "Issue certificate",
  live: "Live",
};

const STATUS_LABELS: Record<string, { label: string; variant: "success" | "warning" | "destructive" | "secondary" }> = {
  pending_dns: { label: "Waiting for DNS", variant: "warning" },
  pending_mapping: { label: "Connecting", variant: "warning" },
  pending_certificate: { label: "Issuing certificate", variant: "warning" },
  live: { label: "Live", variant: "success" },
  failed: { label: "Needs attention", variant: "destructive" },
  pending_unmap: { label: "Delete mapping", variant: "secondary" },
};

function stepClass(state: StepState) {
  if (state === "done") return "border-green-600 bg-green-600 text-white";
  if (state === "current") return "border-primary text-primary";
  if (state === "error") return "border-red-600 bg-red-600 text-white";
  return "border-border text-muted-foreground";
}

function errorDetail(e: unknown): string {
  const detail = (e as any)?.response?.data?.detail;
  if (detail && typeof detail === "object" && typeof detail.message === "string") return detail.message;
  return extractApiError(e, "The request failed. Try again.");
}

function CopyButton({ value, label }: { value: string; label: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      aria-label={`Copy ${label}`}
      className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-md border border-border hover:bg-accent sm:h-8 sm:w-8"
      onClick={() => {
        void navigator.clipboard?.writeText(value).then(() => {
          setCopied(true);
          window.setTimeout(() => setCopied(false), 1500);
        });
      }}
    >
      {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
    </button>
  );
}

function Steps({ steps }: { steps: DomainView["steps"] }) {
  return (
    <ol className="grid gap-3 sm:grid-cols-5" aria-label="Domain progress">
      {steps.map((step, index) => (
        <li key={step.key} className="flex items-center gap-2 text-sm" data-state={step.state}>
          <span className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full border text-xs font-semibold ${stepClass(step.state)}`}>
            {step.state === "done" ? <Check className="h-4 w-4" /> : index + 1}
          </span>
          <span className={step.state === "pending" ? "text-muted-foreground" : "font-medium"}>{STEP_LABELS[step.key] || step.key}</span>
        </li>
      ))}
    </ol>
  );
}

function RecordsTable({ records }: { records: DnsRecord[] }) {
  return (
    <div className="overflow-x-auto rounded-md border">
      <table className="w-full text-left text-sm">
        <thead className="bg-muted/50 text-xs uppercase text-muted-foreground">
          <tr>
            <th className="px-3 py-2">Type</th>
            <th className="px-3 py-2">Name</th>
            <th className="px-3 py-2">Value</th>
          </tr>
        </thead>
        <tbody>
          {records.map((record) => (
            <tr key={`${record.type}-${record.name}`} className="border-t align-top">
              <td className="px-3 py-2 font-mono">{record.type}</td>
              <td className="px-3 py-2">
                <div className="flex items-start gap-2">
                  <code className="break-all">{record.name}</code>
                  <CopyButton value={record.name} label={`${record.type} name`} />
                </div>
              </td>
              <td className="px-3 py-2">
                {record.value ? (
                  <div className="flex items-start gap-2">
                    <code className="break-all">{record.value}</code>
                    <CopyButton value={record.value} label={`${record.type} value`} />
                  </div>
                ) : null}
                {record.note ? <p className="mt-1 text-xs text-muted-foreground">{record.note}</p> : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function OperatorQueue() {
  const [items, setItems] = useState<QueueItem[] | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [reasons, setReasons] = useState<Record<string, string>>({});

  const load = useCallback(async () => {
    try {
      const { data } = await customDomainsApi.operatorQueue();
      setItems(data.items || []);
    } catch (e: any) {
      // 404: this organization is not the operator; the section stays hidden.
      if (e?.response?.status !== 404) setMessage(errorDetail(e));
      setItems(null);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  if (items === null) return message ? <p className="text-sm text-red-700">{message}</p> : null;

  const act = async (item: QueueItem, action: "mapped" | "failed" | "deleted") => {
    try {
      if (action === "mapped") await customDomainsApi.markMapped(item.id);
      else if (action === "deleted") await customDomainsApi.markUnmapped(item.id);
      else await customDomainsApi.markFailed(item.id, reasons[item.id] || "");
      setMessage(`${item.host}: marked ${action}.`);
      await load();
    } catch (e) {
      setMessage(errorDetail(e));
    }
  };

  return (
    <section className="space-y-3 rounded-lg border p-4" aria-labelledby="operator-queue-title">
      <h3 id="operator-queue-title" className="text-lg font-semibold">Operator queue</h3>
      <p className="text-sm text-muted-foreground">
        Domains that passed DNS and ownership checks and wait for their Cloud Run domain mapping, and removed domains whose mapping is still to delete. Run the command, then mark the item; the status check moves a mapped domain to live once its certificate is active.
      </p>
      {message ? <p className="text-sm" role="status">{message}</p> : null}
      {items.length === 0 ? <p className="text-sm text-muted-foreground">Nothing is waiting.</p> : null}
      {items.map((item) =>
        item.status === "pending_unmap" ? (
          <div key={item.id} className="space-y-2 rounded-md border p-3" data-testid="operator-queue-item">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-semibold">{item.host}</span>
              <Badge variant="secondary">{STATUS_LABELS.pending_unmap.label}</Badge>
              <span className="text-xs text-muted-foreground">removed by merchant {item.merchant_id}</span>
            </div>
            {item.mapping_in_use ? (
              <p className="text-sm">Another domain now uses this host: keep the mapping and mark this item done.</p>
            ) : (
              <div className="flex items-start gap-2 rounded bg-muted/50 p-2">
                <code className="break-all text-xs">{item.commands.delete}</code>
                <CopyButton value={item.commands.delete} label="delete command" />
              </div>
            )}
            {item.error ? <p className="text-xs text-red-700">{item.error.code}: {item.error.detail}</p> : null}
            <Button size="sm" onClick={() => void act(item, "deleted")}>
              {item.mapping_in_use ? "Mark done" : "Mark deleted"}
            </Button>
          </div>
        ) : (
        <div key={item.id} className="space-y-2 rounded-md border p-3" data-testid="operator-queue-item">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-semibold">{item.host}</span>
            <Badge variant={STATUS_LABELS[item.status]?.variant || "secondary"}>{STATUS_LABELS[item.status]?.label || item.status}</Badge>
            <span className="text-xs text-muted-foreground">merchant {item.merchant_id}</span>
            <span className="text-xs text-muted-foreground">
              Search Console owner {item.search_console_owner_confirmed ? "confirmed" : "not confirmed"}
            </span>
          </div>
          <div className="flex items-start gap-2 rounded bg-muted/50 p-2">
            <code className="break-all text-xs">{item.commands.create}</code>
            <CopyButton value={item.commands.create} label="mapping command" />
          </div>
          {item.error ? <p className="text-xs text-red-700">{item.error.code}: {item.error.detail}</p> : null}
          <div className="flex flex-wrap items-center gap-2">
            {item.status === "pending_mapping" ? (
              <Button size="sm" onClick={() => void act(item, "mapped")}>Mark mapped</Button>
            ) : null}
            <input
              className="h-11 min-w-[16rem] flex-1 rounded-md border px-3 text-sm sm:h-9"
              placeholder="Reason shown to the merchant"
              aria-label={`Failure reason for ${item.host}`}
              value={reasons[item.id] || ""}
              onChange={(event) => setReasons({ ...reasons, [item.id]: event.target.value })}
            />
            <Button size="sm" variant="outline" disabled={(reasons[item.id] || "").trim().length < 3} onClick={() => void act(item, "failed")}>
              Mark failed
            </Button>
          </div>
        </div>
        ),
      )}
    </section>
  );
}

export default function CustomDomains() {
  const [merchantId, setMerchantId] = useState("");
  const [sellerAgentId, setSellerAgentId] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [enabled, setEnabled] = useState(true);
  const [domain, setDomain] = useState<DomainView | null>(null);
  const [cnameTarget, setCnameTarget] = useState("ghs.googlehosted.com");
  const [host, setHost] = useState("");
  const [ownerAdded, setOwnerAdded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const scope = useCallback(() => (sellerAgentId.trim() ? { seller_agent_id: sellerAgentId.trim() } : {}), [sellerAgentId]);

  const load = async () => {
    if (!merchantId.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const { data } = await customDomainsApi.get(merchantId.trim(), scope());
      setEnabled(true);
      setDomain(data.domain);
      setCnameTarget(data.cname_target || cnameTarget);
      setOwnerAdded(Boolean(data.domain?.search_console?.confirmed));
      setLoaded(true);
    } catch (e: any) {
      if (e?.response?.status === 404 && e?.response?.data?.detail === "Not Found") {
        setEnabled(false);
      } else {
        setError(errorDetail(e));
      }
    } finally {
      setBusy(false);
    }
  };

  const run = async (action: () => Promise<{ data: any }>, apply: (data: any) => void) => {
    setBusy(true);
    setError(null);
    try {
      const { data } = await action();
      apply(data);
    } catch (e) {
      setError(errorDetail(e));
    } finally {
      setBusy(false);
    }
  };

  const add = () =>
    run(
      () => customDomainsApi.add(merchantId.trim(), { host: host.trim(), ...scope() }),
      (data) => setDomain(data),
    );
  const check = () =>
    run(
      () => customDomainsApi.check(merchantId.trim(), { search_console_owner_added: ownerAdded, ...scope() }),
      (data) => setDomain(data),
    );
  const remove = () => {
    if (!domain || !window.confirm(`Disconnect ${domain.host}? Agents will stop finding your profile there.`)) return;
    void run(
      () => customDomainsApi.remove(merchantId.trim(), scope()),
      () => setDomain(null),
    );
  };

  const status = domain ? STATUS_LABELS[domain.status] : undefined;

  return (
    <div className="space-y-6">
      <div>
        <h2 className="flex items-center gap-2 text-2xl font-bold">
          <Globe className="h-6 w-6" /> Custom domains
        </h2>
        <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
          Serve your agent profile (<code>/.well-known/ucp</code>) and catalog on a subdomain you own, such as agent.your-store.example. You publish two DNS records, we check them, connect the domain and issue its certificate.
        </p>
      </div>

      <div className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-sm">
          Merchant ID
          <input className="h-11 rounded-md border px-3 sm:h-10" value={merchantId} onChange={(event) => setMerchantId(event.target.value)} />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          Seller agent ID (optional)
          <input className="h-11 rounded-md border px-3 sm:h-10" value={sellerAgentId} onChange={(event) => setSellerAgentId(event.target.value)} />
        </label>
        <Button variant="outline" disabled={busy || !merchantId.trim()} onClick={() => void load()}>Load</Button>
      </div>

      {!enabled ? (
        <div className="rounded-lg border bg-muted/40 px-4 py-3 text-sm" role="status">
          Custom domains are not enabled for this deployment yet.
        </div>
      ) : null}

      {error ? (
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800" role="alert">{error}</div>
      ) : null}

      {enabled && loaded && !domain ? (
        <section className="space-y-3 rounded-lg border p-4" aria-labelledby="add-domain-title">
          <h3 id="add-domain-title" className="text-lg font-semibold">Add a domain</h3>
          <p className="text-sm text-muted-foreground">
            Use a subdomain (a CNAME record cannot sit at the apex). To also answer on your apex domain, follow the{" "}
            <a className="underline" href={APEX_GUIDE_URL} target="_blank" rel="noreferrer">apex domain guide</a>.
          </p>
          <div className="flex flex-wrap gap-3">
            <input
              className="h-11 min-w-[18rem] flex-1 rounded-md border px-3 sm:h-10"
              placeholder="agent.your-store.example"
              aria-label="Domain"
              value={host}
              onChange={(event) => setHost(event.target.value)}
            />
            <Button disabled={busy || host.trim().length < 3} onClick={() => void add()}>Add domain</Button>
          </div>
        </section>
      ) : null}

      {domain ? (
        <section className="space-y-5 rounded-lg border p-4" aria-labelledby="domain-title">
          <div className="flex flex-wrap items-center gap-3">
            <h3 id="domain-title" className="text-lg font-semibold">{domain.host}</h3>
            {status ? <Badge variant={status.variant}>{status.label}</Badge> : null}
            <div className="ml-auto flex gap-2">
              {domain.can_retry ? (
                <Button size="sm" disabled={busy} onClick={() => void check()}>
                  <RefreshCw className="mr-2 h-4 w-4" />
                  {domain.status === "failed" ? "Retry" : domain.status === "pending_dns" ? "Verify records" : "Check status"}
                </Button>
              ) : null}
              <Button size="sm" variant="outline" className="min-h-11 min-w-11 sm:min-h-0 sm:min-w-0" disabled={busy} onClick={remove} aria-label="Disconnect domain">
                <Trash2 className="h-4 w-4" />
              </Button>
            </div>
          </div>

          <Steps steps={domain.steps} />

          {domain.error ? (
            <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800" role="alert">
              <p className="font-medium">{domain.error.message}</p>
              {domain.error.detail ? <p className="mt-1">{domain.error.detail}</p> : null}
            </div>
          ) : null}

          {domain.status === "live" ? (
            <p className="text-sm">
              Live. Agents find your profile at <a className="underline" href={domain.profile_url} target="_blank" rel="noreferrer">{domain.profile_url}</a>.
            </p>
          ) : null}

          <div className="space-y-2">
            <h4 className="font-semibold">1. Add these records at your DNS provider</h4>
            <RecordsTable records={domain.records} />
            <p className="text-xs text-muted-foreground">
              The CNAME points at {cnameTarget}. Keep it DNS only (not proxied); proxies and CAA records that exclude pki.goog and letsencrypt.org stop the certificate.
            </p>
            <p className="text-xs text-muted-foreground">
              To answer on your apex domain as well, pass <code>/.well-known/ucp</code> and <code>/ucp/*</code> through to this host as described in the{" "}
              <a className="underline" href={APEX_GUIDE_URL} target="_blank" rel="noreferrer">apex domain guide</a> (no redirects).
            </p>
          </div>

          <div className="space-y-2">
            <h4 className="font-semibold">2. Let us connect the domain</h4>
            <p className="text-sm text-muted-foreground">{domain.search_console.instructions}</p>
            <label className="flex min-h-11 items-center gap-3 text-sm">
              <input
                type="checkbox"
                className="h-5 w-5 shrink-0"
                checked={ownerAdded}
                disabled={domain.search_console.confirmed}
                onChange={(event) => setOwnerAdded(event.target.checked)}
              />
              I added {domain.search_console.account || "the AgenticOrg mapping account"} as an owner of {domain.search_console.property}
            </label>
          </div>

          {domain.last_checked_at ? (
            <p className="text-xs text-muted-foreground">Last checked {new Date(domain.last_checked_at).toLocaleString()}</p>
          ) : null}
        </section>
      ) : null}

      <OperatorQueue />
    </div>
  );
}
