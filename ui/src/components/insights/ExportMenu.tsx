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

function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Give the browser a tick to start the download before revoking.
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

/**
 * Export menu (CSV / XLSX / JSON) for one metric, using the same filters as
 * the view. The file comes from GET /insights/metrics/{key}/export through
 * the authenticated client; the server audits every export and answers 413
 * with a hint when the range is too large, which is shown here.
 */
export default function ExportMenu({
  metricKey,
  title,
  filters,
}: {
  metricKey: string;
  title: string;
  filters: InsightsFilters;
}) {
  const { t } = useTranslation();
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
    try {
      const blob = await insightsApi.exportBlob(metricKey, format, filters);
      saveBlob(blob, exportFilename(metricKey, format, filters));
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
          </div>
        </>
      ) : null}
      {error ? (
        <p role="alert" className="mt-1 max-w-xs text-right text-xs text-[hsl(var(--state-act))]">
          {error}
        </p>
      ) : null}
    </div>
  );
}
