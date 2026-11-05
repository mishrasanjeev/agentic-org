// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
/**
 * Saved views on a landing view or explorer: open one (its filters replace the page's), save the current filters
 * under a name (optionally shared with everyone who may open this view), or delete one's own.
 */
import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router";
import { useTranslation } from "react-i18next";
import { readErrorDetail } from "@/lib/insights/api";
import { filtersFromSearch, savedViewsApi, searchFromFilters, type SavedView } from "@/lib/insights/savedViews";

export default function SavedViews({ viewKey, keep = [] }: { viewKey: string; keep?: string[] }) {
  const { t } = useTranslation();
  const [searchParams, setSearchParams] = useSearchParams();
  const [views, setViews] = useState<SavedView[]>([]);
  const [saving, setSaving] = useState(false);
  const [name, setName] = useState("");
  const [shared, setShared] = useState(false);
  const [message, setMessage] = useState<{ text: string; error: boolean } | null>(null);

  const load = useCallback(() => {
    savedViewsApi
      .list(viewKey)
      .then(setViews)
      .catch(() => setViews([]));
  }, [viewKey]);

  useEffect(load, [load]);

  const open = (id: string) => {
    const view = views.find((v) => v.id === id);
    if (!view) return;
    const next = searchFromFilters(view.filters);
    // Parameters that pick the page itself (the landing view tab) stay as they are.
    for (const key of keep) {
      const current = searchParams.get(key);
      if (current !== null) next.set(key, current);
    }
    setSearchParams(next);
  };

  const save = async () => {
    setMessage(null);
    try {
      await savedViewsApi.create({ view_key: viewKey, name: name.trim(), filters: filtersFromSearch(searchParams), shared });
      setSaving(false);
      setName("");
      setShared(false);
      setMessage({ text: t("insights.saved.saved"), error: false });
      load();
    } catch (e) {
      setMessage({ text: (await readErrorDetail(e)) || t("insights.saved.failed"), error: true });
    }
  };

  const remove = async (view: SavedView) => {
    setMessage(null);
    try {
      await savedViewsApi.remove(view.id);
      load();
    } catch (e) {
      setMessage({ text: (await readErrorDetail(e)) || t("insights.saved.failed"), error: true });
    }
  };

  return (
    <div className="flex flex-wrap items-center gap-2" data-testid="saved-views">
      <label className="flex items-center gap-1 text-xs text-muted-foreground">
        {t("insights.saved.label")}
        <select
          className="insights-target h-8 rounded-md border bg-background px-2 text-xs text-foreground"
          value=""
          onChange={(e) => open(e.target.value)}
          aria-label={t("insights.saved.label")}
        >
          <option value="">{views.length ? t("insights.saved.choose") : t("insights.saved.none")}</option>
          {views.map((v) => (
            <option key={v.id} value={v.id}>
              {v.name}
              {v.shared ? ` · ${t("insights.saved.sharedTag")}` : ""}
            </option>
          ))}
        </select>
      </label>
      {saving ? (
        <form
          className="flex flex-wrap items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (name.trim()) void save();
          }}
        >
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            maxLength={120}
            placeholder={t("insights.saved.namePlaceholder")}
            aria-label={t("insights.saved.namePlaceholder")}
            className="insights-target h-8 rounded-md border bg-background px-2 text-xs"
          />
          <label className="flex items-center gap-1 text-xs">
            <input type="checkbox" checked={shared} onChange={(e) => setShared(e.target.checked)} />
            {t("insights.saved.share")}
          </label>
          <button type="submit" disabled={!name.trim()} className="h-8 rounded-md border px-3 text-xs hover:bg-muted">
            {t("insights.saved.save")}
          </button>
          <button type="button" onClick={() => setSaving(false)} className="h-8 px-2 text-xs text-muted-foreground">
            {t("insights.saved.cancel")}
          </button>
        </form>
      ) : (
        <button
          type="button"
          onClick={() => setSaving(true)}
          className="h-8 rounded-md border px-3 text-xs hover:bg-muted"
          data-testid="saved-views-save"
        >
          {t("insights.saved.saveCurrent")}
        </button>
      )}
      {views.some((v) => v.mine) ? (
        <details className="text-xs">
          <summary className="cursor-pointer text-muted-foreground">{t("insights.saved.manage")}</summary>
          <ul className="mt-1 flex flex-col gap-1">
            {views
              .filter((v) => v.mine)
              .map((v) => (
                <li key={v.id} className="flex items-center gap-2">
                  <span>{v.name}</span>
                  <button
                    type="button"
                    onClick={() => void remove(v)}
                    className="text-[hsl(var(--state-act))] hover:underline"
                    aria-label={t("insights.saved.deleteNamed", { name: v.name })}
                  >
                    {t("insights.saved.delete")}
                  </button>
                </li>
              ))}
          </ul>
        </details>
      ) : null}
      {message ? (
        <p role={message.error ? "alert" : "status"} className="text-xs text-muted-foreground">
          {message.text}
        </p>
      ) : null}
    </div>
  );
}
