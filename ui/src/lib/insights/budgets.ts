// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
/**
 * Typed client for Insights budgets (api/v1/insights_budgets.py): the
 * organisation's spend limits, with this period's ledger spend against each.
 *
 * The backend is the boundary: it answers 403 to roles other than
 * administrators, the CFO (who change budgets) and auditors (who read them),
 * 409 for a duplicate name and 422 with an error code for invalid input.
 */
import api from "@/lib/api";

export type BudgetPeriod = "daily" | "weekly" | "monthly";
export type BudgetState = "ok" | "warn" | "breached";

export interface Budget {
  id: string;
  name: string;
  period: BudgetPeriod;
  threshold_usd: string;
  warn_at_percent: number;
  company_id: string | null;
  cost_center_id: string | null;
  notify_channels: string;
  spend_usd: string;
  used_pct: number | null;
  state: BudgetState;
  forecast_alert: boolean;
  last_triggered_at: string | null;
}

export interface BudgetList {
  budgets: Budget[];
  can_edit: boolean;
  periods: BudgetPeriod[];
  max_threshold_usd: string;
}

export interface BudgetInput {
  name: string;
  period: BudgetPeriod;
  threshold_usd: string;
  warn_at_percent: number;
  company_id?: string | null;
}

const BASE = "/insights/budgets";
const PERIODS: BudgetPeriod[] = ["daily", "weekly", "monthly"];
const STATES: BudgetState[] = ["ok", "warn", "breached"];

function text(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function nullable(value: unknown): string | null {
  return typeof value === "string" && value !== "" ? value : null;
}

export function normaliseBudget(raw: Record<string, unknown>): Budget {
  const period = PERIODS.includes(raw.period as BudgetPeriod) ? (raw.period as BudgetPeriod) : "monthly";
  const state = STATES.includes(raw.state as BudgetState) ? (raw.state as BudgetState) : "ok";
  return {
    id: text(raw.id),
    name: text(raw.name),
    period,
    threshold_usd: text(raw.threshold_usd, "0.00"),
    warn_at_percent: typeof raw.warn_at_percent === "number" ? raw.warn_at_percent : 80,
    company_id: nullable(raw.company_id),
    cost_center_id: nullable(raw.cost_center_id),
    notify_channels: text(raw.notify_channels, "email"),
    spend_usd: text(raw.spend_usd, "0.00"),
    used_pct: typeof raw.used_pct === "number" ? raw.used_pct : null,
    state,
    forecast_alert: raw.forecast_alert === true,
    last_triggered_at: nullable(raw.last_triggered_at),
  };
}

export function normaliseList(data: unknown): BudgetList {
  const raw = (data && typeof data === "object" ? data : {}) as Record<string, unknown>;
  const budgets = (Array.isArray(raw.budgets) ? raw.budgets : [])
    .filter((b): b is Record<string, unknown> => Boolean(b) && typeof (b as { id?: unknown }).id === "string")
    .map(normaliseBudget);
  const periods = Array.isArray(raw.periods)
    ? raw.periods.filter((p): p is BudgetPeriod => PERIODS.includes(p as BudgetPeriod))
    : PERIODS;
  return {
    budgets,
    can_edit: raw.can_edit === true,
    periods: periods.length ? periods : PERIODS,
    max_threshold_usd: text(raw.max_threshold_usd, "10000000"),
  };
}

function budgetOf(data: unknown): Budget {
  const raw = (data && typeof data === "object" ? (data as { budget?: unknown }).budget : null) ?? {};
  return normaliseBudget(raw as Record<string, unknown>);
}

export const budgetsApi = {
  async list(companyId?: string | null): Promise<BudgetList> {
    const { data } = await api.get(BASE, { params: companyId ? { company_id: companyId } : undefined });
    return normaliseList(data);
  },
  async create(input: BudgetInput): Promise<Budget> {
    const { data } = await api.post(BASE, input);
    return budgetOf(data);
  },
  async update(id: string, changes: Partial<BudgetInput>): Promise<Budget> {
    const { data } = await api.patch(`${BASE}/${encodeURIComponent(id)}`, changes);
    return budgetOf(data);
  },
  async remove(id: string): Promise<void> {
    await api.delete(`${BASE}/${encodeURIComponent(id)}`);
  },
};

/** Client-side checks that mirror the API's, so the form explains a problem before it is sent. */
export function validateBudget(input: BudgetInput, maxThreshold: string): string | null {
  const name = input.name.trim();
  if (!name || name.length > 100) return "name";
  const amount = Number(input.threshold_usd);
  if (!Number.isFinite(amount) || amount < 0.01 || amount > Number(maxThreshold)) return "threshold";
  if (!Number.isInteger(input.warn_at_percent) || input.warn_at_percent < 1 || input.warn_at_percent > 100) {
    return "warnAt";
  }
  return null;
}
