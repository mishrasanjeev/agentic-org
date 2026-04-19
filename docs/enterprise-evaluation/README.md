# Enterprise Evaluation Scripts

Structured walkthroughs that prove AgenticOrg survives a buyer / admin /
developer / operator evaluation. Each scenario is a short, scripted
path a reviewer can follow against a running instance (local or staging)
to verify a capability end-to-end.

Every scenario names the Playwright spec that automates the same path
so "works in my browser" and "passes in CI" cite the same evidence.

## Scenarios

| # | Scenario | Playwright | Read if you're… |
|---|---|---|---|
| 1 | [SMB quick-start](01-smb-quickstart.md) | `ui/e2e/onboarding-e2e.spec.ts` | founder / SMB buyer |
| 2 | [Admin onboarding + governance](02-admin-governance.md) | `ui/e2e/settings-governance.spec.ts` + `decorative-state.spec.ts` | IT admin / compliance |
| 3 | [Developer integration (SDK + MCP)](03-developer-integration.md) | `ui/e2e/sdk-examples.spec.ts` | platform engineer |
| 4 | [Operations failure recovery](04-ops-failure-recovery.md) | `ui/e2e/v4-features.spec.ts` (HITL), `decorative-state.spec.ts` | SRE / oncall |
| 5 | [Knowledge base semantic search](05-kb-semantic-search.md) | `tests/regression/test_embeddings.py` | knowledge owner |

## How to run an evaluation

1. Stand up the stack: `bash scripts/local_e2e.sh` (stops at vite dev
   server + demo tenant seeded).
2. Pick a scenario above.
3. Follow the narrative; the expected observable outcome is stated at
   each step.
4. If you want to verify the path is drift-guarded, run the named
   Playwright spec: `cd ui && npx playwright test <spec-name>`.
5. Cross-check the public numeric surface with
   `python scripts/consistency_sweep.py` — every count on landing,
   pricing, dashboard must agree with `/api/v1/product-facts`.

## When to add a new scenario

Add one when a new enterprise persona starts using the product, or when
an existing scenario grew past a single buyer/admin/developer/operator
concern. Each scenario is ~1 page; if it's growing beyond that, split
it.
