# Post-deploy validation checklist — Uday CA Firms sweep (2026-05-02)

Use this as a PR comment after deployment.

## 1) Generate Test Sample (BUG-08)
- Open the same agent used in reproduction.
- Click **Generate Test Sample**.
- Verify response payload has:
  - `tool_calls` non-empty.
  - `confidence > 0.70`.
- Confirm no literal `shadow_sample` tool/function error appears in logs.

## 2) Google sign-in with stale cookies (BUG-09)
- Scenario A: clean tab, no stale cookies.
- Scenario B: tab with stale `agenticorg_session` cookie.
- In both scenarios:
  - click **Sign in with Google**.
  - verify no `CSRF token mismatch (SEC-2026-05-P1-003)` error.

## 3) Hard-refresh protected routes (BUG-10)
- While authenticated, hard-refresh each path:
  - `/dashboard`
  - `/dashboard/cfo`
  - `/dashboard/cmo`
- Expected: user stays on route (no bounce to `/login`).

## 4) Negative safety checks
- Cookie-authenticated mutating non-bootstrap route **without** CSRF header should still 403.
- Bearer-token API calls should remain unaffected.
