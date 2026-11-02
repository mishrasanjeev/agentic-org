// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import type { InsightsFilters, MetricResponse } from "@/lib/insights/api";
import { dataShape, isEmptyMetric, tableColumns, tableRows } from "@/lib/insights/chartData";
import { chartKindFor, tableFromSeries, type ChartKind } from "@/lib/insights/metrics";
import DataQualityBadge from "./DataQualityBadge";
import DataTable from "./DataTable";
import EmptyState from "./EmptyState";
import ExportMenu from "./ExportMenu";
import InsightChart, { chartSummary } from "./InsightChart";

/**
 * A chart with its title, data-quality badge, source, a text summary for
 * assistive technology, a "View data" table toggle and the export menu
 * (left out with `exportable={false}` for data that has no export endpoint).
 */
export default function ChartCard({
  metric,
  filters,
  currency,
  kind,
  exportable = true,
}: {
  metric: MetricResponse;
  filters: InsightsFilters;
  currency?: string | null;
  kind?: ChartKind;
  exportable?: boolean;
}) {
  const { t } = useTranslation();
  const [showData, setShowData] = useState(false);
  const baseId = useId();
  const summaryId = `${baseId}-summary`;
  const tableId = `${baseId}-table`;
  const empty = isEmptyMetric(metric);
  const chartKind = kind ?? chartKindFor(metric.key, dataShape(metric));
  const fromSeries = tableFromSeries(chartKind);
  const summary = chartSummary(metric, chartKind, t, currency);
  const cur = metric.currency || currency || null;

  return (
    <section
      data-testid="chart-card"
      data-metric-key={metric.key}
      aria-labelledby={`${baseId}-title`}
      className="flex min-w-0 flex-col gap-3 rounded-lg border bg-background p-4"
    >
      <header className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h3 id={`${baseId}-title`} className="text-sm font-semibold text-foreground">
              {metric.title}
            </h3>
            <DataQualityBadge quality={metric.data_quality} />
          </div>
          {metric.definition ? <p className="mt-1 text-xs text-muted-foreground">{metric.definition}</p> : null}
        </div>
        <div className="flex items-start gap-2">
          {!empty ? (
            <button
              type="button"
              aria-expanded={showData}
              aria-controls={tableId}
              onClick={() => setShowData((s) => !s)}
              className="rounded-md border px-2.5 py-1 text-xs font-medium hover:bg-muted"
            >
              {showData ? t("insights.chart.hideData") : t("insights.chart.viewData")}
            </button>
          ) : null}
          {exportable ? <ExportMenu metricKey={metric.key} title={metric.title} filters={filters} /> : null}
        </div>
      </header>

      {empty ? (
        <EmptyState hint={metric.hint} />
      ) : (
        <figure role="figure" aria-labelledby={`${baseId}-title`} aria-describedby={summaryId} className="m-0">
          <InsightChart metric={metric} kind={chartKind} currency={currency} />
          <figcaption id={summaryId} className="sr-only" data-testid="chart-summary">
            {summary}
          </figcaption>
        </figure>
      )}

      {showData && !empty ? (
        <div id={tableId}>
          <DataTable
            columns={tableColumns(metric, fromSeries)}
            rows={tableRows(metric, fromSeries)}
            caption={t("insights.table.caption", { title: metric.title })}
            unit={metric.unit}
            currency={cur}
          />
        </div>
      ) : null}

      {metric.source ? (
        <p className="text-[11px] text-muted-foreground">{t("insights.quality.source", { source: metric.source })}</p>
      ) : null}
    </section>
  );
}
