// SPDX-License-Identifier: Apache-2.0
import { formatTimestamp, type Evidence } from "@/lib/governedCases";

export interface CitationAnchors {
  /** The element id of the cited record on this page, or null when it is not indexed here. */
  recordId: (provider: string, recordId: string) => string | null;
  excerptId: (ref: string) => string | null;
}

/**
 * The upstream records behind an assertion. Each entry links to the cited
 * record in the memo's record index and, when the provider attached one, to
 * the excerpt reference. Provider values are rendered as text, never as markup.
 */
export default function EvidenceList({
  evidence,
  anchors,
  label,
}: {
  evidence: Evidence[];
  anchors: CitationAnchors;
  label: string;
}) {
  if (evidence.length === 0) {
    return <p className="text-sm text-muted-foreground">No evidence cited.</p>;
  }
  return (
    <ul aria-label={label} className="space-y-1.5" data-testid="evidence-list">
      {evidence.map((entry, index) => {
        const excerptAnchor = entry.excerpt_ref ? anchors.excerptId(entry.excerpt_ref) : null;
        const recordAnchor = anchors.recordId(entry.provider, entry.record_id);
        return (
          <li
            key={`${entry.provider}-${entry.record_id}-${entry.field}-${index}`}
            className="rounded-md border border-border bg-muted/40 px-3 py-2 text-xs leading-relaxed"
            data-testid="evidence-entry"
          >
            <span className="font-semibold">{entry.provider}</span>
            <span aria-hidden="true"> · </span>
            {recordAnchor ? (
              <a
                href={`#${recordAnchor}`}
                className="break-all font-mono underline underline-offset-2"
                aria-label={`Cited record ${entry.record_id} from ${entry.provider}`}
              >
                {entry.record_id}
              </a>
            ) : (
              <code className="break-all font-mono">{entry.record_id}</code>
            )}
            <span aria-hidden="true"> · </span>
            <span>
              field <code className="break-all font-mono">{entry.field}</code>
            </span>
            <span aria-hidden="true"> · </span>
            <span>
              retrieved <time dateTime={entry.retrieved_at}>{formatTimestamp(entry.retrieved_at)}</time>
            </span>
            {entry.excerpt_ref && (
              <>
                <span aria-hidden="true"> · </span>
                {excerptAnchor ? (
                  <a
                    href={`#${excerptAnchor}`}
                    className="break-all font-mono underline underline-offset-2"
                    aria-label={`Excerpt ${entry.excerpt_ref}`}
                  >
                    excerpt {entry.excerpt_ref}
                  </a>
                ) : (
                  <span className="break-all font-mono">excerpt {entry.excerpt_ref} (not attached to this memo)</span>
                )}
              </>
            )}
          </li>
        );
      })}
    </ul>
  );
}
