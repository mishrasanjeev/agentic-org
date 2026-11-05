// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
import { Link } from "react-router";
import { useTranslation } from "react-i18next";

export interface Crumb {
  label: string;
  /** Omitted for the current page (the last crumb). */
  to?: string;
}

/** The drill path on Insights pages: Insights › explorer › entity or run. The last crumb is the current page. */
export default function Breadcrumbs({ items }: { items: Crumb[] }) {
  const { t } = useTranslation();
  const all: Crumb[] = [{ label: t("insights.title"), to: "/dashboard/insights" }, ...items];
  return (
    <nav aria-label={t("insights.breadcrumbs")} className="text-xs text-muted-foreground">
      <ol className="flex flex-wrap items-center gap-1">
        {all.map((crumb, i) => {
          const last = i === all.length - 1;
          return (
            <li key={`${crumb.label}-${i}`} className="flex items-center gap-1">
              {crumb.to && !last ? (
                <Link to={crumb.to} className="hover:text-foreground hover:underline">
                  {crumb.label}
                </Link>
              ) : (
                <span aria-current={last ? "page" : undefined} className={last ? "text-foreground" : undefined}>
                  {crumb.label}
                </span>
              )}
              {last ? null : <span aria-hidden="true">›</span>}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
