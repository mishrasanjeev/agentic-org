// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
/**
 * Downloads: the caller's Insights export files (an administrator sees the
 * tenant's), kept for seven days. Files come through the authenticated API;
 * each download is audited by the server. The list refreshes every 10 seconds
 * while a file is still being made.
 */
import { useCallback, useEffect, useState } from "react";
import { Helmet } from "react-helmet-async";
import { Link } from "react-router";
import { useTranslation } from "react-i18next";
import EmptyState from "@/components/insights/EmptyState";
import { useOptionalRole } from "@/contexts/AuthContext";
import { errorStatus, readErrorDetail } from "@/lib/insights/api";
import { saveBlob } from "@/lib/insights/download";
import { anyPending, exportsApi, type ExportJob } from "@/lib/insights/exports";
import { MISSING, formatCount } from "@/lib/insights/format";
import { STATE_CHIP_CLASSES } from "@/lib/insights/tokens";

export const POLL_MS = 10_000;
const SIGNING_ROLES = new Set(["admin", "auditor"]);

type State =
  | { kind: "loading" }
  | { kind: "ready"; jobs: ExportJob[]; storage: boolean }
  | { kind: "disabled" }
  | { kind: "error"; message: string | null };

function formatBytes(bytes: number | null): string {
  if (bytes === null) return MISSING;
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatWhen(iso: string | null, locale: string): string {
  if (!iso) return MISSING;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? MISSING : d.toLocaleString(locale, { dateStyle: "medium", timeStyle: "short" });
}

const STATUS_CLASS: Record<string, string> = {
  ready: STATE_CHIP_CLASSES.ok,
  failed: STATE_CHIP_CLASSES.act,
  queued: STATE_CHIP_CLASSES.watch,
  running: STATE_CHIP_CLASSES.watch,
  expired: "bg-muted text-muted-foreground",
};

export default function InsightsDownloads() {
  const { t, i18n } = useTranslation();
  const [state, setState] = useState<State>({ kind: "loading" });
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ id: string; text: string; error?: boolean } | null>(null);
  const role = useOptionalRole();
  const [signing, setSigning] = useState<{ id: string; statement: string } | null>(null);

  const sign = async (job: ExportJob, statement: string) => {
    setBusy(job.id);
    setNotice(null);
    try {
      await exportsApi.sign(job.id, statement);
      setSigning(null);
      setNotice({ id: job.id, text: t("insightsDownloads.signed") });
      await load(true);
    } catch (e) {
      setNotice({ id: job.id, text: (await readErrorDetail(e)) || t("insightsDownloads.signFailed"), error: true });
    } finally {
      setBusy(null);
    }
  };

  const load = useCallback(async (quiet = false) => {
    if (!quiet) setState({ kind: "loading" });
    try {
      const { jobs, storage } = await exportsApi.list();
      setState({ kind: "ready", jobs, storage });
    } catch (e) {
      if (errorStatus(e) === 404) return setState({ kind: "disabled" });
      if (!quiet) setState({ kind: "error", message: await readErrorDetail(e) });
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const pending = state.kind === "ready" && anyPending(state.jobs);
  useEffect(() => {
    if (!pending) return;
    const timer = window.setInterval(() => void load(true), POLL_MS);
    return () => window.clearInterval(timer);
  }, [pending, load]);

  const download = async (job: ExportJob) => {
    setBusy(job.id);
    setNotice(null);
    try {
      const blob = await exportsApi.download(job);
      saveBlob(blob, job.filename || `insights-export-${job.id}.${job.format}`);
      setNotice({ id: job.id, text: t("insightsDownloads.saved") });
    } catch (e) {
      setNotice({ id: job.id, text: (await readErrorDetail(e)) || t("insightsDownloads.failed"), error: true });
    } finally {
      setBusy(null);
    }
  };

  let body: React.ReactNode;
  if (state.kind === "loading") {
    body = (
      <p role="status" className="text-sm text-muted-foreground">
        {t("insightsDownloads.loading")}
      </p>
    );
  } else if (state.kind === "disabled") {
    body = <EmptyState title={t("insights.disabled.title")} hint={t("insights.disabled.body")} />;
  } else if (state.kind === "error") {
    body = <EmptyState title={t("insights.error.load")} hint={state.message} />;
  } else if (state.jobs.length === 0) {
    body = (
      <EmptyState
        title={t("insightsDownloads.empty.title")}
        hint={state.storage ? t("insightsDownloads.empty.body") : t("insightsDownloads.noStorage")}
      />
    );
  } else {
    body = (
      <div className="overflow-x-auto rounded-md border">
        <table className="w-full text-sm" data-testid="downloads-table">
          <caption className="sr-only">{t("insightsDownloads.title")}</caption>
          <thead className="bg-muted/50 text-left text-xs text-muted-foreground">
            <tr>
              <th scope="col" className="px-3 py-2 font-medium">{t("insightsDownloads.cols.file")}</th>
              <th scope="col" className="px-3 py-2 font-medium">{t("insightsDownloads.cols.status")}</th>
              <th scope="col" className="px-3 py-2 font-medium">{t("insightsDownloads.cols.rows")}</th>
              <th scope="col" className="px-3 py-2 font-medium">{t("insightsDownloads.cols.size")}</th>
              <th scope="col" className="px-3 py-2 font-medium">{t("insightsDownloads.cols.made")}</th>
              <th scope="col" className="px-3 py-2 font-medium">{t("insightsDownloads.cols.expires")}</th>
              <th scope="col" className="px-3 py-2 font-medium">
                <span className="sr-only">{t("insightsDownloads.cols.action")}</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {state.jobs.map((job) => (
              <tr key={job.id} className="border-t align-top" data-testid={`download-${job.id}`}>
                <td className="px-3 py-2">
                  <div className="font-medium">
                    {job.filename || t(`insightsDownloads.kinds.${job.kind}`, { defaultValue: job.kind })}
                  </div>
                  <div className="text-xs text-muted-foreground">
                    {[
                      t(`insightsDownloads.kinds.${job.kind}`, { defaultValue: job.kind }),
                      job.key,
                      job.format.toUpperCase(),
                      job.include_pii ? t("insightsDownloads.rawPii") : null,
                    ]
                      .filter(Boolean)
                      .join(" · ")}
                  </div>
                  {job.sha256 ? (
                    <div className="break-all text-[11px] text-muted-foreground">SHA-256 {job.sha256}</div>
                  ) : null}
                  {job.error ? <div className="text-xs text-[hsl(var(--state-act))]">{job.error}</div> : null}
                  {job.verification_status ? (
                    <div className="text-xs" data-testid={`verification-${job.id}`}>
                      {t(`insightsDownloads.verification.${job.verification_status}`, {
                        defaultValue: job.verification_status,
                      })}
                    </div>
                  ) : null}
                  {job.signature ? (
                    <div className="text-xs text-muted-foreground" data-testid={`signature-${job.id}`}>
                      {t("insightsDownloads.signedBy", {
                        name: String(job.signature.by ?? ""),
                        when: formatWhen(job.signed_at, i18n.language),
                      })}{" "}
                      “{String(job.signature.statement ?? "")}”
                    </div>
                  ) : null}
                  {signing?.id === job.id ? (
                    <form
                      className="mt-2 flex flex-col gap-1"
                      onSubmit={(e) => {
                        e.preventDefault();
                        void sign(job, signing.statement);
                      }}
                    >
                      <label className="text-xs font-medium" htmlFor={`statement-${job.id}`}>
                        {t("insightsDownloads.statementLabel")}
                      </label>
                      <textarea
                        id={`statement-${job.id}`}
                        className="min-h-[64px] rounded-md border p-2 text-xs"
                        maxLength={1000}
                        value={signing.statement}
                        onChange={(e) => setSigning({ id: job.id, statement: e.target.value })}
                      />
                      <div className="flex gap-2">
                        <button
                          type="submit"
                          disabled={busy === job.id || !signing.statement.trim()}
                          className="insights-target inline-flex h-8 items-center rounded-md border px-3 text-xs font-medium hover:bg-muted disabled:opacity-60"
                        >
                          {t("insightsDownloads.confirmSign")}
                        </button>
                        <button
                          type="button"
                          onClick={() => setSigning(null)}
                          className="insights-target inline-flex h-8 items-center rounded-md px-3 text-xs hover:bg-muted"
                        >
                          {t("insightsDownloads.cancel")}
                        </button>
                      </div>
                    </form>
                  ) : null}
                  {notice?.id === job.id ? (
                    <div
                      role={notice.error ? "alert" : "status"}
                      className={`text-xs ${notice.error ? "text-[hsl(var(--state-act))]" : "text-muted-foreground"}`}
                    >
                      {notice.text}
                    </div>
                  ) : null}
                </td>
                <td className="px-3 py-2">
                  <span className={`rounded px-1.5 py-0.5 text-xs ${STATUS_CLASS[job.status] ?? ""}`}>
                    {t(`insightsDownloads.status.${job.status}`)}
                  </span>
                </td>
                <td className="px-3 py-2 tabular-nums">{job.row_count === null ? MISSING : formatCount(job.row_count)}</td>
                <td className="px-3 py-2 tabular-nums">{formatBytes(job.size_bytes)}</td>
                <td className="px-3 py-2 whitespace-nowrap">{formatWhen(job.created_at, i18n.language)}</td>
                <td className="px-3 py-2 whitespace-nowrap">{formatWhen(job.expires_at, i18n.language)}</td>
                <td className="px-3 py-2 text-right">
                  {job.kind === "evidence_pack" &&
                  job.status === "ready" &&
                  !job.signature &&
                  SIGNING_ROLES.has(role) &&
                  signing?.id !== job.id ? (
                    <button
                      type="button"
                      className="insights-target mb-1 mr-2 inline-flex h-8 items-center rounded-md border px-3 text-xs font-medium hover:bg-muted"
                      onClick={() =>
                        setSigning({
                          id: job.id,
                          statement: t("insightsDownloads.defaultStatement", {
                            from: job.period.from ?? "",
                            to: job.period.to ?? "",
                          }),
                        })
                      }
                    >
                      {t("insightsDownloads.sign")}
                    </button>
                  ) : null}
                  {job.status === "ready" ? (
                    <button
                      type="button"
                      className="insights-target inline-flex h-8 items-center rounded-md border px-3 text-xs font-medium hover:bg-muted disabled:opacity-60"
                      onClick={() => void download(job)}
                      disabled={busy === job.id}
                      aria-label={t("insightsDownloads.downloadLabel", { name: job.filename || job.id })}
                    >
                      {busy === job.id ? t("insightsDownloads.downloading") : t("insightsDownloads.download")}
                    </button>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4" data-testid="insights-downloads">
      <Helmet>
        <title>{`${t("insightsDownloads.title")} — AgenticOrg`}</title>
      </Helmet>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold md:text-2xl">{t("insightsDownloads.title")}</h1>
          <p className="text-sm text-muted-foreground">{t("insightsDownloads.subtitle")}</p>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => void load()}
            className="insights-target inline-flex h-8 items-center rounded-md border px-3 text-sm font-medium hover:bg-muted"
          >
            {t("insights.refresh")}
          </button>
          <Link
            to="/dashboard/insights"
            className="insights-target inline-flex h-8 items-center rounded-md border px-3 text-sm font-medium hover:bg-muted"
          >
            {t("insightsDownloads.back")}
          </Link>
        </div>
      </div>
      {pending ? (
        <p role="status" className="text-xs text-muted-foreground">
          {t("insightsDownloads.pending")}
        </p>
      ) : null}
      {body}
    </div>
  );
}
