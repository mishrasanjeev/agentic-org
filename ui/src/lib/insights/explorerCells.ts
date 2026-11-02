// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
/**
 * How an explorer cell reads: by the column's declared type, never by
 * guessing from the value. Missing values read as an em dash, never "NaN",
 * "null" or "undefined". Money is shown in the explorer's reporting currency
 * except columns that name their own (`cost_usd`).
 */
import type { ExplorerColumn, ExplorerRow } from "./explore";
import {
  MISSING,
  formatCount,
  formatDuration,
  formatMoney,
  formatPercent,
  formatTokens,
  toFiniteNumber,
} from "./format";

export function columnCurrency(
  column: ExplorerColumn,
  currency: string | null | undefined,
): string | null {
  if (column.key.endsWith("_usd")) return "USD";
  return currency ?? null;
}

export function formatDateTime(
  value: unknown,
  timezone?: string | null,
): string {
  if (typeof value !== "string" || value === "") return MISSING;
  const moment = new Date(value);
  if (Number.isNaN(moment.getTime())) return MISSING;
  try {
    return moment.toLocaleString(undefined, {
      timeZone: timezone || undefined,
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return moment.toLocaleString();
  }
}

/** The cell text for a value of `column`; booleans come back as the given yes / no labels. */
export function formatCell(
  column: ExplorerColumn,
  value: unknown,
  options: {
    currency?: string | null;
    timezone?: string | null;
    yes: string;
    no: string;
  },
): string {
  if (value === null || value === undefined || value === "") return MISSING;
  switch (column.type) {
    case "money":
      return formatMoney(value, columnCurrency(column, options.currency));
    case "percent":
      return formatPercent(value);
    case "duration_ms":
      return formatDuration(value);
    case "tokens":
      return formatTokens(value);
    case "count":
    case "number":
      return toFiniteNumber(value) === null ? MISSING : formatCount(value);
    case "datetime":
    case "date":
      return formatDateTime(value, options.timezone);
    case "bool":
      return value === true
        ? options.yes
        : value === false
          ? options.no
          : MISSING;
    case "id": {
      const text = String(value);
      return text.length > 12 ? `${text.slice(0, 8)}…` : text;
    }
    default:
      return String(value).replace(/_/g, " ");
  }
}

/** Numbers sit right-aligned so columns of figures line up. */
export function isNumericColumn(column: ExplorerColumn): boolean {
  return [
    "money",
    "percent",
    "duration_ms",
    "tokens",
    "count",
    "number",
  ].includes(column.type);
}

/** Where a row drills to: the run, then the approval, the workflow run, the agent, the connector. */
export function primaryLink(row: ExplorerRow): string | null {
  const links = row.links ?? {};
  for (const name of [
    "run",
    "approval",
    "workflow_run",
    "agent",
    "connector",
  ]) {
    const target = links[name];
    if (typeof target === "string" && target.startsWith("/")) return target;
  }
  return null;
}

/** The column that carries a row's link: its first text column. */
export function linkColumn(columns: ExplorerColumn[]): string | null {
  const preferred = [
    "agent",
    "title",
    "workflow",
    "connector",
    "started_at",
    "created_at",
  ];
  for (const key of preferred)
    if (columns.some((c) => c.key === key)) return key;
  return columns[0]?.key ?? null;
}
