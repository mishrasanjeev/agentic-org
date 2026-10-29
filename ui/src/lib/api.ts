import axios from "axios";
const API_BASE = import.meta.env.VITE_API_URL
  ? `${import.meta.env.VITE_API_URL}/api/v1`
  : "/api/v1";
// withCredentials=true is required so the browser ships the
// agenticorg_session + agenticorg_csrf cookies on every request, even
// for cross-origin deploys (VITE_API_URL set). Without this the
// cookies stay on the document and CSRF middleware sees the request
// as a bearer-only client.
const api = axios.create({
  baseURL: API_BASE,
  timeout: 30000,
  withCredentials: true,
});

// SEC-2026-05-P1-003 (PR-B): read the CSRF token from the
// agenticorg_csrf cookie and echo it back via X-CSRF-Token on every
// mutating request. The double-submit pattern relies on this - the
// server compares the cookie value against the header value with a
// constant-time check. ``document.cookie`` is the only JS-readable
// store; the session cookie remains HttpOnly and stays unreadable.
function readCookie(name: string): string {
  const match = document.cookie.match(
    new RegExp("(?:^|; )" + name.replace(/[$()*+./?[\]\\^{|}]/g, "\\$&") + "=([^;]*)"),
  );
  return match ? decodeURIComponent(match[1]) : "";
}

api.interceptors.request.use((config) => {
  // SEC-002 (PR-F): browser auth is COOKIE-FIRST. The browser ships
  // the HttpOnly ``agenticorg_session`` cookie automatically because
  // ``withCredentials: true`` is set above. We DO NOT read a bearer
  // token from localStorage anymore - that was the SEC-002 attack
  // surface.

  // CSRF: only attach for mutating methods. The server-side middleware
  // exempts safe methods + bearer-only API clients, but sending the
  // header on a GET is harmless and aligns with future hardening.
  const method = (config.method || "get").toLowerCase();
  if (method === "post" || method === "put" || method === "patch" || method === "delete") {
    const csrf = readCookie("agenticorg_csrf");
    if (csrf) {
      config.headers["X-CSRF-Token"] = csrf;
    }
  }
  return config;
});

// TC-001: Auto-retry failed GET requests once (covers transient failures on navigation)
api.interceptors.response.use(undefined, async (error) => {
  const config = error.config;
  if (
    config &&
    !config._retried &&
    config.method === "get" &&
    (!error.response || error.response.status >= 500)
  ) {
    config._retried = true;
    await new Promise((r) => setTimeout(r, 1000));
    return api(config);
  }
  return Promise.reject(error);
});
let isRedirectingTo401 = false;
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401 && !isRedirectingTo401) {
      isRedirectingTo401 = true;
      // SEC-002 (PR-F): cookie is HttpOnly so we can't clear it from
      // JS. The redirect to /login itself is the user-visible signal;
      // the backend will clear/expire the cookie on the next /logout
      // call (or it expires server-side via JWT TTL). We still purge
      // legacy localStorage entries in case an older client wrote
      // them before the PR-F upgrade.
      try {
        localStorage.removeItem("token");
        localStorage.removeItem("user");
      } catch {
        // ignore - private browsing or storage disabled
      }
      window.location.href = "/login";
    }
    return Promise.reject(error);
  }
);
export function extractApiError(e: unknown, fallback = "An error occurred"): string {
  const data = (e as any)?.response?.data;
  const detail = data?.detail;
  if (typeof detail === "string") return detail;
  // Pydantic/FastAPI 422 validation errors: detail is an array of {loc, msg}.
  if (Array.isArray(detail)) {
    const parts = detail
      .map((item: any) => {
        if (typeof item === "string") return item;
        if (!item || typeof item !== "object") return "";
        const loc = Array.isArray(item.loc)
          ? item.loc.filter((p: unknown) => p !== "body" && p !== "query" && p !== "path").join(".")
          : "";
        const msg = typeof item.msg === "string" ? item.msg : "";
        if (loc && msg) return `${loc}: ${msg}`;
        return msg || loc;
      })
      .filter(Boolean);
    if (parts.length) return parts.join("; ");
    return fallback;
  }
  if (detail && typeof detail === "object") {
    // Row-level validation payloads (e.g. professional tax): detail.row_errors /
    // detail.errors are arrays of strings or {row_number, employee_ref, errors[]}.
    const rows = Array.isArray(detail.row_errors)
      ? detail.row_errors
      : Array.isArray(detail.errors)
        ? detail.errors
        : null;
    if (rows) {
      const parts = rows
        .map((r: any) => {
          if (typeof r === "string") return r;
          if (!r || typeof r !== "object") return "";
          const msgs = Array.isArray(r.errors) ? r.errors.map(String).join(", ") : String(r.msg || r.message || "");
          const label = r.row_number != null ? `Row ${r.row_number}${r.employee_ref ? ` (${r.employee_ref})` : ""}` : "";
          return label && msgs ? `${label}: ${msgs}` : msgs || label;
        })
        .filter(Boolean);
      if (parts.length) {
        const prefix = typeof detail.message === "string" ? `${detail.message} ` : "";
        return `${prefix}${parts.join("; ")}`;
      }
    }
    if (Array.isArray((detail as any).connectors)) {
      const connectors = (detail as any).connectors
        .map((c: any) => `${c.connector || "connector"} (${String(c.reason || "not ready").replace(/_/g, " ")})`)
        .join(", ");
      if (typeof (detail as any).message === "string") return `${(detail as any).message} Affected: ${connectors}.`;
    }
    if (typeof detail.message === "string") return detail.message;
    if (typeof detail.error === "string") {
      return detail.message ? `${detail.error}: ${detail.message}` : detail.error;
    }
  }
  if (typeof data?.message === "string") return data.message;
  if (typeof data?.error === "string") return data.error;
  return fallback;
}
export default api;

async function listAllPages<T>(
  path: string,
  params?: Record<string, string>,
): Promise<T[]> {
  const items: T[] = [];
  let page = 1;
  let pages: number;
  const perPage = "100";

  do {
    const { data } = await api.get(path, {
      params: { ...(params || {}), page: String(page), per_page: perPage },
    });
    const pageItems: T[] = Array.isArray(data)
      ? data
      : Array.isArray(data?.items)
        ? data.items
        : [];

    items.push(...pageItems);
    pages = Number.isFinite(Number(data?.pages)) ? Number(data.pages) : page;

    if (!data?.pages) {
      const total = Number(data?.total ?? items.length);
      if (items.length >= total || pageItems.length < Number(perPage)) break;
      pages = page + 1;
    }

    page += 1;
  } while (page <= pages);

  return items;
}

export const agentsApi = {
  list: (params?: Record<string, string>) => api.get("/agents", { params }),
  listAll: (params?: Record<string, string>) => listAllPages<any>("/agents", params),
  get: (id: string) => api.get(`/agents/${id}`),
  create: (data: any) => api.post("/agents", data),
  update: (id: string, data: any) => api.patch(`/agents/${id}`, data),
  run: (id: string, payload?: any) => api.post(`/agents/${id}/run`, payload),
  pause: (id: string) => api.post(`/agents/${id}/pause`),
  resume: (id: string) => api.post(`/agents/${id}/resume`),
  promote: (id: string) => api.post(`/agents/${id}/promote`),
  clone: (id: string, data: any) => api.post(`/agents/${id}/clone`, data),
  promptHistory: (id: string) => api.get(`/agents/${id}/prompt-history`),
  orgTree: (params?: Record<string, string>) => api.get("/agents/org-tree", { params }),
  importCsv: (file: File, params?: Record<string, string>) => { const fd = new FormData(); fd.append("file", file); return api.post("/agents/import-csv", fd, { params }); },
  generate: (description: string, deploy = false) => api.post("/agents/generate", { description, deploy }),
};
export const promptTemplatesApi = {
  list: (params?: Record<string, string>) => api.get("/prompt-templates", { params }),
  get: (id: string) => api.get(`/prompt-templates/${id}`),
  create: (data: any) => api.post("/prompt-templates", data),
  update: (id: string, data: any) => api.put(`/prompt-templates/${id}`, data),
  delete: (id: string) => api.delete(`/prompt-templates/${id}`),
};
export const workflowsApi = {
  templates: (params?: Record<string, string>) => api.get("/workflows/templates", { params }),
  list: (params?: Record<string, string>) => api.get("/workflows", { params }),
  get: (id: string) => api.get(`/workflows/${id}`),
  generate: (description: string, deploy = false) => api.post("/workflows/generate", { description, deploy }),
  create: (data: any) => api.post("/workflows", data),
  run: (id: string, payload?: any) => api.post(`/workflows/${id}/run`, { payload: payload || {} }),
  getRun: (id: string) => api.get(`/workflows/runs/${id}`),
};
export const approvalsApi = {
  list: () => api.get("/approvals"),
  decide: (id: string, decision: string, notes: string) => api.post(`/approvals/${id}/decide`, { decision, notes }),
};
export const auditApi = { query: (params: any) => api.get("/audit", { params }) };

// KPI Dashboards
export const kpisApi = {
  cfo: (companyId?: string) => api.get("/kpis/cfo", { params: companyId ? { company_id: companyId } : {} }),
  cmo: (companyId?: string) => api.get("/kpis/cmo", { params: companyId ? { company_id: companyId } : {} }),
};

// NL Query Chat
export const chatApi = {
  query: (query: string, companyId?: string) => api.post("/chat/query", { query, company_id: companyId }),
  history: () => api.get("/chat/history"),
};

// Multi-Company
export const companiesApi = {
  list: () => api.get("/companies"),
  create: (data: any) => api.post("/companies", data),
  get: (id: string) => api.get(`/companies/${id}`),
};

export const commerceRuntimeApi = {
  getMerchantConfig: (merchantId: string, params?: any) =>
    api.get(`/commerce/runtime/merchant-configs/${merchantId}`, { params }),
  upsertMerchantConfig: (merchantId: string, data: any) =>
    api.put(`/commerce/runtime/merchant-configs/${merchantId}`, data),
  getMerchantConfigReadiness: (merchantId: string, params?: any) =>
    api.get(`/commerce/runtime/merchant-configs/${merchantId}/readiness`, { params }),
  createOnboardingPacket: (data: any) =>
    api.post("/commerce/runtime/seller-agents/onboarding-packets", data),
  getOnboardingPacket: (packetId: string) =>
    api.get(`/commerce/runtime/seller-agents/onboarding-packets/${packetId}`),
  upsertShopifyCredentials: (data: any) =>
    api.post("/commerce/runtime/seller-agents/connectors/shopify/credentials", data),
  getShopifyStatus: (params: any) =>
    api.get("/commerce/runtime/seller-agents/connectors/shopify/status", { params }),
  syncShopify: (data: any) => api.post("/commerce/runtime/seller-agents/shopify/sync", data),
  requestGrantexAuthority: (data: any) => api.post("/commerce/runtime/authority/grantex/request", data),
  cacheArtifacts: (data: any) => api.post("/commerce/runtime/artifacts/cache", data),
  askBuyerQuestion: (data: any) => api.post("/commerce/runtime/buyer-sessions/ask", data),
  getBridgeSurfaces: () => api.get("/commerce/runtime/bridges/surfaces"),
  getProtocolAdapters: (params: any) => api.get("/commerce/runtime/protocol-adapters", { params }),
  getProtocolAdapterSurface: (surface: string, params: any) =>
    api.get(`/commerce/runtime/protocol-adapters/${surface}`, { params }),
  preparePurchase: (data: any) => api.post("/commerce/runtime/purchase/prepare", data),
  getOfflinePosReadiness: () => api.get("/commerce/runtime/pos/offline/readiness"),
  createOfflinePosHandoff: (data: any) => api.post("/commerce/runtime/pos/offline/handoffs", data),
  simulateOfflinePosConfirmation: (data: any) =>
    api.post("/commerce/runtime/pos/offline/simulator/confirm", data),
  listProducts: (params: any) => api.get("/commerce/runtime/products", { params }),
  verifyPluralPineCapability: (data: any) =>
    api.post("/commerce/runtime/providers/plural-pine/mandate-capability/verify", data),
};

// Sell everywhere: the AgenticOrg shopping network (api/v1/network.py)
export const sellEverywhereApi = {
  get: (merchantId: string, params?: { seller_agent_id?: string }) =>
    api.get(`/commerce/runtime/merchant-configs/${encodeURIComponent(merchantId)}/sell-everywhere`, { params }),
  put: (
    merchantId: string,
    data: { listed: boolean; accept_terms: boolean; slug?: string; seller_agent_id?: string },
  ) => api.put(`/commerce/runtime/merchant-configs/${encodeURIComponent(merchantId)}/sell-everywhere`, data),
  publish: (merchantId: string, data: { seller_agent_id?: string } = {}) =>
    api.post(`/commerce/runtime/merchant-configs/${encodeURIComponent(merchantId)}/sell-everywhere/publish`, data),
};

// Merchant setup: merchants, catalog, policies and order limits (api/v1/commerce_merchant_setup.py)
const merchantPath = (merchantId: string) => `/commerce/runtime/merchant-configs/${encodeURIComponent(merchantId)}`;
export const merchantSetupApi = {
  list: () => api.get("/commerce/runtime/merchant-configs"),
  create: (data: { display_name: string; categories?: string[] }) => api.post("/commerce/runtime/merchant-setup", data),
  get: (merchantId: string) => api.get(`${merchantPath(merchantId)}/setup`),
  connectShopify: (
    merchantId: string,
    data: { shop_domain: string; admin_access_token: string; api_version?: string; webhook_secret?: string },
  ) => api.post(`${merchantPath(merchantId)}/catalog/shopify`, data),
  uploadCatalog: (merchantId: string, data: { format: "csv" | "json"; content: string }) =>
    api.post(`${merchantPath(merchantId)}/catalog/upload`, data),
  syncCatalog: (merchantId: string) => api.post(`${merchantPath(merchantId)}/catalog/sync`, {}),
  savePolicies: (merchantId: string, data: { returns: string; shipping: string; pickup: string }) =>
    api.put(`${merchantPath(merchantId)}/setup/policies`, data),
  saveOrderLimits: (
    merchantId: string,
    data: { currency: string; max_order_amount: string; max_daily_amount: string; max_quantity_per_order: number },
  ) => api.put(`${merchantPath(merchantId)}/setup/order-limits`, data),
};

// Merchant custom domains (api/v1/merchant_custom_domains.py)
export const customDomainsApi = {
  get: (merchantId: string, params?: { seller_agent_id?: string }) =>
    api.get(`/commerce/runtime/merchant-configs/${encodeURIComponent(merchantId)}/custom-domain`, { params }),
  add: (merchantId: string, data: { host: string; seller_agent_id?: string }) =>
    api.post(`/commerce/runtime/merchant-configs/${encodeURIComponent(merchantId)}/custom-domain`, data),
  check: (merchantId: string, data: { search_console_owner_added: boolean; seller_agent_id?: string }) =>
    api.post(`/commerce/runtime/merchant-configs/${encodeURIComponent(merchantId)}/custom-domain/check`, data),
  remove: (merchantId: string, params?: { seller_agent_id?: string }) =>
    api.delete(`/commerce/runtime/merchant-configs/${encodeURIComponent(merchantId)}/custom-domain`, { params }),
  operatorQueue: () => api.get("/commerce/custom-domains/operator-queue"),
  markMapped: (domainId: string) =>
    api.post(`/commerce/custom-domains/operator-queue/${encodeURIComponent(domainId)}/mapped`, {}),
  markUnmapped: (domainId: string) =>
    api.post(`/commerce/custom-domains/operator-queue/${encodeURIComponent(domainId)}/unmapped`, {}),
  markFailed: (domainId: string, reason: string) =>
    api.post(`/commerce/custom-domains/operator-queue/${encodeURIComponent(domainId)}/failed`, { reason }),
};

// How a merchant gets paid: payment hand-off mode and Plural credentials (api/v1/commerce_payments.py)
export const paymentsApi = {
  get: (merchantId: string) => api.get(`/commerce/runtime/merchant-configs/${encodeURIComponent(merchantId)}/payments`),
  setMode: (merchantId: string, mode: "shopify_cart" | "plural_hosted" | "off") =>
    api.put(`/commerce/runtime/merchant-configs/${encodeURIComponent(merchantId)}/payments`, { mode }),
  saveCredentials: (
    merchantId: string,
    data: { environment: "sandbox" | "production"; client_id: string; client_secret?: string; webhook_secret?: string },
  ) => api.put(`/commerce/runtime/merchant-configs/${encodeURIComponent(merchantId)}/payments/plural-credentials`, data),
  testCredentials: (merchantId: string) =>
    api.post(`/commerce/runtime/merchant-configs/${encodeURIComponent(merchantId)}/payments/plural-credentials/test`, {}),
};

// The merchant's orders (api/v1/commerce_payments.py)
export const ordersApi = {
  list: (params?: { merchant_id?: string; limit?: number; offset?: number }) => api.get("/commerce/runtime/orders", { params }),
};

// ABM (Account-Based Marketing)
export const abmApi = {
  listAccounts: (params?: Record<string, string>) => api.get("/abm/accounts", { params }),
  getAccount: (id: string) => api.get(`/abm/accounts/${id}`),
  createAccount: (data: any) => api.post("/abm/accounts", data),
  getIntent: (id: string) => api.get(`/abm/accounts/${id}/intent`),
  launchCampaign: (id: string, data: any) => api.post(`/abm/accounts/${id}/campaign`, data),
  dashboard: (params?: Record<string, string>) =>
    api.get("/abm/dashboard", { params }),
  uploadCsv: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return api.post("/abm/accounts/upload", fd);
  },
};
