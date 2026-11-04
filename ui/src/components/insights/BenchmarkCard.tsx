// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
/**
 * The benchmark card on the Executive, Finance and Operations views: the
 * organisation's figures for the last 30 complete days against the 25th, 50th
 * and 75th percentiles of the opted-in cohorts it belongs to, or why there are
 * none. An administrator opts the organisation in or out here.
 */
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { errorStatus, readErrorDetail } from "@/lib/insights/api";
import { benchmarksApi, formatBenchmark, type BenchmarkCard as Card } from "@/lib/insights/benchmarks";

const BUTTON =
  "h-8 rounded-md border px-3 text-xs font-medium hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50";

export const BENCHMARK_VIEWS = new Set(["executive", "finance", "operations"]);
export const BENCHMARK_ROLES = new Set(["admin", "cfo", "coo"]);

function cohortLabel(cohort: string, t: (key: string, opts?: Record<string, unknown>) => string): string {
  if (cohort === "all") return t("insightsBenchmarks.cohorts.all");
  if (cohort.startsWith("plan:")) return t("insightsBenchmarks.cohorts.plan", { plan: cohort.slice(5) });
  return cohort;
}

export default function BenchmarkCard() {
  const { t } = useTranslation();
  const [card, setCard] = useState<Card | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "hidden" | "error">("loading");
  const [reloadKey, setReloadKey] = useState(0);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    setState("loading");
    benchmarksApi
      .card()
      .then((data) => {
        if (!live) return;
        setCard(data);
        setState("ready");
      })
      .catch((e) => {
        if (!live) return;
        const code = errorStatus(e);
        setState(code === 403 || code === 404 ? "hidden" : "error");
      });
    return () => {
      live = false;
    };
  }, [reloadKey]);

  const change = async (optIn: boolean) => {
    setError(null);
    setSaving(true);
    try {
      await benchmarksApi.setOptIn(optIn);
      setReloadKey((k) => k + 1);
    } catch (e) {
      setError((await readErrorDetail(e)) || t("insightsBenchmarks.changeFailed"));
    } finally {
      setSaving(false);
    }
  };

  if (state === "hidden") return null;
  return (
    <section aria-labelledby="insights-benchmarks" className="rounded-md border p-4" data-testid="insights-benchmarks">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 id="insights-benchmarks" className="text-sm font-semibold">
            {t("insightsBenchmarks.title")}
          </h3>
          {card ? (
            <p className="text-xs text-muted-foreground">
              {t("insightsBenchmarks.window", { from: card.window.from, to: card.window.to, count: card.participating })}
            </p>
          ) : null}
        </div>
        {card?.can_change ? (
          <button
            type="button"
            className={BUTTON}
            disabled={saving}
            onClick={() => void change(!card.opted_in)}
            data-testid="benchmarks-toggle"
          >
            {card.opted_in ? t("insightsBenchmarks.optOut") : t("insightsBenchmarks.optIn")}
          </button>
        ) : null}
      </div>
      {error ? (
        <p role="alert" className="mt-2 text-xs text-destructive">
          {error}
        </p>
      ) : null}
      {state === "loading" ? (
        <p role="status" className="mt-2 text-xs text-muted-foreground">
          {t("insightsBenchmarks.loading")}
        </p>
      ) : null}
      {state === "error" ? (
        <p role="alert" className="mt-2 text-xs text-destructive">
          {t("insightsBenchmarks.loadFailed")}
        </p>
      ) : null}
      {card && state === "ready" ? (
        <>
          {!card.opted_in ? (
            <p className="mt-2 text-sm text-muted-foreground" data-testid="benchmarks-not-opted-in">
              {t("insightsBenchmarks.notOptedIn", { min: card.min_tenants })}
            </p>
          ) : (
            <div className="mt-3 overflow-x-auto">
              <table className="w-full text-sm" data-testid="benchmarks-table">
                <caption className="sr-only">{t("insightsBenchmarks.title")}</caption>
                <thead className="text-left text-xs uppercase text-muted-foreground">
                  <tr>
                    <th scope="col" className="py-1 pr-3">
                      {t("insightsBenchmarks.columns.metric")}
                    </th>
                    <th scope="col" className="py-1 pr-3 text-right">
                      {t("insightsBenchmarks.columns.yours")}
                    </th>
                    <th scope="col" className="py-1 pr-3">
                      {t("insightsBenchmarks.columns.cohort")}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {card.metrics.map((metric) => (
                    <tr key={metric.key} className="border-t align-top" data-testid={`benchmark-${metric.key}`}>
                      <th scope="row" className="py-1.5 pr-3 font-medium">
                        {t(`insightsBenchmarks.metrics.${metric.key}`, { defaultValue: metric.title })}
                      </th>
                      <td className="py-1.5 pr-3 text-right tabular-nums">{formatBenchmark(metric.value, metric.unit)}</td>
                      <td className="py-1.5 pr-3">
                        {metric.bands.length === 0 ? (
                          <span className="text-xs text-muted-foreground">{t("insightsBenchmarks.noCohort")}</span>
                        ) : (
                          <ul className="flex flex-col gap-0.5 text-xs">
                            {metric.bands.map((band) => (
                              <li key={band.cohort}>
                                {t("insightsBenchmarks.band", {
                                  cohort: cohortLabel(band.cohort, t),
                                  count: band.tenants,
                                  p25: formatBenchmark(band.p25, metric.unit),
                                  p50: formatBenchmark(band.p50, metric.unit),
                                  p75: formatBenchmark(band.p75, metric.unit),
                                })}
                                {band.position ? ` · ${t(`insightsBenchmarks.positions.${band.position}`)}` : ""}
                              </li>
                            ))}
                          </ul>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {card.opted_in && card.hint ? <p className="mt-2 text-xs text-muted-foreground">{card.hint}</p> : null}
          <p className="mt-2 text-xs text-muted-foreground">{t("insightsBenchmarks.footnote", { min: card.min_tenants, runs: card.min_runs })}</p>
        </>
      ) : null}
    </section>
  );
}
