## Scenario and boundary

Example Bank wants analysts to spend less time assembling a business-onboarding file and more time reviewing exceptions. This playbook uses AgenticOrg's governed-case path for read-only investigation, cited memo preparation, screening proposals, deterministic policy evaluation and a human decision.

It does not automate account opening, sanctions clearance, loan approval or regulatory reporting. Provider availability, country coverage, bank policy and system-of-record integration are institution-specific. Use fictional businesses until the institution approves real data.

## Prepare the pilot

Assign an onboarding owner, screening analyst, authorized approver, provider integration owner and platform/security owner. Configure the tenant-gated case runtime, one active shared agent per required case role, reviewed provider manifest/read tools, exact local purpose controls, Grantex grants and a reviewed policy. Configure the decision issuer and signed handoff destination separately.

The institution's application/LOS system submits cases through the reviewed API/workflow. The approvals console is for queue and review, not a universal business-intake form.

## Process map

This map assigns each handoff in the configured case path. A missing source or
unresolved review is a stop, not an implied clearance.

```flow
Application | A fictional business submits details through the bank's approved intake. | Owner: Bank intake team | If blocked: Incomplete intake stays with the bank; no case outcome is inferred.
Investigation | Configured agents read approved registry, ownership and screening sources. | Owner: Configured case agents | If blocked: Missing grant, provider error or absent record remains missing evidence, not a clear result.
Memo and policy | The case shows cited findings, missing items and the reviewed policy tier. | Owner: AgenticOrg case runtime | If blocked: Unsupported citations or incomplete evidence require investigation before decision.
Screening review | The analyst checks each proposed disposition against the cited record. | Owner: Screening analyst | Human decision: Accept the proposal or override it with a written reason. | If blocked: An unresolved hit stays open for human review; the proposal does not clear it.
Decision request | The exact case version and action go to the configured approval issuer. | Owner: Authorized case operator and approval issuer | Human decision: A designated approver decides on the issuer page; a distinct second approver participates where required. | If blocked: Missing, expired or superseded approval cannot record a decision; request a new decision on the current case.
Bank handoff | The signed outcome is delivered for the bank system to interpret and act on. | Owner: Bank system-of-record team | If blocked: Delivery failure or duplicate evidence needs reconciliation; do not infer account opening.
```

## Follow one fictional application

1. Open **Approvals > governed cases** at `/dashboard/approvals/cases`.
2. Find the fictional case and inspect investigation state.
3. Read identity, registry, ownership, screening, activity and missing evidence.
4. Open retained source passages where available; confirm provider record and retrieval time.
5. Inspect policy ID/version and fired rules. Do not treat missing evidence as passing.
6. Review screening proposals. Override only with a documented reason; the agent's proposal does not clear the hit.
7. Request a decision for the current case. The named approver authenticates on the issuer's page, including distinct second approval where required.
8. Record the verified decision and inspect signed handoff delivery. Confirm what the bank system did with it.

## What a successful trial proves

It proves the configured evidence/review/decision/handoff path for that test. It does not prove universal registry coverage, calibrated model accuracy, case-bound pooled grants or regulatory approval. The published Grantex SDK's token-level purpose/per-case-cap limitations must remain visible in the rollout review.

## Negative cases to demonstrate

Try a unavailable provider capability, unresolved screening hit, missing grant, cross-company request, machine-credential human decision, changed memo after approval request and duplicate handoff. Expected outcomes are visible missing evidence/refusals or safe idempotent handling, never a manufactured approval.

## Measure adoption

Compare manual file-assembly time, missing-information rate, analyst overrides, re-investigation, review age and delivery failures on a reviewed sample. Track reopening causes. Do not publish a numerical accuracy claim from a few happy-path examples.

Next: [Governed cases](/docs/governed-cases), [Approvals](/docs/approvals), [Adoption checklist](/docs/adoption-checklist).
