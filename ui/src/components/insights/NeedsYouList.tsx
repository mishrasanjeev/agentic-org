// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
import { Link } from "react-router";
import { useTranslation } from "react-i18next";
import type { NeedsYouItem } from "@/lib/insights/api";
import { formatAge, MISSING, toFiniteNumber } from "@/lib/insights/format";
import StateChip from "./StateChip";

/** Age text from whichever form the API sent: seconds, days, or a ready string. */
export function itemAge(item: NeedsYouItem): string | null {
  const seconds = toFiniteNumber(item.age_seconds);
  if (seconds !== null) return formatAge(seconds);
  const days = toFiniteNumber(item.age_days);
  if (days !== null) return formatAge(days * 86400);
  if (typeof item.age === "string" && item.age.trim()) return item.age;
  const n = toFiniteNumber(item.age);
  if (n !== null) return formatAge(n);
  return null;
}

/** Only in-app paths become links; anything else is ignored rather than followed. */
function safeInternalPath(link: string | null | undefined): string | null {
  if (typeof link !== "string") return null;
  return link.startsWith("/") && !link.startsWith("//") ? link : null;
}

/** The "needs you" list: oldest pending approvals, budgets at warning or breached, long-paused agents. */
export default function NeedsYouList({ items }: { items: NeedsYouItem[] }) {
  const { t } = useTranslation();
  return (
    <section aria-labelledby="insights-needs-you" className="rounded-lg border bg-background p-4" data-testid="needs-you">
      <h2 id="insights-needs-you" className="text-sm font-semibold">
        {t("insights.needsYou.title")}
      </h2>
      {items.length === 0 ? (
        <p className="mt-2 text-sm text-muted-foreground">{t("insights.needsYou.empty")}</p>
      ) : (
        <ul className="mt-2 divide-y">
          {items.map((item, i) => {
            const age = itemAge(item);
            const to = safeInternalPath(item.link);
            return (
              <li key={`${item.kind ?? "item"}-${i}`} className="flex flex-wrap items-center justify-between gap-2 py-2 text-sm">
                <div className="min-w-0">
                  <p className="truncate font-medium">{item.title || MISSING}</p>
                  {item.detail || age ? (
                    <p className="text-xs text-muted-foreground">
                      {[item.detail, age ? t("insights.needsYou.age", { age }) : null].filter(Boolean).join(" · ")}
                    </p>
                  ) : null}
                </div>
                <div className="flex items-center gap-2">
                  <StateChip state={item.state} />
                  {to ? (
                    <Link to={to} className="insights-target text-xs font-medium text-primary underline-offset-4 hover:underline">
                      {t("insights.needsYou.open")}
                    </Link>
                  ) : null}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
