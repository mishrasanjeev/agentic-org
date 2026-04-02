import axios from "axios";
const api = axios.create({ baseURL: "/api/v1" });
api.interceptors.request.use((config) => {
  const token = localStorage.getItem("token");
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem("token");
      localStorage.removeItem("user");
      window.location.href = "/login";
    }
    return Promise.reject(error);
  }
);
export function extractApiError(e: unknown, fallback = "An error occurred"): string {
  const detail = (e as any)?.response?.data?.detail;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object") {
    if (typeof detail.message === "string") return detail.message;
    if (typeof detail.error === "string") {
      return detail.message ? `${detail.error}: ${detail.message}` : detail.error;
    }
  }
  return fallback;
}
export default api;
export const agentsApi = {
  list: (params?: Record<string, string>) => api.get("/agents", { params }),
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
  importCsv: (file: File) => { const fd = new FormData(); fd.append("file", file); return api.post("/agents/import-csv", fd); },
};
export const promptTemplatesApi = {
  list: (params?: Record<string, string>) => api.get("/prompt-templates", { params }),
  get: (id: string) => api.get(`/prompt-templates/${id}`),
  create: (data: any) => api.post("/prompt-templates", data),
  update: (id: string, data: any) => api.put(`/prompt-templates/${id}`, data),
  delete: (id: string) => api.delete(`/prompt-templates/${id}`),
};
export const workflowsApi = {
  list: () => api.get("/workflows"),
  create: (data: any) => api.post("/workflows", data),
  run: (id: string, payload?: any) => api.post(`/workflows/${id}/run`, payload),
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
