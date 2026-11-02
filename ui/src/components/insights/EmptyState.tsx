// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";

/**
 * Shown instead of a number or chart when a metric has no data. It always
 * says what is missing and, when the API sends one, how to populate it.
 */
export default function EmptyState({
  title,
  hint,
  action,
  compact = false,
  className,
}: {
  title?: string;
  hint?: string | null;
  action?: ReactNode;
  compact?: boolean;
  className?: string;
}) {
  const { t } = useTranslation();
  return (
    <div
      role="status"
      data-testid="insights-empty"
      className={cn(
        "flex flex-col items-start gap-1 rounded-md border border-dashed bg-muted/40 text-sm",
        compact ? "p-3" : "p-5",
        className,
      )}
    >
      <p className="font-medium text-foreground">{title || t("insights.empty.title")}</p>
      <p className="text-muted-foreground">{hint && hint.trim() ? hint : t("insights.empty.default")}</p>
      {action ? <div className="mt-2">{action}</div> : null}
    </div>
  );
}
