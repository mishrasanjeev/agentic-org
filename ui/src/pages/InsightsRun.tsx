// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
/**
 * Run evidence (Level 4): one run and the records around it, from
 * GET /insights/runs/{id}?at=... . The page shows ids, timings, statuses,
 * tokens and cost; the API never sends output text, prompts or tool
 * arguments. A run outside the caller's tenant, domains or company is a 404.
 */
import { useEffect, useState } from "react";
import { Helmet } from "react-helmet-async";
import { Link, useParams, useSearchParams } from "react-router";
import { useTranslation } from "react-i18next";
import Breadcrumbs from "@/components/insights/Breadcrumbs";
import DataQualityBadge from "@/components/insights/DataQualityBadge";
import EmptyState from "@/components/insights/EmptyState";
import SpanWaterfall from "@/components/insights/SpanWaterfall";
import { errorStatus, readErrorDetail } from "@/lib/insights/api";
import { saveBlob } from "@/lib/insights/download";
import {
  exploreApi,
  type ExplorerColumn,
  type RunEvidence,
  type RunFailure,
} from "@/lib/insights/explore";
import { formatCell } from "@/lib/insights/explorerCells";
import { MISSING, formatDuration } from "@/lib/insights/format";
import { STATE_CHIP_CLASSES } from "@/lib/insights/tokens";

type State =
  | { kind: "loading" }
  | { kind: "ready"; data: RunEvidence }
  | { kind: "missing" }
  | { kind: "error"; message: string | null };

function col(
  key: string,
  title: string,
  type: ExplorerColumn["type"],
): ExplorerColumn {
  return { key, title, type, sortable: false, filter: null };
}

function readCompanyId(): string {
  try {
    return localStorage.getItem("company_id") || "";
  } catch {
    return "";
  }
}

export default function InsightsRun() {
  const { t } = useTranslation();
  const { runId = "" } = useParams();
  const [searchParams] = useSearchParams();
  const at = searchParams.get("at") || "";
  const [state, setState] = useState<State>({ kind: "loading" });
  const [bundle, setBundle] = useState<
    { kind: "idle" } | { kind: "busy" } | { kind: "done"; sha256: string | null } | { kind: "error"; message: string | null }
  >({ kind: "idle" });

  async function downloadBundle() {
    setBundle({ kind: "busy" });
    try {
      const { blob, sha256 } = await exploreApi.runBundle(runId, at, readCompanyId() || undefined);
      saveBlob(blob, `run-${runId}.json`);
      setBundle({ kind: "done", sha256 });
    } catch (e) {
      setBundle({ kind: "error", message: await readErrorDetail(e) });
    }
  }

  useEffect(() => {
    let live = true;
    setState({ kind: "loading" });
    exploreApi
      .run(runId, at, readCompanyId() || undefined)
      .then((data) => {
        if (live) setState({ kind: "ready", data });
      })
      .catch(async (e) => {
        if (!live) return;
        const code = errorStatus(e);
        if (code === 404 || code === 403 || code === 422)
          return setState({ kind: "missing" });
        const message = await readErrorDetail(e);
        if (live) setState({ kind: "error", message });
      });
    return () => {
      live = false;
    };
  }, [runId, at]);

  const yesNo = { yes: t("insightsExplore.yes"), no: t("insightsExplore.no") };
  const cell = (column: ExplorerColumn, value: unknown) =>
    formatCell(column, value, { ...yesNo });

  let body: React.ReactNode;
  if (state.kind === "loading") {
    body = (
      <p role="status" className="text-sm text-muted-foreground">
        {t("insightsRun.loading")}
      </p>
    );
  } else if (state.kind === "missing") {
    body = (
      <EmptyState
        title={t("insightsRun.missing.title")}
        hint={t("insightsRun.missing.body")}
      />
    );
  } else if (state.kind === "error") {
    body = <EmptyState title={t("insights.error.load")} hint={state.message} />;
  } else {
    const {
      run,
      gateway_records: gateway,
      spans,
      approvals,
      audit,
      failure,
    } = state.data;
    const approvalRows = approvals.map((a) => ({
      ...a,
      votes_text: (a.votes ?? [])
        .map((v) => `${v.by}: ${v.decision ?? MISSING}`)
        .join(", "),
    }));
    const facts: Array<[string, ExplorerColumn, unknown]> = [
      [
        "status",
        col("status", t("insightsRun.fields.status"), "status"),
        run.status,
      ],
      [
        "started_at",
        col("started_at", t("insightsRun.fields.started"), "datetime"),
        run.started_at,
      ],
      [
        "duration_ms",
        col("duration_ms", t("insightsRun.fields.duration"), "duration_ms"),
        run.duration_ms,
      ],
      [
        "entrypoint",
        col("entrypoint", t("insightsRun.fields.entrypoint"), "text"),
        run.entrypoint,
      ],
      [
        "task_type",
        col("task_type", t("insightsRun.fields.task"), "text"),
        run.task_type,
      ],
      ["model", col("model", t("insightsRun.fields.model"), "text"), run.model],
      [
        "tokens",
        col("tokens", t("insightsRun.fields.tokens"), "tokens"),
        run.tokens?.total,
      ],
      [
        "cost_usd",
        col("cost_usd", t("insightsRun.fields.cost"), "money"),
        run.cost_usd,
      ],
      [
        "confidence",
        col("confidence", t("insightsRun.fields.confidence"), "percent"),
        // Confidence is recorded as 0 to 1; shown as a percentage.
        typeof run.confidence === "number" ? run.confidence * 100 : null,
      ],
      [
        "hitl_required",
        col("hitl_required", t("insightsRun.fields.approval"), "bool"),
        run.hitl_required,
      ],
      [
        "error_class",
        col("error_class", t("insightsRun.fields.error"), "text"),
        run.error_class,
      ],
      [
        "environment",
        col("environment", t("insightsRun.fields.environment"), "text"),
        run.environment,
      ],
    ];
    body = (
      <>
        {failure ? <FailurePanel failure={failure} /> : null}
        <section aria-labelledby="run-facts" className="rounded-lg border p-4">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <h2 id="run-facts" className="text-base font-semibold">
              {run.agent_id ? (
                <Link
                  to={`/dashboard/insights/agents/${run.agent_id}`}
                  className="text-primary hover:underline"
                >
                  {run.agent || t("insightsRun.agent")}
                </Link>
              ) : (
                run.agent || t("insightsRun.agent")
              )}
            </h2>
            <DataQualityBadge
              quality={String(run.data_quality ?? "measured")}
            />
          </div>
          <dl className="grid grid-cols-1 gap-x-6 gap-y-2 text-sm sm:grid-cols-2 lg:grid-cols-3">
            {facts.map(([key, column, value]) => (
              <div
                key={key}
                className="flex justify-between gap-3 border-b pb-1"
              >
                <dt className="text-muted-foreground">{column.title}</dt>
                <dd className="text-right tabular-nums">
                  {cell(column, value)}
                </dd>
              </div>
            ))}
          </dl>
          <p className="mt-3 text-xs text-muted-foreground">
            {t("insightsRun.tokensSplit", {
              input: run.tokens?.input ?? MISSING,
              output: run.tokens?.output ?? MISSING,
              cached: run.tokens?.cached ?? MISSING,
            })}
          </p>
        </section>

        <EvidenceTable
          id="run-gateway"
          title={t("insightsRun.gateway")}
          empty={t("insightsRun.noGateway")}
          rows={gateway}
          columns={[
            col("created_at", t("insightsRun.fields.when"), "datetime"),
            col("provider", t("insightsRun.fields.provider"), "text"),
            col("model", t("insightsRun.fields.model"), "text"),
            col("outcome", t("insightsRun.fields.outcome"), "status"),
            col("fallback_from", t("insightsRun.fields.fallback"), "text"),
            col("latency_ms", t("insightsRun.fields.latency"), "duration_ms"),
            col("input_tokens", t("insightsRun.fields.input"), "tokens"),
            col("output_tokens", t("insightsRun.fields.output"), "tokens"),
            col("cost_usd", t("insightsRun.fields.cost"), "money"),
          ]}
          cell={cell}
        />
        <section aria-labelledby="run-spans" className="flex flex-col gap-2">
          <h2 id="run-spans" className="text-sm font-semibold">
            {t("insightsRun.spans")}{" "}
            <span className="font-normal text-muted-foreground">({spans.length})</span>
          </h2>
          {spans.length === 0 ? (
            <p className="text-xs text-muted-foreground">{t("insightsRun.noSpans")}</p>
          ) : (
            <SpanWaterfall
              spans={spans}
              totalMs={state.data.span_total_ms ?? 0}
              failedSpanId={failure?.source === "span" ? failure.span_id : null}
            />
          )}
        </section>
        <EvidenceTable
          id="run-approvals"
          title={t("insightsRun.approvals")}
          empty={t("insightsRun.noApprovals")}
          rows={approvalRows}
          columns={[
            col("title", t("insightsRun.fields.approval"), "text"),
            col("status", t("insightsRun.fields.status"), "status"),
            col("decision", t("insightsRun.fields.decision"), "text"),
            col("decided_by", t("insightsRun.fields.decidedBy"), "text"),
            col("delegated_from", t("insightsRun.fields.delegatedFrom"), "text"),
            col("votes_text", t("insightsRun.fields.votes"), "text"),
            col("decision_at", t("insightsRun.fields.decided"), "datetime"),
          ]}
          cell={cell}
          linkKey="title"
        />
        <EvidenceTable
          id="run-audit"
          title={t("insightsRun.audit")}
          empty={t("insightsRun.noAudit")}
          rows={audit}
          columns={[
            col("created_at", t("insightsRun.fields.when"), "datetime"),
            col("event_type", t("insightsRun.fields.event"), "text"),
            col("action", t("insightsRun.fields.action"), "text"),
            col("outcome", t("insightsRun.fields.outcome"), "status"),
            col("signed", t("insightsRun.fields.signed"), "bool"),
            col("verified", t("insightsRun.fields.verified"), "bool"),
            col("chain_seq", t("insightsRun.fields.chainSeq"), "count"),
          ]}
          cell={cell}
        />
      </>
    );
  }

  return (
    <div className="flex flex-col gap-4" data-testid="insights-run">
      <Helmet>
        <title>{`${t("insightsRun.title")} — AgenticOrg`}</title>
      </Helmet>
      <Breadcrumbs
        items={[
          { label: t("insightsExplore.title"), to: "/dashboard/insights/explore/runs" },
          { label: t("insightsRun.title") },
        ]}
      />
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold md:text-2xl">
            {t("insightsRun.title")}
          </h1>
          <p className="text-sm text-muted-foreground">
            {t("insightsRun.subtitle")}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {state.kind === "ready" ? (
            <button
              type="button"
              className="insights-target inline-flex h-8 items-center rounded-md border px-3 text-sm font-medium hover:bg-muted disabled:opacity-60"
              onClick={downloadBundle}
              disabled={bundle.kind === "busy"}
              data-testid="run-bundle"
            >
              {bundle.kind === "busy" ? t("insightsRun.bundle.busy") : t("insightsRun.bundle.download")}
            </button>
          ) : null}
          <Link
            to="/dashboard/insights/explore/runs"
            className="insights-target inline-flex h-8 items-center rounded-md border px-3 text-sm font-medium hover:bg-muted"
          >
            {t("insightsRun.back")}
          </Link>
        </div>
      </div>
      {bundle.kind === "done" ? (
        <p role="status" className="break-all text-xs text-muted-foreground" data-testid="run-bundle-sha">
          {bundle.sha256 ? t("insightsRun.bundle.checksum", { sha256: bundle.sha256 }) : t("insightsRun.bundle.saved")}
        </p>
      ) : null}
      {bundle.kind === "error" ? (
        <p role="alert" className="text-xs text-destructive">
          {bundle.message || t("insightsRun.bundle.failed")}
        </p>
      ) : null}
      {body}
    </div>
  );
}

function FailurePanel({ failure }: { failure: RunFailure }) {
  const { t } = useTranslation();
  const where =
    failure.source === "span"
      ? t("insightsRun.failure.atSpan", {
          name: (failure.name ?? "").replace(/^agenticorg\./, ""),
          offset: formatDuration(failure.offset_ms ?? 0),
        })
      : t("insightsRun.failure.atRun", { status: failure.status ?? MISSING });
  const detail = [failure.exception, failure.summary, failure.error_class].filter(Boolean).join(" · ");
  return (
    <section
      aria-labelledby="run-failure"
      className={`rounded-lg border p-4 ${STATE_CHIP_CLASSES.act}`}
      data-testid="run-failure"
    >
      <h2 id="run-failure" className="text-base font-semibold">
        {t("insightsRun.failure.title")}
      </h2>
      <p className="text-sm">{where}</p>
      {detail ? <p className="mt-1 text-sm">{detail}</p> : null}
    </section>
  );
}

function EvidenceTable({
  id,
  title,
  empty,
  rows,
  columns,
  cell,
  linkKey,
}: {
  id: string;
  title: string;
  empty: string;
  rows: Array<Record<string, unknown> & { link?: string }>;
  columns: ExplorerColumn[];
  cell: (column: ExplorerColumn, value: unknown) => string;
  linkKey?: string;
}) {
  return (
    <section aria-labelledby={id} className="flex flex-col gap-2">
      <h2 id={id} className="text-sm font-semibold">
        {title}{" "}
        <span className="font-normal text-muted-foreground">
          ({rows.length})
        </span>
      </h2>
      {rows.length === 0 ? (
        <p className="text-xs text-muted-foreground">{empty}</p>
      ) : (
        <div className="overflow-x-auto rounded-md border">
          <table className="w-full text-sm">
            <caption className="sr-only">{title}</caption>
            <thead className="bg-muted/50 text-left text-xs text-muted-foreground">
              <tr>
                {columns.map((c) => (
                  <th
                    key={c.key}
                    scope="col"
                    className="whitespace-nowrap px-3 py-2 font-medium"
                  >
                    {c.title}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, i) => (
                <tr key={i} className="border-t">
                  {columns.map((c) => (
                    <td key={c.key} className="whitespace-nowrap px-3 py-2">
                      {linkKey === c.key &&
                      typeof row.link === "string" &&
                      row.link.startsWith("/") ? (
                        <Link
                          to={row.link}
                          className="text-primary hover:underline"
                        >
                          {cell(c, row[c.key])}
                        </Link>
                      ) : (
                        cell(c, row[c.key])
                      )}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
