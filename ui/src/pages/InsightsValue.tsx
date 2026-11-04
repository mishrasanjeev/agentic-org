// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
/**
 * Money saved: the estimate for the period and the baselines and hourly rates
 * behind it (GET /insights/value).
 *
 * Savings stay hidden until a tenant administrator has confirmed every
 * baseline and rate the period uses; this page shows what is unconfirmed and
 * lets an administrator confirm, edit or add them (the server checks that the
 * caller is an active human administrator and audits every change). The CFO
 * reads the same page without the controls.
 */
import { useCallback, useEffect, useId, useMemo, useState } from "react";
import { Helmet } from "react-helmet-async";
import { Link, useSearchParams } from "react-router";
import { useTranslation } from "react-i18next";
import EmptyState from "@/components/insights/EmptyState";
import FilterBar, { type FilterValue } from "@/components/insights/FilterBar";
import ChartCard from "@/components/insights/ChartCard";
import { errorStatus, insightsApi, readErrorDetail, type MetricResponse, type ReportingSettings } from "@/lib/insights/api";
import { isIsoDate, presetRange, RANGE_PRESETS, todayIn, type RangePreset } from "@/lib/insights/dates";
import { formatMoney, formatPercent, MISSING } from "@/lib/insights/format";
import { valueApi, type ValueBaseline, type ValueRate, type ValueResponse } from "@/lib/insights/value";

type State =
  | { kind: "loading" }
  | { kind: "ready"; data: ValueResponse }
  | { kind: "forbidden" }
  | { kind: "disabled" }
  | { kind: "error"; message: string | null };

export default function InsightsValue() {
  const { t } = useTranslation();
  const [searchParams, setSearchParams] = useSearchParams();
  const [settings, setSettings] = useState<ReportingSettings | null>(null);
  const [state, setState] = useState<State>({ kind: "loading" });
  const [reloadKey, setReloadKey] = useState(0);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ kind: "ok" | "error"; text: string } | null>(null);

  useEffect(() => {
    insightsApi
      .status()
      .then((s) => setSettings(s.settings ?? null))
      .catch(() => setSettings(null));
  }, []);

  const rawPreset = searchParams.get("range") as RangePreset | null;
  const preset: RangePreset = rawPreset && RANGE_PRESETS.includes(rawPreset) ? rawPreset : "30d";
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

  useEffect(() => {
    let live = true;
    setState({ kind: "loading" });
    valueApi
      .get(range)
      .then((data) => {
        if (live) setState({ kind: "ready", data });
      })
      .catch(async (e) => {
        if (!live) return;
        const code = errorStatus(e);
        if (code === 403) return setState({ kind: "forbidden" });
        if (code === 404) return setState({ kind: "disabled" });
        const text = await readErrorDetail(e);
        if (live) setState({ kind: "error", message: text });
      });
    return () => {
      live = false;
    };
  }, [range.from, range.to, reloadKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const onRange = (next: Partial<FilterValue>) => {
    setSearchParams(
      (prev) => {
        const out = new URLSearchParams(prev);
        if (next.preset) {
          if (next.preset === "30d") out.delete("range");
          else out.set("range", next.preset);
          if (next.preset === "custom") {
            out.set("from", next.from ?? range.from);
            out.set("to", next.to ?? range.to);
          } else {
            out.delete("from");
            out.delete("to");
          }
        }
        return out;
      },
      { replace: true },
    );
  };

  const run = useCallback(
    async (action: () => Promise<unknown>, ok: string) => {
      setBusy(true);
      setMessage(null);
      try {
        await action();
        setMessage({ kind: "ok", text: ok });
        setReloadKey((k) => k + 1);
      } catch (e) {
        const code = errorStatus(e);
        setMessage({
          kind: "error",
          text: code === 403 ? t("insightsValue.adminOnly") : (await readErrorDetail(e)) || t("insightsValue.saveFailed"),
        });
      } finally {
        setBusy(false);
      }
    },
    [t],
  );

  let body: React.ReactNode;
  if (state.kind === "loading") {
    body = (
      <p role="status" className="text-sm text-muted-foreground">
        {t("insightsValue.loading")}
      </p>
    );
  } else if (state.kind === "forbidden") {
    body = <EmptyState title={t("insightsValue.forbidden.title")} hint={t("insightsValue.forbidden.body")} />;
  } else if (state.kind === "disabled") {
    body = <EmptyState title={t("insights.disabled.title")} hint={t("insights.disabled.body")} />;
  } else if (state.kind === "error") {
    body = <EmptyState title={t("insights.error.load")} hint={state.message} />;
  } else {
    body = <ValueBody data={state.data} busy={busy} run={run} />;
  }

  return (
    <div className="flex flex-col gap-4" data-testid="insights-value">
      <Helmet>
        <title>{`${t("insightsValue.title")} — AgenticOrg`}</title>
      </Helmet>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold md:text-2xl">{t("insightsValue.title")}</h1>
          <p className="text-sm text-muted-foreground">{t("insightsValue.subtitle")}</p>
        </div>
        <Link
          to="/dashboard/insights"
          className="insights-target inline-flex h-8 items-center rounded-md border px-3 text-sm font-medium hover:bg-muted"
        >
          {t("insightsExplore.back")}
        </Link>
      </div>
      <FilterBar
        value={{ preset, from: range.from, to: range.to, compare: "none" }}
        onChange={onRange}
        disabled={state.kind === "loading"}
        showCompare={false}
      />
      {message ? (
        <p
          role={message.kind === "error" ? "alert" : "status"}
          className={`text-sm ${message.kind === "error" ? "text-[hsl(var(--state-act))]" : "text-muted-foreground"}`}
        >
          {message.text}
        </p>
      ) : null}
      {body}
    </div>
  );
}

function ValueBody({
  data,
  busy,
  run,
}: {
  data: ValueResponse;
  busy: boolean;
  run: (action: () => Promise<unknown>, ok: string) => Promise<void>;
}) {
  const { t } = useTranslation();
  const { summary, currency } = data;
  const unconfirmedCount = summary.unconfirmed.baseline_ids.length + summary.unconfirmed.rate_ids.length;
  const metric: MetricResponse = useMemo(
    () => ({
      key: "value.money_saved",
      title: t("insightsValue.waterfallTitle"),
      definition: "",
      unit: currency.toLowerCase(),
      currency,
      source: "value",
      data_quality: "estimated",
      computed_at: "",
      period: data.period,
      rows: (summary.waterfall ?? []) as unknown as MetricResponse["rows"],
    }),
    [currency, data.period, summary.waterfall, t],
  );
  const net = summary.waterfall?.find((r) => r.label === "net_saved")?.value ?? null;

  return (
    <>
      <section aria-labelledby="value-summary" className="rounded-lg border p-4" data-testid="value-summary">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 id="value-summary" className="text-sm font-semibold">
              {t("insightsValue.summaryTitle")}
            </h2>
            <p className="text-xs text-muted-foreground">
              {t("insightsValue.estimated")} · {t(`insightsValue.confidence.${summary.confidence}`, { defaultValue: summary.confidence })}
            </p>
          </div>
          {summary.confirmed ? null : data.can_edit && unconfirmedCount > 0 ? (
            <button
              type="button"
              disabled={busy}
              onClick={() =>
                void run(() => valueApi.confirm(summary.unconfirmed), t("insightsValue.confirmedAll"))
              }
              className="insights-target h-9 rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground disabled:opacity-60"
            >
              {t("insightsValue.confirmAll", { count: unconfirmedCount })}
            </button>
          ) : null}
        </div>
        {summary.confirmed ? (
          <div className="mt-3 grid grid-cols-1 gap-4 lg:grid-cols-3">
            <dl className="grid grid-cols-2 gap-3 text-sm lg:col-span-1 lg:grid-cols-1">
              <div>
                <dt className="text-muted-foreground">{t("insightsValue.net")}</dt>
                <dd className="text-2xl font-semibold tabular-nums" data-testid="value-net">
                  {formatMoney(net, currency)}
                </dd>
              </div>
              <div>
                <dt className="text-muted-foreground">{t("insightsValue.hours")}</dt>
                <dd className="tabular-nums">{summary.hours_saved ?? MISSING}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">{t("insightsValue.roi")}</dt>
                <dd className="tabular-nums">
                  {summary.roi_percent !== null
                    ? formatPercent(summary.roi_percent)
                    : t("insightsValue.roiBelowFloor", { floor: formatMoney(data.roi_floor, currency) })}
                </dd>
              </div>
            </dl>
            <div className="lg:col-span-2">
              {/* A card: the chart's text summary, its data table and its export (money saved for this period). */}
              <ChartCard
                metric={metric}
                kind="waterfall"
                currency={currency}
                filters={{ from: data.period.from, to: data.period.to, compare: "none" }}
              />
            </div>
          </div>
        ) : (
          <div className="mt-3 text-sm" data-testid="value-unconfirmed">
            <p className="font-medium">{t("insightsValue.unconfirmedTitle")}</p>
            <p className="text-muted-foreground">
              {t("insightsValue.unconfirmedBody", {
                baselines: summary.unconfirmed.baseline_ids.length,
                rates: summary.unconfirmed.rate_ids.length,
              })}
            </p>
            {!data.can_edit ? <p className="text-muted-foreground">{t("insightsValue.askAdmin")}</p> : null}
          </div>
        )}
        <p className="mt-3 text-xs text-muted-foreground">
          {t("insightsValue.counts", {
            completed: summary.completed_tasks,
            failed: summary.failed_tasks,
            reviews: summary.review_items,
          })}
        </p>
        {[...summary.hints, ...(summary.unpriced_task_types.length ? [t("insightsValue.unpriced", { types: summary.unpriced_task_types.join(", ") })] : [])].map(
          (hint) => (
            <p key={hint} className="mt-1 text-xs text-muted-foreground">
              {hint}
            </p>
          ),
        )}
      </section>

      <Baselines data={data} busy={busy} run={run} />
      <Rates data={data} busy={busy} run={run} />
      {data.can_edit ? <RoiFloor data={data} busy={busy} run={run} /> : null}
    </>
  );
}

function SourceCell({ source, confirmed }: { source: string; confirmed: boolean }) {
  const { t } = useTranslation();
  return (
    <span className="text-xs">
      {t(`insightsValue.source.${source}`, { defaultValue: source })}
      {" · "}
      <span className={confirmed ? "text-foreground" : "text-[hsl(var(--state-watch))]"}>
        {confirmed ? t("insightsValue.confirmed") : t("insightsValue.notConfirmed")}
      </span>
    </span>
  );
}

function Baselines({
  data,
  busy,
  run,
}: {
  data: ValueResponse;
  busy: boolean;
  run: (action: () => Promise<unknown>, ok: string) => Promise<void>;
}) {
  const { t } = useTranslation();
  const id = useId();
  const used = new Set(data.lines.map((l) => l.agent_type));
  const roles = data.rates.map((r) => r.role);
  const [newType, setNewType] = useState(data.summary.unpriced_task_types[0] ?? "");
  const [newMinutes, setNewMinutes] = useState("");
  const [newRole, setNewRole] = useState(roles[0] ?? "");
  const sorted = [...data.baselines].sort(
    (a, b) => Number(used.has(b.agent_type)) - Number(used.has(a.agent_type)) || a.agent_type.localeCompare(b.agent_type),
  );
  return (
    <section aria-labelledby={`${id}-b`} className="flex flex-col gap-2">
      <h2 id={`${id}-b`} className="text-sm font-semibold">
        {t("insightsValue.baselines")}
      </h2>
      <p className="text-xs text-muted-foreground">{t("insightsValue.baselinesHint")}</p>
      <div className="overflow-x-auto rounded-md border">
        <table className="w-full text-sm">
          <caption className="sr-only">{t("insightsValue.baselines")}</caption>
          <thead className="bg-muted/50 text-left text-xs text-muted-foreground">
            <tr>
              <th scope="col" className="px-3 py-2">{t("insightsValue.agentType")}</th>
              <th scope="col" className="px-3 py-2">{t("insightsValue.task")}</th>
              <th scope="col" className="px-3 py-2 text-right">{t("insightsValue.minutes")}</th>
              <th scope="col" className="px-3 py-2">{t("insightsValue.role")}</th>
              <th scope="col" className="px-3 py-2">{t("insightsValue.status")}</th>
              {data.can_edit ? <th scope="col" className="px-3 py-2">{t("insightsExplore.actions")}</th> : null}
            </tr>
          </thead>
          <tbody>
            {sorted.map((b) => (
              <BaselineRow key={b.id} b={b} roles={roles} canEdit={data.can_edit} busy={busy} run={run} used={used.has(b.agent_type)} />
            ))}
          </tbody>
        </table>
      </div>
      {data.can_edit && data.summary.unpriced_task_types.length > 0 ? (
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            const minutes = Number(newMinutes);
            if (!newType || !newRole || !(minutes > 0)) return;
            void run(
              () => valueApi.addBaseline({ agent_type: newType, baseline_minutes: minutes, role: newRole }),
              t("insightsValue.added"),
            );
          }}
        >
          <label className="flex flex-col gap-1 text-xs font-medium text-muted-foreground">
            {t("insightsValue.addFor")}
            <select value={newType} onChange={(e) => setNewType(e.target.value)} className="insights-filter h-8 rounded-md border bg-background px-2 text-sm">
              {data.summary.unpriced_task_types.map((type) => (
                <option key={type} value={type}>
                  {type}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs font-medium text-muted-foreground">
            {t("insightsValue.minutes")}
            <input
              type="number"
              min={1}
              max={1440}
              step="0.5"
              value={newMinutes}
              onChange={(e) => setNewMinutes(e.target.value)}
              className="insights-filter h-8 w-24 rounded-md border bg-background px-2 text-sm"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs font-medium text-muted-foreground">
            {t("insightsValue.role")}
            <select value={newRole} onChange={(e) => setNewRole(e.target.value)} className="insights-filter h-8 rounded-md border bg-background px-2 text-sm">
              {roles.map((r) => (
                <option key={r} value={r}>
                  {r.replace(/_/g, " ")}
                </option>
              ))}
            </select>
          </label>
          <button type="submit" disabled={busy} className="insights-target h-8 rounded-md border px-3 text-sm font-medium hover:bg-muted">
            {t("insightsValue.add")}
          </button>
        </form>
      ) : null}
    </section>
  );
}

function BaselineRow({
  b,
  roles,
  canEdit,
  busy,
  run,
  used,
}: {
  b: ValueBaseline;
  roles: string[];
  canEdit: boolean;
  busy: boolean;
  run: (action: () => Promise<unknown>, ok: string) => Promise<void>;
  used: boolean;
}) {
  const { t } = useTranslation();
  const [minutes, setMinutes] = useState(String(b.baseline_minutes));
  const [role, setRole] = useState(b.role);
  useEffect(() => {
    setMinutes(String(b.baseline_minutes));
    setRole(b.role);
  }, [b.baseline_minutes, b.role]);
  const dirty = Number(minutes) !== b.baseline_minutes || role !== b.role;
  const valid = Number(minutes) > 0 && Number(minutes) <= 1440;
  return (
    <tr className="border-t" data-testid={`baseline-${b.agent_type}`}>
      <td className="px-3 py-2">
        {b.agent_type.replace(/_/g, " ")}
        {used ? <span className="ml-1 text-xs text-muted-foreground">· {t("insightsValue.usedInPeriod")}</span> : null}
      </td>
      <td className="px-3 py-2">{b.task_type === "*" ? t("insightsValue.anyTask") : b.task_type}</td>
      <td className="px-3 py-2 text-right tabular-nums">
        {canEdit ? (
          <input
            aria-label={t("insightsValue.minutesFor", { type: b.agent_type })}
            type="number"
            min={1}
            max={1440}
            step="0.5"
            value={minutes}
            onChange={(e) => setMinutes(e.target.value)}
            className="insights-filter h-8 w-20 rounded-md border bg-background px-2 text-right text-sm"
          />
        ) : (
          b.baseline_minutes
        )}
      </td>
      <td className="px-3 py-2">
        {canEdit ? (
          <select
            aria-label={t("insightsValue.roleFor", { type: b.agent_type })}
            value={role}
            onChange={(e) => setRole(e.target.value)}
            className="insights-filter h-8 rounded-md border bg-background px-2 text-sm"
          >
            {[...new Set([b.role, ...roles])].map((r) => (
              <option key={r} value={r}>
                {r.replace(/_/g, " ")}
              </option>
            ))}
          </select>
        ) : (
          b.role.replace(/_/g, " ")
        )}
      </td>
      <td className="px-3 py-2">
        <SourceCell source={b.source} confirmed={b.confirmed} />
      </td>
      {canEdit ? (
        <td className="px-3 py-2">
          {dirty ? (
            <button
              type="button"
              disabled={busy || !valid}
              onClick={() =>
                void run(() => valueApi.editBaseline(b.id, { baseline_minutes: Number(minutes), role }), t("insightsValue.saved"))
              }
              className="insights-target h-8 rounded-md bg-primary px-3 text-xs font-medium text-primary-foreground disabled:opacity-60"
            >
              {t("insightsValue.save")}
            </button>
          ) : !b.confirmed ? (
            <button
              type="button"
              disabled={busy}
              onClick={() => void run(() => valueApi.confirm({ baseline_ids: [b.id], rate_ids: [] }), t("insightsValue.confirmedOne"))}
              className="insights-target h-8 rounded-md border px-3 text-xs font-medium hover:bg-muted"
              aria-label={t("insightsValue.confirmLabel", { name: b.agent_type })}
            >
              {t("insightsValue.confirm")}
            </button>
          ) : null}
        </td>
      ) : null}
    </tr>
  );
}

function Rates({
  data,
  busy,
  run,
}: {
  data: ValueResponse;
  busy: boolean;
  run: (action: () => Promise<unknown>, ok: string) => Promise<void>;
}) {
  const { t } = useTranslation();
  const id = useId();
  return (
    <section aria-labelledby={`${id}-r`} className="flex flex-col gap-2">
      <h2 id={`${id}-r`} className="text-sm font-semibold">
        {t("insightsValue.rates")}
      </h2>
      <p className="text-xs text-muted-foreground">{t("insightsValue.ratesHint")}</p>
      <div className="overflow-x-auto rounded-md border">
        <table className="w-full text-sm">
          <caption className="sr-only">{t("insightsValue.rates")}</caption>
          <thead className="bg-muted/50 text-left text-xs text-muted-foreground">
            <tr>
              <th scope="col" className="px-3 py-2">{t("insightsValue.role")}</th>
              <th scope="col" className="px-3 py-2 text-right">{t("insightsValue.hourly")}</th>
              <th scope="col" className="px-3 py-2">{t("insightsValue.status")}</th>
              <th scope="col" className="px-3 py-2">{t("insightsValue.citation")}</th>
              {data.can_edit ? <th scope="col" className="px-3 py-2">{t("insightsExplore.actions")}</th> : null}
            </tr>
          </thead>
          <tbody>
            {data.rates.map((r) => (
              <RateRow key={r.id} r={r} canEdit={data.can_edit} busy={busy} run={run} />
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function RateRow({
  r,
  canEdit,
  busy,
  run,
}: {
  r: ValueRate;
  canEdit: boolean;
  busy: boolean;
  run: (action: () => Promise<unknown>, ok: string) => Promise<void>;
}) {
  const { t } = useTranslation();
  const [cost, setCost] = useState(String(r.hourly_cost));
  useEffect(() => setCost(String(r.hourly_cost)), [r.hourly_cost]);
  const dirty = Number(cost) !== r.hourly_cost;
  const valid = cost.trim() !== "" && Number(cost) >= 0;
  return (
    <tr className="border-t align-top" data-testid={`rate-${r.role}`}>
      <td className="px-3 py-2">{r.role.replace(/_/g, " ")}</td>
      <td className="px-3 py-2 text-right tabular-nums">
        {canEdit ? (
          <span className="inline-flex items-center gap-1">
            <input
              aria-label={t("insightsValue.hourlyFor", { role: r.role })}
              type="number"
              min={0}
              step="0.01"
              value={cost}
              onChange={(e) => setCost(e.target.value)}
              className="insights-filter h-8 w-24 rounded-md border bg-background px-2 text-right text-sm"
            />
            <span className="text-xs text-muted-foreground">{r.currency}</span>
          </span>
        ) : (
          formatMoney(r.hourly_cost, r.currency)
        )}
      </td>
      <td className="px-3 py-2">
        <SourceCell source={r.source} confirmed={r.confirmed} />
      </td>
      <td className="max-w-md px-3 py-2 text-xs text-muted-foreground">{r.citation || MISSING}</td>
      {canEdit ? (
        <td className="px-3 py-2">
          {dirty ? (
            <button
              type="button"
              disabled={busy || !valid}
              onClick={() => void run(() => valueApi.editRate(r.id, { hourly_cost: Number(cost) }), t("insightsValue.saved"))}
              className="insights-target h-8 rounded-md bg-primary px-3 text-xs font-medium text-primary-foreground disabled:opacity-60"
            >
              {t("insightsValue.save")}
            </button>
          ) : !r.confirmed ? (
            <button
              type="button"
              disabled={busy}
              onClick={() => void run(() => valueApi.confirm({ baseline_ids: [], rate_ids: [r.id] }), t("insightsValue.confirmedOne"))}
              className="insights-target h-8 rounded-md border px-3 text-xs font-medium hover:bg-muted"
              aria-label={t("insightsValue.confirmLabel", { name: r.role })}
            >
              {t("insightsValue.confirm")}
            </button>
          ) : null}
        </td>
      ) : null}
    </tr>
  );
}

function RoiFloor({
  data,
  busy,
  run,
}: {
  data: ValueResponse;
  busy: boolean;
  run: (action: () => Promise<unknown>, ok: string) => Promise<void>;
}) {
  const { t } = useTranslation();
  const id = useId();
  const [floor, setFloor] = useState(String(data.roi_floor));
  return (
    <form
      className="flex flex-wrap items-end gap-2"
      aria-labelledby={`${id}-f`}
      onSubmit={(e) => {
        e.preventDefault();
        const n = Number(floor);
        if (!(n >= 0)) return;
        void run(() => valueApi.setRoiFloor(n), t("insightsValue.saved"));
      }}
    >
      <label id={`${id}-f`} className="flex flex-col gap-1 text-xs font-medium text-muted-foreground">
        {t("insightsValue.roiFloor", { currency: data.currency })}
        <input
          type="number"
          min={0}
          step="1"
          value={floor}
          onChange={(e) => setFloor(e.target.value)}
          className="insights-filter h-8 w-32 rounded-md border bg-background px-2 text-sm"
        />
      </label>
      <button type="submit" disabled={busy} className="insights-target h-8 rounded-md border px-3 text-sm font-medium hover:bg-muted">
        {t("insightsValue.save")}
      </button>
      {data.roi_floor_custom ? (
        <button
          type="button"
          disabled={busy}
          onClick={() => void run(() => valueApi.setRoiFloor(null), t("insightsValue.saved"))}
          className="insights-target h-8 rounded-md border px-3 text-sm hover:bg-muted"
        >
          {t("insightsValue.roiFloorDefault")}
        </button>
      ) : null}
    </form>
  );
}
