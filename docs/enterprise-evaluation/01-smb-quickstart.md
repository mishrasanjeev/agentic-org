# Scenario 1 — SMB quick-start

**Persona:** SMB founder / solo operator
**Goal:** go from landing page to running one automation in under 10 minutes
**Playwright:** `ui/e2e/onboarding-e2e.spec.ts`

## Steps

1. Hit `https://agenticorg.ai/`. Landing stats strip reads current
   counts from `/api/v1/product-facts` — no stale numbers.
2. Click **Start free**. Email + password signup. Tenant is created
   server-side; no manual portal step.
3. Run the onboarding wizard. Pick a starter workflow from the backend
   catalog (`/workflows/templates`) — not a hardcoded list.
4. Trigger the workflow. Execution lands on the Runs page; approvals
   surface in the HITL Queue. Run detail shows every tool call that
   fired with its actual output.
5. Open Dashboard. KPIs (Active Agents, Approvals Resolved, Workflow
   Runs 24 h) come from real endpoints — no "73 %" decorative strings.

## Expected outcome

- First successful workflow run in ≤ 10 min.
- Every number on screen traceable back to an API call.
- No dead-ends: if an integration isn't wired, the button is labelled
  "Demo" with "OAuth handoff pending — UI state only" rather than
  silently faking success.

## Drift guards

- `scripts/consistency_sweep.py` — public counts match runtime.
- `ui/e2e/decorative-state.spec.ts` — no fake "Configured" dots.
- `ui/e2e/product-claims.spec.ts` — landing/dashboard counts align.
