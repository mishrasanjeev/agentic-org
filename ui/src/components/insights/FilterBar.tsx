// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
import { useEffect, useId, useState } from "react";
import { useTranslation } from "react-i18next";
import type { CompareMode } from "@/lib/insights/api";
import { isIsoDate, RANGE_PRESETS, type RangePreset } from "@/lib/insights/dates";

export interface FilterValue {
  preset: RangePreset;
  from: string;
  to: string;
  compare: CompareMode;
}

/**
 * Date range (7d / 30d / MTD / QTD / custom) and comparison controls. The page
 * keeps the chosen values in the URL so a view can be shared as a link.
 */
export default function FilterBar({
  value,
  onChange,
  companyScoped,
  disabled = false,
  showCompare = true,
}: {
  value: FilterValue;
  onChange: (next: Partial<FilterValue>) => void;
  companyScoped?: boolean;
  disabled?: boolean;
  /** Explorers compare on their own terms (the cost explorer always uses the previous period). */
  showCompare?: boolean;
}) {
  const { t } = useTranslation();
  const id = useId();
  const [draftFrom, setDraftFrom] = useState(value.from);
  const [draftTo, setDraftTo] = useState(value.to);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setDraftFrom(value.from);
    setDraftTo(value.to);
  }, [value.from, value.to]);

  const applyCustom = () => {
    if (!isIsoDate(draftFrom) || !isIsoDate(draftTo) || draftFrom > draftTo) {
      setError(t("insights.filters.invalidRange"));
      return;
    }
    setError(null);
    onChange({ preset: "custom", from: draftFrom, to: draftTo });
  };

  return (
    <div
      role="group"
      aria-label={t("insights.filters.label")}
      className="insights-filter flex flex-wrap items-end gap-3 rounded-lg border bg-muted/30 p-3"
      data-testid="insights-filter-bar"
    >
      <label className="flex flex-col gap-1 text-xs font-medium text-muted-foreground" htmlFor={`${id}-preset`}>
        {t("insights.filters.range")}
        <select
          id={`${id}-preset`}
          value={value.preset}
          disabled={disabled}
          onChange={(e) => {
            setError(null);
            onChange({ preset: e.target.value as RangePreset });
          }}
          className="h-8 rounded-md border bg-background px-2 text-sm text-foreground"
        >
          {RANGE_PRESETS.map((p) => (
            <option key={p} value={p}>
              {t(`insights.filters.presets.${p}`)}
            </option>
          ))}
        </select>
      </label>

      {value.preset === "custom" ? (
        <>
          <label className="flex flex-col gap-1 text-xs font-medium text-muted-foreground" htmlFor={`${id}-from`}>
            {t("insights.filters.from")}
            <input
              id={`${id}-from`}
              type="date"
              value={draftFrom}
              max={draftTo || undefined}
              disabled={disabled}
              onChange={(e) => setDraftFrom(e.target.value)}
              className="h-8 rounded-md border bg-background px-2 text-sm text-foreground"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs font-medium text-muted-foreground" htmlFor={`${id}-to`}>
            {t("insights.filters.to")}
            <input
              id={`${id}-to`}
              type="date"
              value={draftTo}
              min={draftFrom || undefined}
              disabled={disabled}
              onChange={(e) => setDraftTo(e.target.value)}
              className="h-8 rounded-md border bg-background px-2 text-sm text-foreground"
            />
          </label>
          <button
            type="button"
            onClick={applyCustom}
            disabled={disabled}
            className="h-8 rounded-md bg-primary px-3 text-sm font-medium text-primary-foreground disabled:opacity-60"
          >
            {t("insights.filters.apply")}
          </button>
        </>
      ) : (
        <p className="pb-1.5 text-xs tabular-nums text-muted-foreground" data-testid="insights-range-text">
          {t("insights.period", { from: value.from, to: value.to })}
        </p>
      )}

      {showCompare ? (
      <label className="flex flex-col gap-1 text-xs font-medium text-muted-foreground" htmlFor={`${id}-compare`}>
        {t("insights.filters.compare")}
        <select
          id={`${id}-compare`}
          value={value.compare}
          disabled={disabled}
          onChange={(e) => onChange({ compare: e.target.value as CompareMode })}
          className="h-8 rounded-md border bg-background px-2 text-sm text-foreground"
        >
          <option value="previous">{t("insights.filters.comparePrevious")}</option>
          <option value="none">{t("insights.filters.compareNone")}</option>
        </select>
      </label>
      ) : null}

      {companyScoped ? (
        <p className="pb-1.5 text-xs text-muted-foreground" data-testid="insights-company-scope">
          {t("insights.filters.companyScoped")}
        </p>
      ) : null}

      {error ? (
        <p role="alert" className="basis-full text-xs text-[hsl(var(--state-act))]">
          {error}
        </p>
      ) : null}
    </div>
  );
}
