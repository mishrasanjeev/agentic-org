// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
/**
 * Insights: Executive and Finance views over measured run and cost data.
 *
 * Reads GET /insights/status, then GET /insights/views/{view} with the
 * filters held in the URL (view, range preset, custom from/to, compare) and
 * the company chosen in the header switcher. The backend decides which views
 * a role may read and whether Insights is on; this page never shows a
 * number the API did not send.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Helmet } from "react-helmet-async";
import { Link, useSearchParams } from "react-router";
import { useTranslation } from "react-i18next";
import { useAuth } from "@/contexts/AuthContext";
import ChartCard from "@/components/insights/ChartCard";
import DecisionTile from "@/components/insights/DecisionTile";
import EmptyState from "@/components/insights/EmptyState";
import FilterBar, { type FilterValue } from "@/components/insights/FilterBar";
import NeedsYouList from "@/components/insights/NeedsYouList";
import { ChartSkeleton, TileSkeleton } from "@/components/insights/Skeleton";
import {
  errorStatus,
  insightsApi,
  readErrorDetail,
  type CompareMode,
  type InsightsFilters,
  type InsightsStatus,
  type InsightsViewKey,
  type InsightsViewResponse,
} from "@/lib/insights/api";
import { isIsoDate, presetRange, RANGE_PRESETS, todayIn, type RangePreset } from "@/lib/insights/dates";
import { exploreApi, type ExplorerDescription } from "@/lib/insights/explore";
import { defaultViewForRole, METRIC_PRESENTATION, ROLE_VIEWS } from "@/lib/insights/metrics";

type StatusState =
  | { kind: "loading" }
  | { kind: "ready"; status: InsightsStatus }
  | { kind: "disabled" }
  | { kind: "error"; message: string | null };

type ViewState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ready"; data: InsightsViewResponse }
  | { kind: "disabled" }
  | { kind: "forbidden" }
  | { kind: "error"; message: string | null };

function readCompanyId(): string {
  try {
    return localStorage.getItem("company_id") || "";
  } catch {
    return "";
  }
}

export default function InsightsDashboard() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const role = user?.role || "";
  const [searchParams, setSearchParams] = useSearchParams();
  const [statusState, setStatusState] = useState<StatusState>({ kind: "loading" });
  const [viewState, setViewState] = useState<ViewState>({ kind: "idle" });
  const [reloadKey, setReloadKey] = useState(0);
  const [explorers, setExplorers] = useState<ExplorerDescription[]>([]);
  const tabRefs = useRef<Partial<Record<InsightsViewKey, HTMLButtonElement | null>>>({});

  // -- status -------------------------------------------------------------
  useEffect(() => {
    let live = true;
    setStatusState({ kind: "loading" });
    insightsApi
      .status()
      .then((status) => {
        if (!live) return;
        setStatusState(status.enabled ? { kind: "ready", status } : { kind: "disabled" });
        if (status.enabled) {
          // The tables behind the tiles; a role with no landing view yet still opens these.
          exploreApi
            .list()
            .then((list) => {
              if (live) setExplorers(list);
            })
            .catch(() => {
              if (live) setExplorers([]);
            });
        }
      })
      .catch(async (e) => {
        if (!live) return;
        if (errorStatus(e) === 404) {
          setStatusState({ kind: "disabled" });
          return;
        }
        const message = await readErrorDetail(e);
        if (live) setStatusState({ kind: "error", message });
      });
    return () => {
      live = false;
    };
  }, [reloadKey]);

  const status = statusState.kind === "ready" ? statusState.status : null;
  const settings = status?.settings ?? null;
  const currency = settings?.currency ?? null;

  // -- views this person may open ----------------------------------------
  const views = useMemo<InsightsViewKey[]>(() => {
    if (!status) return [];
    const allowedForRole = ROLE_VIEWS[role] ?? [];
    return status.views.filter((v) => allowedForRole.includes(v));
  }, [status, role]);

  const requestedView = searchParams.get("view") as InsightsViewKey | null;
  const activeView: InsightsViewKey | null =
    requestedView && views.includes(requestedView)
      ? requestedView
      : views.includes(defaultViewForRole(role))
        ? defaultViewForRole(role)
        : (views[0] ?? null);

  // -- filters from the URL -----------------------------------------------
  const rawPreset = searchParams.get("range") as RangePreset | null;
  const preset: RangePreset = rawPreset && RANGE_PRESETS.includes(rawPreset) ? rawPreset : "30d";
  const compare: CompareMode = searchParams.get("compare") === "none" ? "none" : "previous";
  const today = todayIn(settings?.timezone);
  const urlFrom = searchParams.get("from");
  const urlTo = searchParams.get("to");
  const customValid = preset === "custom" && isIsoDate(urlFrom) && isIsoDate(urlTo) && urlFrom <= urlTo;
  const range =
    preset === "custom"
      ? customValid
        ? { from: urlFrom as string, to: urlTo as string }
        : presetRange("30d", today)
      : presetRange(preset, today, settings?.fiscal_year_start_month ?? 1);
  const companyId = readCompanyId();

  const filters: InsightsFilters = useMemo(
    () => ({ from: range.from, to: range.to, compare, company_id: companyId || undefined }),
    [range.from, range.to, compare, companyId],
  );

  const updateParams = useCallback(
    (patch: Record<string, string | null>) => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          for (const [k, v] of Object.entries(patch)) {
            if (v === null || v === "") next.delete(k);
            else next.set(k, v);
          }
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );

  const onFilterChange = (next: Partial<FilterValue>) => {
    const patch: Record<string, string | null> = {};
    if (next.preset) {
      patch.range = next.preset === "30d" ? null : next.preset;
      if (next.preset === "custom") {
        patch.from = next.from ?? range.from;
        patch.to = next.to ?? range.to;
      } else {
        patch.from = null;
        patch.to = null;
      }
    }
    if (next.compare) patch.compare = next.compare === "previous" ? null : next.compare;
    updateParams(patch);
  };

  // -- view data ------------------------------------------------------------
  useEffect(() => {
    if (!activeView) {
      setViewState({ kind: "idle" });
      return;
    }
    let live = true;
    setViewState({ kind: "loading" });
    insightsApi
      .view(activeView, filters)
      .then((data) => {
        if (live) setViewState({ kind: "ready", data });
      })
      .catch(async (e) => {
        if (!live) return;
        const code = errorStatus(e);
        if (code === 404) return setViewState({ kind: "disabled" });
        if (code === 403) return setViewState({ kind: "forbidden" });
        const message = await readErrorDetail(e);
        if (live) setViewState({ kind: "error", message });
      });
    return () => {
      live = false;
    };
  }, [activeView, filters, reloadKey]);

  const selectView = (view: InsightsViewKey) => updateParams({ view });

  const onTabKey = (e: React.KeyboardEvent<HTMLButtonElement>, index: number) => {
    let next = -1;
    if (e.key === "ArrowRight") next = (index + 1) % views.length;
    else if (e.key === "ArrowLeft") next = (index - 1 + views.length) % views.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = views.length - 1;
    if (next < 0) return;
    e.preventDefault();
    const target = views[next];
    selectView(target);
    tabRefs.current[target]?.focus();
  };

  const retry = () => setReloadKey((k) => k + 1);

  // -- render ---------------------------------------------------------------
  const header = (
    <div className="flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-xl font-bold md:text-2xl">{t("insights.title")}</h1>
        <p className="text-sm text-muted-foreground">{t("insights.subtitle")}</p>
      </div>
      {statusState.kind === "ready" ? (
        <div className="flex flex-wrap items-center gap-2">
          {/* Per-tenant BI database access, for administrators while it is on and configured. */}
          {role === "admin" && statusState.status.bi_access ? (
            <Link
              to="/dashboard/insights/bi-access"
              className="insights-target inline-flex h-8 items-center rounded-md border px-3 text-sm font-medium hover:bg-muted"
              data-testid="insights-bi-access-link"
            >
              {t("insightsBi.link")}
            </Link>
          ) : null}
          <button
            type="button"
            onClick={retry}
            className="h-8 rounded-md border px-3 text-sm font-medium hover:bg-muted"
            disabled={viewState.kind === "loading"}
          >
            {t("insights.refresh")}
          </button>
        </div>
      ) : null}
    </div>
  );

  let content: React.ReactNode;
  if (statusState.kind === "loading") {
    content = <LoadingGrid label={t("insights.loading")} />;
  } else if (statusState.kind === "disabled") {
    content = <EmptyState title={t("insights.disabled.title")} hint={t("insights.disabled.body")} />;
  } else if (statusState.kind === "error") {
    content = (
      <EmptyState
        title={t("insights.error.load")}
        hint={statusState.message}
        action={<RetryButton onClick={retry} label={t("insights.error.retry")} />}
      />
    );
  } else if (!activeView) {
    content =
      explorers.length > 0 ? (
        <>
          <EmptyState compact title={t("insights.exploreOnly.title")} hint={t("insights.exploreOnly.body")} />
          <ExploreLinks explorers={explorers} />
        </>
      ) : (
        <EmptyState title={t("insights.forbidden.title")} hint={t("insights.forbidden.body")} />
      );
  } else {
    content = (
      <>
        {views.length > 1 ? (
          <div role="tablist" aria-label={t("insights.tabsLabel")} className="flex gap-1 border-b">
            {views.map((v, i) => {
              const selected = v === activeView;
              return (
                <button
                  key={v}
                  ref={(el) => {
                    tabRefs.current[v] = el;
                  }}
                  id={`insights-tab-${v}`}
                  type="button"
                  role="tab"
                  aria-selected={selected}
                  aria-controls="insights-panel"
                  tabIndex={selected ? 0 : -1}
                  onClick={() => selectView(v)}
                  onKeyDown={(e) => onTabKey(e, i)}
                  className={`-mb-px border-b-2 px-4 py-2 text-sm font-medium transition-colors ${
                    selected ? "border-primary text-foreground" : "border-transparent text-muted-foreground hover:text-foreground"
                  }`}
                >
                  {t(`insights.tabs.${v}`)}
                </button>
              );
            })}
          </div>
        ) : (
          <h2 className="text-base font-semibold" id={`insights-tab-${activeView}`}>
            {t(`insights.tabs.${activeView}`)}
          </h2>
        )}

        <FilterBar
          value={{ preset, from: range.from, to: range.to, compare }}
          onChange={onFilterChange}
          companyScoped={Boolean(companyId)}
          disabled={viewState.kind === "loading"}
        />

        <div
          id="insights-panel"
          role={views.length > 1 ? "tabpanel" : undefined}
          aria-labelledby={`insights-tab-${activeView}`}
          aria-busy={viewState.kind === "loading"}
          className="flex flex-col gap-4"
        >
          <ViewBody state={viewState} filters={filters} currency={currency} onRetry={retry} />
        </div>
        {explorers.length > 0 ? <ExploreLinks explorers={explorers} /> : null}
      </>
    );
  }

  return (
    <div className="flex flex-col gap-4" data-testid="insights-dashboard">
      <Helmet>
        <title>{`${t("insights.title")} — AgenticOrg`}</title>
      </Helmet>
      {header}
      {content}
    </div>
  );
}

/** The explorers this role may open: the rows behind every tile. */
function ExploreLinks({ explorers }: { explorers: ExplorerDescription[] }) {
  const { t } = useTranslation();
  return (
    <nav aria-labelledby="insights-explore-heading" className="flex flex-col gap-2" data-testid="insights-explore-links">
      <h2 id="insights-explore-heading" className="text-sm font-semibold">
        {t("insights.explore.heading")}
      </h2>
      <ul className="flex flex-wrap gap-2">
        {explorers.map((e) => (
          <li key={e.key}>
            <Link
              to={`/dashboard/insights/explore/${e.key}`}
              className="insights-target inline-flex h-8 items-center rounded-md border px-3 text-sm font-medium hover:bg-muted"
            >
              {t(`insightsExplore.explorers.${e.key}`, { defaultValue: e.title })}
            </Link>
          </li>
        ))}
      </ul>
    </nav>
  );
}

function RetryButton({ onClick, label }: { onClick: () => void; label: string }) {
  return (
    <button type="button" onClick={onClick} className="h-8 rounded-md border px-3 text-sm font-medium hover:bg-muted">
      {label}
    </button>
  );
}

function LoadingGrid({ label }: { label: string }) {
  return (
    <div role="status" aria-label={label} className="flex flex-col gap-4" data-testid="insights-loading">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {Array.from({ length: 4 }, (_, i) => (
          <TileSkeleton key={i} />
        ))}
      </div>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <ChartSkeleton />
        <ChartSkeleton />
      </div>
    </div>
  );
}

function ViewBody({
  state,
  filters,
  currency,
  onRetry,
}: {
  state: ViewState;
  filters: InsightsFilters;
  currency: string | null;
  onRetry: () => void;
}) {
  const { t } = useTranslation();
  if (state.kind === "idle" || state.kind === "loading") return <LoadingGrid label={t("insights.loading")} />;
  if (state.kind === "disabled") return <EmptyState title={t("insights.disabled.title")} hint={t("insights.disabled.body")} />;
  if (state.kind === "forbidden") return <EmptyState title={t("insights.forbidden.title")} hint={t("insights.forbidden.body")} />;
  if (state.kind === "error") {
    return (
      <EmptyState
        title={t("insights.error.load")}
        hint={state.message}
        action={<RetryButton onClick={onRetry} label={t("insights.error.retry")} />}
      />
    );
  }

  const { data } = state;
  const computedAt = [...data.tiles, ...data.charts]
    .map((m) => m.computed_at)
    .filter((c): c is string => typeof c === "string" && c !== "")
    .sort()
    .pop();
  const updated = computedAt ? new Date(computedAt) : null;

  return (
    <>
      {data.tiles.length > 0 ? (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4" data-testid="insights-tiles">
          {data.tiles.map((metric) => {
            const presentation = METRIC_PRESENTATION[metric.key];
            return (
              <DecisionTile
                key={metric.key}
                metric={metric}
                currency={currency}
                action={presentation ? { to: presentation.action.to, label: t(presentation.action.labelKey) } : null}
              />
            );
          })}
        </div>
      ) : (
        <EmptyState />
      )}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <div className="grid grid-cols-1 gap-4 lg:col-span-2 lg:grid-cols-2" data-testid="insights-charts">
          {data.charts.map((metric) => (
            <ChartCard key={metric.key} metric={metric} filters={filters} currency={currency} />
          ))}
        </div>
        <NeedsYouList items={data.needs_you} />
      </div>

      {updated && !Number.isNaN(updated.getTime()) ? (
        <p className="text-xs text-muted-foreground" data-testid="insights-updated">
          {t("insights.updated", { time: updated.toLocaleString() })}
        </p>
      ) : null}
    </>
  );
}
