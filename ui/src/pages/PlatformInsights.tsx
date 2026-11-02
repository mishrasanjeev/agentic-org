// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
/**
 * Insights platform view: cross-tenant totals, runs per day, one row per
 * tenant and the platform posture, for the platform operator only.
 *
 * Reads GET /platform/insights/summary, /tenants (with the range from the
 * URL) and /posture. The backend decides who the operator is and answers
 * 404 to anyone else; this page then says the view is not available and
 * shows nothing else. Every figure comes from the API.
 */
import { useEffect, useMemo, useState } from "react";
import { Helmet } from "react-helmet-async";
import { useSearchParams } from "react-router";
import { useTranslation } from "react-i18next";
import ChartCard from "@/components/insights/ChartCard";
import DecisionTile from "@/components/insights/DecisionTile";
import EmptyState from "@/components/insights/EmptyState";
import { ChartSkeleton, TileSkeleton } from "@/components/insights/Skeleton";
import StateChip from "@/components/insights/StateChip";
import { errorStatus, readErrorDetail } from "@/lib/insights/api";
import { formatCount, formatPercent, formatUsd, MISSING } from "@/lib/insights/format";
import {
  PLATFORM_METRIC_KEYS,
  PLATFORM_RANGE_PRESETS,
  platformApi,
  platformMetric,
  platformRange,
  type PlatformPosture,
  type PlatformRangePreset,
  type PlatformSummary,
  type PlatformTenantRow,
  type PlatformTenants,
} from "@/lib/insights/platform";
import { stateColor } from "@/lib/insights/tokens";

type LoadState =
  | { kind: "loading" }
  | { kind: "ready"; summary: PlatformSummary; tenants: PlatformTenants; posture: PlatformPosture }
  | { kind: "unavailable" }
  | { kind: "error"; message: string | null };

export default function PlatformInsights() {
  const { t } = useTranslation();
  const [searchParams, setSearchParams] = useSearchParams();
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [reloadKey, setReloadKey] = useState(0);

  const rawPreset = searchParams.get("range") as PlatformRangePreset | null;
  const preset: PlatformRangePreset = rawPreset && PLATFORM_RANGE_PRESETS.includes(rawPreset) ? rawPreset : "30d";
  const range = useMemo(() => platformRange(preset), [preset]);

  useEffect(() => {
    let live = true;
    setState({ kind: "loading" });
    Promise.all([platformApi.summary(range), platformApi.tenants(range), platformApi.posture()])
      .then(([summary, tenants, posture]) => {
        if (live) setState({ kind: "ready", summary, tenants, posture });
      })
      .catch(async (e) => {
        if (!live) return;
        if (errorStatus(e) === 404) {
          setState({ kind: "unavailable" });
          return;
        }
        const message = await readErrorDetail(e);
        if (live) setState({ kind: "error", message });
      });
    return () => {
      live = false;
    };
  }, [range, reloadKey]);

  const selectPreset = (next: PlatformRangePreset) =>
    setSearchParams(
      (prev) => {
        const params = new URLSearchParams(prev);
        if (next === "30d") params.delete("range");
        else params.set("range", next);
        return params;
      },
      { replace: true },
    );
  const retry = () => setReloadKey((k) => k + 1);

  let content: React.ReactNode;
  if (state.kind === "loading") {
    content = <LoadingGrid label={t("platformInsights.loading")} />;
  } else if (state.kind === "unavailable") {
    content = (
      <EmptyState title={t("platformInsights.unavailable.title")} hint={t("platformInsights.unavailable.body")} />
    );
  } else if (state.kind === "error") {
    content = (
      <EmptyState
        title={t("platformInsights.error.load")}
        hint={state.message}
        action={
          <button type="button" onClick={retry} className="h-8 rounded-md border px-3 text-sm font-medium hover:bg-muted">
            {t("platformInsights.error.retry")}
          </button>
        }
      />
    );
  } else {
    content = <Body summary={state.summary} tenants={state.tenants} posture={state.posture} />;
  }

  return (
    <div className="flex flex-col gap-4" data-testid="platform-insights">
      <Helmet>
        <title>{`${t("platformInsights.title")} — AgenticOrg`}</title>
      </Helmet>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold md:text-2xl">{t("platformInsights.title")}</h1>
          <p className="text-sm text-muted-foreground">{t("platformInsights.subtitle")}</p>
        </div>
        {state.kind !== "unavailable" ? (
          <div role="group" aria-label={t("platformInsights.range.label")} className="flex gap-1">
            {PLATFORM_RANGE_PRESETS.map((p) => (
              <button
                key={p}
                type="button"
                aria-pressed={p === preset}
                onClick={() => selectPreset(p)}
                disabled={state.kind === "loading"}
                className={`h-8 rounded-md border px-3 text-sm font-medium ${
                  p === preset ? "bg-primary text-primary-foreground" : "hover:bg-muted"
                }`}
              >
                {t(`platformInsights.range.${p}`)}
              </button>
            ))}
          </div>
        ) : null}
      </div>
      {state.kind !== "unavailable" ? (
        <p className="text-xs text-muted-foreground" data-testid="platform-period">
          {t("platformInsights.period", { from: range.from, to: range.to })}
        </p>
      ) : null}
      {content}
    </div>
  );
}

function LoadingGrid({ label }: { label: string }) {
  return (
    <div role="status" aria-label={label} className="flex flex-col gap-4" data-testid="platform-loading">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {Array.from({ length: 4 }, (_, i) => (
          <TileSkeleton key={i} />
        ))}
      </div>
      <ChartSkeleton />
    </div>
  );
}

function Body({
  summary,
  tenants,
  posture,
}: {
  summary: PlatformSummary;
  tenants: PlatformTenants;
  posture: PlatformPosture;
}) {
  const { t } = useTranslation();
  const source = t("platformInsights.source");
  const hint = t("platformInsights.noRuns");
  const days = summary.series;
  const { totals } = summary;

  const tiles = [
    platformMetric(
      PLATFORM_METRIC_KEYS.tenantsActive,
      { title: t("platformInsights.tiles.tenantsActive"), definition: t("platformInsights.tiles.tenantsActiveDef"), source },
      summary,
      {
        unit: "count",
        value: totals.tenants_active,
        series: days.map((d) => ({ t: d.day, value: d.tenants_active })),
        hint,
      },
    ),
    platformMetric(
      PLATFORM_METRIC_KEYS.runs,
      { title: t("platformInsights.tiles.runs"), definition: t("platformInsights.tiles.runsDef"), source },
      summary,
      { unit: "count", value: totals.runs, series: days.map((d) => ({ t: d.day, value: d.runs })), hint },
    ),
    platformMetric(
      PLATFORM_METRIC_KEYS.failureRate,
      { title: t("platformInsights.tiles.failureRate"), definition: t("platformInsights.tiles.failureRateDef"), source },
      summary,
      {
        unit: "percent",
        value: totals.failure_rate === null ? null : totals.failure_rate * 100,
        series: days.map((d) => ({ t: d.day, value: d.runs ? (d.failed / d.runs) * 100 : null })),
        hint,
      },
    ),
    platformMetric(
      PLATFORM_METRIC_KEYS.spend,
      { title: t("platformInsights.tiles.spend"), definition: t("platformInsights.tiles.spendDef"), source },
      summary,
      { unit: "usd", value: totals.cost_usd, series: days.map((d) => ({ t: d.day, value: d.cost_usd })), hint },
    ),
  ];
  const runsChart = platformMetric(
    PLATFORM_METRIC_KEYS.runsPerDay,
    { title: t("platformInsights.chart.runsPerDay"), definition: t("platformInsights.chart.runsPerDayDef"), source },
    summary,
    { unit: "count", value: totals.runs, series: days.map((d) => ({ t: d.day, value: d.runs })), hint },
  );

  return (
    <>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4" data-testid="platform-tiles">
        {tiles.map((metric) => (
          <DecisionTile key={metric.key} metric={metric} currency="USD" />
        ))}
      </div>
      <ChartCard
        metric={runsChart}
        filters={{ from: summary.period.from, to: summary.period.to }}
        currency="USD"
        kind="line"
        exportable={false}
      />
      <TenantTable data={tenants} />
      <PostureChecklist posture={posture} />
    </>
  );
}

// ---------------------------------------------------------------- tenant table

type SortKey = "tenant_name" | "plan" | "runs" | "failure_rate" | "cost_usd" | "active_agents" | "last_active_day" | "reconciled_nights";
type SortDir = "ascending" | "descending";

const COLUMNS: Array<{ key: SortKey; labelKey: string; numeric: boolean }> = [
  { key: "tenant_name", labelKey: "platformInsights.tenants.name", numeric: false },
  { key: "plan", labelKey: "platformInsights.tenants.plan", numeric: false },
  { key: "runs", labelKey: "platformInsights.tenants.runs", numeric: true },
  { key: "failure_rate", labelKey: "platformInsights.tenants.failureRate", numeric: true },
  { key: "cost_usd", labelKey: "platformInsights.tenants.spend", numeric: true },
  { key: "active_agents", labelKey: "platformInsights.tenants.activeAgents", numeric: true },
  { key: "last_active_day", labelKey: "platformInsights.tenants.lastActive", numeric: false },
  { key: "reconciled_nights", labelKey: "platformInsights.tenants.reconciledNights", numeric: true },
];

function compareRows(a: PlatformTenantRow, b: PlatformTenantRow, key: SortKey): number {
  const x = a[key];
  const y = b[key];
  if (x === null || x === undefined) return y === null || y === undefined ? 0 : 1;
  if (y === null || y === undefined) return -1;
  if (typeof x === "number" && typeof y === "number") return x - y;
  return String(x).localeCompare(String(y));
}

function TenantTable({ data }: { data: PlatformTenants }) {
  const { t } = useTranslation();
  const [sort, setSort] = useState<{ key: SortKey; dir: SortDir } | null>(null);
  const rows = useMemo(() => {
    if (!sort) return data.tenants;
    const factor = sort.dir === "ascending" ? 1 : -1;
    return [...data.tenants].sort((a, b) => factor * compareRows(a, b, sort.key));
  }, [data.tenants, sort]);
  const toggle = (key: SortKey) =>
    setSort((prev) =>
      prev && prev.key === key
        ? { key, dir: prev.dir === "ascending" ? "descending" : "ascending" }
        : { key, dir: "ascending" },
    );
  const caption = t("platformInsights.tenants.title");

  return (
    <section aria-labelledby="platform-tenants-title" className="flex min-w-0 flex-col gap-3 rounded-lg border bg-background p-4">
      <header>
        <h2 id="platform-tenants-title" className="text-sm font-semibold text-foreground">
          {caption}
        </h2>
        <p className="mt-1 text-xs text-muted-foreground">
          {t("platformInsights.tenants.definition", { nights: data.reconciled_gate_nights })}
        </p>
      </header>
      {data.tenants.length === 0 ? (
        <EmptyState hint={t("platformInsights.tenants.empty")} />
      ) : (
        <div className="max-h-[32rem] overflow-auto rounded-md border" tabIndex={0} role="region" aria-label={caption}>
          <table className="w-full border-collapse text-left text-xs" data-testid="platform-tenants-table">
            <caption className="sr-only">{caption}</caption>
            <thead className="sticky top-0 bg-muted">
              <tr>
                {COLUMNS.map((col) => {
                  const active = sort?.key === col.key;
                  const label = t(col.labelKey);
                  return (
                    <th
                      key={col.key}
                      scope="col"
                      aria-sort={active ? sort.dir : "none"}
                      className={`px-3 py-2 font-medium ${col.numeric ? "text-right" : ""}`}
                    >
                      <button
                        type="button"
                        onClick={() => toggle(col.key)}
                        className="inline-flex items-center gap-1 rounded hover:text-foreground"
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
              {rows.map((row) => (
                <tr key={row.tenant_id} className="border-t" data-testid="platform-tenant-row">
                  <th scope="row" className="px-3 py-1.5 font-medium text-foreground">
                    {row.tenant_name || MISSING}
                  </th>
                  <td className="px-3 py-1.5">{row.plan || MISSING}</td>
                  <td className="px-3 py-1.5 text-right tabular-nums">{formatCount(row.runs)}</td>
                  <td className="px-3 py-1.5 text-right tabular-nums">
                    {row.failure_rate === null ? MISSING : formatPercent(row.failure_rate * 100)}
                  </td>
                  <td className="px-3 py-1.5 text-right tabular-nums">{formatUsd(row.cost_usd)}</td>
                  <td className="px-3 py-1.5 text-right tabular-nums">{formatCount(row.active_agents)}</td>
                  <td className="px-3 py-1.5 tabular-nums">{row.last_active_day ?? MISSING}</td>
                  <td className="px-3 py-1.5">
                    <GateBullet nights={row.reconciled_nights} gate={data.reconciled_gate_nights} status={row.last_rollup_status} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {data.truncated ? (
            <p className="border-t px-3 py-2 text-xs text-muted-foreground">
              {t("platformInsights.tenants.truncated", { shown: data.tenants.length })}
            </p>
          ) : null}
        </div>
      )}
    </section>
  );
}

/** Consecutive reconciled nights against the gate, as a bullet: a bar filled to the gate, the count beside it. */
function GateBullet({ nights, gate, status }: { nights: number; gate: number; status: string | null }) {
  const { t } = useTranslation();
  const met = nights >= gate;
  const pct = Math.max(0, Math.min(1, gate > 0 ? nights / gate : 0)) * 100;
  const label = t("platformInsights.gate.label", { nights, gate });
  return (
    <div className="flex min-w-[9rem] items-center gap-2" title={status ? t("platformInsights.gate.latest", { status }) : undefined}>
      <div
        role="meter"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={gate}
        aria-valuenow={Math.min(nights, gate)}
        aria-valuetext={label}
        data-testid="gate-bullet"
        data-met={met}
        className="relative h-2 w-20 overflow-hidden rounded-full bg-muted"
      >
        <div
          className="h-full rounded-full"
          style={{ width: `${pct}%`, backgroundColor: stateColor(met ? "ok" : "watch") }}
        />
      </div>
      <span className="tabular-nums text-muted-foreground">
        {t("platformInsights.gate.short", { nights, gate })}
      </span>
    </div>
  );
}

// ---------------------------------------------------------------- posture

function PostureChecklist({ posture }: { posture: PlatformPosture }) {
  const { t } = useTranslation();
  const detail = (key: string): string => {
    if (key === "schema" && posture.schema) {
      return t("platformInsights.posture.schemaDetail", {
        database: posture.schema.database_heads.join(", ") || MISSING,
        code: posture.schema.code_heads.join(", ") || MISSING,
      });
    }
    if (key === "rls" && posture.rls) {
      const base = t("platformInsights.posture.rlsDetail", { forced: posture.rls.forced, total: posture.rls.tenant_tables });
      return posture.rls.unforced.length
        ? `${base} ${t("platformInsights.posture.rlsUnforced", { tables: posture.rls.unforced.join(", ") })}`
        : base;
    }
    if (key === "role" && posture.role) {
      return t(posture.role.ok ? "platformInsights.posture.roleOk" : "platformInsights.posture.roleBypass", {
        role: posture.role.role ?? MISSING,
      });
    }
    if (key === "health" && posture.health) {
      const h = posture.health;
      if (!h.snapshots) return t("platformInsights.posture.healthNone", { hours: h.window_hours });
      return t("platformInsights.posture.healthDetail", {
        snapshots: h.snapshots,
        unhealthy: h.counts?.unhealthy ?? 0,
        hours: h.window_hours,
        version: h.latest_version || MISSING,
        commit: h.latest_commit || MISSING,
      });
    }
    return "";
  };

  return (
    <section aria-labelledby="platform-posture-title" className="flex flex-col gap-3 rounded-lg border bg-background p-4">
      <header className="flex items-center justify-between gap-2">
        <h2 id="platform-posture-title" className="text-sm font-semibold text-foreground">
          {t("platformInsights.posture.title")}
        </h2>
        <StateChip state={posture.ok ? "ok" : "act"} />
      </header>
      {posture.checks.length === 0 ? (
        <EmptyState hint={t("platformInsights.posture.empty")} />
      ) : (
        <ul className="flex flex-col divide-y" data-testid="platform-posture">
          {posture.checks.map((check) => (
            <li key={check.key} className="flex items-start justify-between gap-3 py-2" data-testid="posture-check" data-ok={check.ok}>
              <div className="min-w-0">
                <p className="text-sm font-medium text-foreground">
                  {t(`platformInsights.posture.checks.${check.key}`, { defaultValue: check.key })}
                </p>
                <p className="break-words text-xs text-muted-foreground">{detail(check.key)}</p>
              </div>
              <StateChip state={check.ok ? "ok" : "act"} />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
