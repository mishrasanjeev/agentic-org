// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import type { MetricRow } from "@/lib/insights/api";
import {
  formatDecimal,
  formatLowerBound,
  formatMetricValue,
  formatMoney,
  humaniseKey,
  MISSING,
  toFiniteNumber,
} from "@/lib/insights/format";

type SortDir = "ascending" | "descending";

/** Columns that carry the metric's own unit; other numeric columns render as plain numbers. */
const UNIT_COLUMNS = new Set(["value", "point", "low", "high", "actual", "threshold", "compare_value"]);
/** Amount columns of rows that name their own currency (budget rows). */
const MONEY_COLUMNS = new Set(["actual", "threshold", "amount", "cost", "cost_usd", "amount_inr"]);

export function formatCell(
  column: string,
  value: unknown,
  unit?: string | null,
  currency?: string | null,
  rowCurrency?: unknown,
  lowerBound?: boolean,
): string {
  if (value === null || value === undefined || value === "") return MISSING;
  if (MONEY_COLUMNS.has(column) && typeof rowCurrency === "string" && rowCurrency && toFiniteNumber(value) !== null) {
    return formatMoney(value, rowCurrency);
  }
  if (typeof value === "number" || (typeof value === "string" && UNIT_COLUMNS.has(column) && toFiniteNumber(value) !== null)) {
    if (UNIT_COLUMNS.has(column) && unit) {
      const shown = formatMetricValue(value, unit, currency);
      // A row whose value is only a floor (a latency percentile over 10 minutes) reads "> 10m".
      return lowerBound && column === "value" ? formatLowerBound(shown) : shown;
    }
    return formatDecimal(value, currency);
  }
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "object") {
    try {
      return JSON.stringify(value);
    } catch {
      return MISSING;
    }
  }
  return String(value);
}

function compare(a: unknown, b: unknown): number {
  const na = toFiniteNumber(a);
  const nb = toFiniteNumber(b);
  if (na !== null && nb !== null) return na - nb;
  if (a === null || a === undefined) return b === null || b === undefined ? 0 : 1;
  if (b === null || b === undefined) return -1;
  return String(a).localeCompare(String(b));
}

/**
 * Sortable data table. Column headers are buttons (Enter/Space sort), the
 * sorted column carries aria-sort, and the scroll region is focusable so
 * keyboard users can scroll wide tables.
 */
export default function DataTable({
  columns,
  rows,
  caption,
  unit,
  currency,
  maxRows = 500,
}: {
  columns: string[];
  rows: MetricRow[];
  caption: string;
  unit?: string | null;
  currency?: string | null;
  maxRows?: number;
}) {
  const { t } = useTranslation();
  const [sort, setSort] = useState<{ column: string; dir: SortDir } | null>(null);

  const sorted = useMemo(() => {
    const list = rows.slice(0, maxRows);
    if (!sort) return list;
    const factor = sort.dir === "ascending" ? 1 : -1;
    return [...list].sort((a, b) => factor * compare(a[sort.column], b[sort.column]));
  }, [rows, sort, maxRows]);

  const toggle = (column: string) => {
    setSort((prev) =>
      prev && prev.column === column
        ? { column, dir: prev.dir === "ascending" ? "descending" : "ascending" }
        : { column, dir: "ascending" },
    );
  };

  return (
    <div className="max-h-80 overflow-auto rounded-md border" tabIndex={0} role="region" aria-label={caption}>
      <table className="w-full border-collapse text-left text-xs" data-testid="insights-data-table">
        <caption className="sr-only">{caption}</caption>
        <thead className="sticky top-0 bg-muted">
          <tr>
            {columns.map((col) => {
              const active = sort?.column === col;
              const label = humaniseKey(col === "t" ? "date" : col);
              return (
                <th key={col} scope="col" aria-sort={active ? sort.dir : "none"} className="px-3 py-2 font-medium">
                  <button
                    type="button"
                    onClick={() => toggle(col)}
                    className="inline-flex items-center gap-1 rounded text-left hover:text-foreground"
                    aria-label={t("insights.table.sortBy", { column: label })}
                  >
                    {label}
                    <span aria-hidden="true" className="text-muted-foreground">
                      {active ? (sort.dir === "ascending" ? "▲" : "▼") : "↕"}
                    </span>
                  </button>
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {sorted.length === 0 ? (
            <tr>
              <td colSpan={Math.max(columns.length, 1)} className="px-3 py-3 text-muted-foreground">
                {t("insights.table.empty")}
              </td>
            </tr>
          ) : (
            sorted.map((row, i) => (
              <tr key={i} className="border-t">
                {columns.map((col) => (
                  <td key={col} className="px-3 py-1.5 tabular-nums">
                    {formatCell(col, row[col], unit, currency, row.currency, row.lower_bound === true)}
                  </td>
                ))}
              </tr>
            ))
          )}
        </tbody>
      </table>
      {rows.length > maxRows ? (
        <p className="border-t px-3 py-2 text-xs text-muted-foreground">
          {t("insights.table.truncated", { shown: maxRows, total: rows.length })}
        </p>
      ) : null}
    </div>
  );
}
