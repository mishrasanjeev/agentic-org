// SPDX-License-Identifier: Apache-2.0
import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import api, { extractApiError } from "@/lib/api";

/**
 * Workbench search: cases, documents, customers and accounts in one query. Words and quoted phrases
 * must all match, a leading minus excludes, and the facets beside the results narrow a kind by the
 * values present. The results region announces its count; every control is labelled and reachable
 * by keyboard.
 */

export interface Hit {
  kind: string;
  id: string;
  title: string;
  subtitle: string;
  snippet: string;
  path: string;
  facets: Record<string, string | string[]>;
  updated_at: string | null;
}

export interface SearchResponse {
  query: { must: string[]; must_not: string[] };
  kinds: string[];
  hits: Hit[];
  counts: Record<string, number>;
  facets: Record<string, Record<string, Record<string, number>>>;
  total: number;
  allowed_kinds: string[];
  filters: Record<string, string[]>;
}

export const KIND_LABELS: Record<string, string> = { case: "Cases", document: "Documents", customer: "Customers", account: "Accounts" };
export const FACET_LABELS: Record<string, string> = {
  state: "Case state",
  purpose: "Purpose",
  provider: "Provider",
  status: "Document status",
  document_type: "Document type",
  industry: "Industry",
  state_code: "State",
  active: "Active",
};

/** The query parameters for a search: the text, the kinds and each facet value as a repeated parameter. */
export function buildParams(q: string, kinds: string[], filters: Record<string, string[]>): URLSearchParams {
  const params = new URLSearchParams();
  if (q.trim()) params.set("q", q.trim());
  for (const kind of kinds) params.append("kind", kind);
  for (const [name, values] of Object.entries(filters)) for (const value of values) params.append(name, value);
  params.set("limit", "50");
  return params;
}

export function toggle(values: string[], value: string): string[] {
  return values.includes(value) ? values.filter((v) => v !== value) : [...values, value];
}

export default function WorkbenchSearch() {
  const [q, setQ] = useState("");
  const [kinds, setKinds] = useState<string[]>([]);
  const [filters, setFilters] = useState<Record<string, string[]>>({});
  const [result, setResult] = useState<SearchResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const seq = useRef(0);

  const run = useCallback(
    async (text: string, wantedKinds: string[], wantedFilters: Record<string, string[]>) => {
      const hasFilter = Object.values(wantedFilters).some((v) => v.length > 0);
      if (!text.trim() && !hasFilter) {
        setResult(null);
        return;
      }
      const mine = ++seq.current;
      setBusy(true);
      setError(null);
      try {
        const { data } = await api.get(`/workbench/search?${buildParams(text, wantedKinds, wantedFilters).toString()}`);
        if (mine !== seq.current) return;
        setResult(data as SearchResponse);
      } catch (err) {
        if (mine !== seq.current) return;
        setResult(null);
        setError(extractApiError(err, "The search failed."));
      } finally {
        if (mine === seq.current) setBusy(false);
      }
    },
    [],
  );

  useEffect(() => {
    void run(q, kinds, filters);
    // the query text runs on submit; kinds and facets re-run at once
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kinds, filters]);

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    void run(q, kinds, filters);
  };

  const facetGroups = result ? Object.entries(result.facets) : [];

  return (
    <div className="grid gap-4 lg:grid-cols-12" data-testid="workbench-search">
      <form className="space-y-3 lg:col-span-3" onSubmit={submit} role="search" aria-label="Workbench search">
        <label className="block text-sm text-slate-700" htmlFor="workbench-search-q">
          Search cases, documents, customers and accounts
        </label>
        <input
          id="workbench-search-q"
          className="w-full rounded-md border border-slate-300 px-2 py-1 text-sm"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder='words, "a phrase", -excluded'
          aria-describedby="workbench-search-hint"
        />
        <p id="workbench-search-hint" className="text-xs text-slate-500">
          Every word must match; quote a phrase; a leading minus excludes a word.
        </p>
        <button type="submit" className="rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50" disabled={busy}>
          Search
        </button>
        <fieldset className="space-y-1">
          <legend className="text-sm font-medium text-slate-800">Kinds</legend>
          {(result?.allowed_kinds || Object.keys(KIND_LABELS)).map((kind) => (
            <label key={kind} className="flex items-center gap-2 text-sm text-slate-700">
              <input type="checkbox" checked={kinds.length === 0 || kinds.includes(kind)} onChange={() => setKinds((prev) => (prev.length === 0 ? Object.keys(KIND_LABELS).filter((k) => k !== kind) : toggle(prev, kind)))} data-testid={`search-kind-${kind}`} />
              {KIND_LABELS[kind] || kind}
              {result?.counts[kind] !== undefined ? <span className="text-xs text-slate-500">({result.counts[kind]})</span> : null}
            </label>
          ))}
        </fieldset>
        {facetGroups.map(([kind, facets]) => (
          <div key={kind} className="space-y-2">
            {Object.entries(facets).map(([facet, values]) => (
              <fieldset key={`${kind}:${facet}`} className="space-y-1" data-testid={`search-facet-${facet}`}>
                <legend className="text-sm font-medium text-slate-800">
                  {FACET_LABELS[facet] || facet} <span className="text-xs font-normal text-slate-500">({KIND_LABELS[kind] || kind})</span>
                </legend>
                {Object.entries(values).map(([value, count]) => (
                  <label key={value} className="flex items-center gap-2 text-sm text-slate-700">
                    <input type="checkbox" checked={(filters[facet] || []).includes(value)} onChange={() => setFilters((prev) => ({ ...prev, [facet]: toggle(prev[facet] || [], value) }))} />
                    {value || "(none)"} <span className="text-xs text-slate-500">{count}</span>
                  </label>
                ))}
              </fieldset>
            ))}
          </div>
        ))}
      </form>
      <section className="lg:col-span-9" aria-label="Search results">
        {error && (
          <div role="alert" className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
            {error}
          </div>
        )}
        <p className="text-sm text-slate-600" aria-live="polite" data-testid="search-count">
          {busy ? "Searching…" : result ? `${result.total} result${result.total === 1 ? "" : "s"}` : "Enter a query to search."}
        </p>
        {result && result.hits.length > 0 && (
          <ul className="mt-2 divide-y divide-slate-200 rounded-md border border-slate-200">
            {result.hits.map((hit) => (
              <li key={`${hit.kind}:${hit.id}`} className="px-3 py-2 text-sm" data-testid="search-hit">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="rounded bg-slate-100 px-1.5 text-xs uppercase text-slate-600">{KIND_LABELS[hit.kind]?.replace(/s$/, "") || hit.kind}</span>
                  <Link to={hit.path} className="font-medium text-indigo-700 hover:underline">
                    {hit.title}
                  </Link>
                  <span className="text-xs text-slate-500">{hit.subtitle}</span>
                </div>
                <p className="mt-1 text-xs text-slate-600">{hit.snippet}</p>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
