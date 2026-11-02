// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import { normaliseState, STATE_CHIP_CLASSES } from "@/lib/insights/tokens";

/** OK / Watch / Act chip. Renders nothing when the metric has no threshold state. */
export default function StateChip({ state, className }: { state: string | null | undefined; className?: string }) {
  const { t } = useTranslation();
  const s = normaliseState(state);
  if (!s) return null;
  const label = t(`insights.state.${s}`);
  return (
    <span
      data-testid="state-chip"
      data-state={s}
      className={cn(
        "inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] font-semibold leading-4",
        STATE_CHIP_CLASSES[s],
        className,
      )}
    >
      <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-current" />
      <span className="sr-only">{t("insights.state.prefix")} </span>
      {label}
    </span>
  );
}
