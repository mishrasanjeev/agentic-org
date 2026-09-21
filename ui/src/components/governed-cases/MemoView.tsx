// SPDX-License-Identifier: Apache-2.0
import EvidenceList, { type CitationAnchors } from "@/components/governed-cases/EvidenceList";
import { SectionStatusBadge, SeverityBadge } from "@/components/governed-cases/CaseBadges";
import {
  ERROR_REASON_MESSAGES,
  NOT_AVAILABLE_MESSAGES,
  RECOMMENDATION_LABELS,
  SECTION_LABELS,
  citationAnchors,
  citedRecords,
  formatTimestamp,
  type MemoSection,
  type UnderwritingMemo,
} from "@/lib/governedCases";

function SectionCard({ section, anchors }: { section: MemoSection; anchors: CitationAnchors }) {
  const headingId = `memo-section-${section.section_id}`;
  const title = SECTION_LABELS[section.section_id] ?? section.section_id;
  return (
    <section
      aria-labelledby={headingId}
      className="rounded-lg border bg-background p-4 shadow-sm"
      data-testid={`memo-section-${section.section_id}`}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 id={headingId} className="text-base font-semibold">
          {title}
        </h3>
        <SectionStatusBadge status={section.status} />
      </div>

      {section.status === "not_available" && (
        <p className="mt-3 rounded-md border border-dashed px-3 py-2 text-sm" data-testid="section-not-available">
          <strong>Not available.</strong>{" "}
          {NOT_AVAILABLE_MESSAGES[section.not_available_reason ?? ""] ??
            `This section could not be produced (${section.not_available_reason ?? "no reason given"}).`}{" "}
          Nothing in this section has been checked; treat it as missing evidence, not as a clear result.
        </p>
      )}
      {section.status === "error" && (
        <p className="mt-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800" role="note">
          <strong>Provider error.</strong>{" "}
          {ERROR_REASON_MESSAGES[section.error_reason ?? ""] ?? `The provider call failed (${section.error_reason ?? "unknown"}).`}{" "}
          Nothing in this section has been checked.
        </p>
      )}
      {section.status === "partial" && (
        <p className="mt-3 text-sm text-muted-foreground">Some of this section's data could not be produced.</p>
      )}

      {section.findings.length > 0 && (
        <div className="mt-4">
          <h4 className="text-sm font-semibold">Findings</h4>
          <ol className="mt-2 space-y-3">
            {section.findings.map((finding, index) => (
              <li key={`${finding.code}-${index}`} className="space-y-2" data-testid="memo-finding">
                <div className="flex flex-wrap items-center gap-2">
                  <SeverityBadge severity={finding.severity} />
                  <code className="text-xs">{finding.code}</code>
                </div>
                <p className="text-sm">{finding.statement}</p>
                <EvidenceList
                  evidence={finding.evidence}
                  anchors={anchors}
                  label={`Evidence for finding ${finding.code}`}
                />
              </li>
            ))}
          </ol>
        </div>
      )}
      {(section.status === "complete" || section.status === "partial") && section.findings.length === 0 && (
        <p className="mt-3 text-sm">No findings.</p>
      )}

      {section.evidence.length > 0 && (
        <div className="mt-4">
          <h4 className="text-sm font-semibold">Sources</h4>
          <div className="mt-2">
            <EvidenceList evidence={section.evidence} anchors={anchors} label={`Sources for ${title}`} />
          </div>
        </div>
      )}
    </section>
  );
}

/** The underwriting memo: recommendation, cited sections and the record index the citations link to. */
export default function MemoView({ memo }: { memo: UnderwritingMemo }) {
  const anchors = citationAnchors(memo);
  const records = citedRecords(memo);
  const confidence = memo.provenance.model_confidence;

  return (
    <div className="space-y-6">
      <section aria-labelledby="memo-recommendation" className="rounded-lg border bg-background p-4 shadow-sm">
        <h2 id="memo-recommendation" className="text-lg font-semibold">
          Recommendation
        </h2>
        <p className="mt-2 text-2xl font-bold" data-testid="memo-recommendation">
          {RECOMMENDATION_LABELS[memo.recommendation.proposed] ?? memo.recommendation.proposed}
        </p>
        <p className="mt-1 text-sm text-muted-foreground">
          Proposed from the policy result, not from model confidence. It is a proposal only and needs a human decision.
        </p>
        {memo.missing_items.length > 0 && (
          <div className="mt-4">
            <h3 className="text-sm font-semibold">Missing items</h3>
            <ul className="mt-2 list-disc space-y-1 pl-5 text-sm" data-testid="memo-missing-items">
              {memo.missing_items.map((item) => (
                <li key={item.item}>
                  <code className="text-xs">{item.item}</code>: {item.reason}
                </li>
              ))}
            </ul>
          </div>
        )}
      </section>

      <section aria-labelledby="memo-sections" className="space-y-4">
        <h2 id="memo-sections" className="text-lg font-semibold">
          Memo
        </h2>
        {memo.sections.map((section) => (
          <SectionCard key={section.section_id} section={section} anchors={anchors} />
        ))}
      </section>

      <section
        aria-labelledby="cited-records-heading"
        id="cited-records"
        className="rounded-lg border bg-background p-4 shadow-sm"
      >
        <h2 id="cited-records-heading" className="text-lg font-semibold">
          Cited records
        </h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Every upstream record the memo cites. Excerpt content is stored apart from the memo; the digest identifies the
          exact passage the provider returned.
        </p>
        {records.length === 0 ? (
          <p className="mt-3 text-sm">The memo cites no records.</p>
        ) : (
          <ul className="mt-3 space-y-3" data-testid="cited-records">
            {records.map((record) => (
              <li
                key={`${record.provider}-${record.record_id}`}
                id={anchors.recordId(record.provider, record.record_id) ?? undefined}
                tabIndex={-1}
                className="rounded-md border px-3 py-2 text-sm target:ring-2 target:ring-primary"
                data-testid="cited-record"
              >
                <p className="break-all">
                  <span className="font-semibold">{record.provider}</span>{" "}
                  <code className="font-mono text-xs">{record.record_id}</code>
                </p>
                <p className="mt-1 text-xs text-muted-foreground">
                  Fields: {record.fields.join(", ")} · Sections:{" "}
                  {record.sections.map((s) => SECTION_LABELS[s] ?? s).join(", ")} · Retrieved:{" "}
                  {record.retrieved_at.map((at) => formatTimestamp(at)).join(", ")}
                </p>
                {record.excerpts.length > 0 && (
                  <ul className="mt-2 space-y-1">
                    {record.excerpts.map((excerpt) => (
                      <li
                        key={excerpt.excerpt_ref}
                        id={anchors.excerptId(excerpt.excerpt_ref) ?? undefined}
                        tabIndex={-1}
                        className="break-all text-xs target:ring-2 target:ring-primary"
                        data-testid="memo-excerpt"
                      >
                        Excerpt <code className="font-mono">{excerpt.excerpt_ref}</code> ({excerpt.media_type}) digest{" "}
                        <code className="font-mono">{excerpt.sha256}</code>
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-labelledby="memo-provenance" className="rounded-lg border bg-background p-4 text-sm shadow-sm">
        <h2 id="memo-provenance" className="text-lg font-semibold">
          Provenance
        </h2>
        <dl className="mt-2 grid grid-cols-1 gap-x-4 gap-y-1 sm:grid-cols-[max-content_1fr]">
          <dt className="font-medium">Memo</dt>
          <dd className="break-all font-mono text-xs">
            {memo.memo_id} · created {formatTimestamp(memo.created_at)}
          </dd>
          <dt className="font-medium">Agent</dt>
          <dd>
            {memo.provenance.agent} {memo.provenance.agent_version} (prompt {memo.provenance.prompt_version})
          </dd>
          <dt className="font-medium">Model</dt>
          <dd className="break-all">{memo.provenance.model_id}</dd>
          <dt className="font-medium">Model confidence</dt>
          <dd>
            {typeof confidence === "number" ? confidence.toFixed(2) : "not reported"}{" "}
            <span className="text-muted-foreground">(metadata only; it gates nothing)</span>
          </dd>
        </dl>
      </section>
    </div>
  );
}
