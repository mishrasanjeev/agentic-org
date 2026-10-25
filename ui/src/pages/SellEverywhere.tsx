// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
import { useState } from "react";
import { Link } from "react-router";
import { Check, Copy, Megaphone } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { extractApiError, sellEverywhereApi } from "../lib/api";

interface Listing {
  slug: string;
  status: "listed" | "paused";
  display_name: string;
  serving_host: string | null;
  subdomain_host: string | null;
  terms_version: string;
  opted_in_at: string | null;
}

interface Surfaces {
  web: { status: string; host: string | null };
  mcp: { status: string; server_url: string };
  telegram: { status: string; link: string | null };
  google: { status: string };
  microsoft: { status: string };
  perplexity: { status: string };
  payments: { status: string };
}

interface View {
  terms_version: string;
  suggested_slug: string;
  listing: Listing | null;
  surfaces: Surfaces;
  platform_subdomains: { apex: string | null; in_use: number; cap: number };
  subdomain_note?: { code: string; message: string } | null;
}

const STATUS: Record<string, { label: string; variant: "success" | "warning" | "destructive" | "secondary" }> = {
  live: { label: "Live", variant: "success" },
  pending_dns: { label: "Waiting for DNS", variant: "warning" },
  pending_mapping: { label: "Setting up", variant: "warning" },
  pending_certificate: { label: "Issuing certificate", variant: "warning" },
  failed: { label: "Needs attention", variant: "destructive" },
  waiting_for_publication: { label: "Waiting for publication", variant: "warning" },
  coming_soon: { label: "Coming soon", variant: "secondary" },
  registration_pending: { label: "Coming: registration in progress", variant: "secondary" },
  not_started: { label: "Not started", variant: "secondary" },
  off: { label: "Off", variant: "secondary" },
};

function StatusBadge({ status }: { status: string }) {
  const known = STATUS[status] ?? { label: status, variant: "secondary" as const };
  return <Badge variant={known.variant}>{known.label}</Badge>;
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

function SurfaceRow({ title, status, children }: { title: string; status: string; children?: React.ReactNode }) {
  return (
    <li className="flex flex-col gap-2 rounded-lg border p-4 sm:flex-row sm:items-start sm:justify-between">
      <div className="space-y-1">
        <p className="font-medium">{title}</p>
        {children ? <div className="text-sm text-muted-foreground">{children}</div> : null}
      </div>
      <StatusBadge status={status} />
    </li>
  );
}

/** One page to put a shop on every AI surface the AgenticOrg network reaches. */
export default function SellEverywhere() {
  const [merchantId, setMerchantId] = useState("");
  const [sellerAgentId, setSellerAgentId] = useState("");
  const [view, setView] = useState<View | null>(null);
  const [enabled, setEnabled] = useState(true);
  const [slug, setSlug] = useState("");
  const [accepted, setAccepted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const scope = () => (sellerAgentId.trim() ? { seller_agent_id: sellerAgentId.trim() } : {});

  const apply = (data: View) => {
    setView(data);
    setSlug(data.listing?.slug ?? data.suggested_slug ?? "");
  };

  const load = async () => {
    if (!merchantId.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const { data } = await sellEverywhereApi.get(merchantId.trim(), scope());
      setEnabled(true);
      apply(data);
    } catch (e: any) {
      if (e?.response?.status === 404 && e?.response?.data?.detail === "Not Found") setEnabled(false);
      else setError(errorDetail(e));
    } finally {
      setBusy(false);
    }
  };

  const save = async (listed: boolean) => {
    setBusy(true);
    setError(null);
    try {
      const body = {
        listed,
        accept_terms: listed ? accepted : false,
        ...(view?.listing ? {} : { slug: slug.trim() || undefined }),
        ...scope(),
      };
      const { data } = await sellEverywhereApi.put(merchantId.trim(), body);
      apply(data);
    } catch (e) {
      setError(errorDetail(e));
    } finally {
      setBusy(false);
    }
  };

  const listing = view?.listing ?? null;
  const listed = listing?.status === "listed";
  const surfaces = view?.surfaces;

  return (
    <div className="space-y-6">
      <div>
        <h2 className="flex items-center gap-2 text-2xl font-bold">
          <Megaphone className="h-6 w-6" /> Sell everywhere
        </h2>
        <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
          Turn your shop on once. Buyers then reach it from ChatGPT, Claude and other AI apps, the AgenticOrg
          shopping bot on Telegram and the open web, with no setup on each surface. You stay the seller: buyers
          always finish and pay with you.
        </p>
      </div>

      <div className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-sm">
          Merchant ID
          <input className="h-11 rounded-md border px-3 sm:h-10" value={merchantId} onChange={(e) => setMerchantId(e.target.value)} />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          Seller agent ID (optional)
          <input className="h-11 rounded-md border px-3 sm:h-10" value={sellerAgentId} onChange={(e) => setSellerAgentId(e.target.value)} />
        </label>
        <Button variant="outline" disabled={busy || !merchantId.trim()} onClick={() => void load()}>
          Load
        </Button>
      </div>

      {!enabled ? (
        <div className="rounded-lg border bg-muted/40 px-4 py-3 text-sm" role="status">
          Sell everywhere is not enabled for this deployment yet.
        </div>
      ) : null}
      {error ? (
        <div className="rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive" role="alert">
          {error}
        </div>
      ) : null}

      {view ? (
        <>
          <section className="space-y-4 rounded-xl border p-5" aria-label="Turn on Sell everywhere">
            <ol className="space-y-4 text-sm">
              <li>
                <p className="font-medium">1. Your catalog</p>
                <p className="text-muted-foreground">
                  Connect Shopify or add your products in <Link className="underline" to="/dashboard/commerce-runtime">Commerce Runtime</Link>.
                </p>
              </li>
              <li>
                <p className="font-medium">2. Your policies</p>
                <p className="text-muted-foreground">
                  Check returns, shipping and store pickup in <Link className="underline" to="/dashboard/commerce-runtime">Commerce Runtime</Link>.
                </p>
              </li>
              <li className="space-y-3">
                <p className="font-medium">3. Turn on Sell everywhere</p>
                <div className="flex max-w-md flex-col gap-1">
                  <label htmlFor="network-slug">Shop name on the network</label>
                  <input
                    id="network-slug"
                    className="h-11 rounded-md border px-3 sm:h-10"
                    value={slug}
                    disabled={Boolean(listing)}
                    onChange={(e) => setSlug(e.target.value.toLowerCase())}
                    aria-describedby="slug-help"
                  />
                  <span id="slug-help" className="text-xs text-muted-foreground">
                    3 to 40 lowercase letters, digits or hyphens. It names your shop in AI apps, in your Telegram link and in{" "}
                    {view.platform_subdomains.apex ? `${slug || "your-shop"}.${view.platform_subdomains.apex}` : "your web address"}. It cannot change later.
                  </span>
                </div>
                {!listed ? (
                  <label className="flex max-w-2xl items-start gap-2">
                    <input type="checkbox" className="mt-1" checked={accepted} onChange={(e) => setAccepted(e.target.checked)} />
                    <span>
                      I accept the AgenticOrg network terms (version {view.terms_version}): AgenticOrg lists my shop and opens
                      checkouts for buyers who find it. I remain the seller and take payment myself.
                    </span>
                  </label>
                ) : null}
                <div className="flex flex-wrap gap-2">
                  {listed ? (
                    <Button variant="outline" disabled={busy} onClick={() => void save(false)}>
                      Pause
                    </Button>
                  ) : (
                    <Button disabled={busy || !accepted || (!listing && !slug.trim())} onClick={() => void save(true)}>
                      {listing ? "Resume Sell everywhere" : "Turn on Sell everywhere"}
                    </Button>
                  )}
                </div>
                {view.subdomain_note ? (
                  <p className="text-sm text-amber-700" role="status">
                    {view.subdomain_note.message}
                  </p>
                ) : null}
              </li>
            </ol>
          </section>

          {surfaces ? (
            <section className="space-y-3" aria-label="Where buyers can find you">
              <h3 className="text-lg font-semibold">Where buyers can find you</h3>
              <ul className="space-y-3">
                <SurfaceRow title="Your web address" status={surfaces.web.status}>
                  {surfaces.web.host ? (
                    <span>
                      https://{surfaces.web.host} (AI search engines and agents read your profile here)
                    </span>
                  ) : (
                    <span>Turn on Sell everywhere to get an address, or connect your own in Custom Domains.</span>
                  )}
                </SurfaceRow>
                <SurfaceRow title="ChatGPT, Claude and other AI apps" status={surfaces.mcp.status}>
                  <span className="flex items-center gap-2">
                    <code className="break-all">{surfaces.mcp.server_url}</code>
                    <CopyButton value={surfaces.mcp.server_url} label="AI app server address" />
                  </span>
                  <span>Buyers add this once as a custom MCP server and can then shop every network shop.</span>
                </SurfaceRow>
                <SurfaceRow title="Telegram" status={surfaces.telegram.status}>
                  {surfaces.telegram.link ? (
                    <span className="flex items-center gap-2">
                      <a className="break-all underline" href={surfaces.telegram.link} target="_blank" rel="noreferrer">
                        {surfaces.telegram.link}
                      </a>
                      <CopyButton value={surfaces.telegram.link} label="Telegram link" />
                    </span>
                  ) : (
                    <span>Your link to the AgenticOrg shopping bot appears here once it is on.</span>
                  )}
                </SurfaceRow>
                <SurfaceRow title="Google Gemini and AI Mode" status={surfaces.google.status} />
                <SurfaceRow title="Microsoft Copilot" status={surfaces.microsoft.status} />
                <SurfaceRow title="Perplexity" status={surfaces.perplexity.status} />
                <SurfaceRow title="Payments in the chat" status={surfaces.payments.status}>
                  <span>Not in this release: buyers finish and pay with your shop.</span>
                </SurfaceRow>
              </ul>
              {view.platform_subdomains.apex ? (
                <p className="text-xs text-muted-foreground">
                  {view.platform_subdomains.in_use} of {view.platform_subdomains.cap} platform web addresses in use.
                </p>
              ) : null}
            </section>
          ) : null}
        </>
      ) : null}
    </div>
  );
}
