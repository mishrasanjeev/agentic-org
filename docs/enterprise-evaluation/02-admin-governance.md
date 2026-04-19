# Scenario 2 — Admin onboarding + governance

**Persona:** tenant admin / compliance reviewer
**Goal:** configure PII masking, data region, audit retention, and API
keys; verify each setting is persisted and audited server-side
**Playwright:** `ui/e2e/settings-governance.spec.ts`,
`ui/e2e/decorative-state.spec.ts`, `ui/e2e/dashboard-403.spec.ts`

## Steps

1. Sign in as a tenant admin. Navigate to **Settings → Compliance & Data**.
2. Toggle **PII Masking** off → Save. Observe audit row at `/audit`
   referencing the change with `updated_by` + `updated_at` fields.
3. Change **Data Region** IN → EU → Save. Reload — the new region
   persists. Audit row appended.
4. Set **Audit Retention** to 3 years. Database-level `CHECK`
   constraint rejects values outside 1–10 with HTTP 400 — regression
   test `tests/regression/test_governance_api.py`.
5. On the **Grantex Integration** card, **API Key Status** reflects
   `GET /integrations/status.grantex_configured` — green + "Configured"
   when the env var is set, amber + "Not configured" otherwise. No
   hardcoded green dot.
6. As a non-admin (CFO/analyst), open the URL `/dashboard/settings`:
   `ProtectedRoute` redirects to `/dashboard/access-denied` which
   renders `attempted_path`, `current_role`, `allowed_roles`. Direct
   API hits (`PUT /governance/config`) return 403 — UI gating is
   convenience, backend is the real control.
7. Revoke an API key in **Settings → API Keys**. Subsequent calls
   against that key return 401 immediately.

## Expected outcome

- Every toggle persists across reload + session.
- Every mutation creates an audit row.
- Server enforces admin gate; UI never the only line of defence.
- No decorative healthy-state anywhere on the Settings page.

## Drift guards

- `tests/regression/test_governance_api.py` (CHECK constraints,
  admin-scope enforcement).
- `ui/e2e/settings-governance.spec.ts` (round-trip persistence).
- `ui/e2e/decorative-state.spec.ts` (Grantex badge matches live API).
