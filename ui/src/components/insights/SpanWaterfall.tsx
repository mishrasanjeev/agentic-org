// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
/**
 * One run's trace spans as a waterfall: each span is a row indented by its
 * depth, with a bar placed by its offset from the run start and sized by its
 * duration. Error spans use the critical colour. A text summary and a "view
 * data" table give the same facts without the chart.
 */
import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import type { RunSpan } from "@/lib/insights/explore";
import { formatDuration } from "@/lib/insights/format";
import { STATE_CHIP_CLASSES, chartColor, stateColor } from "@/lib/insights/tokens";

function shortName(name: string): string {
  return name.replace(/^agenticorg\./, "");
}

/** Errors take the critical state colour; otherwise one categorical colour per span family. */
function barColour(span: RunSpan): string {
  if (span.status === "error") return stateColor("act");
  if (span.name.includes("reason")) return chartColor(0);
  if (span.name.includes("tool")) return chartColor(1);
  if (span.name.includes("knowledge")) return chartColor(2);
  return chartColor(3);
}

export default function SpanWaterfall({
  spans,
  totalMs,
  failedSpanId,
}: {
  spans: RunSpan[];
  totalMs: number;
  failedSpanId?: string | null;
}) {
  const { t } = useTranslation();
  const [table, setTable] = useState(false);
  const tableId = useId();
  const total = Math.max(1, totalMs || Math.max(...spans.map((s) => s.offset_ms + s.duration_ms), 1));
  const errors = spans.filter((s) => s.status === "error").length;
  const longest = spans.reduce<RunSpan | null>((a, s) => (a && a.duration_ms >= s.duration_ms ? a : s), null);
  const summary = t("insightsRun.waterfall.summary", {
    count: spans.length,
    total: formatDuration(total),
    errors,
    longest: longest ? shortName(longest.name) : "",
    longestMs: longest ? formatDuration(longest.duration_ms) : "",
  });

  return (
    <div className="flex flex-col gap-2" data-testid="span-waterfall">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-muted-foreground">{summary}</p>
        <button
          type="button"
          className="insights-target inline-flex h-8 items-center rounded-md border px-3 text-xs font-medium hover:bg-muted"
          aria-expanded={table}
          aria-controls={tableId}
          onClick={() => setTable((open) => !open)}
        >
          {table ? t("insightsRun.waterfall.hideData") : t("insightsRun.waterfall.viewData")}
        </button>
      </div>
      {table ? (
        <div id={tableId} className="overflow-x-auto rounded-md border">
          <table className="w-full text-sm">
            <caption className="sr-only">{t("insightsRun.spans")}</caption>
            <thead className="bg-muted/50 text-left text-xs text-muted-foreground">
              <tr>
                <th scope="col" className="px-3 py-2 font-medium">{t("insightsRun.fields.span")}</th>
                <th scope="col" className="px-3 py-2 font-medium">{t("insightsRun.fields.status")}</th>
                <th scope="col" className="px-3 py-2 font-medium">{t("insightsRun.waterfall.offset")}</th>
                <th scope="col" className="px-3 py-2 font-medium">{t("insightsRun.fields.duration")}</th>
                <th scope="col" className="px-3 py-2 font-medium">{t("insightsRun.waterfall.detail")}</th>
              </tr>
            </thead>
            <tbody>
              {spans.map((span) => (
                <tr key={span.span_id} className="border-t">
                  <td className="whitespace-nowrap px-3 py-1.5" style={{ paddingLeft: `${12 + span.depth * 12}px` }}>
                    {shortName(span.name)}
                  </td>
                  <td className="px-3 py-1.5">{span.status}</td>
                  <td className="px-3 py-1.5 tabular-nums">{formatDuration(span.offset_ms)}</td>
                  <td className="px-3 py-1.5 tabular-nums">{formatDuration(span.duration_ms)}</td>
                  <td className="px-3 py-1.5 text-xs text-muted-foreground">
                    {[span.summary, ...span.events.map((e) => e.label)].filter(Boolean).join(" · ")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <ol className="flex flex-col gap-1" aria-label={t("insightsRun.spans")}>
          {spans.map((span) => {
            const left = Math.min(100, (span.offset_ms / total) * 100);
            const width = Math.max(0.5, Math.min(100 - left, (span.duration_ms / total) * 100));
            const failed = span.span_id === failedSpanId;
            return (
              <li
                key={span.span_id}
                className={`grid grid-cols-12 items-center gap-2 rounded text-xs ${failed ? "bg-[hsl(var(--state-act)/0.08)] ring-1 ring-[hsl(var(--state-act)/0.4)]" : ""}`}
                data-testid={`waterfall-span-${span.span_id}`}
                aria-label={t("insightsRun.waterfall.row", {
                  name: shortName(span.name),
                  status: span.status,
                  offset: formatDuration(span.offset_ms),
                  duration: formatDuration(span.duration_ms),
                })}
              >
                <div className="col-span-12 truncate sm:col-span-5" style={{ paddingLeft: `${span.depth * 12}px` }} title={span.name}>
                  <span className="font-medium">{shortName(span.name)}</span>
                  {span.summary ? <span className="ml-1 text-muted-foreground">{span.summary}</span> : null}
                  {span.events.length > 0 ? (
                    <span className="mt-0.5 flex flex-wrap gap-1">
                      {span.events.map((event, i) => (
                        <span key={`${span.span_id}-${i}`} className="rounded bg-muted px-1 text-[10px]">
                          {event.label}
                        </span>
                      ))}
                    </span>
                  ) : null}
                </div>
                <div className="col-span-9 h-3 rounded bg-muted sm:col-span-5" aria-hidden="true">
                  <div
                    className="h-3 rounded"
                    style={{ marginLeft: `${left}%`, width: `${width}%`, background: barColour(span) }}
                  />
                </div>
                <div className="col-span-3 text-right tabular-nums sm:col-span-2">
                  {span.status === "error" ? (
                    <span className={`mr-1 rounded px-1 ${STATE_CHIP_CLASSES.act}`}>{t("insightsRun.waterfall.error")}</span>
                  ) : null}
                  {formatDuration(span.duration_ms)}
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}
