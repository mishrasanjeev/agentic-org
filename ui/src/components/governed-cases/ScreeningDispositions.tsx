// SPDX-License-Identifier: Apache-2.0
import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import EvidenceList, { type CitationAnchors } from "@/components/governed-cases/EvidenceList";
import { useAuth } from "@/contexts/AuthContext";
import {
  COMPARISON_LABELS,
  COMPARISON_RESULT_LABELS,
  OUTCOME_LABELS,
  describeCaseReason,
  formatTimestamp,
  governedCasesApi,
  toCaseApiError,
  type CaseState,
  type DispositionOutcome,
  type ScreeningDisposition,
  type ScreeningHit,
  type ScreeningResult,
} from "@/lib/governedCases";

const OUTCOMES: DispositionOutcome[] = ["true_match", "false_positive", "insufficient_information"];

const RESULT_VARIANTS: Record<string, "success" | "warning" | "destructive" | "secondary"> = {
  match: "destructive",
  partial_match: "warning",
  mismatch: "success",
  not_comparable: "secondary",
};

function hitFor(results: ScreeningResult[], hitId: string): { hit: ScreeningHit; result: ScreeningResult } | null {
  for (const result of results) {
    const hit = result.hits.find((h) => h.hit_id === hitId);
    if (hit) return { hit, result };
  }
  return null;
}

function ReviewRecord({ review }: { review: NonNullable<ScreeningDisposition["review"]> }) {
  return (
    <div className="rounded-md border bg-muted/40 px-3 py-2 text-sm" data-testid="disposition-review">
      <p>
        <span className="font-semibold">{review.action === "accepted" ? "Accepted" : "Overridden"}</span> as{" "}
        <span className="font-semibold">{OUTCOME_LABELS[review.final_outcome] ?? review.final_outcome}</span>
      </p>
      <p className="mt-1 break-all text-xs text-muted-foreground">
        {review.analyst_id} · <time dateTime={review.reviewed_at}>{formatTimestamp(review.reviewed_at)}</time>
      </p>
      {review.reason && (
        <p className="mt-2 whitespace-pre-wrap text-sm">
          <span className="font-medium">Reason: </span>
          {review.reason}
        </p>
      )}
      <p className="mt-2 text-xs text-muted-foreground">
        Recording a review does not close the hit in any system; closing it is your action in the system of record.
      </p>
    </div>
  );
}

function ReviewForm({
  caseRef,
  disposition,
  onReviewed,
}: {
  caseRef: string;
  disposition: ScreeningDisposition;
  onReviewed: () => void;
}) {
  const { user } = useAuth();
  const [action, setAction] = useState<"accepted" | "overridden">("accepted");
  const [outcome, setOutcome] = useState<DispositionOutcome>(
    OUTCOMES.find((o) => o !== disposition.proposed_outcome) ?? "true_match",
  );
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const formId = `review-${disposition.hit_id}`;
  const overriding = action === "overridden";
  const trimmedReason = reason.trim();

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    if (overriding && !trimmedReason) {
      setError("An override needs a written reason.");
      return;
    }
    setSubmitting(true);
    try {
      await governedCasesApi.reviewDisposition(caseRef, disposition.hit_id, {
        action,
        final_outcome: overriding ? outcome : disposition.proposed_outcome,
        ...(overriding ? { reason: trimmedReason } : {}),
      });
      onReviewed();
    } catch (e) {
      const refusal = toCaseApiError(e);
      setError(`${describeCaseReason(refusal.reason)} (${refusal.reason})`);
      // The case moved on (decided, withdrawn, re-investigated) or someone else
      // reviewed this hit: reload so the screen stops offering a review the API
      // will refuse.
      if (["transition_not_allowed", "already_reviewed", "case_version_conflict"].includes(refusal.reason)) {
        onReviewed();
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-3 rounded-md border px-3 py-3" data-testid="disposition-review-form">
      <fieldset className="space-y-2">
        <legend className="text-sm font-semibold">Your review</legend>
        <label className="flex items-start gap-2 text-sm">
          <input
            type="radio"
            name={formId}
            className="mt-1"
            checked={!overriding}
            onChange={() => setAction("accepted")}
          />
          <span>
            Accept the proposed outcome ({OUTCOME_LABELS[disposition.proposed_outcome] ?? disposition.proposed_outcome})
          </span>
        </label>
        <label className="flex items-start gap-2 text-sm">
          <input
            type="radio"
            name={formId}
            className="mt-1"
            checked={overriding}
            onChange={() => setAction("overridden")}
          />
          <span>Override it</span>
        </label>
      </fieldset>

      {overriding && (
        <div className="space-y-3">
          <div>
            <label htmlFor={`${formId}-outcome`} className="block text-sm font-medium">
              Outcome
            </label>
            <select
              id={`${formId}-outcome`}
              value={outcome}
              onChange={(e) => setOutcome(e.target.value as DispositionOutcome)}
              className="mt-1 w-full rounded-md border px-3 py-2 text-sm"
            >
              {OUTCOMES.filter((o) => o !== disposition.proposed_outcome).map((o) => (
                <option key={o} value={o}>
                  {OUTCOME_LABELS[o]}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label htmlFor={`${formId}-reason`} className="block text-sm font-medium">
              Reason for the override (required)
            </label>
            <textarea
              id={`${formId}-reason`}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              required
              maxLength={4000}
              rows={3}
              aria-describedby={`${formId}-reason-help`}
              className="mt-1 w-full rounded-md border px-3 py-2 text-sm"
            />
            <p id={`${formId}-reason-help`} className="mt-1 text-xs text-muted-foreground">
              Recorded with your identity on the case. {4000 - reason.length} characters left.
            </p>
          </div>
        </div>
      )}

      {error && (
        <p role="alert" className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
          {error}
        </p>
      )}

      <div className="flex flex-wrap items-center gap-3">
        <Button type="submit" size="sm" disabled={submitting || (overriding && !trimmedReason)}>
          {submitting ? "Recording…" : "Record review"}
        </Button>
        <span className="text-xs text-muted-foreground">
          Recorded as {user?.email ?? "your signed-in account"}; the server takes the analyst identity from your
          session, never from this page.
        </span>
      </div>
    </form>
  );
}

/**
 * The agent's proposed dispositions for each screening hit, with the
 * per-identifier comparison behind each one and the analyst's accept or
 * override. Nothing here closes a hit, in any configuration.
 */
export default function ScreeningDispositions({
  caseRef,
  caseState,
  dispositions,
  screeningResults,
  anchors,
  onReviewed,
}: {
  caseRef: string;
  caseState: CaseState;
  dispositions: ScreeningDisposition[];
  screeningResults: ScreeningResult[];
  anchors: CitationAnchors;
  onReviewed: () => void;
}) {
  const reviewable = caseState === "awaiting_decision";
  const outstanding = dispositions.filter((d) => d.review === null).length;

  return (
    <section
      aria-labelledby="screening-dispositions-heading"
      className="rounded-lg border bg-background p-4 shadow-sm"
      data-testid="screening-dispositions"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="screening-dispositions-heading" className="text-lg font-semibold">
          Screening dispositions
        </h2>
        <Badge variant={outstanding > 0 ? "warning" : "success"}>
          {outstanding > 0 ? `${outstanding} awaiting review` : "All reviewed"}
        </Badge>
      </div>
      <p className="mt-1 text-sm text-muted-foreground">
        Each hit is pre-analysed by the Screening Disposition agent. The proposal is a proposal: you accept it or
        override it with a reason, and nothing closes a hit automatically.
      </p>

      {dispositions.length === 0 ? (
        <p className="mt-3 text-sm" data-testid="no-dispositions">
          No screening hit on this case has a proposed disposition.
        </p>
      ) : (
        <ul className="mt-4 space-y-5">
          {dispositions.map((disposition) => {
            const context = hitFor(screeningResults, disposition.hit_id);
            return (
              <li
                key={disposition.disposition_id}
                className="rounded-lg border p-3"
                data-testid="screening-disposition"
                data-hit-id={disposition.hit_id}
              >
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div className="min-w-0">
                    <h3 className="break-words text-base font-semibold">
                      {context ? context.hit.matched_name : disposition.hit_id}
                    </h3>
                    <p className="break-all text-xs text-muted-foreground">
                      {context ? `${context.hit.list_type} · ${context.hit.source.name} · ` : ""}
                      screened subject {context ? context.result.subject.name : "unknown"} · hit{" "}
                      <code>{disposition.hit_id}</code>
                    </p>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <Badge variant="outline" data-testid="proposed-outcome">
                      Proposed: {OUTCOME_LABELS[disposition.proposed_outcome] ?? disposition.proposed_outcome}
                    </Badge>
                    <Badge variant="secondary">Confidence band: {disposition.confidence_band}</Badge>
                  </div>
                </div>

                <p className="mt-3 whitespace-pre-wrap text-sm">{disposition.rationale}</p>

                <div className="mt-3 overflow-x-auto">
                  <table className="w-full min-w-[520px] text-left text-sm" data-testid="comparison-table">
                    <caption className="sr-only">
                      Per-identifier comparison of the screened subject and the list entry
                    </caption>
                    <thead className="bg-muted/40">
                      <tr>
                        <th scope="col" className="px-2 py-1 font-medium">
                          Identifier
                        </th>
                        <th scope="col" className="px-2 py-1 font-medium">
                          Subject
                        </th>
                        <th scope="col" className="px-2 py-1 font-medium">
                          List entry
                        </th>
                        <th scope="col" className="px-2 py-1 font-medium">
                          Result
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {disposition.comparisons.map((comparison) => (
                        <tr key={comparison.identifier} className="border-t align-top">
                          <th scope="row" className="px-2 py-2 text-left font-medium">
                            {COMPARISON_LABELS[comparison.identifier] ?? comparison.identifier}
                          </th>
                          <td className="break-words px-2 py-2">{comparison.subject_value ?? "—"}</td>
                          <td className="break-words px-2 py-2">{comparison.hit_value ?? "—"}</td>
                          <td className="px-2 py-2">
                            <Badge variant={RESULT_VARIANTS[comparison.result] ?? "outline"}>
                              {COMPARISON_RESULT_LABELS[comparison.result] ?? comparison.result}
                            </Badge>
                            {comparison.note && <p className="mt-1 text-xs text-muted-foreground">{comparison.note}</p>}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                <div className="mt-3">
                  <h4 className="text-sm font-semibold">Evidence</h4>
                  <div className="mt-2">
                    <EvidenceList
                      evidence={disposition.evidence}
                      anchors={anchors}
                      label={`Evidence for the disposition of hit ${disposition.hit_id}`}
                    />
                  </div>
                </div>

                <div className="mt-3">
                  {disposition.review ? (
                    <ReviewRecord review={disposition.review} />
                  ) : reviewable ? (
                    <ReviewForm caseRef={caseRef} disposition={disposition} onReviewed={onReviewed} />
                  ) : (
                    <p className="rounded-md border border-dashed px-3 py-2 text-sm" data-testid="review-not-open">
                      Reviews are recorded only while the case is awaiting a decision.
                    </p>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
