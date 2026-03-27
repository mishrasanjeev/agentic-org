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
