// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
/**
 * Insights explorers: the Level 2 tables behind every tile.
 *
 * Reads GET /insights/explorers (which tables this role may open), then
 * GET /insights/explore/{key} with the period, the table's filters
 * (`filter[column]`), sort and page, all held in the URL so a table can be
 * shared or opened from a tile. The backend decides access and which columns
 * filter or sort; this page never shows a number the API did not send.
 */
import { useCallback, useEffect, useId, useMemo, useState } from "react";
import { Helmet } from "react-helmet-async";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";
import Breadcrumbs from "@/components/insights/Breadcrumbs";
import SavedViews from "@/components/insights/SavedViews";
import SearchPalette from "@/components/insights/SearchPalette";
import { useTranslation } from "react-i18next";
import api from "@/lib/api";
import DataQualityBadge from "@/components/insights/DataQualityBadge";
import EmptyState from "@/components/insights/EmptyState";
import ExportMenu from "@/components/insights/ExportMenu";
import FilterBar, { type FilterValue } from "@/components/insights/FilterBar";
import {
  errorStatus,
  insightsApi,
  readErrorDetail,
  type ReportingSettings,
} from "@/lib/insights/api";
import {
  isIsoDate,
  presetRange,
  RANGE_PRESETS,
  todayIn,
  type RangePreset,
} from "@/lib/insights/dates";
import {
  explorerExportFilename,
  explorerParams,
  exploreApi,
  filtersFromSearch,
  type ExplorerColumn,
  type ExplorerDescription,
  type ExplorerQuery,
  type ExplorerResponse,
  type ExplorerRow,
  type SortOrder,
} from "@/lib/insights/explore";
import {
  formatCell,
  isNumericColumn,
  linkColumn,
  primaryLink,
} from "@/lib/insights/explorerCells";

type ListState =
  | { kind: "loading" }
  | {
      kind: "ready";
      explorers: ExplorerDescription[];
      settings: ReportingSettings | null;
    }
  | { kind: "disabled" }
  | { kind: "error"; message: string | null };

type PageState =
  | { kind: "loading" }
  | { kind: "ready"; data: ExplorerResponse }
  | { kind: "forbidden" }
  | { kind: "invalid"; message: string | null }
  | { kind: "error"; message: string | null };

const PAGE_SIZE = 50;

function readCompanyId(): string {
  try {
    return localStorage.getItem("company_id") || "";
  } catch {
    return "";
  }
}

export default function InsightsExplorer() {
  const { t } = useTranslation();
  const { explorer: routeKey } = useParams();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [list, setList] = useState<ListState>({ kind: "loading" });
  const [page, setPage] = useState<PageState>({ kind: "loading" });
  const [reloadKey, setReloadKey] = useState(0);

  // -- which explorers this person may open ---------------------------------
  useEffect(() => {
    let live = true;
    Promise.all([exploreApi.list(), insightsApi.status().catch(() => null)])
      .then(([explorers, status]) => {
        if (live)
          setList({
            kind: "ready",
            explorers,
            settings: status?.settings ?? null,
          });
      })
      .catch(async (e) => {
        if (!live) return;
        if (errorStatus(e) === 404) return setList({ kind: "disabled" });
        const message = await readErrorDetail(e);
        if (live) setList({ kind: "error", message });
      });
    return () => {
      live = false;
    };
  }, []);

  const explorers = useMemo(
    () => (list.kind === "ready" ? list.explorers : []),
    [list],
  );
  const settings = list.kind === "ready" ? list.settings : null;
  const active = explorers.find((e) => e.key === routeKey) ?? null;

  // An unknown or forbidden key in the URL lands on the first explorer this role may open.
  useEffect(() => {
    if (list.kind === "ready" && !active && explorers.length > 0) {
      navigate(`/dashboard/insights/explore/${explorers[0].key}`, {
        replace: true,
      });
    }
  }, [list.kind, active, explorers, navigate]);

  // -- filters from the URL ---------------------------------------------------
  const rawPreset = searchParams.get("range") as RangePreset | null;
  const preset: RangePreset =
    rawPreset && RANGE_PRESETS.includes(rawPreset) ? rawPreset : "30d";
  const today = todayIn(settings?.timezone);
  const urlFrom = searchParams.get("from");
  const urlTo = searchParams.get("to");
  const customValid =
    preset === "custom" &&
    isIsoDate(urlFrom) &&
    isIsoDate(urlTo) &&
    urlFrom <= urlTo;
  const range =
    preset === "custom"
      ? customValid
        ? { from: urlFrom as string, to: urlTo as string }
        : presetRange("30d", today)
      : presetRange(preset, today, settings?.fiscal_year_start_month ?? 1);
  const tableFilters = useMemo(
    () => filtersFromSearch(searchParams),
    [searchParams],
  );
  const sort = searchParams.get("sort") || active?.default_sort || "";
  const order: SortOrder =
    searchParams.get("order") === "asc"
      ? "asc"
      : searchParams.get("order") === "desc"
        ? "desc"
        : (active?.default_order ?? "desc");
  const pageNumber = Math.max(
    1,
    Number.parseInt(searchParams.get("page") || "1", 10) || 1,
  );
  const companyId = readCompanyId();
  const filterKey = JSON.stringify(tableFilters);

  const query: ExplorerQuery = useMemo(
    () => ({
      from: active?.uses_period === false ? undefined : range.from,
      to: active?.uses_period === false ? undefined : range.to,
      company_id: companyId || undefined,
      filters: JSON.parse(filterKey) as Record<string, string>,
      sort,
      order,
      page: pageNumber,
      page_size: PAGE_SIZE,
    }),
    [
      active?.uses_period,
      range.from,
      range.to,
      companyId,
      filterKey,
      sort,
      order,
      pageNumber,
    ],
  );

  const updateParams = useCallback(
    (patch: Record<string, string | null>, resetPage = true) => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          for (const [k, v] of Object.entries(patch)) {
            if (v === null || v === "") next.delete(k);
            else next.set(k, v);
          }
          if (resetPage && !("page" in patch)) next.delete("page");
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );

  // -- the table ----------------------------------------------------------------
  useEffect(() => {
    if (!active) return;
    let live = true;
    setPage({ kind: "loading" });
    exploreApi
      .page(active.key, query)
      .then((data) => {
        if (live) setPage({ kind: "ready", data });
      })
      .catch(async (e) => {
        if (!live) return;
        const code = errorStatus(e);
        const message = await readErrorDetail(e);
        if (!live) return;
        if (code === 403) return setPage({ kind: "forbidden" });
        if (code === 422) return setPage({ kind: "invalid", message });
        setPage({ kind: "error", message });
      });
    return () => {
      live = false;
    };
  }, [active, query, reloadKey]);

  const onRangeChange = (next: Partial<FilterValue>) => {
    const patch: Record<string, string | null> = {};
    if (next.preset) {
      patch.range = next.preset === "30d" ? null : next.preset;
      patch.from = next.preset === "custom" ? (next.from ?? range.from) : null;
      patch.to = next.preset === "custom" ? (next.to ?? range.to) : null;
    }
    updateParams(patch);
  };

  const setFilter = (column: string, value: string | null) =>
    updateParams({ [`filter[${column}]`]: value });
  const clearFilters = () => {
    const patch: Record<string, null> = {};
    for (const column of Object.keys(tableFilters))
      patch[`filter[${column}]`] = null;
    updateParams(patch);
  };
  const onSort = (column: ExplorerColumn) => {
    if (!column.sortable) return;
    const nextOrder: SortOrder =
      sort === column.key && order === "desc" ? "asc" : "desc";
    updateParams({ sort: column.key, order: nextOrder });
  };
  const goToPage = (n: number) =>
    updateParams({ page: n > 1 ? String(n) : null }, false);
  const reload = () => setReloadKey((k) => k + 1);

  // -- render ---------------------------------------------------------------------
  let body: React.ReactNode;
  if (list.kind === "loading") {
    body = (
      <p role="status" className="text-sm text-muted-foreground">
        {t("insightsExplore.loading")}
      </p>
    );
  } else if (list.kind === "disabled") {
    body = (
      <EmptyState
        title={t("insights.disabled.title")}
        hint={t("insights.disabled.body")}
      />
    );
  } else if (list.kind === "error") {
    body = <EmptyState title={t("insights.error.load")} hint={list.message} />;
  } else if (explorers.length === 0) {
    body = (
      <EmptyState
        title={t("insightsExplore.none.title")}
        hint={t("insightsExplore.none.body")}
      />
    );
  } else if (!active) {
    body = null;
  } else {
    body = (
      <>
        <nav
          aria-label={t("insightsExplore.tabsLabel")}
          className="flex flex-wrap gap-1 border-b"
        >
          {explorers.map((e) => {
            const selected = e.key === active.key;
            return (
              <Link
                key={e.key}
                to={`/dashboard/insights/explore/${e.key}`}
                aria-current={selected ? "page" : undefined}
                className={`insights-target -mb-px border-b-2 px-3 py-2 text-sm font-medium ${
                  selected
                    ? "border-primary text-foreground"
                    : "border-transparent text-muted-foreground hover:text-foreground"
                }`}
              >
                {t(`insightsExplore.explorers.${e.key}`, {
                  defaultValue: e.title,
                })}
              </Link>
            );
          })}
        </nav>

        <p className="text-sm text-muted-foreground">{active.definition}</p>

        <div className="flex flex-wrap items-end justify-between gap-3">
          {active.uses_period ? (
            <FilterBar
              value={{
                preset,
                from: range.from,
                to: range.to,
                compare: "previous",
              }}
              onChange={onRangeChange}
              companyScoped={Boolean(companyId)}
              disabled={page.kind === "loading"}
              showCompare={false}
            />
          ) : (
            <p className="text-xs text-muted-foreground">
              {t("insightsExplore.snapshot")}
            </p>
          )}
          <SavedViews viewKey={`explore:${active.key}`} />
          <ExportMenu
            metricKey={active.key}
            title={active.title}
            filters={query}
            download={async (format, includePii) => ({
              blob: await exploreApi.exportBlob(active.key, format, query, includePii),
              filename: explorerExportFilename(active.key, format, query),
            })}
            background={(format) => {
              const { page: _page, page_size: _size, filters: tableFilters, compare: _compare, from, to, company_id, ...rest } =
                query;
              return {
                kind: "explorer",
                format,
                params: {
                  key: active.key,
                  from,
                  to,
                  company_id,
                  query: explorerParams({ ...rest, filters: tableFilters }),
                },
              };
            }}
          />
        </div>

        <ColumnFilters
          columns={active.columns}
          values={tableFilters}
          onChange={setFilter}
          onClear={clearFilters}
          disabled={page.kind === "loading"}
        />

        <TableBody
          state={page}
          sort={sort}
          order={order}
          onSort={onSort}
          onPage={goToPage}
          onRetry={reload}
          onChanged={reload}
          timezone={settings?.timezone ?? null}
          currency={settings?.currency ?? null}
        />
      </>
    );
  }

  return (
    <div className="flex flex-col gap-4" data-testid="insights-explorer">
      <Helmet>
        <title>{`${t("insightsExplore.title")} — AgenticOrg`}</title>
      </Helmet>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Breadcrumbs
          items={
            active
              ? [{ label: t("insightsExplore.title"), to: "/dashboard/insights/explore" }, { label: active.title }]
              : [{ label: t("insightsExplore.title") }]
          }
        />
        <SearchPalette />
      </div>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold md:text-2xl">
            {t("insightsExplore.title")}
          </h1>
          <p className="text-sm text-muted-foreground">
            {t("insightsExplore.subtitle")}
          </p>
        </div>
        <Link
          to="/dashboard/insights"
          className="insights-target inline-flex h-8 items-center rounded-md border px-3 text-sm font-medium hover:bg-muted"
        >
          {t("insightsExplore.back")}
        </Link>
      </div>
      {body}
    </div>
  );
}

// ---------------------------------------------------------------- filters

function ColumnFilters({
  columns,
  values,
  onChange,
  onClear,
  disabled,
}: {
  columns: ExplorerColumn[];
  values: Record<string, string>;
  onChange: (column: string, value: string | null) => void;
  onClear: () => void;
  disabled: boolean;
}) {
  const { t } = useTranslation();
  const id = useId();
  // Free-text and id filters (agent ids from a drill) show as chips; the rest as controls.
  const controls = columns.filter(
    (c) =>
      c.filter &&
      c.type !== "id" &&
      !(c.filter === "enum" && !c.options?.length),
  );
  const chips = Object.entries(values).filter(
    ([key]) => !controls.some((c) => c.key === key),
  );
  const active = Object.keys(values).length;
  return (
    <div
      className="flex flex-col gap-2"
      aria-label={t("insightsExplore.filters.label")}
      role="group"
    >
      <div className="flex flex-wrap items-end gap-3">
        {controls.map((column) => (
          <FilterControl
            key={column.key}
            id={`${id}-${column.key}`}
            column={column}
            value={values[column.key] ?? ""}
            onChange={(v) => onChange(column.key, v)}
            disabled={disabled}
          />
        ))}
        {active > 0 ? (
          <button
            type="button"
            onClick={onClear}
            className="insights-target h-8 rounded-md border px-3 text-sm font-medium hover:bg-muted"
          >
            {t("insightsExplore.filters.clear", { count: active })}
          </button>
        ) : null}
      </div>
      {chips.length > 0 ? (
        <ul
          className="flex flex-wrap gap-2"
          aria-label={t("insightsExplore.filters.applied")}
        >
          {chips.map(([key, value]) => {
            const column = columns.find((c) => c.key === key);
            const label = column?.title ?? key;
            return (
              <li
                key={key}
                className="inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs"
              >
                <span>
                  {label}:{" "}
                  <span className="font-mono">
                    {value.length > 20 ? `${value.slice(0, 8)}…` : value}
                  </span>
                </span>
                <button
                  type="button"
                  className="insights-target rounded px-1 hover:bg-muted"
                  aria-label={t("insightsExplore.filters.remove", { label })}
                  onClick={() => onChange(key, null)}
                >
                  ×
                </button>
              </li>
            );
          })}
        </ul>
      ) : null}
    </div>
  );
}

function FilterControl({
  id,
  column,
  value,
  onChange,
  disabled,
}: {
  id: string;
  column: ExplorerColumn;
  value: string;
  onChange: (value: string | null) => void;
  disabled: boolean;
}) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  const commit = () => onChange(draft.trim() === "" ? null : draft.trim());
  const labelClass =
    "flex flex-col gap-1 text-xs font-medium text-muted-foreground";
  const inputClass =
    "insights-filter h-8 rounded-md border bg-background px-2 text-sm text-foreground";

  if (column.filter === "enum" || column.filter === "bool") {
    const options =
      column.filter === "bool" ? ["true", "false"] : (column.options ?? []);
    return (
      <label className={labelClass} htmlFor={id}>
        {column.title}
        <select
          id={id}
          value={value}
          disabled={disabled}
          onChange={(e) => onChange(e.target.value || null)}
          className={inputClass}
        >
          <option value="">{t("insightsExplore.filters.any")}</option>
          {options.map((o) => (
            <option key={o} value={o}>
              {column.filter === "bool"
                ? t(o === "true" ? "insightsExplore.yes" : "insightsExplore.no")
                : o.replace(/_/g, " ")}
            </option>
          ))}
        </select>
      </label>
    );
  }
  return (
    <label className={labelClass} htmlFor={id}>
      {column.filter === "min"
        ? t("insightsExplore.filters.atLeast", { title: column.title })
        : column.title}
      <input
        id={id}
        type={column.filter === "min" ? "number" : "search"}
        min={column.filter === "min" ? 0 : undefined}
        inputMode={column.filter === "min" ? "decimal" : undefined}
        value={draft}
        disabled={disabled}
        maxLength={100}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") commit();
        }}
        className={`${inputClass} w-36`}
      />
    </label>
  );
}

// ---------------------------------------------------------------- table

function TableBody({
  state,
  sort,
  order,
  onSort,
  onPage,
  onRetry,
  onChanged,
  timezone,
  currency,
}: {
  state: PageState;
  sort: string;
  order: SortOrder;
  onSort: (column: ExplorerColumn) => void;
  onPage: (page: number) => void;
  onRetry: () => void;
  onChanged: () => void;
  timezone: string | null;
  currency: string | null;
}) {
  const { t } = useTranslation();
  if (state.kind === "loading") {
    return (
      <p role="status" className="text-sm text-muted-foreground">
        {t("insightsExplore.loading")}
      </p>
    );
  }
  if (state.kind === "forbidden") {
    return (
      <EmptyState
        title={t("insightsExplore.forbidden.title")}
        hint={t("insightsExplore.forbidden.body")}
      />
    );
  }
  if (state.kind === "invalid") {
    return (
      <EmptyState title={t("insightsExplore.invalid")} hint={state.message} />
    );
  }
  if (state.kind === "error") {
    return (
      <EmptyState
        title={t("insights.error.load")}
        hint={state.message}
        action={
          <button
            type="button"
            onClick={onRetry}
            className="h-8 rounded-md border px-3 text-sm font-medium hover:bg-muted"
          >
            {t("insights.error.retry")}
          </button>
        }
      />
    );
  }
  const { data } = state;
  const shownCurrency =
    (data.summary?.currency as string | undefined) ?? currency;
  const first = data.total === 0 ? 0 : (data.page - 1) * data.page_size + 1;
  const last = Math.min(data.total, data.page * data.page_size);
  const pages = Math.max(1, Math.ceil(data.total / data.page_size));
  // Ids are for drills and exports; the table shows the names they stand for.
  const shown = data.columns.filter((c) => c.type !== "id");
  const linkKey = linkColumn(shown);
  const hasActions = data.rows.some(
    (r) => r.actions && Object.keys(r.actions).length > 0,
  );
  return (
    <section
      className="flex flex-col gap-2"
      aria-labelledby="insights-explorer-results"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="insights-explorer-results" className="text-sm font-semibold">
          {data.total === 0
            ? t("insightsExplore.noRows")
            : t("insightsExplore.results", { first, last, total: data.total })}
        </h2>
        <DataQualityBadge quality={data.data_quality} />
      </div>
      {data.hint ? (
        <p
          className="text-xs text-muted-foreground"
          data-testid="insights-explorer-hint"
        >
          {data.hint}
        </p>
      ) : null}
      {data.rows.length === 0 ? (
        <EmptyState
          compact
          title={t("insightsExplore.empty")}
          hint={t("insightsExplore.emptyHint")}
        />
      ) : (
        <div className="overflow-x-auto rounded-md border">
          <table
            className="w-full text-sm"
            data-testid="insights-explorer-table"
          >
            <caption className="sr-only">{data.title}</caption>
            <thead className="bg-muted/50 text-left text-xs text-muted-foreground">
              <tr>
                {hasActions ? (
                  <th scope="col" className="px-3 py-2 font-medium">
                    {t("insightsExplore.actions")}
                  </th>
                ) : null}
                {shown.map((column) => {
                  const sorted = sort === column.key;
                  const ariaSort = sorted
                    ? order === "asc"
                      ? "ascending"
                      : "descending"
                    : "none";
                  return (
                    <th
                      key={column.key}
                      scope="col"
                      aria-sort={column.sortable ? ariaSort : undefined}
                      className={`whitespace-nowrap px-3 py-2 font-medium ${isNumericColumn(column) ? "text-right" : ""}`}
                      title={column.description}
                    >
                      {column.sortable ? (
                        <button
                          type="button"
                          onClick={() => onSort(column)}
                          className="insights-target inline-flex items-center gap-1 hover:text-foreground"
                        >
                          {column.title}
                          <span aria-hidden="true">
                            {sorted ? (order === "asc" ? "▲" : "▼") : ""}
                          </span>
                        </button>
                      ) : (
                        column.title
                      )}
                    </th>
                  );
                })}
              </tr>
            </thead>
            <tbody>
              {data.rows.map((row, i) => (
                <tr key={i} className="border-t">
                  {hasActions ? (
                    <td className="px-3 py-2 align-top">
                      <RowActions row={row} onChanged={onChanged} />
                    </td>
                  ) : null}
                  {shown.map((column) => {
                    const text = formatCell(column, row[column.key], {
                      currency: shownCurrency,
                      timezone,
                      yes: t("insightsExplore.yes"),
                      no: t("insightsExplore.no"),
                    });
                    const link =
                      column.key === linkKey ? primaryLink(row) : null;
                    return (
                      <td
                        key={column.key}
                        className={`px-3 py-2 align-top ${isNumericColumn(column) ? "text-right tabular-nums" : ""} ${
                          column.type === "id" ? "font-mono text-xs" : ""
                        } ${column.key === "rule" || column.key === "promotion_reason" ? "min-w-[16rem]" : "whitespace-nowrap"}`}
                      >
                        {link ? (
                          <Link
                            to={link}
                            className="text-primary underline-offset-2 hover:underline"
                          >
                            {text}
                          </Link>
                        ) : (
                          text
                        )}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {data.total > data.page_size ? (
        <nav
          className="flex items-center justify-end gap-2 text-sm"
          aria-label={t("insightsExplore.pagination")}
        >
          <button
            type="button"
            onClick={() => onPage(data.page - 1)}
            disabled={data.page <= 1}
            className="insights-target h-8 rounded-md border px-3 disabled:opacity-50"
          >
            {t("insightsExplore.previous")}
          </button>
          <span className="tabular-nums text-muted-foreground">
            {t("insightsExplore.pageOf", { page: data.page, pages })}
          </span>
          <button
            type="button"
            onClick={() => onPage(data.page + 1)}
            disabled={data.page >= pages}
            className="insights-target h-8 rounded-md border px-3 disabled:opacity-50"
          >
            {t("insightsExplore.next")}
          </button>
        </nav>
      ) : null}
    </section>
  );
}

/**
 * Row actions. Revoking a learned rule calls the agent's existing endpoint
 * (owner or administrator; the server decides) after checking the rule at that
 * position is still the one shown, so a list that changed meanwhile is never
 * edited blindly.
 */
function RowActions({
  row,
  onChanged,
}: {
  row: ExplorerRow;
  onChanged: () => void;
}) {
  const { t } = useTranslation();
  const revoke = row.actions?.revoke as
    { agent_id?: string; index?: number } | undefined;
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  if (
    !revoke ||
    typeof revoke.agent_id !== "string" ||
    typeof revoke.index !== "number"
  )
    return null;
  const { agent_id: agentId, index } = revoke;

  const run = async () => {
    setBusy(true);
    setMessage(null);
    try {
      const { data } = await api.get(
        `/agents/${encodeURIComponent(agentId)}/amendments`,
      );
      const current = Array.isArray(
        (data as { amendments?: unknown })?.amendments,
      )
        ? ((data as { amendments: unknown[] }).amendments.map(
            String,
          ) as string[])
        : [];
      const shown = String(row.rule ?? "");
      if (!current[index] || !current[index].startsWith(shown.slice(0, 200))) {
        setMessage(t("insightsExplore.revoke.changed"));
        return;
      }
      await api.delete(
        `/agents/${encodeURIComponent(agentId)}/amendments/${index}`,
      );
      setConfirming(false);
      onChanged();
    } catch (e) {
      const code = errorStatus(e);
      setMessage(
        code === 403
          ? t("insightsExplore.revoke.forbidden")
          : (await readErrorDetail(e)) || t("insightsExplore.revoke.failed"),
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col items-start gap-1">
      {confirming ? (
        <div className="flex gap-1">
          <button
            type="button"
            onClick={() => void run()}
            disabled={busy}
            className="insights-target h-8 rounded-md bg-[hsl(var(--state-act))] px-3 text-xs font-medium text-white disabled:opacity-60"
          >
            {t("insightsExplore.revoke.confirm")}
          </button>
          <button
            type="button"
            onClick={() => setConfirming(false)}
            disabled={busy}
            className="insights-target h-8 rounded-md border px-3 text-xs"
          >
            {t("insightsExplore.revoke.cancel")}
          </button>
        </div>
      ) : (
        <button
          type="button"
          onClick={() => setConfirming(true)}
          className="insights-target h-8 rounded-md border px-3 text-xs font-medium hover:bg-muted"
          aria-label={t("insightsExplore.revoke.label", { index: index + 1 })}
        >
          {t("insightsExplore.revoke.button")}
        </button>
      )}
      {message ? (
        <p
          role="alert"
          className="max-w-xs text-xs text-[hsl(var(--state-act))]"
        >
          {message}
        </p>
      ) : null}
    </div>
  );
}
