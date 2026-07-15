export const BILLING_CATALOG_SCHEMA_VERSION = "agenticorg.billing-plans.v1";

export interface PlanPrice {
  currency: "USD" | "INR";
  amount_minor: number;
  interval: "month";
}

export interface PlanLimits {
  agent_count: number | null;
  agent_runs: number | null;
  agent_runs_interval: "month";
  storage_bytes: number | null;
}

export interface PublicPlan {
  plan_id: string;
  display_name: string;
  display_order: number;
  prices: PlanPrice[];
  limits: PlanLimits;
  signup_available: boolean;
  checkout_mode: "none" | "hosted";
}

export interface PublicPlanCatalog {
  schema_version: typeof BILLING_CATALOG_SCHEMA_VERSION;
  catalog_version: string;
  complete: true;
  plan_count: number;
  plans: PublicPlan[];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isPositiveIntegerOrNull(value: unknown): value is number | null {
  return value === null || (Number.isInteger(value) && Number(value) > 0);
}

function isPrice(value: unknown): value is PlanPrice {
  if (!isRecord(value)) return false;
  return (
    (value.currency === "USD" || value.currency === "INR") &&
    Number.isInteger(value.amount_minor) &&
    Number(value.amount_minor) >= 0 &&
    value.interval === "month"
  );
}

function isPlan(value: unknown): value is PublicPlan {
  if (!isRecord(value) || !isRecord(value.limits) || !Array.isArray(value.prices)) return false;
  const currencies = value.prices.filter(isPrice).map((price) => price.currency);
  return (
    typeof value.plan_id === "string" &&
    /^[a-z][a-z0-9_-]*$/.test(value.plan_id) &&
    typeof value.display_name === "string" &&
    value.display_name.trim().length > 0 &&
    Number.isInteger(value.display_order) &&
    Number(value.display_order) >= 0 &&
    value.prices.length > 0 &&
    currencies.length === value.prices.length &&
    new Set(currencies).size === currencies.length &&
    isPositiveIntegerOrNull(value.limits.agent_count) &&
    isPositiveIntegerOrNull(value.limits.agent_runs) &&
    value.limits.agent_runs_interval === "month" &&
    isPositiveIntegerOrNull(value.limits.storage_bytes) &&
    typeof value.signup_available === "boolean" &&
    (value.checkout_mode === "none" || value.checkout_mode === "hosted")
  );
}

export function isPublicPlanCatalog(value: unknown): value is PublicPlanCatalog {
  if (!isRecord(value) || !Array.isArray(value.plans)) return false;
  if (
    value.schema_version !== BILLING_CATALOG_SCHEMA_VERSION ||
    typeof value.catalog_version !== "string" ||
    value.catalog_version.trim().length === 0 ||
    value.complete !== true ||
    !Number.isInteger(value.plan_count) ||
    Number(value.plan_count) <= 0 ||
    value.plan_count !== value.plans.length ||
    !value.plans.every(isPlan)
  ) {
    return false;
  }
  const plans = value.plans as PublicPlan[];
  return (
    new Set(plans.map((plan) => plan.plan_id)).size === plans.length &&
    new Set(plans.map((plan) => plan.display_order)).size === plans.length
  );
}

export function orderedPlans(catalog: PublicPlanCatalog): PublicPlan[] {
  return [...catalog.plans].sort((left, right) => left.display_order - right.display_order);
}

export function formatPlanPrice(price: PlanPrice): string {
  const amount = price.amount_minor / 100;
  const formatted = new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: price.currency,
    minimumFractionDigits: amount % 1 === 0 ? 0 : 2,
    maximumFractionDigits: 2,
  }).format(amount);
  return `${formatted} / ${price.interval}`;
}
