// SPDX-License-Identifier: Apache-2.0
import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router";
import { Button } from "@/components/ui/button";
import ApprovalsSubnav from "@/components/governed-cases/ApprovalsSubnav";
import { StateBadge, TierBadge } from "@/components/governed-cases/CaseBadges";
import MemoView from "@/components/governed-cases/MemoView";
import PolicyScore from "@/components/governed-cases/PolicyScore";
import DecisionPanel from "@/components/governed-cases/DecisionPanel";
import ScreeningDispositions from "@/components/governed-cases/ScreeningDispositions";
import {
  STATE_LABELS,
  citationAnchors,
  describeCaseReason,
  formatTimestamp,
  governedCasesApi,
  toCaseApiError,
  type CaseApiError,
  type CaseDetail,
} from "@/lib/governedCases";
import { useDwellTimer } from "@/lib/useDwellTimer";

const NO_CITATIONS = { recordId: () => null, excerptId: () => null };

const NO_MEMO_MESSAGES: Record<string, string> = {
  submitted: "The case has been submitted and has not been investigated yet.",
  in_progress: "The agents are investigating this case. The memo appears here when they finish.",
  failed: "The investigation did not produce a memo.",
  withdrawn: "The case was withdrawn before a memo was produced.",
};

function CaseHistory({ detail }: { detail: CaseDetail }) {
  return (
    <section aria-labelledby="case-history-heading" className="rounded-lg border bg-background p-4 shadow-sm">
      <h2 id="case-history-heading" className="text-lg font-semibold">
        History
      </h2>
      {detail.transitions.length === 0 ? (
        <p className="mt-2 text-sm">No transitions recorded.</p>
      ) : (
        <ol className="mt-3 space-y-2 text-sm" data-testid="case-history">
          {detail.transitions.map((t, index) => (
            <li key={`${t.to_state}-${index}`} className="border-l-2 pl-3">
              <p>
                <span className="font-medium">{STATE_LABELS[t.to_state] ?? t.to_state}</span>{" "}
                <span className="text-muted-foreground">({t.reason})</span>
              </p>
              <p className="break-all text-xs text-muted-foreground">
                {t.actor} · {t.at ? <time dateTime={t.at}>{formatTimestamp(t.at)}</time> : "time not recorded"}
              </p>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

/** One governed case: the cited memo, the policy score and the case history. */
export default function GovernedCaseDetail() {
  const { caseRef = "" } = useParams<{ caseRef: string }>();
  const [detail, setDetail] = useState<CaseDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<CaseApiError | null>(null);
  // Render-to-submit dwell for the decision action; advisory telemetry only.
  const dwellMs = useDwellTimer();

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setDetail(await governedCasesApi.get(caseRef));
    } catch (e) {
      setDetail(null);
      setError(toCaseApiError(e));
    } finally {
      setLoading(false);
    }
  }, [caseRef]);

  useEffect(() => {
    void load();
  }, [load]);

  const backLink = (
    <Link to="/dashboard/approvals/cases" className="text-sm underline underline-offset-2">
      Back to governed cases
    </Link>
  );

  if (loading) {
    return (
      <div className="space-y-6">
        <ApprovalsSubnav />
        <p className="text-muted-foreground" role="status">
          Loading case…
        </p>
      </div>
    );
  }
  if (error || !detail) {
    return (
      <div className="space-y-6">
        <ApprovalsSubnav />
        {backLink}
        <div
          role="alert"
          className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800"
          data-testid="governed-case-error"
        >
          <span>
            {describeCaseReason(error?.reason ?? "request_failed")}{" "}
            <code className="text-xs">({error?.reason ?? "request_failed"})</code>
          </span>
          {error && !["governed_cases_disabled", "case_not_found"].includes(error.reason) && (
            <Button variant="outline" size="sm" onClick={() => void load()}>
              Retry
            </Button>
          )}
        </div>
      </div>
    );
  }

  const { case: businessCase, memo } = detail;
  const policy = detail.policy_result ?? memo?.policy_result ?? null;

  return (
    <div className="space-y-6">
      <ApprovalsSubnav />
      {backLink}

      <header className="space-y-2">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="break-words text-2xl font-bold">{businessCase.application.legal_name}</h1>
          <StateBadge state={businessCase.state} />
          {policy && <TierBadge tier={policy.tier} />}
        </div>
        <p className="break-all text-sm text-muted-foreground">
          <code>{businessCase.case_id}</code> · {businessCase.application.jurisdiction} · purpose{" "}
          <code>{businessCase.purpose}</code> · updated{" "}
          <time dateTime={businessCase.updated_at}>{formatTimestamp(businessCase.updated_at)}</time>
        </p>
      </header>

      {detail.failure_reason && (
        <p role="alert" className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
          The investigation failed: <code>{detail.failure_reason}</code>
        </p>
      )}

      <div className="grid gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <div className="min-w-0 space-y-6">
          {memo ? (
            <MemoView
              memo={memo}
              caseRef={businessCase.case_id}
              toolCalls={detail.tool_calls ?? []}
              excerpts={detail.excerpts ?? []}
            />
          ) : (
            <p className="rounded-lg border border-dashed px-4 py-3 text-sm" data-testid="memo-not-ready">
              {NO_MEMO_MESSAGES[businessCase.state] ?? "There is no memo for this case."}
            </p>
          )}
          {(detail.screening_dispositions.length > 0 || detail.screening_results.length > 0) && (
            <ScreeningDispositions
              caseRef={businessCase.case_id}
              caseState={businessCase.state}
              dispositions={detail.screening_dispositions}
              screeningResults={detail.screening_results}
              anchors={memo ? citationAnchors(memo) : NO_CITATIONS}
              onReviewed={() => void load()}
            />
          )}
        </div>
        <div className="min-w-0 space-y-6">
          <DecisionPanel
            caseRef={businessCase.case_id}
            caseState={businessCase.state}
            recommendation={memo?.recommendation.proposed ?? null}
            decision={businessCase.decision}
            storedRequests={detail.decision_requests ?? []}
            dwellMs={dwellMs}
            onDecided={() => void load()}
          />
          {policy ? (
            <PolicyScore result={policy} />
          ) : (
            <p className="rounded-lg border border-dashed px-4 py-3 text-sm">No policy result yet.</p>
          )}
          <CaseHistory detail={detail} />
        </div>
      </div>
    </div>
  );
}
