// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
import { Link } from "react-router";
import { useTranslation } from "react-i18next";
import type { MetricResponse } from "@/lib/insights/api";
import { computeDelta, formatLowerBound, formatMetricValue } from "@/lib/insights/format";
import { isEmptyMetric, lineData } from "@/lib/insights/chartData";
import { chartColor } from "@/lib/insights/tokens";
import DataQualityBadge from "./DataQualityBadge";
import EmptyState from "./EmptyState";
import StateChip from "./StateChip";

/** Tiny trend line for a tile. Plain SVG: it carries no axes, so recharts would be overkill. */
export function Sparkline({ values, label }: { values: number[]; label: string }) {
  if (values.length < 2) return null;
  const width = 120;
  const height = 28;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const step = width / (values.length - 1);
  const points = values
    .map((v, i) => `${(i * step).toFixed(1)},${(height - 2 - ((v - min) / span) * (height - 4)).toFixed(1)}`)
    .join(" ");
  return (
    <svg
      data-testid="sparkline"
      role="img"
      aria-label={label}
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      className="h-7 w-full"
    >
      <polyline points={points} fill="none" stroke={chartColor(0)} strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

/**
 * A decision tile: value, change against the comparison period, state chip,
 * sparkline and one action. With no data it shows the API's remediation hint.
 */
export default function DecisionTile({
  metric,
  action,
  currency,
}: {
  metric: MetricResponse;
  action?: { to: string; label: string } | null;
  currency?: string | null;
}) {
  const { t } = useTranslation();
  const cur = metric.currency || currency || null;
  const empty = isEmptyMetric(metric);
  // With a running today in the period the API compares complete days only: the change is the
  // complete days' value against the comparison's, never today's partial value.
  const completeDays = metric.compare?.basis === "complete_days";
  const delta = computeDelta(completeDays ? metric.compare?.current_value : metric.value, metric.compare_value);
  const hasCompare = Boolean(metric.compare) && metric.compare_value !== undefined && metric.compare_value !== null;
  const trend = lineData(metric.series).map((p) => p.value);
  const shown = formatMetricValue(metric.value, metric.unit, cur);
  // A latency in the open-ended bucket is only a floor: "> 10m", never "10m".
  const valueText = metric.lower_bound ? formatLowerBound(shown) : shown;
  const arrow = delta.direction === "up" ? "↑" : delta.direction === "down" ? "↓" : "";

  return (
    <article
      data-testid="decision-tile"
      data-metric-key={metric.key}
      aria-labelledby={`tile-${metric.key}`}
      className="flex min-w-0 flex-col gap-2 rounded-lg border bg-background p-4"
    >
      <div className="flex items-start justify-between gap-2">
        <h3 id={`tile-${metric.key}`} className="text-sm font-medium text-muted-foreground" title={metric.definition}>
          {metric.title}
        </h3>
        <StateChip state={metric.state} />
      </div>

      {empty ? (
        <EmptyState compact hint={metric.hint} />
      ) : (
        <>
          <p className="text-2xl font-semibold tabular-nums text-foreground" data-testid="tile-value">
            {valueText}
          </p>
          <p className="text-xs text-muted-foreground tabular-nums" data-testid="tile-delta">
            {hasCompare && delta.pct !== null ? (
              <>
                <span className="font-medium text-foreground">
                  <span aria-hidden="true">{arrow} </span>
                  {delta.text}
                </span>{" "}
                {completeDays ? t("insights.tile.vsCompareCompleteDays") : t("insights.tile.vsCompare")}
              </>
            ) : (
              t("insights.tile.noCompare")
            )}
          </p>
          <Sparkline values={trend} label={t("insights.tile.trendLabel", { title: metric.title })} />
        </>
      )}

      <div className="mt-auto flex items-center justify-between gap-2 pt-1">
        <DataQualityBadge quality={metric.data_quality} />
        {action ? (
          <Link to={action.to} className="text-xs font-medium text-primary underline-offset-4 hover:underline">
            {action.label}
          </Link>
        ) : null}
      </div>
    </article>
  );
}
