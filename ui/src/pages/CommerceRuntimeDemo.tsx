import { useMemo, useState } from "react";
import {
  Bot,
  Box,
  Building2,
  CreditCard,
  Globe2,
  KeyRound,
  MapPin,
  RefreshCw,
  Send,
  ShieldCheck,
  Store,
} from "lucide-react";
import { commerceRuntimeApi, extractApiError } from "../lib/api";

type RuntimeLog = {
  label: string;
  detail: string;
  tone: "ok" | "warn" | "neutral";
};

type ChannelKey = "web" | "chatgpt" | "claude" | "gemini" | "perplexity" | "whatsapp" | "telegram";

const CHANNELS: { key: ChannelKey; label: string }[] = [
  { key: "web", label: "Web" },
  { key: "chatgpt", label: "ChatGPT" },
  { key: "claude", label: "Claude" },
  { key: "gemini", label: "Gemini" },
  { key: "perplexity", label: "Perplexity" },
  { key: "whatsapp", label: "WhatsApp" },
  { key: "telegram", label: "Telegram" },
];

const SOURCE_TYPES = [
  { value: "shopify", label: "Shopify" },
  { value: "woocommerce", label: "WooCommerce" },
  { value: "erp", label: "ERP" },
  { value: "pim", label: "PIM" },
  { value: "oms", label: "OMS" },
  { value: "wms", label: "WMS" },
  { value: "custom_api", label: "Custom API" },
];

const PAYMENT_TYPES = [
  { value: "plural_pine", label: "Pine Labs Plural/P3P" },
  { value: "bank", label: "Bank-owned rail" },
  { value: "fintech_rail", label: "Fintech rail" },
  { value: "custom_provider", label: "Custom provider" },
  { value: "none", label: "None" },
];

const CUSTODY_OPTIONS = [
  { value: "agenticorg_vault", label: "AgenticOrg vault" },
  { value: "merchant_owned_integration", label: "Merchant owned" },
  { value: "external_integration_provider", label: "Integration provider" },
  { value: "provider_owned", label: "Provider owned" },
  { value: "not_required", label: "Not required" },
];

const APPROVAL_OPTIONS = [
  { value: "not_started", label: "Not started" },
  { value: "pending", label: "Pending" },
  { value: "approved", label: "Approved" },
  { value: "rejected", label: "Rejected" },
];

function fieldClassName() {
  return "rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100";
}

function buttonClassName(kind: "primary" | "secondary" = "secondary") {
  if (kind === "primary") {
    return "inline-flex items-center justify-center gap-2 rounded-md bg-emerald-400 px-3 py-2 text-sm font-semibold text-slate-950 disabled:opacity-50";
  }
  return "inline-flex items-center justify-center gap-2 rounded-md border border-slate-700 px-3 py-2 text-sm font-semibold text-slate-100 disabled:opacity-50";
}

function toneClassName(tone: RuntimeLog["tone"]) {
  if (tone === "ok") return "text-sm font-medium text-emerald-300";
  if (tone === "warn") return "text-sm font-medium text-amber-300";
  return "text-sm font-medium text-slate-200";
}

function statusTone(value: string | undefined) {
  return value && !/missing|blocked|disabled|pending|not_|failed|refused/i.test(value) ? "ok" : "warn";
}

export default function CommerceRuntimeDemo() {
  const [merchantId, setMerchantId] = useState("merchant_demo");
  const [sellerAgentId, setSellerAgentId] = useState("seller_agent_demo");
  const [buyerAgentId, setBuyerAgentId] = useState("buyer_agent_demo");
  const [displayName, setDisplayName] = useState("Demo Shopify Store");
  const [categories, setCategories] = useState("apparel, accessories");

  const [sourceType, setSourceType] = useState("shopify");
  const [sourceStoreId, setSourceStoreId] = useState("mgx0n6-22.myshopify.com");
  const [sourceBaseUrl, setSourceBaseUrl] = useState("");
  const [sourceApiVersion, setSourceApiVersion] = useState("2026-04");
  const [sourceCredentialCustody, setSourceCredentialCustody] = useState("agenticorg_vault");
  const [sourceCredentialRef, setSourceCredentialRef] = useState("tenant_connector_config");

  const [shopDomain, setShopDomain] = useState("mgx0n6-22.myshopify.com");
  const [shopifyToken, setShopifyToken] = useState("");
  const [shopifyOauthCode, setShopifyOauthCode] = useState("");
  const [shopifyClientId, setShopifyClientId] = useState("");
  const [shopifyClientSecret, setShopifyClientSecret] = useState("");
  const [shopifyRedirectUri, setShopifyRedirectUri] = useState("https://app.agenticorg.ai/callback");
  const [shopifyStatus, setShopifyStatus] = useState<any>(null);

  const [channelEnabled, setChannelEnabled] = useState<Record<ChannelKey, boolean>>({
    web: true,
    chatgpt: true,
    claude: true,
    gemini: true,
    perplexity: true,
    whatsapp: false,
    telegram: false,
  });
  const [channelApprovals, setChannelApprovals] = useState<Record<ChannelKey, string>>({
    web: "not_required",
    chatgpt: "pending",
    claude: "pending",
    gemini: "pending",
    perplexity: "pending",
    whatsapp: "not_started",
    telegram: "not_started",
  });
  const [whatsappCredentialRef, setWhatsappCredentialRef] = useState("provider:wa");
  const [telegramCredentialRef, setTelegramCredentialRef] = useState("provider:tg");

  const [paymentType, setPaymentType] = useState("plural_pine");
  const [paymentProviderName, setPaymentProviderName] = useState("Pine Labs Plural/P3P");
  const [paymentProviderKey, setPaymentProviderKey] = useState("plural_pine");
  const [paymentEnvironment, setPaymentEnvironment] = useState("sandbox");
  const [paymentCredentialCustody, setPaymentCredentialCustody] = useState("provider_owned");
  const [paymentCredentialRef, setPaymentCredentialRef] = useState("provider:plural");

  const [publicCatalogEnabled, setPublicCatalogEnabled] = useState(false);
  const [publicBaseUrl, setPublicBaseUrl] = useState("");
  const [offlinePosEnabled, setOfflinePosEnabled] = useState(false);
  const [posStoreId, setPosStoreId] = useState("store_demo");
  const [posStoreName, setPosStoreName] = useState("Demo Store POS");
  const [posProvider, setPosProvider] = useState("merchant_pos");
  const [posCity, setPosCity] = useState("Bengaluru");
  const [posCountryCode, setPosCountryCode] = useState("IN");
  const [posWebhookSecretRef, setPosWebhookSecretRef] = useState("env:POS_SECRET_REF");

  const [merchantConfig, setMerchantConfig] = useState<any>(null);
  const [configReadiness, setConfigReadiness] = useState<any>(null);
  const [packetId, setPacketId] = useState("");
  const [evidenceId, setEvidenceId] = useState("");
  const [buyerQuestion, setBuyerQuestion] = useState("Show me available products with prices");
  const [buyerAnswer, setBuyerAnswer] = useState<any>(null);
  const [products, setProducts] = useState<any[]>([]);
  const [bridgeMatrix, setBridgeMatrix] = useState<any>(null);
  const [adapterPayloads, setAdapterPayloads] = useState<any>(null);
  const [capabilityStatus, setCapabilityStatus] = useState<any>(null);
  const [purchaseStatus, setPurchaseStatus] = useState<any>(null);
  const [posReadiness, setPosReadiness] = useState<any>(null);
  const [posStatus, setPosStatus] = useState<any>(null);
  const [logs, setLogs] = useState<RuntimeLog[]>([]);
  const [busy, setBusy] = useState<string | null>(null);

  const categoryList = useMemo(
    () => categories.split(",").map((item) => item.trim()).filter(Boolean),
    [categories],
  );

  const buyerChannels = useMemo(() => {
    const channels: Record<string, any> = {};
    for (const channel of CHANNELS) {
      channels[channel.key] = {
        enabled: channelEnabled[channel.key],
        external_approval_status: channelApprovals[channel.key],
        credential_ref:
          channel.key === "whatsapp"
            ? whatsappCredentialRef
            : channel.key === "telegram"
              ? telegramCredentialRef
              : undefined,
      };
    }
    return channels;
  }, [channelApprovals, channelEnabled, telegramCredentialRef, whatsappCredentialRef]);

  const sourceConnector = useMemo(() => {
    const storeId = sourceType === "shopify" ? shopDomain : sourceStoreId;
    const sourceLabel = SOURCE_TYPES.find((item) => item.value === sourceType)?.label || sourceType;
    return {
      connector_key: `${sourceType}:${storeId || merchantId}`,
      connector_type: sourceType,
      store_id: storeId || merchantId,
      mode: sourceType === "shopify" ? "read_only" : "external_reference",
      enabled: true,
      credential_custody: sourceCredentialCustody,
      credential_ref: sourceCredentialRef,
      source_of_record: `${sourceLabel} source of record`,
      shop_domain: sourceType === "shopify" ? shopDomain : undefined,
      base_url: sourceType === "shopify" ? undefined : sourceBaseUrl,
      api_version: sourceApiVersion,
      sync_enabled: sourceType === "shopify",
    };
  }, [
    merchantId,
    shopDomain,
    sourceApiVersion,
    sourceBaseUrl,
    sourceCredentialCustody,
    sourceCredentialRef,
    sourceStoreId,
    sourceType,
  ]);

  const paymentProviders = useMemo(() => {
    if (paymentType === "none") return [];
    return [
      {
        provider_key: paymentProviderKey || paymentType,
        provider_type: paymentType,
        provider_display_name: paymentProviderName || paymentProviderKey || paymentType,
        environment: paymentEnvironment,
        enabled: true,
        credential_custody: paymentCredentialCustody,
        credential_ref: paymentCredentialRef,
        capability_types: ["mandate_capability"],
      },
    ];
  }, [
    paymentCredentialCustody,
    paymentCredentialRef,
    paymentEnvironment,
    paymentProviderKey,
    paymentProviderName,
    paymentType,
  ]);

  const offlinePosStores = useMemo(() => {
    if (!offlinePosEnabled) return [];
    return [
      {
        store_id: posStoreId,
        display_name: posStoreName,
        pos_provider: posProvider,
        enabled: true,
        city: posCity,
        country_code: posCountryCode,
        webhook_secret_ref: posWebhookSecretRef,
        staff_review_required: true,
      },
    ];
  }, [
    offlinePosEnabled,
    posCity,
    posCountryCode,
    posProvider,
    posStoreId,
    posStoreName,
    posWebhookSecretRef,
  ]);

  const setupStates = useMemo(() => {
    const bridgeReady = bridgeMatrix?.surfaces?.filter((item: any) => item.status === "bridge_ready").length || 0;
    const bridgeTotal = bridgeMatrix?.surfaces?.length || 0;
    const sourceState = configReadiness?.source_connectors?.[0]?.status || merchantConfig?.status || "not saved";
    const publicState = configReadiness?.public_publishing?.status || "not saved";
    const authorityState = adapterPayloads?.status === "adapter_payloads_ready"
      ? "issued and usable"
      : evidenceId
        ? "evidence ready"
        : "not requested";
    const cacheState = adapterPayloads?.status === "adapter_payloads_ready"
      ? "cache usable"
      : evidenceId
        ? "not cached"
        : "empty";
    const shopifySyncState = evidenceId
      ? "sync evidence ready"
      : shopifyStatus?.last_sync_at
        ? "synced"
        : "not synced";
    const bridgeState = bridgeTotal ? `${bridgeReady}/${bridgeTotal} ready` : "not checked";
    const paymentState = configReadiness?.payment_providers?.[0]?.status
      || capabilityStatus?.evidence?.result_status
      || "not verified";
    const posState = configReadiness?.offline_pos_stores?.[0]?.status
      || posReadiness?.simulator?.status
      || "not checked";
    return [
      { label: "Merchant Config", value: sourceState, tone: statusTone(sourceState) },
      { label: "Onboarding Packet", value: packetId ? "packet ready" : "not created", tone: packetId ? "ok" : "warn" },
      { label: "Shopify Connection", value: shopifyStatus?.status || "not checked", tone: statusTone(shopifyStatus?.status) },
      { label: "Shopify Webhook", value: shopifyStatus?.webhook_status || "not checked", tone: statusTone(shopifyStatus?.webhook_status) },
      { label: "Shopify Sync", value: shopifySyncState, tone: evidenceId || shopifyStatus?.last_sync_at ? "ok" : "warn" },
      { label: "Grantex Authority", value: authorityState, tone: adapterPayloads?.status === "adapter_payloads_ready" ? "ok" : "warn" },
      { label: "Artifact Cache", value: cacheState, tone: adapterPayloads?.status === "adapter_payloads_ready" ? "ok" : "warn" },
      { label: "Public Catalog", value: publicState, tone: publicState === "enabled" ? "ok" : "warn" },
      { label: "Bridge Readiness", value: bridgeState, tone: bridgeReady === bridgeTotal && bridgeTotal > 0 ? "ok" : "warn" },
      { label: "Pine/Plural", value: paymentState, tone: statusTone(paymentState) },
      { label: "POS Bridge", value: posState, tone: statusTone(posState) },
    ];
  }, [
    adapterPayloads,
    bridgeMatrix,
    capabilityStatus,
    configReadiness,
    evidenceId,
    merchantConfig,
    packetId,
    posReadiness,
    shopifyStatus,
  ]);

  function pushLog(label: string, detail: string, tone: RuntimeLog["tone"] = "neutral") {
    setLogs((items) => [{ label, detail, tone }, ...items].slice(0, 8));
  }

  function buildMerchantConfigPayload(syncOnboardingPacket = true) {
    return {
      seller_agent_id: sellerAgentId,
      merchant_display_name: displayName,
      public_brand_profile: { display_name: displayName },
      commerce_categories: categoryList,
      source_connectors: [sourceConnector],
      buyer_channels: buyerChannels,
      payment_providers: paymentProviders,
      offline_pos_stores: offlinePosStores,
      public_publishing: {
        enabled: publicCatalogEnabled,
        base_url: publicBaseUrl,
        publish_schema_org: true,
        publish_llms_txt: true,
        publish_sitemap: true,
      },
      source_freshness_policy: { max_age_seconds: 900 },
      provider_policy: {
        provider_owned_execution: true,
        grantex_executes_payment: false,
        agenticorg_executes_payment: false,
      },
      status: "configured",
      sync_onboarding_packet: syncOnboardingPacket,
    };
  }

  function hydrateMerchantConfig(config: any) {
    if (!config) return;
    setMerchantConfig(config);
    setSellerAgentId(config.seller_agent_id || sellerAgentId);
    setDisplayName(config.merchant_display_name || displayName);
    setCategories((config.commerce_categories || categoryList).join(", "));

    const source = config.source_connectors?.[0];
    if (source) {
      setSourceType(source.connector_type || "shopify");
      setSourceStoreId(source.store_id || source.shop_domain || sourceStoreId);
      setSourceBaseUrl(source.base_url || "");
      setSourceApiVersion(source.api_version || "2026-04");
      setSourceCredentialCustody(source.credential_custody || "agenticorg_vault");
      setSourceCredentialRef(source.credential_ref || "tenant_connector_config");
      if (source.shop_domain) setShopDomain(source.shop_domain);
    }

    const channels = config.buyer_channels || {};
    setChannelEnabled((current) => ({
      ...current,
      ...Object.fromEntries(CHANNELS.map((channel) => [channel.key, channels[channel.key]?.enabled === true])),
    }));
    setChannelApprovals((current) => ({
      ...current,
      ...Object.fromEntries(
        CHANNELS.map((channel) => [
          channel.key,
          channels[channel.key]?.external_approval_status || (channel.key === "web" ? "not_required" : "not_started"),
        ]),
      ),
    }));
    if (channels.whatsapp?.credential_ref) setWhatsappCredentialRef(channels.whatsapp.credential_ref);
    if (channels.telegram?.credential_ref) setTelegramCredentialRef(channels.telegram.credential_ref);

    const provider = config.payment_providers?.[0];
    if (provider) {
      setPaymentType(provider.provider_type || "none");
      setPaymentProviderName(provider.provider_display_name || "");
      setPaymentProviderKey(provider.provider_key || "");
      setPaymentEnvironment(provider.environment || "sandbox");
      setPaymentCredentialCustody(provider.credential_custody || "provider_owned");
      setPaymentCredentialRef(provider.credential_ref || "");
    } else {
      setPaymentType("none");
    }

    const publishing = config.public_publishing || {};
    setPublicCatalogEnabled(publishing.enabled === true);
    setPublicBaseUrl(publishing.base_url || "");

    const posStore = config.offline_pos_stores?.[0];
    setOfflinePosEnabled(Boolean(posStore));
    if (posStore) {
      setPosStoreId(posStore.store_id || "store_demo");
      setPosStoreName(posStore.display_name || "Store POS");
      setPosProvider(posStore.pos_provider || "merchant_pos");
      setPosCity(posStore.city || "");
      setPosCountryCode(posStore.country_code || "IN");
      setPosWebhookSecretRef(posStore.webhook_secret_ref || "env:POS_SECRET_REF");
    }
  }

  async function saveMerchantConfig() {
    setBusy("merchant-config");
    try {
      const { data } = await commerceRuntimeApi.upsertMerchantConfig(
        merchantId,
        buildMerchantConfigPayload(sourceType === "shopify"),
      );
      setMerchantConfig(data.config);
      setConfigReadiness(data.readiness);
      if (data.packet_id) setPacketId(data.packet_id);
      pushLog(
        "Merchant config",
        data.synced_onboarding_packet ? `saved, packet ${data.packet_id}` : data.onboarding_packet_sync_blocker,
        data.synced_onboarding_packet || sourceType !== "shopify" ? "ok" : "warn",
      );
    } catch (error) {
      pushLog("Merchant config blocked", extractApiError(error), "warn");
    } finally {
      setBusy(null);
    }
  }

  async function loadMerchantConfig() {
    setBusy("merchant-config-load");
    try {
      const { data } = await commerceRuntimeApi.getMerchantConfig(merchantId, {
        seller_agent_id: sellerAgentId,
      });
      if (data.config) {
        hydrateMerchantConfig(data.config);
        setConfigReadiness(data.readiness);
      }
      pushLog("Merchant config", data.status, data.status === "configured" ? "ok" : "warn");
    } catch (error) {
      pushLog("Merchant config load blocked", extractApiError(error), "warn");
    } finally {
      setBusy(null);
    }
  }

  async function loadMerchantReadiness() {
    setBusy("merchant-config-readiness");
    try {
      const { data } = await commerceRuntimeApi.getMerchantConfigReadiness(merchantId, {
        seller_agent_id: sellerAgentId,
      });
      setConfigReadiness(data);
      pushLog("Config readiness", data.status, data.status === "merchant_config_ready" ? "ok" : "warn");
    } catch (error) {
      pushLog("Config readiness blocked", extractApiError(error), "warn");
    } finally {
      setBusy(null);
    }
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
        channel_capability_preferences: Object.fromEntries(
          CHANNELS.map((channel) => [channel.key, channelEnabled[channel.key]]),
        ),
        payment_mandate_rail_preference: paymentType === "plural_pine" ? "plural_pine_p3p" : "none",
        connector_metadata: {
          credential_ref: sourceCredentialRef,
          mode: "read_only",
          shop_domain: shopDomain,
        },
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
        api_version: sourceApiVersion,
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
      setSourceCredentialRef(`commerce_shopify_${merchantId}`);
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

  async function loadPosReadiness() {
    setBusy("pos-readiness");
    try {
      const { data } = await commerceRuntimeApi.getOfflinePosReadiness();
      setPosReadiness(data);
      pushLog("Offline POS", data.real_pos_provider?.status || data.status, data.simulator?.status === "ready" ? "ok" : "warn");
    } catch (error) {
      pushLog("POS readiness blocked", extractApiError(error), "warn");
    } finally {
      setBusy(null);
    }
  }

  async function createPosHandoff() {
    const firstProduct = products[0];
    const query = firstProduct?.product_ref || firstProduct?.title || "Canvas Tote";
    setBusy("pos-handoff");
    try {
      const { data } = await commerceRuntimeApi.createOfflinePosHandoff({
        merchant_id: merchantId,
        seller_agent_id: sellerAgentId,
        buyer_agent_id: buyerAgentId || undefined,
        buyer_session_ref: "buyer_session_demo",
        product_ref_or_query: query,
        quantity: 1,
        store_id: posStoreId,
        pos_location: {
          display_name: posStoreName,
          city: posCity,
          country_code: posCountryCode,
          pos_provider: posProvider,
        },
        grantex_available: true,
      });
      setPosStatus(data);
      pushLog("POS handoff", data.status, data.pos_handoff_created ? "ok" : "warn");
    } catch (error) {
      pushLog("POS handoff blocked", extractApiError(error), "warn");
    } finally {
      setBusy(null);
    }
  }

  async function simulatePosConfirmation() {
    const packetIdForConfirmation = posStatus?.packet?.packet_id;
    if (!packetIdForConfirmation) return;
    setBusy("pos-confirm");
    try {
      const { data } = await commerceRuntimeApi.simulateOfflinePosConfirmation({
        packet_id: packetIdForConfirmation,
        confirmation_status: "accepted",
      });
      setPosStatus(data);
      pushLog("POS simulator", data.reconciliation?.seller_operator_status || data.status, "ok");
    } catch (error) {
      pushLog("POS simulator blocked", extractApiError(error), "warn");
    } finally {
      setBusy(null);
    }
  }

  return (
    <main className="min-h-screen bg-slate-950 text-slate-100">
      <div className="mx-auto flex max-w-7xl flex-col gap-6 px-4 py-6 lg:px-8">
        <section className="grid gap-4 lg:grid-cols-[1.2fr_0.8fr]">
          <div className="rounded-lg border border-slate-800 bg-slate-900/70 p-4">
            <div className="mb-4 flex items-center gap-2 text-sm font-semibold text-slate-200">
              <Store className="h-4 w-4" />
              Merchant Commerce Configuration
            </div>
            <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
              <input className={fieldClassName()} value={merchantId} onChange={(event) => setMerchantId(event.target.value)} aria-label="Merchant ID" />
              <input className={fieldClassName()} value={sellerAgentId} onChange={(event) => setSellerAgentId(event.target.value)} aria-label="Seller agent ID" />
              <input className={fieldClassName()} value={buyerAgentId} onChange={(event) => setBuyerAgentId(event.target.value)} aria-label="Buyer agent ID" />
              <input className={fieldClassName()} value={displayName} onChange={(event) => setDisplayName(event.target.value)} aria-label="Merchant display name" />
              <input className={fieldClassName()} value={categories} onChange={(event) => setCategories(event.target.value)} aria-label="Commerce categories" />
              <select className={fieldClassName()} value={sourceType} onChange={(event) => setSourceType(event.target.value)} aria-label="Source connector type">
                {SOURCE_TYPES.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
              </select>
            </div>

            <div className="mt-4 grid gap-3 border-t border-slate-800 pt-4 md:grid-cols-2 lg:grid-cols-3">
              <input className={fieldClassName()} value={sourceStoreId} onChange={(event) => setSourceStoreId(event.target.value)} aria-label="Source store ID" />
              <input className={fieldClassName()} value={shopDomain} onChange={(event) => setShopDomain(event.target.value)} aria-label="Shopify shop domain" />
              <input className={fieldClassName()} value={sourceBaseUrl} onChange={(event) => setSourceBaseUrl(event.target.value)} aria-label="Source base URL" />
              <input className={fieldClassName()} value={sourceApiVersion} onChange={(event) => setSourceApiVersion(event.target.value)} aria-label="Source API version" />
              <select className={fieldClassName()} value={sourceCredentialCustody} onChange={(event) => setSourceCredentialCustody(event.target.value)} aria-label="Source credential custody">
                {CUSTODY_OPTIONS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
              </select>
              <input className={fieldClassName()} value={sourceCredentialRef} onChange={(event) => setSourceCredentialRef(event.target.value)} aria-label="Source credential reference" autoComplete="off" />
            </div>

            <div className="mt-4 grid gap-3 border-t border-slate-800 pt-4 md:grid-cols-2 lg:grid-cols-4">
              {CHANNELS.map((channel) => (
                <label key={channel.key} className="flex min-h-11 items-center justify-between gap-3 rounded-md border border-slate-800 bg-slate-950 px-3 py-2 text-sm">
                  <span>{channel.label}</span>
                  <input
                    type="checkbox"
                    checked={channelEnabled[channel.key]}
                    onChange={(event) => setChannelEnabled((value) => ({ ...value, [channel.key]: event.target.checked }))}
                    aria-label={`${channel.label} channel enabled`}
                  />
                </label>
              ))}
              <select className={fieldClassName()} value={channelApprovals.chatgpt} onChange={(event) => setChannelApprovals((value) => ({ ...value, chatgpt: event.target.value }))} aria-label="ChatGPT approval status">
                {APPROVAL_OPTIONS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
              </select>
              <select className={fieldClassName()} value={channelApprovals.claude} onChange={(event) => setChannelApprovals((value) => ({ ...value, claude: event.target.value }))} aria-label="Claude approval status">
                {APPROVAL_OPTIONS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
              </select>
              <select className={fieldClassName()} value={channelApprovals.gemini} onChange={(event) => setChannelApprovals((value) => ({ ...value, gemini: event.target.value }))} aria-label="Gemini approval status">
                {APPROVAL_OPTIONS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
              </select>
              <select className={fieldClassName()} value={channelApprovals.perplexity} onChange={(event) => setChannelApprovals((value) => ({ ...value, perplexity: event.target.value }))} aria-label="Perplexity approval status">
                {APPROVAL_OPTIONS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
              </select>
              <input className={fieldClassName()} value={whatsappCredentialRef} onChange={(event) => setWhatsappCredentialRef(event.target.value)} aria-label="WhatsApp credential reference" autoComplete="off" />
              <input className={fieldClassName()} value={telegramCredentialRef} onChange={(event) => setTelegramCredentialRef(event.target.value)} aria-label="Telegram credential reference" autoComplete="off" />
            </div>

            <div className="mt-4 grid gap-3 border-t border-slate-800 pt-4 md:grid-cols-2 lg:grid-cols-3">
              <select className={fieldClassName()} value={paymentType} onChange={(event) => setPaymentType(event.target.value)} aria-label="Payment provider type">
                {PAYMENT_TYPES.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
              </select>
              <input className={fieldClassName()} value={paymentProviderName} onChange={(event) => setPaymentProviderName(event.target.value)} aria-label="Payment provider display name" />
              <input className={fieldClassName()} value={paymentProviderKey} onChange={(event) => setPaymentProviderKey(event.target.value)} aria-label="Payment provider key" />
              <select className={fieldClassName()} value={paymentEnvironment} onChange={(event) => setPaymentEnvironment(event.target.value)} aria-label="Payment environment">
                <option value="sandbox">Sandbox</option>
                <option value="production">Production</option>
              </select>
              <select className={fieldClassName()} value={paymentCredentialCustody} onChange={(event) => setPaymentCredentialCustody(event.target.value)} aria-label="Payment credential custody">
                {CUSTODY_OPTIONS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
              </select>
              <input className={fieldClassName()} value={paymentCredentialRef} onChange={(event) => setPaymentCredentialRef(event.target.value)} aria-label="Payment credential reference" autoComplete="off" />
            </div>

            <div className="mt-4 grid gap-3 border-t border-slate-800 pt-4 md:grid-cols-2 lg:grid-cols-3">
              <label className="flex min-h-11 items-center justify-between gap-3 rounded-md border border-slate-800 bg-slate-950 px-3 py-2 text-sm">
                <span>Public catalog</span>
                <input type="checkbox" checked={publicCatalogEnabled} onChange={(event) => setPublicCatalogEnabled(event.target.checked)} aria-label="Public catalog enabled" />
              </label>
              <input className={fieldClassName()} value={publicBaseUrl} onChange={(event) => setPublicBaseUrl(event.target.value)} aria-label="Public catalog base URL" />
              <label className="flex min-h-11 items-center justify-between gap-3 rounded-md border border-slate-800 bg-slate-950 px-3 py-2 text-sm">
                <span>Offline POS</span>
                <input type="checkbox" checked={offlinePosEnabled} onChange={(event) => setOfflinePosEnabled(event.target.checked)} aria-label="Offline POS enabled" />
              </label>
              <input className={fieldClassName()} value={posStoreId} onChange={(event) => setPosStoreId(event.target.value)} aria-label="POS store ID" />
              <input className={fieldClassName()} value={posStoreName} onChange={(event) => setPosStoreName(event.target.value)} aria-label="POS store display name" />
              <input className={fieldClassName()} value={posProvider} onChange={(event) => setPosProvider(event.target.value)} aria-label="POS provider" />
              <input className={fieldClassName()} value={posCity} onChange={(event) => setPosCity(event.target.value)} aria-label="POS city" />
              <input className={fieldClassName()} value={posCountryCode} onChange={(event) => setPosCountryCode(event.target.value)} aria-label="POS country code" />
              <input className={fieldClassName()} value={posWebhookSecretRef} onChange={(event) => setPosWebhookSecretRef(event.target.value)} aria-label="POS webhook secret reference" autoComplete="off" />
            </div>

            <div className="mt-4 flex flex-wrap gap-2">
              <button className={buttonClassName("primary")} onClick={saveMerchantConfig} disabled={busy !== null}>
                <ShieldCheck className="h-4 w-4" />
                Save config
              </button>
              <button className={buttonClassName()} onClick={loadMerchantConfig} disabled={busy !== null}>
                <RefreshCw className="h-4 w-4" />
                Load config
              </button>
              <button className={buttonClassName()} onClick={loadMerchantReadiness} disabled={busy !== null}>
                <Box className="h-4 w-4" />
                Readiness
              </button>
            </div>
          </div>

          <div className="rounded-lg border border-slate-800 bg-slate-900/70 p-4">
            <div className="mb-4 flex items-center gap-2 text-sm font-semibold text-slate-200">
              <KeyRound className="h-4 w-4" />
              Shopify Connector
            </div>
            <div className="grid gap-3">
              <input className={fieldClassName()} value={shopifyToken} onChange={(event) => setShopifyToken(event.target.value)} aria-label="Shopify Admin API token" autoComplete="off" type="password" />
              <input className={fieldClassName()} value={shopifyOauthCode} onChange={(event) => setShopifyOauthCode(event.target.value)} aria-label="Shopify OAuth code" autoComplete="off" />
              <input className={fieldClassName()} value={shopifyClientId} onChange={(event) => setShopifyClientId(event.target.value)} aria-label="Shopify client ID" autoComplete="off" />
              <input className={fieldClassName()} value={shopifyClientSecret} onChange={(event) => setShopifyClientSecret(event.target.value)} aria-label="Shopify client secret" autoComplete="off" type="password" />
              <input className={fieldClassName()} value={shopifyRedirectUri} onChange={(event) => setShopifyRedirectUri(event.target.value)} aria-label="Shopify redirect URI" autoComplete="off" />
            </div>
            <div className="mt-3 grid grid-cols-2 gap-2">
              <button className={buttonClassName("primary")} onClick={saveShopifyConnector} disabled={busy !== null}>
                <KeyRound className="h-4 w-4" />
                Save
              </button>
              <button className={buttonClassName()} onClick={loadShopifyStatus} disabled={busy !== null}>
                <ShieldCheck className="h-4 w-4" />
                Status
              </button>
            </div>
            {shopifyStatus && (
              <div className="mt-3 rounded-md border border-slate-800 bg-slate-950 p-3 text-xs text-slate-300">
                <div>{shopifyStatus.status}</div>
                <div>{shopifyStatus.shop_domain}</div>
                <div>Webhook: {shopifyStatus.webhook_status || "not checked"}</div>
                {shopifyStatus.last_sync_at && <div>Last sync: {shopifyStatus.last_sync_at}</div>}
                {shopifyStatus.last_error && <div className="text-amber-300">Last error: {shopifyStatus.last_error}</div>}
                {shopifyStatus.redacted_credential_ref && <div>Credential ref: {shopifyStatus.redacted_credential_ref}</div>}
                {Array.isArray(shopifyStatus.granted_scopes) && shopifyStatus.granted_scopes.length > 0 && (
                  <div>Scopes: {shopifyStatus.granted_scopes.join(", ")}</div>
                )}
                <div>{shopifyStatus.credential_values_redacted ? "credentials redacted" : ""}</div>
              </div>
            )}
          </div>
        </section>

        <section className="grid gap-4 lg:grid-cols-[0.85fr_1.15fr]">
          <div className="rounded-lg border border-slate-800 bg-slate-900/70 p-4">
            <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-200">
              <Building2 className="h-4 w-4" />
              Runtime Actions
            </div>
            <div className="grid grid-cols-2 gap-2">
              <button className={buttonClassName("primary").replace("bg-emerald-400", "bg-cyan-500")} onClick={createPacket} disabled={busy !== null}>
                <ShieldCheck className="h-4 w-4" />
                Create
              </button>
              <button className={buttonClassName()} onClick={syncShopify} disabled={!packetId || busy !== null}>
                <RefreshCw className="h-4 w-4" />
                Sync
              </button>
              <button className={buttonClassName()} onClick={requestAuthority} disabled={!packetId || !evidenceId || busy !== null}>
                <Box className="h-4 w-4" />
                Issue
              </button>
              <button className={buttonClassName()} onClick={verifyCapability} disabled={busy !== null}>
                <CreditCard className="h-4 w-4" />
                Verify
              </button>
              <button className={buttonClassName()} onClick={loadBridgeReadiness} disabled={busy !== null}>
                <Bot className="h-4 w-4" />
                Channels
              </button>
              <button className={buttonClassName()} onClick={loadAdapters} disabled={busy !== null}>
                <Globe2 className="h-4 w-4" />
                Adapters
              </button>
              <button className={buttonClassName()} onClick={loadPosReadiness} disabled={busy !== null}>
                <MapPin className="h-4 w-4" />
                POS
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
              <button className={buttonClassName("primary")} onClick={askBuyer} disabled={busy !== null}>
                <Send className="h-4 w-4" />
                Ask
              </button>
              <button className={buttonClassName()} onClick={loadProducts} disabled={busy !== null}>
                Products
              </button>
              <button className={buttonClassName()} onClick={preparePurchase} disabled={busy !== null}>
                Prepare
              </button>
              <button className={buttonClassName()} onClick={createPosHandoff} disabled={busy !== null}>
                POS handoff
              </button>
              <button className={buttonClassName()} onClick={simulatePosConfirmation} disabled={busy !== null || !posStatus?.packet?.packet_id}>
                POS confirm
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
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-8">
            {setupStates.map((item) => (
              <div key={item.label} className="rounded-md border border-slate-800 bg-slate-950 p-3">
                <div className="text-xs text-slate-400">{item.label}</div>
                <div className={item.tone === "ok" ? "mt-1 text-sm font-semibold text-emerald-300" : "mt-1 text-sm font-semibold text-amber-300"}>{item.value}</div>
              </div>
            ))}
          </div>
          {merchantConfig && (
            <div className="mt-3 rounded-md border border-slate-800 bg-slate-950 p-3 text-xs text-slate-300">
              <div className="font-semibold text-slate-100">{merchantConfig.config_id}</div>
              <div>{merchantConfig.merchant_id} / {merchantConfig.seller_agent_id}</div>
              <div>{merchantConfig.source_connectors?.[0]?.connector_type} / {merchantConfig.source_connectors?.[0]?.adapter_status}</div>
            </div>
          )}
          {purchaseStatus && (
            <div className="mt-3 rounded-md border border-slate-800 bg-slate-950 p-3 text-xs text-slate-300">
              <div className="font-semibold text-slate-100">{purchaseStatus.status}</div>
              <div>{purchaseStatus.source_label}</div>
              <div>{purchaseStatus.freshness_label}</div>
              {purchaseStatus.blocker && <div className="mt-1 text-amber-300">{purchaseStatus.blocker.code}: {purchaseStatus.blocker.action}</div>}
            </div>
          )}
          {posStatus && (
            <div className="mt-3 rounded-md border border-slate-800 bg-slate-950 p-3 text-xs text-slate-300">
              <div className="font-semibold text-slate-100">{posStatus.status}</div>
              {posStatus.packet && <div>{posStatus.packet.packet_id}</div>}
              {posStatus.reconciliation && <div>{posStatus.reconciliation.buyer_safe_status}</div>}
              {posStatus.blocker && <div className="mt-1 text-amber-300">{posStatus.blocker.code}: {posStatus.blocker.action}</div>}
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
                  <div className={toneClassName(item.tone)}>{item.label}</div>
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
