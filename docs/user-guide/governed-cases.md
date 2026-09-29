## What a governed case is

A governed business case brings read-only investigation, cited evidence, deterministic policy results, screening proposals and human decision authority into one traceable record. An agent may investigate and prepare a recommendation. It cannot approve, decline, close, file or pay through this case path.

This feature is tenant-gated and configuration-dependent. Your operator must configure the active case-role agents, reviewed provider manifest, reviewed policy, Grantex grants and decision issuer. Shipped example policies are not production-approved bank policies.

## Where to work

The case queue is at `/dashboard/approvals/cases`. Open a case at `/dashboard/approvals/cases/{case_ref}`. These screens require the relevant scopes and tenant feature flag. `governed_cases_disabled` means the feature is not enabled; it is not an empty successful queue.

```flow
Application submitted | A reviewed API/workflow supplies the authorized business application.
Read-only investigation | Registered tools check permitted evidence under grants and exact purpose controls.
Memo and policy | Findings carry citations, missing items, risk tier and fired rules.
Analyst review | A person reviews screening proposals and requests missing information.
Human decision | Named approvers use the configured issuer; AgenticOrg records verified grants.
Signed handoff | The operator's system receives the current case outcome; it owns the business action.
```

## Read the case correctly

Inspect each memo section: complete, partial, not available and provider error mean different things. Missing evidence is not a clean result. Citations identify the provider, upstream record, field and retrieval time. Where retained, **Show the passage** retrieves encrypted, digest-checked evidence as text.

The policy section shows the policy ID/version, score, tier, input digest and fired rules. Model confidence is metadata and gates nothing in this case path. A recommendation marked `requires_human_decision` is a proposal, not approval.

## Review screening proposals

Compare identifiers and evidence. **Accept** preserves the proposed outcome; **Override** requires a different outcome and a written reason. The server records the signed-in human identity. A review is written once and only while the case is awaiting decision. It does not close the upstream screening hit automatically.

## Request and record the final decision

1. Choose the outcome to request. Supply a reason if it differs from the recommendation.
2. Open the issuer's approval page. The person authenticates and performs required step-up there.
3. Watch approval progress, including a distinct second approver when required.
4. Record the decision only when valid grants exist for the current exact case version/action.

If the issuer is unconfigured, the default verifier refuses decisions. `decision_required`, `same_approver`, `case_changed` and `decision_service_not_configured` are meaningful reasons, not errors to hide.

## Integrate with the institution

Applications and signed case handoffs require reviewed API integration. The console is not a universal application-intake form or core-banking adapter. The signed outbox and REST case record provide evidence to your system of record; the institution controls account opening, monitoring, reporting and retention.

The current published Grantex Python SDK verifies tool authority but does not enforce every token-level case-purpose or per-case-cap requirement. AgenticOrg has local purpose controls; pooled grants are not case-bound. Evaluate that boundary explicitly before enabling a production process.

Next: [BFSI onboarding walkthrough](/docs/bfsi-business-onboarding), [Security and data](/docs/security-and-data).
