// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
import { useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  EXPORT_FORMATS,
  exportFilename,
  insightsApi,
  readErrorDetail,
  type ExportFormat,
  type InsightsFilters,
} from "@/lib/insights/api";
import { useOptionalRole } from "@/contexts/AuthContext";
import { saveBlob } from "@/lib/insights/download";
import { svgToPng } from "@/lib/insights/chartImage";
import { exportsApi, offersBackground, type ExportRequest } from "@/lib/insights/exports";

/**
 * Export menu (CSV / XLSX / JSON) for one metric, using the same filters as
 * the view. The file comes from GET /insights/metrics/{key}/export through
 * the authenticated client; the server audits every export and answers 413
 * with a hint when the range is too large, which is shown here with an offer
 * to make the file in the background (POST /insights/exports; it appears on
 * the Downloads page). An explorer passes its own ``download`` and
 * ``background`` (its table filters). Administrators and auditors may ask for
 * raw personal data where the organisation masks it; the server audits that.
 */
const RAW_PII_ROLES = new Set(["admin", "auditor"]);

export default function ExportMenu({
  metricKey,
  title,
  filters,
  download,
  background,
  picture,
}: {
  metricKey: string;
  title: string;
  filters: InsightsFilters;
  download?: (format: ExportFormat, includePii: boolean) => Promise<{ blob: Blob; filename: string }>;
  background?: (format: ExportFormat) => ExportRequest;
  /** The chart as a picture (SVG markup with its context), when the metric is drawn as one. */
  picture?: () => string | null;
}) {
  const { t } = useTranslation();
  const mayIncludePii = RAW_PII_ROLES.has(useOptionalRole());
  const [includePii, setIncludePii] = useState(false);
  const [tooLarge, setTooLarge] = useState<ExportFormat | null>(null);
  const [queued, setQueued] = useState(false);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<ExportFormat | null>(null);
  const [error, setError] = useState<string | null>(null);
  const menuId = useId();
  const buttonRef = useRef<HTMLButtonElement>(null);
  const itemRefs = useRef<Array<HTMLButtonElement | null>>([]);

  useEffect(() => {
    if (open) itemRefs.current[0]?.focus();
  }, [open]);

  const close = (refocus = true) => {
    setOpen(false);
    if (refocus) buttonRef.current?.focus();
  };

  const run = async (format: ExportFormat) => {
    close();
    setBusy(format);
    setError(null);
    setTooLarge(null);
    setQueued(false);
    try {
      if (download) {
        const file = await download(format, includePii);
        saveBlob(file.blob, file.filename);
      } else {
        const blob = await insightsApi.exportBlob(metricKey, format, filters, includePii);
        saveBlob(blob, exportFilename(metricKey, format, filters));
      }
    } catch (e) {
      setError((await readErrorDetail(e)) || t("insights.export.failed"));
      if (await offersBackground(e)) setTooLarge(format);
    } finally {
      setBusy(null);
    }
  };

  const savePicture = async (kind: "svg" | "png") => {
    close();
    setError(null);
    const markup = picture?.();
    if (!markup) return setError(t("insights.export.noPicture"));
    try {
      const blob =
        kind === "svg" ? new Blob([markup], { type: "image/svg+xml;charset=utf-8" }) : await svgToPng(markup);
      saveBlob(blob, `${exportFilename(metricKey, "csv", filters).replace(/\.csv$/, "")}.${kind}`);
    } catch {
      setError(t("insights.export.failed"));
    }
  };

  const runInBackground = async (format: ExportFormat) => {
    setBusy(format);
    setError(null);
    try {
      const request: ExportRequest = background
        ? background(format)
        : { kind: "metric", format, params: { key: metricKey, ...filters } };
      await exportsApi.create({ ...request, include_pii: includePii });
      setTooLarge(null);
      setQueued(true);
    } catch (e) {
      setError((await readErrorDetail(e)) || t("insights.export.failed"));
    } finally {
      setBusy(null);
    }
  };

  const onMenuKey = (e: React.KeyboardEvent<HTMLDivElement>) => {
    const items = itemRefs.current.filter((el): el is HTMLButtonElement => Boolean(el));
    const idx = items.findIndex((el) => el === document.activeElement);
    if (e.key === "Escape") {
      e.preventDefault();
      close();
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      items[(idx + 1) % items.length]?.focus();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      items[(idx - 1 + items.length) % items.length]?.focus();
    } else if (e.key === "Home") {
      e.preventDefault();
      items[0]?.focus();
    } else if (e.key === "End") {
      e.preventDefault();
      items[items.length - 1]?.focus();
    } else if (e.key === "Tab") {
      close(false);
    }
  };

  return (
    <div className="relative flex flex-col items-end">
      <button
        ref={buttonRef}
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        aria-label={t("insights.export.menuLabel", { title })}
        onClick={() => setOpen((o) => !o)}
        disabled={busy !== null}
        className="rounded-md border px-2.5 py-1 text-xs font-medium hover:bg-muted disabled:opacity-60"
      >
        {busy ? t("insights.export.downloading") : t("insights.export.button")}
      </button>
      {open ? (
        <>
          <div className="fixed inset-0 z-30" aria-hidden="true" onClick={() => close(false)} />
          <div
            id={menuId}
            role="menu"
            aria-label={t("insights.export.menuLabel", { title })}
            onKeyDown={onMenuKey}
            className="absolute right-0 z-40 mt-1 w-40 rounded-md border bg-background py-1 shadow-lg"
          >
            {EXPORT_FORMATS.map((format, i) => (
              <button
                key={format}
                ref={(el) => {
                  itemRefs.current[i] = el;
                }}
                type="button"
                role="menuitem"
                tabIndex={-1}
                data-format={format}
                onClick={() => void run(format)}
                className="block w-full px-3 py-1.5 text-left text-xs hover:bg-muted focus:bg-muted"
              >
                {t(`insights.export.${format}`)}
              </button>
            ))}
            {picture
              ? (["svg", "png"] as const).map((kind, j) => (
                  <button
                    key={kind}
                    ref={(el) => {
                      itemRefs.current[EXPORT_FORMATS.length + j] = el;
                    }}
                    type="button"
                    role="menuitem"
                    tabIndex={-1}
                    data-format={kind}
                    onClick={() => void savePicture(kind)}
                    className="block w-full px-3 py-1.5 text-left text-xs hover:bg-muted focus:bg-muted"
                  >
                    {t(`insights.export.${kind}`)}
                  </button>
                ))
              : null}
            {mayIncludePii ? (
              <label className="mt-1 flex items-start gap-1.5 border-t px-3 pt-1.5 text-[11px] leading-tight">
                <input
                  type="checkbox"
                  className="mt-0.5"
                  checked={includePii}
                  onChange={(e) => setIncludePii(e.target.checked)}
                />
                <span>{t("insights.export.includePii")}</span>
              </label>
            ) : null}
          </div>
        </>
      ) : null}
      {error ? (
        <p role="alert" className="mt-1 max-w-xs text-right text-xs text-[hsl(var(--state-act))]">
          {error}
        </p>
      ) : null}
      {tooLarge ? (
        <button
          type="button"
          onClick={() => void runInBackground(tooLarge)}
          disabled={busy !== null}
          className="mt-1 rounded-md border px-2.5 py-1 text-xs font-medium hover:bg-muted disabled:opacity-60"
          data-testid="export-background"
        >
          {t("insights.export.background")}
        </button>
      ) : null}
      {queued ? (
        <p role="status" className="mt-1 max-w-xs text-right text-xs text-muted-foreground">
          {t("insights.export.queued")}{" "}
          <a href="/dashboard/insights/downloads" className="text-primary hover:underline">
            {t("insights.export.openDownloads")}
          </a>
        </p>
      ) : null}
    </div>
  );
}
