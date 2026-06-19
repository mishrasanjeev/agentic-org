import { useMemo, useState } from "react";
import { Bot, Box, KeyRound, RefreshCw, Send, ShieldCheck } from "lucide-react";
import { commerceRuntimeApi, extractApiError } from "../lib/api";

type RuntimeLog = {
  label: string;
  detail: string;
  tone: "ok" | "warn" | "neutral";
};

export default function CommerceRuntimeDemo() {
  const [merchantId, setMerchantId] = useState("merchant_demo");
  const [sellerAgentId, setSellerAgentId] = useState("seller_agent_demo");
  const [buyerAgentId, setBuyerAgentId] = useState("buyer_agent_demo");
  const [displayName, setDisplayName] = useState("Demo Shopify Store");
  const [categories, setCategories] = useState("apparel, accessories");
  const [shopDomain, setShopDomain] = useState("mgx0n6-22.myshopify.com");
  const [shopifyToken, setShopifyToken] = useState("");
  const [shopifyOauthCode, setShopifyOauthCode] = useState("");
  const [shopifyClientId, setShopifyClientId] = useState("");
  const [shopifyClientSecret, setShopifyClientSecret] = useState("");
  const [shopifyRedirectUri, setShopifyRedirectUri] = useState("https://app.agenticorg.ai/callback");
  const [shopifyStatus, setShopifyStatus] = useState<any>(null);
  const [packetId, setPacketId] = useState("");
  const [evidenceId, setEvidenceId] = useState("");
  const [buyerQuestion, setBuyerQuestion] = useState("Show me available products with prices");
  const [buyerAnswer, setBuyerAnswer] = useState<any>(null);
  const [products, setProducts] = useState<any[]>([]);
  const [bridgeMatrix, setBridgeMatrix] = useState<any>(null);
  const [adapterPayloads, setAdapterPayloads] = useState<any>(null);
  const [capabilityStatus, setCapabilityStatus] = useState<any>(null);
  const [purchaseStatus, setPurchaseStatus] = useState<any>(null);
  const [logs, setLogs] = useState<RuntimeLog[]>([]);
  const [busy, setBusy] = useState<string | null>(null);

  const categoryList = useMemo(
    () => categories.split(",").map((item) => item.trim()).filter(Boolean),
    [categories],
  );
  const setupStates = useMemo(() => {
    const bridgeReady = bridgeMatrix?.surfaces?.filter((item: any) => item.status === "bridge_ready").length || 0;
    const bridgeTotal = bridgeMatrix?.surfaces?.length || 0;
    return [
      { label: "Onboarding", value: packetId ? "packet ready" : "not created", tone: packetId ? "ok" : "warn" },
      { label: "Shopify", value: shopifyStatus?.status || "not checked", tone: shopifyStatus?.status && shopifyStatus.status !== "not_configured" ? "ok" : "warn" },
      { label: "Grantex artifacts", value: adapterPayloads?.status || (evidenceId ? "evidence ready" : "not issued"), tone: adapterPayloads?.status === "adapter_payloads_ready" ? "ok" : "warn" },
      { label: "Buyer channels", value: bridgeTotal ? `${bridgeReady}/${bridgeTotal} ready` : "not checked", tone: bridgeReady === bridgeTotal && bridgeTotal > 0 ? "ok" : "warn" },
      { label: "Plural/Pine", value: capabilityStatus?.evidence?.result_status || "not verified", tone: capabilityStatus?.evidence?.result_status === "available" ? "ok" : "warn" },
    ];
  }, [adapterPayloads, bridgeMatrix, capabilityStatus, evidenceId, packetId, shopifyStatus]);

  function pushLog(label: string, detail: string, tone: RuntimeLog["tone"] = "neutral") {
    setLogs((items) => [{ label, detail, tone }, ...items].slice(0, 8));
  }

  async function createPacket() {
    setBusy("packet");
    try {
      const { data } = await commerceRuntimeApi.createOnboardingPacket({
        merchant_id: merchantId,
        seller_agent_id: sellerAgentId,
        merchant_display_name: displayName,
        public_brand_profile: { display_name: displayName },
        commerce_categories: categoryList,
        requested_grantex_authority_scope: {
          artifact_families: [
            "merchant_profile",
            "seller_agent_card",
            "connector_evidence",
            "catalog_snapshot",
            "offer_price_snapshot",
            "inventory_snapshot",
            "policy_scope",
            "public_discovery_state",
            "mandate_capability",
            "protocol_adapter",
            "authority_request_status",
          ],
        },
        artifact_cache_scope: { merchant_id: merchantId, seller_agent_id: sellerAgentId },
        source_freshness_policy: { max_age_seconds: 900 },
        shopify_shop_domain: shopDomain,
        permitted_sync_actions: [
          "read_products",
          "read_variants",
          "read_product_images",
          "read_prices",
          "read_inventory_snapshot",
          "receive_product_webhooks",
          "receive_inventory_webhooks",
        ],
        channel_capability_preferences: {
          web: true,
          chatgpt: true,
          claude: true,
          gemini: true,
          perplexity: true,
          whatsapp: true,
          telegram: true,
        },
        payment_mandate_rail_preference: "plural_pine_p3p",
        connector_metadata: { credential_ref: "tenant_connector_config", mode: "read_only", shop_domain: shopDomain },
      });
      setPacketId(data.packet.packet_id);
      pushLog("Onboarding packet", data.packet.packet_id, "ok");
    } catch (error) {
      pushLog("Onboarding blocked", extractApiError(error), "warn");
    } finally {
      setBusy(null);
    }
  }

  async function saveShopifyConnector() {
    setBusy("credentials");
    try {
      const payload: any = {
        merchant_id: merchantId,
        shop_domain: shopDomain,
        api_version: "2026-04",
        validate_read: true,
      };
      if (shopifyToken.trim()) {
        payload.admin_access_token = shopifyToken.trim();
      } else {
        payload.oauth_code = shopifyOauthCode.trim();
        payload.client_id = shopifyClientId.trim();
        payload.client_secret = shopifyClientSecret.trim();
        payload.redirect_uri = shopifyRedirectUri.trim();
      }
      const { data } = await commerceRuntimeApi.upsertShopifyCredentials(payload);
      setShopifyToken("");
      setShopifyOauthCode("");
      setShopifyClientSecret("");
      setShopifyStatus(data);
      pushLog("Shopify connector", data.status, "ok");
    } catch (error) {
      setShopifyToken("");
      setShopifyClientSecret("");
      pushLog("Shopify connector blocked", extractApiError(error), "warn");
    } finally {
      setBusy(null);
    }
  }

  async function loadShopifyStatus() {
    setBusy("credential-status");
    try {
      const { data } = await commerceRuntimeApi.getShopifyStatus({ merchant_id: merchantId });
      setShopifyStatus(data);
      pushLog("Shopify connector status", data.health_status || data.status, data.status === "not_configured" ? "warn" : "ok");
    } catch (error) {
      pushLog("Shopify status blocked", extractApiError(error), "warn");
    } finally {
      setBusy(null);
    }
  }

  async function syncShopify() {
    if (!packetId) return;
    setBusy("sync");
    try {
      const { data } = await commerceRuntimeApi.syncShopify({
        packet_id: packetId,
        page_size: 50,
        max_pages: 4,
      });
      setEvidenceId(data.evidence_id);
      pushLog("Shopify sync", `${data.product_count} products, ${data.variant_count} variants`, "ok");
      await loadProducts();
    } catch (error) {
      pushLog("Shopify sync blocked", extractApiError(error), "warn");
    } finally {
      setBusy(null);
    }
  }

  async function requestAuthority() {
    if (!packetId || !evidenceId) return;
    setBusy("authority");
    try {
      const { data } = await commerceRuntimeApi.requestGrantexAuthority({
        packet_id: packetId,
        evidence_id: evidenceId,
      });
      pushLog("Grantex authority", data.status || "request returned", data.artifacts ? "ok" : "warn");
      if (Array.isArray(data.artifacts) && data.artifacts.length) {
        await commerceRuntimeApi.cacheArtifacts({
          artifacts: data.artifacts,
          buyer_agent_id: buyerAgentId || undefined,
        });
        pushLog("Artifact cache", `${data.artifacts.length} artifacts cached`, "ok");
        await loadAdapters();
      }
    } catch (error) {
      pushLog("Authority blocked", extractApiError(error), "warn");
    } finally {
      setBusy(null);
    }
  }

  async function loadProducts() {
    try {
      const { data } = await commerceRuntimeApi.listProducts({
        merchant_id: merchantId,
        seller_agent_id: sellerAgentId,
      });
      setProducts(data.products || []);
    } catch {
      setProducts([]);
    }
  }

  async function askBuyer() {
    setBusy("buyer");
    try {
      const { data } = await commerceRuntimeApi.askBuyerQuestion({
        merchant_id: merchantId,
        seller_agent_id: sellerAgentId,
        buyer_agent_id: buyerAgentId || undefined,
        question: buyerQuestion,
        action_intent: "non_binding_preview",
        grantex_available: false,
      });
      setBuyerAnswer(data);
      pushLog("Buyer answer", data.status, data.status === "answered" ? "ok" : "warn");
    } catch (error) {
      pushLog("Buyer session blocked", extractApiError(error), "warn");
    } finally {
      setBusy(null);
    }
  }

  async function verifyCapability() {
    setBusy("capability");
    try {
      const { data } = await commerceRuntimeApi.verifyPluralPineCapability({
        merchant_id: merchantId,
        seller_agent_id: sellerAgentId,
        capability_type: "mandate_capability",
      });
      setCapabilityStatus(data);
      pushLog("Mandate capability", data.evidence.result_status, data.stored ? "ok" : "warn");
    } catch (error) {
      pushLog("Capability check blocked", extractApiError(error), "warn");
    } finally {
      setBusy(null);
    }
  }

  async function loadBridgeReadiness() {
    setBusy("bridges");
    try {
      const { data } = await commerceRuntimeApi.getBridgeSurfaces();
      setBridgeMatrix(data);
      const blocked = (data.surfaces || []).filter((surface: any) => surface.status !== "bridge_ready").length;
      pushLog("Buyer channels", blocked ? `${blocked} channel configs missing` : "all configured channels ready", blocked ? "warn" : "ok");
    } catch (error) {
      pushLog("Bridge readiness blocked", extractApiError(error), "warn");
    } finally {
      setBusy(null);
    }
  }

  async function loadAdapters() {
    setBusy("adapters");
    try {
      const { data } = await commerceRuntimeApi.getProtocolAdapters({
        merchant_id: merchantId,
        seller_agent_id: sellerAgentId,
        buyer_agent_id: buyerAgentId || undefined,
      });
      setAdapterPayloads(data);
      pushLog("Protocol adapters", data.status, data.status === "adapter_payloads_ready" ? "ok" : "warn");
    } catch (error) {
      pushLog("Adapter generation blocked", extractApiError(error), "warn");
    } finally {
      setBusy(null);
    }
  }

  async function preparePurchase() {
    const firstProduct = products[0];
    const query = firstProduct?.product_ref || firstProduct?.title || "Canvas Tote";
    setBusy("purchase");
    try {
      const { data } = await commerceRuntimeApi.preparePurchase({
        merchant_id: merchantId,
        seller_agent_id: sellerAgentId,
        buyer_agent_id: buyerAgentId || undefined,
        product_ref_or_query: query,
        quantity: 1,
        grantex_available: true,
        live_execution_approved: false,
      });
      setPurchaseStatus(data);
      pushLog("Purchase handoff", data.status, data.status === "prepared_provider_handoff" ? "ok" : "warn");
    } catch (error) {
      pushLog("Purchase handoff blocked", extractApiError(error), "warn");
    } finally {
      setBusy(null);
    }
  }

  return (
    <main className="min-h-screen bg-slate-950 text-slate-100">
      <div className="mx-auto flex max-w-7xl flex-col gap-6 px-4 py-6 lg:px-8">
        <section className="grid gap-4 lg:grid-cols-[1fr_1.1fr]">
          <div className="rounded-lg border border-slate-800 bg-slate-900/70 p-4">
            <div className="mb-4 flex items-center gap-2 text-sm font-semibold text-slate-200">
              <Bot className="h-4 w-4" />
              Seller Commerce Agent
            </div>
            <div className="grid gap-3">
              <input className="rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm" value={merchantId} onChange={(event) => setMerchantId(event.target.value)} aria-label="Merchant ID" />
              <input className="rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm" value={sellerAgentId} onChange={(event) => setSellerAgentId(event.target.value)} aria-label="Seller agent ID" />
              <input className="rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm" value={buyerAgentId} onChange={(event) => setBuyerAgentId(event.target.value)} aria-label="Buyer agent ID" />
              <input className="rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm" value={displayName} onChange={(event) => setDisplayName(event.target.value)} aria-label="Merchant display name" />
              <input className="rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm" value={categories} onChange={(event) => setCategories(event.target.value)} aria-label="Commerce categories" />
            </div>
            <div className="mt-4 grid gap-3 border-t border-slate-800 pt-4">
              <div className="flex items-center gap-2 text-sm font-semibold text-slate-200">
                <KeyRound className="h-4 w-4" />
                Shopify Connector
              </div>
              <input className="rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm" value={shopDomain} onChange={(event) => setShopDomain(event.target.value)} aria-label="Shopify shop domain" />
              <input className="rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm" type="password" value={shopifyToken} onChange={(event) => setShopifyToken(event.target.value)} aria-label="Shopify Admin API token" autoComplete="off" />
              <div className="grid gap-2 md:grid-cols-2">
                <input className="rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm" value={shopifyOauthCode} onChange={(event) => setShopifyOauthCode(event.target.value)} aria-label="Shopify OAuth code" autoComplete="off" />
                <input className="rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm" value={shopifyClientId} onChange={(event) => setShopifyClientId(event.target.value)} aria-label="Shopify client ID" autoComplete="off" />
                <input className="rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm" type="password" value={shopifyClientSecret} onChange={(event) => setShopifyClientSecret(event.target.value)} aria-label="Shopify client secret" autoComplete="off" />
                <input className="rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm" value={shopifyRedirectUri} onChange={(event) => setShopifyRedirectUri(event.target.value)} aria-label="Shopify redirect URI" autoComplete="off" />
              </div>
              <div className="grid grid-cols-2 gap-2">
                <button className="inline-flex items-center justify-center gap-2 rounded-md bg-emerald-400 px-3 py-2 text-sm font-semibold text-slate-950 disabled:opacity-50" onClick={saveShopifyConnector} disabled={busy !== null}>
                  <KeyRound className="h-4 w-4" />
                  Save
                </button>
                <button className="inline-flex items-center justify-center gap-2 rounded-md border border-slate-700 px-3 py-2 text-sm font-semibold disabled:opacity-50" onClick={loadShopifyStatus} disabled={busy !== null}>
                  <ShieldCheck className="h-4 w-4" />
                  Status
                </button>
              </div>
              {shopifyStatus && (
                <div className="rounded-md border border-slate-800 bg-slate-950 p-3 text-xs text-slate-300">
                  <div>{shopifyStatus.status}</div>
                  <div>{shopifyStatus.shop_domain}</div>
                  <div>{shopifyStatus.credential_values_redacted ? "credentials redacted" : ""}</div>
                </div>
              )}
            </div>
            <div className="mt-4 grid grid-cols-2 gap-2">
              <button className="inline-flex items-center justify-center gap-2 rounded-md bg-cyan-500 px-3 py-2 text-sm font-semibold text-slate-950 disabled:opacity-50" onClick={createPacket} disabled={busy !== null}>
                <ShieldCheck className="h-4 w-4" />
                Create
              </button>
              <button className="inline-flex items-center justify-center gap-2 rounded-md border border-slate-700 px-3 py-2 text-sm font-semibold disabled:opacity-50" onClick={syncShopify} disabled={!packetId || busy !== null}>
                <RefreshCw className="h-4 w-4" />
                Sync
              </button>
              <button className="inline-flex items-center justify-center gap-2 rounded-md border border-slate-700 px-3 py-2 text-sm font-semibold disabled:opacity-50" onClick={requestAuthority} disabled={!packetId || !evidenceId || busy !== null}>
                <Box className="h-4 w-4" />
                Issue
              </button>
              <button className="inline-flex items-center justify-center gap-2 rounded-md border border-slate-700 px-3 py-2 text-sm font-semibold disabled:opacity-50" onClick={verifyCapability} disabled={busy !== null}>
                <ShieldCheck className="h-4 w-4" />
                Verify
              </button>
              <button className="inline-flex items-center justify-center gap-2 rounded-md border border-slate-700 px-3 py-2 text-sm font-semibold disabled:opacity-50" onClick={loadBridgeReadiness} disabled={busy !== null}>
                <Bot className="h-4 w-4" />
                Channels
              </button>
              <button className="inline-flex items-center justify-center gap-2 rounded-md border border-slate-700 px-3 py-2 text-sm font-semibold disabled:opacity-50" onClick={loadAdapters} disabled={busy !== null}>
                <Box className="h-4 w-4" />
                Adapters
              </button>
            </div>
          </div>

          <div className="rounded-lg border border-slate-800 bg-slate-900/70 p-4">
            <div className="mb-4 flex items-center gap-2 text-sm font-semibold text-slate-200">
              <Send className="h-4 w-4" />
              Buyer Session
            </div>
            <textarea className="min-h-24 w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm" value={buyerQuestion} onChange={(event) => setBuyerQuestion(event.target.value)} aria-label="Buyer question" />
            <div className="mt-3 flex flex-wrap gap-2">
              <button className="inline-flex items-center justify-center gap-2 rounded-md bg-emerald-400 px-3 py-2 text-sm font-semibold text-slate-950 disabled:opacity-50" onClick={askBuyer} disabled={busy !== null}>
                <Send className="h-4 w-4" />
                Ask
              </button>
              <button className="rounded-md border border-slate-700 px-3 py-2 text-sm font-semibold disabled:opacity-50" onClick={loadProducts} disabled={busy !== null}>
                Products
              </button>
              <button className="rounded-md border border-slate-700 px-3 py-2 text-sm font-semibold disabled:opacity-50" onClick={preparePurchase} disabled={busy !== null}>
                Prepare
              </button>
            </div>
            {buyerAnswer && (
              <div className="mt-4 rounded-md border border-slate-700 bg-slate-950 p-3 text-sm">
                <div className="mb-2 flex flex-wrap gap-2 text-xs text-slate-400">
                  <span>{buyerAnswer.source_label}</span>
                  <span>{buyerAnswer.freshness_label}</span>
                  <span>Inventory: snapshot, not a reservation</span>
                </div>
                <p className="leading-6 text-slate-100">{buyerAnswer.answer}</p>
              </div>
            )}
          </div>
        </section>

        <section className="rounded-lg border border-slate-800 bg-slate-900/70 p-4">
          <div className="mb-3 text-sm font-semibold">Setup State</div>
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-5">
            {setupStates.map((item) => (
              <div key={item.label} className="rounded-md border border-slate-800 bg-slate-950 p-3">
                <div className="text-xs text-slate-400">{item.label}</div>
                <div className={item.tone === "ok" ? "mt-1 text-sm font-semibold text-emerald-300" : "mt-1 text-sm font-semibold text-amber-300"}>{item.value}</div>
              </div>
            ))}
          </div>
          {purchaseStatus && (
            <div className="mt-3 rounded-md border border-slate-800 bg-slate-950 p-3 text-xs text-slate-300">
              <div className="font-semibold text-slate-100">{purchaseStatus.status}</div>
              <div>{purchaseStatus.source_label}</div>
              <div>{purchaseStatus.freshness_label}</div>
              {purchaseStatus.blocker && <div className="mt-1 text-amber-300">{purchaseStatus.blocker.code}: {purchaseStatus.blocker.action}</div>}
            </div>
          )}
        </section>

        <section className="grid gap-4 lg:grid-cols-2">
          <div className="rounded-lg border border-slate-800 bg-slate-900/70 p-4">
            <div className="mb-3 text-sm font-semibold">Cached Products</div>
            <div className="grid gap-2">
              {products.slice(0, 6).map((product) => (
                <div key={product.product_ref || product.title} className="rounded-md border border-slate-800 bg-slate-950 p-3">
                  <div className="font-medium">{product.title}</div>
                  <div className="text-xs text-slate-400">{product.vendor || "Vendor unknown"} / {product.product_type || "Type unknown"}</div>
                </div>
              ))}
              {!products.length && <div className="text-sm text-slate-400">No cached product snapshots loaded.</div>}
            </div>
          </div>
          <div className="rounded-lg border border-slate-800 bg-slate-900/70 p-4">
            <div className="mb-3 text-sm font-semibold">Runtime Status</div>
            <div className="grid gap-2">
              {logs.map((item, index) => (
                <div key={`${item.label}-${index}`} className="rounded-md border border-slate-800 bg-slate-950 p-3">
                  <div className={item.tone === "ok" ? "text-sm font-medium text-emerald-300" : item.tone === "warn" ? "text-sm font-medium text-amber-300" : "text-sm font-medium text-slate-200"}>{item.label}</div>
                  <div className="break-words text-xs text-slate-400">{item.detail}</div>
                </div>
              ))}
              {!logs.length && <div className="text-sm text-slate-400">No runtime events yet.</div>}
            </div>
          </div>
        </section>
      </div>
    </main>
  );
}
