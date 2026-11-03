// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
/**
 * "What changed since you last looked": counts within the caller's reach since their last visit (GET
 * /insights/changes), each linking to the rows behind it. Once shown, the visit is recorded (POST
 * /insights/changes/seen), so the next visit counts from now. Nothing is shown when nothing changed.
 */
import { useEffect, useState } from "react";
import { Link } from "react-router";
import { useTranslation } from "react-i18next";
import { savedViewsApi, type Changes } from "@/lib/insights/savedViews";

export default function WhatChanged() {
  const { t } = useTranslation();
  const [changes, setChanges] = useState<Changes | null>(null);

  useEffect(() => {
    let live = true;
    savedViewsApi
      .changes()
      .then((found) => {
        if (!live) return;
        setChanges(found);
        void savedViewsApi.seen().catch(() => undefined);
      })
      .catch(() => {
        if (live) setChanges(null);
      });
    return () => {
      live = false;
    };
  }, []);

  if (!changes || changes.items.length === 0) return null;
  const since = new Date(changes.since);
  const when = Number.isNaN(since.getTime()) ? changes.since : since.toLocaleString();
  return (
    <section
      aria-labelledby="insights-what-changed"
      className="rounded-lg border bg-muted/30 p-3"
      data-testid="insights-what-changed"
    >
      <h2 id="insights-what-changed" className="text-xs font-semibold">
        {changes.first_visit ? t("insights.changes.firstVisit") : t("insights.changes.title", { since: when })}
      </h2>
      <ul className="mt-1 flex flex-wrap gap-3 text-sm">
        {changes.items.map((item) => (
          <li key={item.key}>
            <Link to={item.link} className="text-primary hover:underline">
              {t(`insights.changes.items.${item.key}`, { count: item.count })}
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
