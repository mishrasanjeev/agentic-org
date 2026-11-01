// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";

const KNOWN = new Set(["measured", "estimated", "seeded", "no_data", "mixed"]);

const CLASSES: Record<string, string> = {
  measured: "border-border text-muted-foreground",
  estimated: "border-dashed border-[hsl(var(--state-watch))] text-[hsl(var(--state-watch))]",
  seeded: "border-[hsl(var(--chart-3))] text-[hsl(var(--chart-3))]",
  no_data: "border-border bg-muted text-muted-foreground",
  mixed: "border-dashed border-border text-muted-foreground",
};

/** Says whether the numbers next to it were measured, estimated, seeded sample data, or absent. */
export default function DataQualityBadge({ quality, className }: { quality: string | null | undefined; className?: string }) {
  const { t } = useTranslation();
  const key = quality && KNOWN.has(quality) ? quality : "mixed";
  const label = t(`insights.quality.${key}`);
  return (
    <span
      data-testid="data-quality-badge"
      data-quality={key}
      title={t("insights.quality.label", { quality: label })}
      className={cn(
        "inline-flex items-center whitespace-nowrap rounded-full border px-2 py-0.5 text-[11px] font-medium leading-4",
        CLASSES[key],
        className,
      )}
    >
      <span className="sr-only">{t("insights.quality.prefix")} </span>
      {label}
    </span>
  );
}
