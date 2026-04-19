# Scenario 4 — Operations failure recovery

**Persona:** SRE / oncall engineer
**Goal:** verify that a failing agent run or degraded integration
surfaces an honest state, not a decorative healthy badge, and that the
recovery path is obvious
**Playwright:** `ui/e2e/v4-features.spec.ts` (HITL), `decorative-state.spec.ts`

## Steps

1. Trigger an agent whose tool call will fail (e.g. a connector with
   a bad credential). The run status lands as `failed` with the tool
   error preserved in the reasoning trace.
2. Open the agent detail page. The **Explanation** block renders
   real bullets sourced from `/agents/{id}/explanation/latest` — each
   references the actual tool name + failure reason. If no run has
   happened, the UI says `Run the agent to see the explanation`,
   **not** a fake one.
3. In the HITL Queue, a `hitl_pending` run shows the agent's
   confidence, the gate reason, and a one-click approve/reject. The
   audit log captures the reviewer + outcome.
4. On **Settings → Grantex Integration**, if `GRANTEX_API_KEY` is
   unset, the badge is amber + "Not configured — set GRANTEX_API_KEY
   to enable" rather than a green "Configured" dot. No oncall engineer
   gets paged on a fake healthy.
5. Hit `/api/v1/integrations/status` — `{grantex_configured,
   composio_configured, ragflow_configured}` booleans reflect real
   env presence. Never leaks the secret values themselves.
6. On the Connectors → Marketplace tab, the **Connect** button carries
   a visible "Demo" label + "OAuth handoff pending — UI state only"
   note. Clicking it does not mark the app as connected in any backend
   system.

## Expected outcome

- Failures are loud and traceable without cross-referencing run IDs.
- Degraded integrations are visibly degraded. No "Connected" phantom
  state.
- Explanation bullets cite real tool calls, not demo copy.

## Drift guards

- `ui/e2e/decorative-state.spec.ts` — Grantex badge matches
  `/integrations/status`, marketplace Connect button retains "Demo".
- `ui/e2e/explainer-real.spec.ts` — explainer bullets match real
  trace tool names.
- `scripts/consistency_sweep.py` — ensures the explainer contract and
  registries don't drift.
