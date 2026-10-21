// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
/**
 * Global setup: delete shadow agents that earlier runs created and left behind.
 *
 * The E2E tenant allows 50 shadow agents (FleetLimits.max_shadow_agents) and
 * 28 of them are seeded fixtures. Specs name the agents they create with
 * `Date.now()`, and several never delete them, so the leftovers filled the
 * limit and agent creation answered 409 "Shadow limit reached" (production
 * run 39585665621). Only agents whose name or type carries such a millisecond
 * timestamp older than two hours are deleted: seeded fixtures carry none, and
 * a run still in progress keeps its own. Deleting is the API's soft delete
 * (status `deleted`, history kept). Best effort: a failure is reported, never
 * thrown, so it cannot hide the run's own result.
 */
import { writeSync } from "node:fs";

// As helpers/auth.ts, read here so this module stays free of Playwright for its unit test.
const APP = process.env.BASE_URL || "https://app.agenticorg.ai";

export const STALE_TEST_AGENT_AGE_MS = 2 * 60 * 60 * 1000;

// 13 digits from 2020-09 (1.6e12) to 2286: Date.now() as the specs embed it.
const EMBEDDED_TIMESTAMP = /(?<!\d)(1[6-9]\d{11})(?!\d)/;

export interface ListedAgent {
  id?: string;
  agent_id?: string;
  name?: string | null;
  employee_name?: string | null;
  agent_type?: string | null;
  status?: string | null;
}

/** The millisecond timestamp a spec embedded in the agent's name or type, if any. */
export function embeddedTimestamp(agent: ListedAgent): number | null {
  for (const field of [agent.employee_name, agent.name, agent.agent_type]) {
    const match = typeof field === "string" ? EMBEDDED_TIMESTAMP.exec(field) : null;
    if (match) return Number(match[1]);
  }
  return null;
}

/** Ids of shadow agents created by a run that ended (timestamp older than maxAgeMs). */
export function staleTestAgentIds(agents: ListedAgent[], nowMs: number, maxAgeMs = STALE_TEST_AGENT_AGE_MS): string[] {
  const ids: string[] = [];
  for (const agent of agents) {
    const id = agent.id ?? agent.agent_id;
    if (!id || agent.status !== "shadow") continue;
    const createdAt = embeddedTimestamp(agent);
    if (createdAt !== null && createdAt <= nowMs - maxAgeMs) ids.push(id);
  }
  return ids;
}

async function listShadowAgents(headers: Record<string, string>): Promise<ListedAgent[]> {
  const agents: ListedAgent[] = [];
  for (let page = 1; page <= 20; page += 1) {
    const resp = await fetch(`${APP}/api/v1/agents?status=shadow&per_page=100&page=${page}`, { headers });
    if (!resp.ok) throw new Error(`listing shadow agents returned HTTP ${resp.status}`);
    const body = (await resp.json()) as { items?: ListedAgent[] } | ListedAgent[];
    const items = Array.isArray(body) ? body : (body.items ?? []);
    agents.push(...items);
    if (items.length < 100) break;
  }
  return agents;
}

export default async function sweepStaleTestAgents(): Promise<void> {
  const token = process.env.E2E_TOKEN;
  if (!token) return;
  const headers = { Authorization: `Bearer ${token}` };
  try {
    const stale = staleTestAgentIds(await listShadowAgents(headers), Date.now());
    let deleted = 0;
    for (const id of stale) {
      const resp = await fetch(`${APP}/api/v1/agents/${id}`, { method: "DELETE", headers });
      if (resp.ok) deleted += 1;
      else writeSync(1, `::warning::stale test agent ${id}: not deleted (HTTP ${resp.status})\n`);
    }
    writeSync(1, `stale test agents: ${deleted} of ${stale.length} deleted\n`);
  } catch (err) {
    writeSync(1, `::warning::stale test agents not swept (${err instanceof Error ? err.message : String(err)})\n`);
  }
}
