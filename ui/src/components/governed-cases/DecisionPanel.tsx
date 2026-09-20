// SPDX-License-Identifier: Apache-2.0
import { useCallback, useEffect, useRef, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DECISION_STATUS_LABELS,
  RECOMMENDATION_LABELS,
  describeCaseReason,
  formatDwell,
  formatTimestamp,
  governedCasesApi,
  isSafeApprovalPage,
  toCaseApiError,
  type CaseDecision,
  type CaseState,
  type DecisionRequestView,
  type Recommendation,
  type StoredDecisionRequest,
} from "@/lib/governedCases";

const POLL_MS = 5000;

type Outcome = "approve" | "decline";

const OUTCOME_LABELS: Record<Outcome, string> = { approve: "Approve", decline: "Decline" };

/** True when the person is asking for something other than what the memo proposed. */
function isOverride(outcome: Outcome, recommendation: Recommendation | null): boolean {
  if (!recommendation) return true;
  return recommendation !== outcome;
}

function DecisionRecord({ decision }: { decision: CaseDecision }) {
  return (
    <div className="rounded-md border bg-muted/40 px-3 py-2 text-sm" data-testid="case-decision">
      <p>
        <span className="font-semibold">{OUTCOME_LABELS[decision.outcome] ?? decision.outcome}</span> recorded{" "}
        <time dateTime={decision.decided_at}>{formatTimestamp(decision.decided_at)}</time>
      </p>
      <ul className="mt-2 space-y-1 text-xs">
        {decision.approvers.map((approver) => (
          <li key={approver.decision_grant_id} className="break-all">
            {approver.approver} · decision grant <code>{approver.decision_grant_id}</code>
          </li>
        ))}
      </ul>
    </div>
  );
}

function ApprovalList({ request }: { request: DecisionRequestView }) {
  const waitingFor = request.approvals_required - request.approvals_received;
  return (
    <div className="mt-3" data-testid="decision-approvals">
      <h4 className="text-sm font-semibold">
        {request.approvals_received} of {request.approvals_required} approvals
      </h4>
      {request.approvals.length === 0 ? (
        <p className="mt-1 text-sm text-muted-foreground">
          Nobody has approved yet. The approval happens on the approval page, not here.
        </p>
      ) : (
        <ol className="mt-2 space-y-2 text-sm">
          {request.approvals.map((approval) => (
            <li key={`${approval.approver}-${approval.position}`} className="rounded-md border px-3 py-2">
              <p className="break-all font-medium">
                {approval.position === 1 ? "First approver" : `Approver ${approval.position}`}:{" "}
                {approval.approver || "identity withheld"}
              </p>
              <p className="mt-1 text-xs text-muted-foreground">
                {approval.approver_auth || "authentication not reported"} · looked at it for{" "}
                {formatDwell(approval.dwell_ms)}{" "}
                {approval.dwell_source === "server" ? "(measured by the approval page)" : `(${approval.dwell_source})`}{" "}
                · <time dateTime={approval.issued_at}>{formatTimestamp(approval.issued_at)}</time>
              </p>
            </li>
          ))}
        </ol>
      )}
      {waitingFor > 0 && request.approvals_received > 0 && (
        <p className="mt-2 rounded-md border border-yellow-300 bg-yellow-50 px-3 py-2 text-sm text-yellow-900" data-testid="four-eyes-waiting">
          Waiting for a second approver. It cannot be{" "}
          <span className="break-all font-medium">{request.approvals[0]?.approver || "the first approver"}</span>: the
          approval page refuses the same person twice.
        </p>
      )}
    </div>
  );
}

/**
 * The decision action. This console cannot approve anything: it asks the
 * decision-grant issuer for a decision, sends the approver to the issuer's own
 * approval page (sign-in, step-up, dwell measurement and four eyes all happen
 * there), shows the approvals as they arrive and records the decision once the
 * grants exist.
 */
export default function DecisionPanel({
  caseRef,
  caseState,
  recommendation,
  decision,
  storedRequests,
  dwellMs,
  onDecided,
}: {
  caseRef: string;
  caseState: CaseState;
  recommendation: Recommendation | null;
  decision: CaseDecision | null;
  storedRequests: StoredDecisionRequest[];
  /** Milliseconds since the case screen rendered. Advisory telemetry only. */
  dwellMs: () => number;
  onDecided: () => void;
}) {
  const latest = storedRequests.length > 0 ? storedRequests[storedRequests.length - 1] : null;
  const [outcome, setOutcome] = useState<Outcome>(recommendation === "decline" ? "decline" : "approve");
  const [reason, setReason] = useState("");
  const [request, setRequest] = useState<DecisionRequestView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"" | "requesting" | "recording">("");
  const requestId = request?.request_id ?? latest?.request_id ?? "";
  const pollRef = useRef<number | null>(null);

  const refresh = useCallback(async () => {
    if (!requestId) return;
    try {
      setRequest(await governedCasesApi.decisionRequest(caseRef, requestId));
    } catch (e) {
      const refusal = toCaseApiError(e);
      setError(`${describeCaseReason(refusal.reason)} (${refusal.reason})`);
    }
  }, [caseRef, requestId]);

  // Poll while an approval is outstanding; stop as soon as the grants exist.
  useEffect(() => {
    if (!requestId) return;
    void refresh();
    const tick = () => {
      if (typeof document !== "undefined" && document.hidden) return;
      void refresh();
    };
    pollRef.current = window.setInterval(tick, POLL_MS);
    return () => {
      if (pollRef.current !== null) window.clearInterval(pollRef.current);
    };
  }, [requestId, refresh]);

  useEffect(() => {
    if (request?.grants_ready && pollRef.current !== null) {
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, [request?.grants_ready]);

  if (caseState === "decided" && decision) {
    return (
      <section aria-labelledby="decision-heading" className="rounded-lg border bg-background p-4 shadow-sm">
        <h2 id="decision-heading" className="text-lg font-semibold">
          Decision
        </h2>
        <div className="mt-3">
          <DecisionRecord decision={decision} />
        </div>
      </section>
    );
  }

  if (caseState !== "awaiting_decision") {
    return (
      <section aria-labelledby="decision-heading" className="rounded-lg border bg-background p-4 shadow-sm">
        <h2 id="decision-heading" className="text-lg font-semibold">
          Decision
        </h2>
        <p className="mt-2 text-sm" data-testid="decision-not-open">
          A decision can only be taken while the case is awaiting one.
        </p>
      </section>
    );
  }

  const overriding = isOverride(outcome, recommendation);
  const trimmedReason = reason.trim();
  const canRequest = !overriding || trimmedReason.length > 0;

  async function ask(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    if (!canRequest) {
      setError("A decision that differs from the recommendation needs a written reason.");
      return;
    }
    setBusy("requesting");
    try {
      const created = await governedCasesApi.requestDecision(caseRef, {
        outcome,
        ...(overriding ? { override_reason: trimmedReason } : {}),
        client_dwell_ms: dwellMs(),
      });
      setRequest(created);
    } catch (e) {
      const refusal = toCaseApiError(e);
      setError(`${describeCaseReason(refusal.reason)} (${refusal.reason})`);
    } finally {
      setBusy("");
    }
  }

  async function record() {
    if (!request?.request_id) return;
    setError(null);
    setBusy("recording");
    try {
      await governedCasesApi.recordDecision(caseRef, {
        outcome: (request.outcome ?? outcome) as Outcome,
        decision_request_id: request.request_id,
        client_dwell_ms: dwellMs(),
      });
      onDecided();
    } catch (e) {
      const refusal = toCaseApiError(e);
      const code = refusal.detail && refusal.reason === "decision_invalid" ? refusal.detail : refusal.reason;
      setError(`${describeCaseReason(code)} (${code})`);
      void refresh();
    } finally {
      setBusy("");
    }
  }

  function openApprovalPage() {
    const url = request?.approval_page ?? "";
    if (!isSafeApprovalPage(url)) {
      setError("The issuer did not return a usable approval page address.");
      return;
    }
    window.open(url, "_blank", "noopener,noreferrer");
  }

  return (
    <section
      aria-labelledby="decision-heading"
      className="rounded-lg border bg-background p-4 shadow-sm"
      data-testid="decision-panel"
    >
      <h2 id="decision-heading" className="text-lg font-semibold">
        Decision
      </h2>
      <p className="mt-1 text-sm text-muted-foreground">
        This console cannot approve anything. Asking for a decision creates a request at the decision-grant issuer; a
        named person signs in on the issuer's own approval page, steps up, reads the memo and the policy score and
        approves there. Only then can the decision be recorded here.
      </p>

      {!request ? (
        <form onSubmit={ask} className="mt-4 space-y-3" data-testid="decision-request-form">
          <fieldset className="space-y-2">
            <legend className="text-sm font-semibold">
              Decision to ask for
              {recommendation && (
                <span className="ml-1 font-normal text-muted-foreground">
                  (the memo proposes {RECOMMENDATION_LABELS[recommendation] ?? recommendation})
                </span>
              )}
            </legend>
            {(["approve", "decline"] as Outcome[]).map((value) => (
              <label key={value} className="flex items-center gap-2 text-sm">
                <input
                  type="radio"
                  name="decision-outcome"
                  value={value}
                  checked={outcome === value}
                  onChange={() => setOutcome(value)}
                />
                <span>{OUTCOME_LABELS[value]}</span>
              </label>
            ))}
          </fieldset>

          {overriding && (
            <div>
              <label htmlFor="decision-override-reason" className="block text-sm font-medium">
                Reason for a decision other than the recommendation (required)
              </label>
              <textarea
                id="decision-override-reason"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                required
                rows={3}
                maxLength={4000}
                aria-describedby="decision-override-help"
                className="mt-1 w-full rounded-md border px-3 py-2 text-sm"
              />
              <p id="decision-override-help" className="mt-1 text-xs text-muted-foreground">
                Shown to the approver on the approval page and recorded on the case.
              </p>
            </div>
          )}

          {error && (
            <p role="alert" className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
              {error}
            </p>
          )}

          <Button type="submit" disabled={busy !== "" || !canRequest}>
            {busy === "requesting" ? "Requesting…" : "Request decision"}
          </Button>
        </form>
      ) : (
        <div className="mt-4 space-y-3" data-testid="decision-request">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant={request.grants_ready ? "success" : "warning"} data-testid="decision-status">
              {DECISION_STATUS_LABELS[request.status] ?? request.status}
            </Badge>
            <Badge variant="outline">
              {OUTCOME_LABELS[(request.outcome ?? outcome) as Outcome]} ·{" "}
              {request.approvals_required === 2 ? "two approvers required" : "one approver required"}
            </Badge>
          </div>
          <dl className="grid grid-cols-1 gap-x-4 gap-y-1 text-xs sm:grid-cols-[max-content_1fr]">
            <dt className="font-medium">Request</dt>
            <dd className="break-all font-mono">{request.request_id}</dd>
            <dt className="font-medium">Action</dt>
            <dd className="break-all font-mono">
              {request.action.action ?? "case_decision"} · {request.action.decision ?? ""} ·{" "}
              {request.action.subject ?? ""}
            </dd>
            <dt className="font-medium">Bound to case version</dt>
            <dd className="font-mono">{request.case_version}</dd>
            <dt className="font-medium">Expires</dt>
            <dd>{formatTimestamp(request.expires_at)}</dd>
          </dl>

          {request.override_reason && (
            <p className="text-sm">
              <span className="font-medium">Reason given: </span>
              {request.override_reason}
            </p>
          )}

          <Button type="button" variant="outline" onClick={openApprovalPage} data-testid="open-approval-page">
            Open approval page
          </Button>

          <ApprovalList request={request} />

          {request.case_changed && (
            <p role="alert" className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800" data-testid="decision-case-changed">
              The case changed after this request was made (now version {request.case_version_now}). These decision
              grants no longer apply; ask for a decision again.
            </p>
          )}

          {error && (
            <p role="alert" className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
              {error}
            </p>
          )}

          <div className="flex flex-wrap items-center gap-3">
            <Button
              type="button"
              onClick={record}
              disabled={!request.grants_ready || busy !== "" || request.case_changed === true}
              data-testid="record-decision"
            >
              {busy === "recording" ? "Recording…" : "Record decision"}
            </Button>
            {!request.grants_ready && (
              <span className="text-xs text-muted-foreground">
                The decision can be recorded only once the approvals are complete on the approval page.
              </span>
            )}
            <Button type="button" variant="ghost" size="sm" onClick={() => void refresh()}>
              Refresh status
            </Button>
          </div>
        </div>
      )}
    </section>
  );
}
