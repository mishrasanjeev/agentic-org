// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
/**
 * Typed client for the Insights explorers and run evidence (api/v1/insights_explore.py).
 *
 * The backend decides which explorers a role may open (403
 * insights_explorer_forbidden), which columns filter and sort (422 for
 * anything else) and which rows a caller sees (tenant, domains, company).
 * This module only shapes requests and responses; the page keeps every
 * filter in the URL so a table can be shared as a link.
 */
import api from "@/lib/api";
import type {
  DataQuality,
  ExportFormat,
  InsightsFilters,
  MetricPeriod,
} from "./api";
import { filterParams } from "./api";

export type ColumnType =
  | "text"
  | "id"
  | "count"
  | "number"
  | "percent"
  | "duration_ms"
  | "money"
  | "tokens"
  | "datetime"
  | "date"
  | "bool"
  | "status";
export type FilterKind = "enum" | "text" | "min" | "bool";
export type SortOrder = "asc" | "desc";

export interface ExplorerColumn {
  key: string;
  title: string;
  type: ColumnType | string;
  sortable: boolean;
  filter: FilterKind | null;
  description?: string;
  options?: string[];
}

export interface ExplorerDescription {
  key: string;
  title: string;
  definition: string;
  source: string[];
  columns: ExplorerColumn[];
  default_sort: string;
  default_order: SortOrder;
  uses_period: boolean;
}

export type ExplorerRow = Record<string, unknown> & {
  links?: Record<string, string>;
  actions?: Record<string, Record<string, unknown>>;
};

export interface ExplorerResponse {
  key: string;
  title: string;
  definition: string;
  source: string;
  data_quality: DataQuality | string;
  computed_at: string;
  period: MetricPeriod | null;
  columns: ExplorerColumn[];
  rows: ExplorerRow[];
  total: number;
  page: number;
  page_size: number;
  sort: string;
  order: SortOrder;
  filters: Record<string, string>;
  hint?: string | null;
  summary?: Record<string, unknown> | null;
}

/** What the explorer page asks for: the period filters plus the table's own filters, sort and page. */
export interface ExplorerQuery extends InsightsFilters {
  filters?: Record<string, string>;
  sort?: string;
  order?: SortOrder;
  page?: number;
  page_size?: number;
}

/** Query parameters for an explorer request: `filter[column]=value` per chosen filter, empty ones dropped. */
export function explorerParams(query: ExplorerQuery): Record<string, string> {
  const {
    filters,
    sort,
    order,
    page,
    page_size,
    compare: _compare,
    ...period
  } = query;
  const params: Record<string, string> = filterParams(period);
  for (const [column, value] of Object.entries(filters ?? {})) {
    if (typeof value === "string" && value.trim() !== "")
      params[`filter[${column}]`] = value.trim();
  }
  if (sort) params.sort = sort;
  if (order) params.order = order;
  if (page && page > 1) params.page = String(page);
  if (page_size) params.page_size = String(page_size);
  return params;
}

/** The explorer page's route for a key and a set of table filters (used by tiles and row links). */
export function explorerRoute(
  key: string,
  filters: Record<string, string> = {},
  extra: Record<string, string> = {},
) {
  const qs = new URLSearchParams();
  for (const [column, value] of Object.entries(filters))
    qs.set(`filter[${column}]`, value);
  for (const [name, value] of Object.entries(extra)) qs.set(name, value);
  const tail = qs.toString();
  return `/dashboard/insights/explore/${encodeURIComponent(key)}${tail ? `?${tail}` : ""}`;
}

/** The table filters carried in a URL's `filter[column]` parameters. */
export function filtersFromSearch(
  search: URLSearchParams,
): Record<string, string> {
  const out: Record<string, string> = {};
  search.forEach((value, name) => {
    const match = /^filter\[([a-z_]{1,40})\]$/.exec(name);
    if (match && value.trim() !== "") out[match[1]] = value;
  });
  return out;
}

export function explorerExportFilename(
  key: string,
  format: ExportFormat,
  query: ExplorerQuery,
): string {
  return `insights-explore-${key}-${query.from || "start"}-${query.to || "today"}.${format}`;
}

export interface RunEvidence {
  run: Record<string, unknown> & {
    id: string;
    agent?: string | null;
    agent_id?: string | null;
    status?: string;
    started_at?: string;
    tokens?: {
      input?: number;
      output?: number;
      cached?: number;
      total?: number;
    };
  };
  gateway_records: Array<Record<string, unknown>>;
  spans: Array<Record<string, unknown>>;
  approvals: Array<Record<string, unknown> & { link?: string }>;
  audit: Array<Record<string, unknown>>;
  links: Record<string, string>;
  limits: { records: number; spans: number };
}

function normaliseExplorer(data: unknown): ExplorerResponse {
  const raw = (
    data && typeof data === "object" ? data : {}
  ) as Partial<ExplorerResponse>;
  return {
    key: String(raw.key ?? ""),
    title: String(raw.title ?? ""),
    definition: String(raw.definition ?? ""),
    source: String(raw.source ?? ""),
    data_quality: raw.data_quality ?? "no_data",
    computed_at: String(raw.computed_at ?? ""),
    period: raw.period ?? null,
    columns: Array.isArray(raw.columns)
      ? raw.columns.filter((c) => c && typeof c.key === "string")
      : [],
    rows: Array.isArray(raw.rows)
      ? raw.rows.filter((r) => r && typeof r === "object")
      : [],
    total: typeof raw.total === "number" ? raw.total : 0,
    page: typeof raw.page === "number" ? raw.page : 1,
    page_size: typeof raw.page_size === "number" ? raw.page_size : 50,
    sort: String(raw.sort ?? ""),
    order: raw.order === "asc" ? "asc" : "desc",
    filters: raw.filters && typeof raw.filters === "object" ? raw.filters : {},
    hint: typeof raw.hint === "string" ? raw.hint : null,
    summary:
      raw.summary && typeof raw.summary === "object" ? raw.summary : null,
  };
}

export const exploreApi = {
  async list(): Promise<ExplorerDescription[]> {
    const { data } = await api.get("/insights/explorers");
    const list = (data as { explorers?: unknown })?.explorers;
    return Array.isArray(list)
      ? (list as ExplorerDescription[]).filter(
          (e) => e && typeof e.key === "string",
        )
      : [];
  },
  async page(key: string, query: ExplorerQuery): Promise<ExplorerResponse> {
    const { data } = await api.get(
      `/insights/explore/${encodeURIComponent(key)}`,
      { params: explorerParams(query) },
    );
    return normaliseExplorer(data);
  },
  /** Every filtered row as a file through the authenticated client; the server audits it and refuses > 50,000 rows. */
  async exportBlob(
    key: string,
    format: ExportFormat,
    query: ExplorerQuery,
  ): Promise<Blob> {
    const { page: _page, page_size: _size, ...rest } = query;
    const { data } = await api.get(
      `/insights/explore/${encodeURIComponent(key)}/export`,
      {
        params: { format, ...explorerParams(rest) },
        responseType: "blob",
      },
    );
    return data as Blob;
  },
  async run(id: string, at: string, companyId?: string): Promise<RunEvidence> {
    const params: Record<string, string> = { at };
    if (companyId) params.company_id = companyId;
    const { data } = await api.get(`/insights/runs/${encodeURIComponent(id)}`, {
      params,
    });
    return data as RunEvidence;
  },
};
