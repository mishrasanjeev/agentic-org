// SPDX-License-Identifier: Apache-2.0
import { TierBadge } from "@/components/governed-cases/CaseBadges";
import type { PolicyResult } from "@/lib/governedCases";

function formatInput(value: string | number | boolean | null): string {
  if (value === null) return "missing";
  return String(value);
}

/** The deterministic policy result: version, score, tier and every fired rule with the inputs it read. */
export default function PolicyScore({ result }: { result: PolicyResult }) {
  return (
    <section
      aria-labelledby="policy-score-heading"
      className="rounded-lg border bg-background p-4 shadow-sm"
      data-testid="policy-score"
    >
      <h2 id="policy-score-heading" className="text-lg font-semibold">
        Policy score
      </h2>
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <span className="text-3xl font-bold" data-testid="policy-score-value">
          {result.score}
        </span>
        <TierBadge tier={result.tier} />
      </div>
      <dl className="mt-3 grid grid-cols-1 gap-x-4 gap-y-1 text-sm sm:grid-cols-[max-content_1fr]">
        <dt className="font-medium">Policy</dt>
        <dd className="break-all">
          <code className="text-xs">{result.policy.policy_id}</code> version {result.policy.version}
        </dd>
        <dt className="font-medium">Reviewed by</dt>
        <dd>{result.policy.reviewed_by ?? "not reviewed"}</dd>
        <dt className="font-medium">Inputs digest</dt>
        <dd className="break-all font-mono text-xs">{result.inputs_digest}</dd>
      </dl>
      {result.policy.example && (
        <p
          className="mt-3 rounded-md border border-yellow-300 bg-yellow-50 px-3 py-2 text-sm text-yellow-900"
          role="note"
          data-testid="policy-example-warning"
        >
          This is a shipped example policy. It has not been reviewed by a compliance owner and must not be used for real
          decisions.
        </p>
      )}

      <h3 className="mt-4 text-sm font-semibold">Fired rules, in evaluation order</h3>
      {result.reasons.length === 0 ? (
        <p className="mt-2 text-sm">No rules fired.</p>
      ) : (
        <ol className="mt-2 space-y-3" data-testid="policy-fired-rules">
          {result.reasons.map((rule, index) => {
            const inputs = Object.entries(rule.inputs);
            return (
              <li key={`${rule.rule_id}-${index}`} className="rounded-md border px-3 py-2 text-sm" data-testid="policy-rule">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <code className="break-all text-xs font-semibold">{rule.rule_id}</code>
                  <TierBadge tier={rule.tier} />
                </div>
                <p className="mt-1">{rule.reason}</p>
                {typeof rule.score === "number" && (
                  <p className="mt-1 text-xs text-muted-foreground">Contributes {rule.score} to the score</p>
                )}
                {inputs.length > 0 && (
                  <table className="mt-2 w-full table-fixed text-left text-xs">
                    <caption className="sr-only">Inputs read by {rule.rule_id}</caption>
                    <thead>
                      <tr>
                        <th scope="col" className="w-3/5 pb-1 font-medium">
                          Input
                        </th>
                        <th scope="col" className="pb-1 font-medium">
                          Value
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {inputs.map(([field, value]) => (
                        <tr key={field} className="border-t">
                          <td className="break-all py-1 pr-2 font-mono">{field}</td>
                          <td className={`break-all py-1 font-mono ${value === null ? "italic" : ""}`}>
                            {formatInput(value)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
