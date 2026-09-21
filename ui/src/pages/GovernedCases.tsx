// SPDX-License-Identifier: Apache-2.0
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router";
import { Button } from "@/components/ui/button";
import ApprovalsSubnav from "@/components/governed-cases/ApprovalsSubnav";
import { RecommendationText, StateBadge, TierBadge } from "@/components/governed-cases/CaseBadges";
import {
  CASE_STATES,
  STATE_LABELS,
  describeCaseReason,
  formatTimestamp,
  governedCasesApi,
  toCaseApiError,
  type CaseApiError,
  type CaseState,
  type CaseSummary,
} from "@/lib/governedCases";

type Filter = CaseState | "all";

/** Governed case queue: the cases a human may need to act on, newest change first. */
export default function GovernedCases() {
  const [filter, setFilter] = useState<Filter>("awaiting_decision");
  const [cases, setCases] = useState<CaseSummary[]>([]);
  const [counts, setCounts] = useState<Partial<Record<CaseState, number>> | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<CaseApiError | null>(null);

  const load = useCallback(async (which: Filter) => {
    setLoading(true);
    setError(null);
    try {
      const [list, stats] = await Promise.all([
        governedCasesApi.list(which === "all" ? undefined : which),
        governedCasesApi.stats(),
      ]);
      setCases(list.cases);
      setCounts(stats.cases_by_state);
    } catch (e) {
      // Explicit error state: a failed load never renders as an empty queue.
      setCases([]);
      setCounts(null);
      setError(toCaseApiError(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(filter);
  }, [filter, load]);

  const total = counts ? Object.values(counts).reduce((sum, n) => sum + (n ?? 0), 0) : null;
  const filters: { value: Filter; label: string; count: number | null | undefined }[] = [
    ...CASE_STATES.map((state) => ({ value: state as Filter, label: STATE_LABELS[state], count: counts?.[state] })),
    { value: "all", label: "All", count: total },
  ];

  return (
    <div className="space-y-6">
      <ApprovalsSubnav />
      <div>
        <h1 className="text-2xl font-bold">Governed cases</h1>
        <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
          Business applications investigated by the reference agents. Agents only propose: every decision on a case needs
          a named person and a decision grant.
        </p>
      </div>

      <div role="group" aria-label="Filter cases by state" className="flex flex-wrap gap-2">
        {filters.map((option) => (
          <button
            key={option.value}
            type="button"
            aria-pressed={filter === option.value}
            onClick={() => setFilter(option.value)}
            className={`rounded-full border px-3 py-1 text-sm font-medium ${
              filter === option.value ? "border-primary bg-primary text-primary-foreground" : "border-border hover:bg-accent"
            }`}
          >
            {option.label}
            {typeof option.count === "number" ? ` (${option.count})` : ""}
          </button>
        ))}
      </div>

      {loading ? (
        <p className="text-muted-foreground" role="status">
          Loading cases…
        </p>
      ) : error ? (
        <div
          role="alert"
          className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800"
          data-testid="governed-cases-error"
        >
          <span>
            {describeCaseReason(error.reason)} <code className="text-xs">({error.reason})</code>
          </span>
          {error.reason !== "governed_cases_disabled" && (
            <Button variant="outline" size="sm" onClick={() => void load(filter)}>
              Retry
            </Button>
          )}
        </div>
      ) : cases.length === 0 ? (
        <p className="text-muted-foreground" data-testid="governed-cases-empty">
          {filter === "all" ? "No governed cases yet." : `No cases are ${STATE_LABELS[filter as CaseState].toLowerCase()}.`}
        </p>
      ) : (
        <div className="overflow-x-auto rounded-lg border">
          <table className="w-full min-w-[640px] text-left text-sm" data-testid="governed-cases-table">
            <caption className="sr-only">Governed cases</caption>
            <thead className="bg-muted/40">
              <tr>
                <th scope="col" className="px-4 py-2 font-medium">
                  Business
                </th>
                <th scope="col" className="px-4 py-2 font-medium">
                  State
                </th>
                <th scope="col" className="px-4 py-2 font-medium">
                  Policy tier
                </th>
                <th scope="col" className="px-4 py-2 font-medium">
                  Recommendation
                </th>
                <th scope="col" className="px-4 py-2 font-medium">
                  Updated
                </th>
              </tr>
            </thead>
            <tbody>
              {cases.map((item) => (
                <tr key={item.case_ref} className="border-t" data-testid="governed-case-row">
                  <td className="px-4 py-3">
                    <Link
                      to={`/dashboard/approvals/cases/${encodeURIComponent(item.case_ref)}`}
                      className="font-medium underline-offset-2 hover:underline"
                    >
                      {item.legal_name ?? item.case_ref}
                    </Link>
                    <div className="text-xs text-muted-foreground">
                      <code>{item.case_ref}</code>
                      {item.jurisdiction ? ` · ${item.jurisdiction}` : ""}
                    </div>
                    {item.failure_reason && (
                      <div className="text-xs text-red-800">Failed: {item.failure_reason}</div>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <StateBadge state={item.state} />
                  </td>
                  <td className="px-4 py-3">
                    <TierBadge tier={item.tier} />
                  </td>
                  <td className="px-4 py-3">
                    <RecommendationText recommendation={item.recommendation} />
                  </td>
                  <td className="whitespace-nowrap px-4 py-3">
                    {item.updated_at ? <time dateTime={item.updated_at}>{formatTimestamp(item.updated_at)}</time> : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
