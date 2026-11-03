// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
/**
 * Saved views (named filter sets on a landing view or an explorer) and the "what changed since you last looked"
 * strip: /insights/saved-views and /insights/changes.
 */
import api from "@/lib/api";

export interface SavedView {
  id: string;
  view_key: string;
  name: string;
  filters: Record<string, string | string[]>;
  shared: boolean;
  mine: boolean;
}

export interface ChangeItem {
  key: string;
  count: number;
  link: string;
}

export interface Changes {
  since: string;
  first_visit: boolean;
  items: ChangeItem[];
}

/** URL query keys a saved view keeps: everything but the company chosen in the header. */
export function filtersFromSearch(params: URLSearchParams): Record<string, string | string[]> {
  const out: Record<string, string | string[]> = {};
  for (const key of new Set(params.keys())) {
    if (key === "company_id") continue;
    const values = params.getAll(key);
    out[key] = values.length > 1 ? values : values[0];
  }
  return out;
}

/** The URL query a saved view opens with. */
export function searchFromFilters(filters: Record<string, string | string[]>): URLSearchParams {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    for (const v of Array.isArray(value) ? value : [value]) params.append(key, String(v));
  }
  return params;
}

export const savedViewsApi = {
  async list(viewKey: string): Promise<SavedView[]> {
    const { data } = await api.get("/insights/saved-views", { params: { view_key: viewKey } });
    const rows = (data as { saved_views?: unknown })?.saved_views;
    return Array.isArray(rows) ? (rows as SavedView[]).filter((v) => v && typeof v.id === "string") : [];
  },
  async create(body: {
    view_key: string;
    name: string;
    filters: Record<string, string | string[]>;
    shared: boolean;
  }): Promise<SavedView> {
    const { data } = await api.post("/insights/saved-views", body);
    return (data as { saved_view: SavedView }).saved_view;
  },
  async remove(id: string): Promise<void> {
    await api.delete(`/insights/saved-views/${encodeURIComponent(id)}`);
  },
  async changes(): Promise<Changes> {
    const { data } = await api.get("/insights/changes");
    const body = data as Partial<Changes>;
    return {
      since: String(body.since ?? ""),
      first_visit: Boolean(body.first_visit),
      items: Array.isArray(body.items) ? body.items : [],
    };
  },
  async seen(): Promise<void> {
    await api.post("/insights/changes/seen");
  },
};
