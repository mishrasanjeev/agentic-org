// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
/**
 * Files of the view on screen, made in the background and listed on the
 * Downloads page: a PDF of any view, the finance pack on the Finance view
 * (administrators and the CFO) and the period evidence pack on the Compliance
 * view (administrators and auditors), for the period and filters shown.
 */
import { useState } from "react";
import { Link } from "react-router";
import { useTranslation } from "react-i18next";
import type { InsightsFilters, InsightsViewKey } from "@/lib/insights/api";
import { readErrorDetail } from "@/lib/insights/api";
import { exportsApi, type ExportRequest } from "@/lib/insights/exports";

const FINANCE_PACK_ROLES = new Set(["admin", "cfo"]);
const EVIDENCE_PACK_ROLES = new Set(["admin", "auditor"]);
// Roles that may open the Report Scheduler (report_schedules scopes) and schedule an Insights view.
const SCHEDULING_ROLES = new Set(["admin", "cfo", "chro", "cmo", "coo"]);

export default function ViewFiles({
  view,
  filters,
  role,
}: {
  view: InsightsViewKey;
  filters: InsightsFilters;
  role: string;
}) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<{ text: string; error: boolean } | null>(null);
  const period = { from: filters.from, to: filters.to, company_id: filters.company_id };

  const ask = async (name: string, request: ExportRequest) => {
    setBusy(name);
    setMessage(null);
    try {
      await exportsApi.create(request);
      setMessage({ text: t("insights.files.queued"), error: false });
    } catch (e) {
      setMessage({ text: (await readErrorDetail(e)) || t("insights.export.failed"), error: true });
    } finally {
      setBusy(null);
    }
  };

  const button = (name: string, label: string, request: ExportRequest) => (
    <button
      type="button"
      onClick={() => void ask(name, request)}
      disabled={busy !== null}
      className="insights-target inline-flex h-8 items-center rounded-md border px-3 text-xs font-medium hover:bg-muted disabled:opacity-60"
      data-testid={`view-file-${name}`}
    >
      {busy === name ? t("insights.files.asking") : label}
    </button>
  );

  return (
    <div className="flex flex-wrap items-center gap-2" data-testid="view-files">
      {button("pdf", t("insights.files.pdf"), {
        kind: "view_pdf",
        format: "pdf",
        params: { view, compare: filters.compare, ...period },
      })}
      {button("pptx", t("insights.files.pptx"), {
        kind: "view_pptx",
        format: "pptx",
        params: { view, compare: filters.compare, ...period },
      })}
      {view === "finance" && FINANCE_PACK_ROLES.has(role)
        ? button("finance", t("insights.files.financePack"), { kind: "finance_pack", format: "zip", params: period })
        : null}
      {SCHEDULING_ROLES.has(role) ? (
        <Link
          to={`/dashboard/report-schedules?type=insights_view&view=${encodeURIComponent(view)}`}
          className="insights-target inline-flex h-8 items-center rounded-md border px-3 text-xs font-medium hover:bg-muted"
          data-testid="view-file-schedule"
        >
          {t("insights.files.schedule")}
        </Link>
      ) : null}
      {view === "partner" && FINANCE_PACK_ROLES.has(role)
        ? button("partner", t("insights.files.partnerPack"), { kind: "partner_pack", format: "zip", params: period })
        : null}
      {view === "compliance" && EVIDENCE_PACK_ROLES.has(role)
        ? button("evidence", t("insights.files.evidencePack"), { kind: "evidence_pack", format: "zip", params: period })
        : null}
      {message ? (
        <p
          role={message.error ? "alert" : "status"}
          className={`text-xs ${message.error ? "text-[hsl(var(--state-act))]" : "text-muted-foreground"}`}
        >
          {message.text}{" "}
          {!message.error ? (
            <Link to="/dashboard/insights/downloads" className="text-primary hover:underline">
              {t("insights.export.openDownloads")}
            </Link>
          ) : null}
        </p>
      ) : null}
    </div>
  );
}
