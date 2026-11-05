// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
/**
 * Level 3 entity pages and the Ctrl/Cmd-K search: GET /insights/entities/{kind}/{id} and GET /insights/search.
 * The route segment of each kind (agents, workflows, ...) maps to the API's kind (agent, workflow, ...).
 */
import api from "@/lib/api";
import type { MetricResponse } from "./api";

export type EntityKind = "agent" | "workflow" | "connector" | "company" | "user" | "cost_centre";

/** URL segment under /dashboard/insights for each kind. */
export const ENTITY_SEGMENTS: Record<EntityKind, string> = {
  agent: "agents",
  workflow: "workflows",
  connector: "connectors",
  company: "companies",
  user: "users",
  cost_centre: "cost-centres",
};

export function kindFromSegment(segment: string): EntityKind | null {
  const found = (Object.entries(ENTITY_SEGMENTS) as Array<[EntityKind, string]>).find(([, s]) => s === segment);
  return found ? found[0] : null;
}

export function entityPath(kind: EntityKind, id: string): string {
  return `/dashboard/insights/${ENTITY_SEGMENTS[kind]}/${encodeURIComponent(id)}`;
}

export interface EntityFact {
  key: string;
  value: string | number | boolean | null;
}

export interface EntityEvent {
  at: string | null;
  event: string;
  action: string;
  outcome: string;
  actor_type: string;
  actor: string;
}

export interface EntityPage {
  kind: EntityKind;
  id: string;
  title: string;
  subtitle?: string;
  period: { from: string; to: string };
  facts: EntityFact[];
  cards: MetricResponse[];
  timeline: EntityEvent[];
  links: Record<string, string>;
  agents?: Array<{ id: string; name: string }>;
  hint?: string | null;
}

export interface SearchResult {
  kind: EntityKind | "run";
  id: string;
  title: string;
  detail?: string | null;
  at?: string | null;
}

/** Where a search result opens: an entity page, or a run's evidence. */
export function resultPath(result: SearchResult): string {
  if (result.kind === "run") {
    const at = result.at ? `?at=${encodeURIComponent(result.at)}` : "";
    return `/dashboard/insights/runs/${encodeURIComponent(result.id)}${at}`;
  }
  return entityPath(result.kind, result.id);
}

export const entitiesApi = {
  async get(
    kind: EntityKind,
    id: string,
    filters: { from?: string; to?: string; company_id?: string } = {},
  ): Promise<EntityPage> {
    const params = Object.fromEntries(Object.entries(filters).filter(([, v]) => Boolean(v)));
    const { data } = await api.get(`/insights/entities/${kind}/${encodeURIComponent(id)}`, { params });
    const page = data as EntityPage;
    return {
      ...page,
      facts: Array.isArray(page.facts) ? page.facts : [],
      cards: Array.isArray(page.cards) ? page.cards : [],
      timeline: Array.isArray(page.timeline) ? page.timeline : [],
      links: page.links && typeof page.links === "object" ? page.links : {},
    };
  },
  async search(q: string): Promise<SearchResult[]> {
    const { data } = await api.get("/insights/search", { params: { q } });
    const results = (data as { results?: unknown })?.results;
    return Array.isArray(results) ? (results as SearchResult[]).filter((r) => r && typeof r.id === "string") : [];
  },
};
