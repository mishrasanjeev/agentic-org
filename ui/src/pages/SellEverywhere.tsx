// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
import { useCallback, useEffect, useState } from "react";
import { Check, Copy, Megaphone } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import PaymentSettings from "@/components/commerce/PaymentSettings";
import { extractApiError, merchantSetupApi, sellEverywhereApi } from "../lib/api";

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
  payments: { status: string; mode?: string | null };
}

interface NetworkView {
  terms_version: string;
  suggested_slug: string;
  listing: Listing | null;
  surfaces: Surfaces;
  platform_subdomains: { apex: string | null; in_use: number; cap: number };
  subdomain_note?: { code: string; message: string } | null;
  grantex_enrolment?: { status: string; message: string; waiting?: boolean; merchant_enrolled?: boolean };
  publication?: { published: boolean; host: string | null; refusal: string | null };
  artifacts?: { status: string; reason: string | null };
}

interface MerchantSummary {
  merchant_id: string;
  seller_agent_id: string | null;
  display_name: string;
  catalog_source: string | null;
}

interface Setup {
  merchant: { merchant_id: string; seller_agent_id: string; display_name: string; categories: string[] };
  catalog: {
    status: string;
    source: string | null;
    shop_domain: string | null;
    product_count: number;
    last_synced_at: string | null;
    currency: string | null;
    last_error: string | null;
    webhook_status: string | null;
    webhook_path: string | null;
  };
  policies: { status: string; returns: string; shipping: string; pickup: string };
  order_limits: {
    status: string;
    currency: string | null;
    max_order_amount: string | null;
    max_daily_amount: string | null;
    max_quantity_per_order: number | null;
  };
  missing: string[];
  ready_to_turn_on: boolean;
}

type UploadProblem = { row: number | null; column: string | null; message: string };

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
  ready: { label: "Done", variant: "success" },
  set: { label: "Done", variant: "success" },
  missing: { label: "To do", variant: "secondary" },
  not_connected: { label: "To do", variant: "secondary" },
  not_uploaded: { label: "To do", variant: "secondary" },
  not_synced: { label: "Read your catalog", variant: "warning" },
  changed_in_store: { label: "Changed in your store", variant: "warning" },
  active: { label: "Enrolled", variant: "success" },
  unavailable: { label: "Waiting", variant: "warning" },
  not_configured: { label: "Waiting", variant: "warning" },
  revoked: { label: "Needs attention", variant: "destructive" },
  refused: { label: "Needs attention", variant: "destructive" },
  published: { label: "Published", variant: "success" },
  not_published: { label: "Not published", variant: "warning" },
};

const REFUSALS: Record<string, string> = {
  serving_host_not_attested:
    "Waiting for your web address. Turn on Sell everywhere, or connect your own domain in Custom Domains.",
  signing_key_missing: "Your shop has no signing key yet. Select Publish to create it.",
  namespace_not_authority_bound: "Your web address cannot carry your shop profile. Contact support.",
  vendor_entry_outside_namespace: "An integration claims your web address outside your shop. Contact support.",
};

const MISSING_LABEL: Record<string, string> = {
  catalog: "your catalog",
  policies: "your policies",
  order_limits: "your order limits",
};

function StatusBadge({ status }: { status: string }) {
  const known = STATUS[status] ?? { label: status.replace(/_/g, " "), variant: "secondary" as const };
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

function Step({
  number,
  title,
  status,
  children,
}: {
  number: number;
  title: string;
  status?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="space-y-4 rounded-xl border p-5" aria-label={title}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-lg font-semibold">
          {number}. {title}
        </h3>
        {status ? <StatusBadge status={status} /> : null}
      </div>
      {children}
    </section>
  );
}

const inputClass = "h-11 rounded-md border bg-background px-3 sm:h-10";
const areaClass = "min-h-[5rem] rounded-md border bg-background px-3 py-2";

/** The one setup page: a shop, its catalog, policies and order limits, then every AI surface at once. */
export default function SellEverywhere() {
  const [merchants, setMerchants] = useState<MerchantSummary[]>([]);
  const [merchantId, setMerchantId] = useState("");
  const [setup, setSetup] = useState<Setup | null>(null);
  const [network, setNetwork] = useState<NetworkView | null>(null);
  const [networkEnabled, setNetworkEnabled] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const [newShopName, setNewShopName] = useState("");
  const [newShopCategory, setNewShopCategory] = useState("");

  const [catalogMode, setCatalogMode] = useState<"shopify" | "upload">("shopify");
  const [shopDomain, setShopDomain] = useState("");
  const [shopToken, setShopToken] = useState("");
  const [webhookSecret, setWebhookSecret] = useState("");
  const [uploadProblems, setUploadProblems] = useState<UploadProblem[]>([]);

  const [returnsPolicy, setReturnsPolicy] = useState("");
  const [shippingPolicy, setShippingPolicy] = useState("");
  const [pickupPolicy, setPickupPolicy] = useState("");

  const [currency, setCurrency] = useState("");
  const [maxOrder, setMaxOrder] = useState("");
  const [maxDaily, setMaxDaily] = useState("");
  const [maxQuantity, setMaxQuantity] = useState("");

  const [slug, setSlug] = useState("");
  const [accepted, setAccepted] = useState(false);

  const applySetup = useCallback((data: Setup) => {
    setSetup(data);
    setReturnsPolicy(data.policies.returns);
    setShippingPolicy(data.policies.shipping);
    setPickupPolicy(data.policies.pickup);
    setCurrency(data.order_limits.currency ?? data.catalog.currency ?? "");
    setMaxOrder(data.order_limits.max_order_amount ?? "");
    setMaxDaily(data.order_limits.max_daily_amount ?? "");
    setMaxQuantity(data.order_limits.max_quantity_per_order ? String(data.order_limits.max_quantity_per_order) : "");
    if (data.catalog.source === "catalog_upload") setCatalogMode("upload");
    if (data.catalog.shop_domain) setShopDomain(data.catalog.shop_domain);
  }, []);

  const applyNetwork = useCallback((data: NetworkView) => {
    setNetwork(data);
    setSlug(data.listing?.slug ?? data.suggested_slug ?? "");
  }, []);

  const loadNetwork = useCallback(
    async (id: string) => {
      try {
        const { data } = await sellEverywhereApi.get(id);
        setNetworkEnabled(true);
        applyNetwork(data);
      } catch (e: any) {
        if (e?.response?.status === 404 && e?.response?.data?.detail === "Not Found") setNetworkEnabled(false);
        else setError(errorDetail(e));
      }
    },
    [applyNetwork],
  );

  const selectMerchant = useCallback(
    async (id: string) => {
      setMerchantId(id);
      setSetup(null);
      setNetwork(null);
      setError(null);
      setNotice(null);
      setUploadProblems([]);
      if (!id) return;
      setBusy("load");
      try {
        const { data } = await merchantSetupApi.get(id);
        applySetup(data);
        await loadNetwork(id);
      } catch (e) {
        setError(errorDetail(e));
      } finally {
        setBusy(null);
      }
    },
    [applySetup, loadNetwork],
  );

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const { data } = await merchantSetupApi.list();
        if (cancelled) return;
        const list: MerchantSummary[] = data.merchants ?? [];
        setMerchants(list);
        if (list.length === 1) void selectMerchant(list[0].merchant_id);
      } catch (e) {
        if (!cancelled) setError(errorDetail(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selectMerchant]);

  async function run(label: string, action: () => Promise<void>) {
    setBusy(label);
    setError(null);
    setNotice(null);
    try {
      await action();
    } catch (e) {
      setError(errorDetail(e));
    } finally {
      setBusy(null);
    }
  }

  const createShop = () =>
    run("create", async () => {
      const categories = newShopCategory.trim() ? [newShopCategory.trim()] : undefined;
      const { data } = await merchantSetupApi.create({ display_name: newShopName.trim(), categories });
      const created: MerchantSummary = {
        merchant_id: data.merchant.merchant_id,
        seller_agent_id: data.merchant.seller_agent_id,
        display_name: data.merchant.display_name,
        catalog_source: null,
      };
      setMerchants((items) => [created, ...items]);
      setNewShopName("");
      setNewShopCategory("");
      setMerchantId(created.merchant_id);
      applySetup(data);
      await loadNetwork(created.merchant_id);
    });

  const connectShopify = () =>
    run("shopify", async () => {
      try {
        const { data } = await merchantSetupApi.connectShopify(merchantId, {
          shop_domain: shopDomain.trim(),
          admin_access_token: shopToken.trim(),
          ...(webhookSecret.trim() ? { webhook_secret: webhookSecret.trim() } : {}),
        });
        applySetup(data.setup);
        if (data.sync_error) {
          const detail = data.sync_error;
          setError(typeof detail === "object" && detail?.message ? detail.message : "Reading the catalog failed. Try again.");
        } else {
          setNotice(`Connected. ${data.setup.catalog.product_count} products read from ${data.setup.catalog.shop_domain}.`);
        }
      } finally {
        setShopToken("");
        setWebhookSecret("");
      }
    });

  const uploadFile = (file: File) =>
    run("upload", async () => {
      setUploadProblems([]);
      const content = await file.text();
      const format = file.name.toLowerCase().endsWith(".json") ? "json" : "csv";
      try {
        const { data } = await merchantSetupApi.uploadCatalog(merchantId, { format, content });
        applySetup(data.setup);
        setNotice(`${data.upload.product_count} products uploaded.`);
      } catch (e: any) {
        const problems = e?.response?.data?.detail?.errors;
        if (Array.isArray(problems)) setUploadProblems(problems);
        throw e;
      }
    });

  const syncCatalog = () =>
    run("sync", async () => {
      const { data } = await merchantSetupApi.syncCatalog(merchantId);
      applySetup(data.setup);
      setNotice(`${data.setup.catalog.product_count} products read.`);
    });

  const savePolicies = () =>
    run("policies", async () => {
      const { data } = await merchantSetupApi.savePolicies(merchantId, {
        returns: returnsPolicy.trim(),
        shipping: shippingPolicy.trim(),
        pickup: pickupPolicy.trim(),
      });
      applySetup(data);
      setNotice("Policies saved.");
    });

  const saveLimits = () =>
    run("limits", async () => {
      const { data } = await merchantSetupApi.saveOrderLimits(merchantId, {
        currency: currency.trim().toUpperCase(),
        max_order_amount: maxOrder.trim(),
        max_daily_amount: maxDaily.trim(),
        max_quantity_per_order: Number(maxQuantity),
      });
      applySetup(data);
      setNotice("Order limits saved.");
    });

  const saveListing = (listed: boolean) =>
    run("listing", async () => {
      const body = {
        listed,
        accept_terms: listed ? accepted : false,
        ...(network?.listing ? {} : { slug: slug.trim() || undefined }),
      };
      const { data } = await sellEverywhereApi.put(merchantId, body);
      applyNetwork(data);
    });

  const publish = () =>
    run("publish", async () => {
      const { data } = await sellEverywhereApi.publish(merchantId);
      applyNetwork(data);
    });

  const listing = network?.listing ?? null;
  const listed = listing?.status === "listed";
  const surfaces = network?.surfaces;
  const webhookUrl = setup?.catalog.webhook_path ? `${window.location.origin}${setup.catalog.webhook_path}` : null;
  const limitsValid =
    /^[A-Za-z]{3}$/.test(currency.trim()) &&
    Number(maxOrder) > 0 &&
    Number(maxDaily) >= Number(maxOrder) &&
    Number(maxQuantity) >= 1;
  const publication = network?.publication;
  const refusalText = publication?.refusal
    ? (REFUSALS[publication.refusal] ?? publication.refusal.replace(/_/g, " "))
    : null;

  return (
    <div className="space-y-6">
      <div>
        <h2 className="flex items-center gap-2 text-2xl font-bold">
          <Megaphone className="h-6 w-6" /> Sell everywhere
        </h2>
        <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
          Set up your shop once, in about ten minutes. Buyers then reach it from ChatGPT, Claude and other AI apps, the
          AgenticOrg shopping bot on Telegram and the open web. You stay the seller: buyers always finish and pay with
          you.
        </p>
      </div>

      {error ? (
        <div className="rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive" role="alert">
          {error}
        </div>
      ) : null}
      {notice ? (
        <div className="rounded-lg border bg-muted/40 px-4 py-3 text-sm" role="status">
          {notice}
        </div>
      ) : null}

      <Step number={1} title="Your shop" status={setup ? "ready" : "missing"}>
        {merchants.length ? (
          <div className="flex max-w-md flex-col gap-1 text-sm">
            <label htmlFor="merchant-select">Shop</label>
            <select
              id="merchant-select"
              className={inputClass}
              value={merchantId}
              disabled={busy !== null}
              onChange={(e) => void selectMerchant(e.target.value)}
            >
              <option value="">Choose a shop</option>
              {merchants.map((item) => (
                <option key={item.merchant_id} value={item.merchant_id}>
                  {item.display_name}
                </option>
              ))}
            </select>
          </div>
        ) : null}
        <div className="flex flex-wrap items-end gap-3 text-sm">
          <label className="flex flex-col gap-1">
            {merchants.length ? "Or add another shop" : "Shop name"}
            <input className={inputClass} value={newShopName} onChange={(e) => setNewShopName(e.target.value)} />
          </label>
          <label className="flex flex-col gap-1">
            What you sell (optional)
            <input
              className={inputClass}
              placeholder="tea"
              value={newShopCategory}
              onChange={(e) => setNewShopCategory(e.target.value)}
            />
          </label>
          <Button
            variant="outline"
            disabled={busy !== null || newShopName.trim().length < 2}
            onClick={() => void createShop()}
          >
            Create shop
          </Button>
        </div>
      </Step>

      {setup ? (
        <>
          <Step number={2} title="Your catalog" status={setup.catalog.status}>
            <div className="flex flex-wrap gap-4 text-sm" role="radiogroup" aria-label="Where your products come from">
              <label className="flex items-center gap-2">
                <input type="radio" checked={catalogMode === "shopify"} onChange={() => setCatalogMode("shopify")} />
                Shopify
              </label>
              <label className="flex items-center gap-2">
                <input type="radio" checked={catalogMode === "upload"} onChange={() => setCatalogMode("upload")} />
                Upload a file (CSV or JSON)
              </label>
            </div>

            {catalogMode === "shopify" ? (
              <div className="space-y-3 text-sm">
                <ol className="list-decimal space-y-1 pl-5 text-muted-foreground">
                  <li>In your Shopify admin, open Settings, then Apps and sales channels, then Develop apps.</li>
                  <li>
                    Create an app (for example AgenticOrg), open Configuration and allow the Admin API scopes
                    read_products and read_inventory.
                  </li>
                  <li>Install the app, open API credentials and reveal the Admin API access token. Shopify shows it once: copy it.</li>
                  <li>
                    Paste your store address (your-store.myshopify.com) and the token below. AgenticOrg only reads
                    products, prices and stock.
                  </li>
                </ol>
                <div className="flex flex-wrap items-end gap-3">
                  <label className="flex flex-col gap-1">
                    Store address
                    <input
                      className={inputClass}
                      placeholder="your-store.myshopify.com"
                      value={shopDomain}
                      onChange={(e) => setShopDomain(e.target.value.trim().toLowerCase())}
                    />
                  </label>
                  <label className="flex flex-col gap-1">
                    Admin API access token
                    <input
                      className={inputClass}
                      type="password"
                      autoComplete="off"
                      value={shopToken}
                      onChange={(e) => setShopToken(e.target.value)}
                    />
                  </label>
                  <label className="flex flex-col gap-1">
                    Webhook signing secret (optional)
                    <input
                      className={inputClass}
                      type="password"
                      autoComplete="off"
                      value={webhookSecret}
                      onChange={(e) => setWebhookSecret(e.target.value)}
                    />
                  </label>
                  <Button
                    disabled={busy !== null || !shopDomain.endsWith(".myshopify.com") || shopToken.trim().length < 10}
                    onClick={() => void connectShopify()}
                  >
                    Connect Shopify
                  </Button>
                </div>
                {webhookUrl && setup.catalog.source === "shopify" ? (
                  <div className="space-y-1 text-muted-foreground">
                    <p>
                      Keep products current: in Shopify, open Settings, then Notifications, then Webhooks. Create a
                      Product update webhook in JSON format to this address, then paste the signing secret Shopify
                      shows there above and connect again.
                    </p>
                    <span className="flex items-center gap-2">
                      <code className="break-all">{webhookUrl}</code>
                      <CopyButton value={webhookUrl} label="webhook address" />
                    </span>
                    <p>
                      Webhook:{" "}
                      {setup.catalog.webhook_status === "configured" ? "signing secret saved" : "no signing secret yet"}
                    </p>
                  </div>
                ) : null}
              </div>
            ) : (
              <div className="space-y-2 text-sm">
                <p className="text-muted-foreground">
                  One product per row. Columns: sku, title, description, price, currency, quantity, image_url, url
                  (sku, title, price, currency and quantity are required; links start with https://). Up to 2,000
                  products, one currency.
                </p>
                <label className="flex flex-col gap-1">
                  Catalog file
                  <input
                    type="file"
                    accept=".csv,.json,text/csv,application/json"
                    disabled={busy !== null}
                    onChange={(e) => {
                      const file = e.target.files?.[0];
                      if (file) void uploadFile(file);
                      e.target.value = "";
                    }}
                  />
                </label>
                {uploadProblems.length ? (
                  <ul className="list-disc space-y-1 pl-5 text-destructive" aria-label="Problems in the file">
                    {uploadProblems.map((problem, index) => (
                      <li key={index}>
                        {problem.row ? `Row ${problem.row}` : "File"}
                        {problem.column ? `, ${problem.column}` : ""}: {problem.message}
                      </li>
                    ))}
                  </ul>
                ) : null}
              </div>
            )}

            {setup.catalog.source && setup.catalog.status !== "not_connected" ? (
              <div className="flex flex-wrap items-center gap-3 text-sm text-muted-foreground">
                <span>
                  {setup.catalog.product_count} products
                  {setup.catalog.last_synced_at ? `, read ${new Date(setup.catalog.last_synced_at).toLocaleString()}` : ""}
                </span>
                <Button variant="outline" disabled={busy !== null} onClick={() => void syncCatalog()}>
                  Read catalog again
                </Button>
              </div>
            ) : null}
          </Step>

          <Step number={3} title="Your policies" status={setup.policies.status}>
            <div className="grid gap-3 text-sm md:grid-cols-3">
              <label className="flex flex-col gap-1">
                Returns
                <textarea
                  className={areaClass}
                  placeholder="Unused items can be returned within 7 days for a full refund."
                  value={returnsPolicy}
                  onChange={(e) => setReturnsPolicy(e.target.value)}
                />
              </label>
              <label className="flex flex-col gap-1">
                Shipping
                <textarea
                  className={areaClass}
                  placeholder="Ships across India in 3 to 5 working days."
                  value={shippingPolicy}
                  onChange={(e) => setShippingPolicy(e.target.value)}
                />
              </label>
              <label className="flex flex-col gap-1">
                Store pickup (leave empty if you do not offer it)
                <textarea className={areaClass} value={pickupPolicy} onChange={(e) => setPickupPolicy(e.target.value)} />
              </label>
            </div>
            <Button
              variant="outline"
              disabled={busy !== null || returnsPolicy.trim().length < 10 || shippingPolicy.trim().length < 10}
              onClick={() => void savePolicies()}
            >
              Save policies
            </Button>
          </Step>

          <Step number={4} title="Order limits" status={setup.order_limits.status}>
            <p className="text-sm text-muted-foreground">
              AI agents can only start orders within these limits. Nothing is set for you: choose what suits your shop.
            </p>
            <div className="flex flex-wrap items-end gap-3 text-sm">
              <label className="flex flex-col gap-1">
                Currency
                <input
                  className={`${inputClass} w-24 uppercase`}
                  maxLength={3}
                  placeholder="INR"
                  value={currency}
                  onChange={(e) => setCurrency(e.target.value.toUpperCase())}
                />
              </label>
              <label className="flex flex-col gap-1">
                Largest single order
                <input
                  className={inputClass}
                  inputMode="decimal"
                  value={maxOrder}
                  onChange={(e) => setMaxOrder(e.target.value)}
                />
              </label>
              <label className="flex flex-col gap-1">
                Most per day
                <input
                  className={inputClass}
                  inputMode="decimal"
                  value={maxDaily}
                  onChange={(e) => setMaxDaily(e.target.value)}
                />
              </label>
              <label className="flex flex-col gap-1">
                Most items per order
                <input
                  className={`${inputClass} w-28`}
                  inputMode="numeric"
                  value={maxQuantity}
                  onChange={(e) => setMaxQuantity(e.target.value.replace(/[^0-9]/g, ""))}
                />
              </label>
              <Button variant="outline" disabled={busy !== null || !limitsValid} onClick={() => void saveLimits()}>
                Save limits
              </Button>
            </div>
          </Step>

          <Step number={5} title="Payments">
            <PaymentSettings merchantId={merchantId} />
          </Step>

          <Step number={6} title="Turn on Sell everywhere" status={listed ? "live" : undefined}>
            {!networkEnabled ? (
              <div className="rounded-lg border bg-muted/40 px-4 py-3 text-sm" role="status">
                Sell everywhere is not enabled for this deployment yet.
              </div>
            ) : network ? (
              <div className="space-y-3 text-sm">
                {!setup.ready_to_turn_on ? (
                  <p className="text-amber-700" role="status">
                    Finish {setup.missing.map((item) => MISSING_LABEL[item] ?? item).join(", ")} first.
                  </p>
                ) : null}
                <div className="flex max-w-md flex-col gap-1">
                  <label htmlFor="network-slug">Shop name on the network</label>
                  <input
                    id="network-slug"
                    className={inputClass}
                    value={slug}
                    disabled={Boolean(listing)}
                    onChange={(e) => setSlug(e.target.value.toLowerCase())}
                    aria-describedby="slug-help"
                  />
                  <span id="slug-help" className="text-xs text-muted-foreground">
                    3 to 40 lowercase letters, digits or hyphens. It names your shop in AI apps, in your Telegram link and
                    in{" "}
                    {network.platform_subdomains.apex
                      ? `${slug || "your-shop"}.${network.platform_subdomains.apex}`
                      : "your web address"}
                    . It cannot change later.
                  </span>
                </div>
                {!listed ? (
                  <label className="flex max-w-2xl items-start gap-2">
                    <input type="checkbox" className="mt-1" checked={accepted} onChange={(e) => setAccepted(e.target.checked)} />
                    <span>
                      I accept the AgenticOrg network terms (version {network.terms_version}): AgenticOrg lists my shop
                      and opens checkouts for buyers who find it. I remain the seller and take payment myself.
                    </span>
                  </label>
                ) : null}
                <div className="flex flex-wrap gap-2">
                  {listed ? (
                    <>
                      <Button disabled={busy !== null} onClick={() => void publish()}>
                        {publication?.published ? "Refresh listing" : "Publish"}
                      </Button>
                      <Button variant="outline" disabled={busy !== null} onClick={() => void saveListing(false)}>
                        Pause
                      </Button>
                    </>
                  ) : (
                    <Button
                      disabled={busy !== null || !accepted || !setup.ready_to_turn_on || (!listing && !slug.trim())}
                      onClick={() => void saveListing(true)}
                    >
                      {listing ? "Resume Sell everywhere" : "Turn on Sell everywhere"}
                    </Button>
                  )}
                </div>
                {network.subdomain_note ? (
                  <p className="text-amber-700" role="status">
                    {network.subdomain_note.message}
                  </p>
                ) : null}
                {listing ? (
                  <ul className="space-y-2" aria-label="Go-live progress">
                    <SurfaceRow title="Grantex enrolment" status={network.grantex_enrolment?.status ?? "not_started"}>
                      {network.grantex_enrolment?.message}
                    </SurfaceRow>
                    <SurfaceRow title="Shop profile" status={publication?.published ? "published" : "not_published"}>
                      {publication?.published
                        ? `Published at https://${publication.host}`
                        : (refusalText ?? "Select Publish to publish your shop profile.")}
                    </SurfaceRow>
                    <SurfaceRow
                      title="Verified catalog"
                      status={network.artifacts?.status === "ready" ? "ready" : "missing"}
                    >
                      {network.artifacts?.status === "ready"
                        ? "Grantex has verified your catalog, prices and stock."
                        : "Waiting for Grantex to verify your catalog. Select Publish to try again."}
                    </SurfaceRow>
                  </ul>
                ) : null}
              </div>
            ) : null}
          </Step>

          {surfaces ? (
            <section className="space-y-3" aria-label="Where buyers can find you">
              <h3 className="text-lg font-semibold">Where buyers can find you</h3>
              <ul className="space-y-3">
                <SurfaceRow title="Your web address" status={surfaces.web.status}>
                  {surfaces.web.host ? (
                    <span>https://{surfaces.web.host} (AI search engines and agents read your profile here)</span>
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
                <SurfaceRow title="Payments" status={surfaces.payments.status}>
                  <span>
                    {surfaces.payments.mode === "shopify_cart"
                      ? "Buyers pay in your own Shopify checkout."
                      : surfaces.payments.mode === "plural_hosted"
                        ? "Buyers pay on your own Plural payment page."
                        : "Choose where buyers pay in step 5."}
                  </span>
                </SurfaceRow>
              </ul>
              {network?.platform_subdomains.apex ? (
                <p className="text-xs text-muted-foreground">
                  {network.platform_subdomains.in_use} of {network.platform_subdomains.cap} platform web addresses in use.
                </p>
              ) : null}
            </section>
          ) : null}
        </>
      ) : null}
    </div>
  );
}
