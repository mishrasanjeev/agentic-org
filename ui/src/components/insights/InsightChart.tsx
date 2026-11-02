// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ComposedChart,
  ErrorBar,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Scatter,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { MetricResponse } from "@/lib/insights/api";
import {
  bulletData,
  forecastData,
  lineData,
  rankedData,
  stackedData,
  waterfallData,
  type BulletItem,
} from "@/lib/insights/chartData";
import { formatMetricValue, formatMoney, MISSING } from "@/lib/insights/format";
import type { ChartKind } from "@/lib/insights/metrics";
import { chartColor, normaliseState, stateColor } from "@/lib/insights/tokens";
import { cn } from "@/lib/utils";

const HEIGHT = 220;
const AXIS_TICK = { fontSize: 11, fill: "hsl(var(--muted-foreground))" };
const GRID_STROKE = "hsl(var(--border))";
const TOOLTIP_STYLE = {
  backgroundColor: "hsl(var(--background))",
  border: "1px solid hsl(var(--border))",
  borderRadius: 6,
  fontSize: 12,
  color: "hsl(var(--foreground))",
};

/** Plain-language summary of what the chart shows, for screen readers and the figure description. */
export function chartSummary(metric: MetricResponse, kind: ChartKind, t: TFunction, currency?: string | null): string {
  const cur = metric.currency || currency || null;
  const fmt = (v: unknown) => formatMetricValue(v, metric.unit, cur);
  const title = metric.title;
  switch (kind) {
    case "line": {
      const pts = lineData(metric.series);
      if (pts.length === 0) break;
      const max = pts.reduce((a, b) => (b.value > a.value ? b : a));
      return t("insights.chart.summaryLine", {
        title,
        count: pts.length,
        from: pts[0].t,
        to: pts[pts.length - 1].t,
        latest: fmt(pts[pts.length - 1].value),
        max: fmt(max.value),
        maxAt: max.t,
      });
    }
    case "stacked": {
      const { groups, rows } = stackedData(metric.series);
      if (rows.length === 0) break;
      const totals = groups.map((g) => ({ g, v: rows.reduce((s, r) => s + (typeof r[g] === "number" ? (r[g] as number) : 0), 0) }));
      const top = totals.reduce((a, b) => (b.v > a.v ? b : a));
      return t("insights.chart.summaryStacked", {
        title,
        count: rows.length,
        from: String(rows[0].t),
        to: String(rows[rows.length - 1].t),
        groups: groups.length,
        top: top.g,
        value: fmt(top.v),
      });
    }
    case "waterfall": {
      const steps = waterfallData(metric.rows);
      if (steps.length === 0) break;
      const first = steps[0];
      const last = steps[steps.length - 1];
      return t("insights.chart.summaryWaterfall", {
        title,
        gross: fmt(first.value),
        net: fmt(last.value),
      });
    }
    case "hbar": {
      const items = rankedData(metric);
      if (items.length === 0) break;
      return t("insights.chart.summaryRanked", { title, count: items.length, top: items[0].label, value: fmt(items[0].value) });
    }
    case "bullet": {
      const items = bulletData(metric.rows);
      if (items.length === 0) break;
      const states = items.map((i) => normaliseState(i.state));
      return t("insights.chart.summaryBullet", {
        title,
        count: items.length,
        breached: states.filter((s) => s === "act").length,
        warn: states.filter((s) => s === "watch").length,
      });
    }
    case "forecast": {
      const f = forecastData(metric);
      if (!f) break;
      if (f.low !== null && f.high !== null) {
        return t("insights.chart.summaryForecast", { title, point: fmt(f.point), low: fmt(f.low), high: fmt(f.high) });
      }
      return t("insights.chart.summaryForecastNoBand", { title, point: fmt(f.point) });
    }
  }
  return t("insights.chart.summaryEmpty", { title });
}

function BulletBars({ items, fmt, t }: { items: BulletItem[]; fmt: (v: unknown) => string; t: TFunction }) {
  return (
    <ul className="flex flex-col gap-3" data-testid="bullet-bars">
      {items.map((item, idx) => {
        // Each budget has its own scale: budgets of ₹100 and ₹2,500 share no axis. The track runs to the
        // larger of actual and threshold, so an overspend fills the bar and the marker shows the limit.
        const scale = Math.max(item.actual, item.threshold ?? 0) || 1;
        const s = normaliseState(item.state) ?? "ok";
        const actualPct = Math.min(100, (item.actual / scale) * 100);
        const thresholdPct = item.threshold !== null ? Math.min(100, (item.threshold / scale) * 100) : null;
        const show = (v: number) => (item.currency ? formatMoney(v, item.currency) : fmt(v));
        return (
          <li key={`${item.label}-${idx}`} className="text-xs">
            <div className="mb-1 flex justify-between gap-2">
              <span className="font-medium">{item.label}</span>
              <span className="tabular-nums text-muted-foreground">
                {show(item.actual)} / {item.threshold !== null ? show(item.threshold) : MISSING}
              </span>
            </div>
            <div className="relative h-3 w-full rounded-sm bg-muted" aria-hidden="true">
              <div className="absolute inset-y-0 left-0 rounded-sm" style={{ width: `${actualPct}%`, backgroundColor: stateColor(s) }} />
              {thresholdPct !== null ? (
                <div
                  className="absolute -inset-y-1 w-0.5 bg-foreground"
                  style={{ left: `calc(${thresholdPct}% - 1px)` }}
                  title={t("insights.chart.threshold")}
                />
              ) : null}
            </div>
          </li>
        );
      })}
    </ul>
  );
}

/** Renders one metric in the chart form its key calls for. */
export default function InsightChart({
  metric,
  kind,
  currency,
  className,
}: {
  metric: MetricResponse;
  kind: ChartKind;
  currency?: string | null;
  className?: string;
}) {
  const { t } = useTranslation();
  const cur = metric.currency || currency || null;
  const fmt = (v: unknown) => formatMetricValue(v, metric.unit, cur);
  const tooltipFormatter = (value: unknown) => fmt(value);

  let body: React.ReactNode = null;

  if (kind === "line") {
    const data = lineData(metric.series);
    body = (
      <ResponsiveContainer width="100%" height={HEIGHT}>
        <LineChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={GRID_STROKE} strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="t" tick={AXIS_TICK} tickLine={false} axisLine={false} minTickGap={24} />
          <YAxis tick={AXIS_TICK} tickLine={false} axisLine={false} width={72} tickFormatter={(v) => fmt(v)} />
          <Tooltip formatter={tooltipFormatter} contentStyle={TOOLTIP_STYLE} />
          <Line type="monotone" dataKey="value" name={metric.title} stroke={chartColor(0)} strokeWidth={2} dot={false} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    );
  } else if (kind === "stacked") {
    const { groups, rows } = stackedData(metric.series);
    body = (
      <ResponsiveContainer width="100%" height={HEIGHT}>
        <BarChart data={rows} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={GRID_STROKE} strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="t" tick={AXIS_TICK} tickLine={false} axisLine={false} minTickGap={24} />
          <YAxis tick={AXIS_TICK} tickLine={false} axisLine={false} width={72} tickFormatter={(v) => fmt(v)} />
          <Tooltip formatter={tooltipFormatter} contentStyle={TOOLTIP_STYLE} />
          {groups.length > 1 ? <Legend wrapperStyle={{ fontSize: 11 }} /> : null}
          {groups.map((g, i) => (
            <Bar key={g} dataKey={g} name={g} stackId="parts" fill={chartColor(i)} isAnimationActive={false} />
          ))}
        </BarChart>
      </ResponsiveContainer>
    );
  } else if (kind === "hbar") {
    const items = rankedData(metric);
    body = (
      <ResponsiveContainer width="100%" height={Math.max(120, items.length * 28 + 24)}>
        <BarChart data={items} layout="vertical" margin={{ top: 4, right: 16, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={GRID_STROKE} strokeDasharray="3 3" horizontal={false} />
          <XAxis type="number" tick={AXIS_TICK} tickLine={false} axisLine={false} tickFormatter={(v) => fmt(v)} />
          <YAxis type="category" dataKey="label" tick={AXIS_TICK} tickLine={false} axisLine={false} width={128} />
          <Tooltip formatter={tooltipFormatter} contentStyle={TOOLTIP_STYLE} />
          <Bar dataKey="value" name={metric.title} fill={chartColor(0)} isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
    );
  } else if (kind === "waterfall") {
    const steps = waterfallData(metric.rows).map((s) => ({ ...s, name: t(`insights.waterfall.${s.label}`, { defaultValue: s.label }) }));
    body = (
      <ResponsiveContainer width="100%" height={HEIGHT}>
        <BarChart data={steps} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={GRID_STROKE} strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="name" tick={AXIS_TICK} tickLine={false} axisLine={false} interval={0} />
          <YAxis tick={AXIS_TICK} tickLine={false} axisLine={false} width={72} tickFormatter={(v) => fmt(v)} />
          <Tooltip
            contentStyle={TOOLTIP_STYLE}
            formatter={(_v: unknown, name: unknown, item: { payload?: { value?: number } }) =>
              name === "size" ? [fmt(item?.payload?.value), metric.title] : [null, null]
            }
          />
          <Bar dataKey="base" stackId="w" fill="transparent" isAnimationActive={false} />
          <Bar dataKey="size" stackId="w" isAnimationActive={false}>
            {steps.map((s) => (
              <Cell key={s.label} fill={s.kind === "decrease" ? stateColor("act") : chartColor(0)} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    );
  } else if (kind === "bullet") {
    body = <BulletBars items={bulletData(metric.rows)} fmt={fmt} t={t} />;
  } else if (kind === "forecast") {
    const f = forecastData(metric);
    if (f) {
      const data = [
        ...(f.actual !== null ? [{ label: t("insights.chart.actual"), point: f.actual, err: [0, 0] }] : []),
        {
          label: t("insights.chart.forecast"),
          point: f.point,
          err: [f.low !== null ? Math.max(0, f.point - f.low) : 0, f.high !== null ? Math.max(0, f.high - f.point) : 0],
        },
      ];
      body = (
        <div className="flex flex-col gap-2">
          <p className="text-sm tabular-nums" data-testid="forecast-text">
            <span className="font-semibold">{fmt(f.point)}</span>
            {f.low !== null && f.high !== null ? (
              <span className="text-muted-foreground">
                {" "}
                ({t("insights.chart.low")} {fmt(f.low)} {"–"} {t("insights.chart.high")} {fmt(f.high)})
              </span>
            ) : null}
          </p>
          <ResponsiveContainer width="100%" height={HEIGHT - 40}>
            <ComposedChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
              <CartesianGrid stroke={GRID_STROKE} strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="label" type="category" tick={AXIS_TICK} tickLine={false} axisLine={false} />
              <YAxis tick={AXIS_TICK} tickLine={false} axisLine={false} width={72} tickFormatter={(v) => fmt(v)} />
              <Tooltip formatter={tooltipFormatter} contentStyle={TOOLTIP_STYLE} />
              <Scatter dataKey="point" name={metric.title} fill={chartColor(0)} isAnimationActive={false}>
                <ErrorBar dataKey="err" direction="y" width={10} stroke={chartColor(0)} strokeWidth={1.5} />
              </Scatter>
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      );
    }
  }

  return (
    <div className={cn("min-w-0", className)} data-chart-kind={kind}>
      {body}
    </div>
  );
}
