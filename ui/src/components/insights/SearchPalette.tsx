// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
/**
 * Ctrl/Cmd-K on Insights pages: find an agent, workflow, connector, company, person or cost centre by name, or a
 * run by its id, among what the caller may open (GET /insights/search). Outside Insights the shortcut keeps
 * focusing the chat bar; here the palette takes it first.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router";
import { useTranslation } from "react-i18next";
import { entitiesApi, resultPath, type SearchResult } from "@/lib/insights/entities";

const DEBOUNCE_MS = 200;
const MIN_QUERY = 2;

export default function SearchPalette() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [active, setActive] = useState(0);
  const [state, setState] = useState<"idle" | "busy" | "error">("idle");
  const inputRef = useRef<HTMLInputElement>(null);

  // Capture phase on window: runs before the chat bar's own Ctrl/Cmd-K listener, which it then stops.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        e.stopImmediatePropagation();
        setOpen(true);
      }
    };
    window.addEventListener("keydown", onKey, { capture: true });
    return () => window.removeEventListener("keydown", onKey, { capture: true });
  }, []);

  useEffect(() => {
    if (open) inputRef.current?.focus();
    else {
      setQuery("");
      setResults([]);
      setActive(0);
    }
  }, [open]);

  useEffect(() => {
    if (!open || query.trim().length < MIN_QUERY) {
      setResults([]);
      setState("idle");
      return;
    }
    let live = true;
    setState("busy");
    const timer = window.setTimeout(() => {
      entitiesApi
        .search(query.trim())
        .then((found) => {
          if (!live) return;
          setResults(found);
          setActive(0);
          setState("idle");
        })
        .catch(() => {
          if (live) setState("error");
        });
    }, DEBOUNCE_MS);
    return () => {
      live = false;
      window.clearTimeout(timer);
    };
  }, [open, query]);

  const go = useCallback(
    (result: SearchResult) => {
      setOpen(false);
      navigate(resultPath(result));
    },
    [navigate],
  );

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="rounded-md border px-3 py-1.5 text-xs text-muted-foreground hover:bg-muted"
        data-testid="insights-search-open"
      >
        {t("insights.search.open")} <kbd className="ml-1 rounded border px-1 text-[10px]">Ctrl K</kbd>
      </button>
    );
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={t("insights.search.title")}
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/40 p-4 pt-24"
      onClick={() => setOpen(false)}
    >
      <div className="w-full max-w-lg rounded-lg border bg-background shadow-lg" onClick={(e) => e.stopPropagation()}>
        <input
          ref={inputRef}
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Escape") setOpen(false);
            else if (e.key === "ArrowDown") {
              e.preventDefault();
              setActive((i) => Math.min(i + 1, Math.max(results.length - 1, 0)));
            } else if (e.key === "ArrowUp") {
              e.preventDefault();
              setActive((i) => Math.max(i - 1, 0));
            } else if (e.key === "Enter" && results[active]) {
              e.preventDefault();
              go(results[active]);
            }
          }}
          placeholder={t("insights.search.placeholder")}
          aria-label={t("insights.search.placeholder")}
          aria-controls="insights-search-results"
          className="w-full rounded-t-lg border-b bg-transparent px-4 py-3 text-sm outline-none"
        />
        <ul id="insights-search-results" role="listbox" className="max-h-80 overflow-y-auto py-1">
          {query.trim().length < MIN_QUERY ? (
            <li className="px-4 py-2 text-xs text-muted-foreground">{t("insights.search.hint")}</li>
          ) : state === "error" ? (
            <li role="alert" className="px-4 py-2 text-xs text-destructive">
              {t("insights.search.error")}
            </li>
          ) : results.length === 0 && state === "idle" ? (
            <li className="px-4 py-2 text-xs text-muted-foreground">{t("insights.search.none")}</li>
          ) : (
            results.map((result, i) => (
              <li
                key={`${result.kind}-${result.id}`}
                role="option"
                aria-selected={i === active}
                onMouseEnter={() => setActive(i)}
                onClick={() => go(result)}
                className={`flex cursor-pointer items-center justify-between gap-3 px-4 py-2 text-sm ${
                  i === active ? "bg-muted" : ""
                }`}
              >
                <span className="truncate">{result.title}</span>
                <span className="shrink-0 text-xs text-muted-foreground">
                  {t(`insights.search.kinds.${result.kind}`)}
                </span>
              </li>
            ))
          )}
        </ul>
      </div>
    </div>
  );
}
