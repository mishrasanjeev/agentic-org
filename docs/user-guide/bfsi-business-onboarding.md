## Scenario and boundary

Example Bank wants analysts to spend less time assembling a business-onboarding file and more time reviewing exceptions. This playbook uses AgenticOrg's governed-case path for read-only investigation, cited memo preparation, screening proposals, deterministic policy evaluation and a human decision.

It does not automate account opening, sanctions clearance, loan approval or regulatory reporting. Provider availability, country coverage, bank policy and system-of-record integration are institution-specific. Use fictional businesses until the institution approves real data.

## Prepare the pilot

Assign an onboarding owner, screening analyst, authorized approver, provider integration owner and platform/security owner. Configure the tenant-gated case runtime, one active shared agent per required case role, reviewed provider manifest/read tools, exact local purpose controls, Grantex grants and a reviewed policy. Configure the decision issuer and signed handoff destination separately.

The institution's application/LOS system submits cases through the reviewed API/workflow. The approvals console is for queue and review, not a universal business-intake form.

## Follow one fictional application

```flow
Application | Example Trading Ltd submits business details through the bank's approved intake.
Investigation | Agents read approved registry, ownership and screening evidence.
Memo | Source-linked findings, missing items and deterministic policy tier are prepared.
Analyst review | A person inspects possible screening matches and records a reasoned disposition.
Decision request | The current case/version/action goes to the configured human approval issuer.
Bank handoff | Verified decision evidence is recorded and signed outcome delivered to the bank system.
```

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
