# Roadmap: Browser Auth — localStorage → httpOnly Cookie

**Status:** scheduled — blocker D from Codex 2026-04-23 re-verification.
**Owner:** frontend + auth
**Target release:** v3.3.x

## Problem

The frontend still reads and writes `token` and `user` in
`localStorage`:

- `ui/src/lib/api.ts` — reads `localStorage.token` for every API call.
- `ui/src/contexts/AuthContext.tsx` — writes on login, deletes on
  logout, rehydrates on mount.
- `ui/src/pages/SSOCallback.tsx` — writes on SSO return.

`localStorage` is readable by any script running in the tenant's
browser origin — which means any XSS vulnerability immediately
harvests a long-lived bearer token. Short of a full CSP lockdown, the
only structural fix is to stop holding the token in JS at all.

## Design

Move the access token to an **httpOnly, Secure, SameSite=Strict
cookie**, server-set on login/SSO. The frontend never sees the
token. API calls rely on the browser attaching the cookie
automatically.

### Backend changes

1. `api/v1/auth.py::login` — on success, set cookie:
   ```python
   response.set_cookie(
       key="ao_session",
       value=access_token,
       httponly=True,
       secure=True,
       samesite="strict",
       max_age=3600,
       path="/",
   )
   ```
   Still return the existing JSON body for a release or two so the
   frontend can read non-sensitive fields (tenant slug, onboarding
   flags) without needing a second `/me` call.
2. `api/v1/auth.py::sso_callback` — same cookie set.
3. `api/v1/auth.py::logout` — `response.delete_cookie("ao_session")`
   AND push the token to the Redis blacklist (already implemented in
   K-E).
4. `auth/middleware.py` — extend token extraction to read from
   `request.cookies.get("ao_session")` in addition to the existing
   `Authorization: Bearer <token>` path. Cookie wins if both are
   present.
5. **CSRF protection:** cookie auth is submitable by cross-origin
   `<form>` posts and so requires CSRF defense.
   - Add `/auth/csrf` endpoint returning a token.
   - All mutating routes require `X-CSRF-Token` header matching the
     cookie-stored double-submit token.
   - Pure reads (GET) are exempt.

### Frontend changes

1. `ui/src/lib/api.ts` — remove `Authorization` header set from
   `localStorage.token`; set `credentials: 'include'` so the browser
   attaches the cookie. Add `X-CSRF-Token` header from a cached CSRF
   fetch.
2. `ui/src/contexts/AuthContext.tsx` — stop writing `token` to
   `localStorage`. Derive "is authed" from the presence of a `user`
   object returned by `/auth/me`, itself cookie-authenticated.
3. `ui/src/pages/SSOCallback.tsx` — stop reading
   `?token=...` from the URL. SSO callback now sets the cookie
   server-side before redirecting home.
4. Remove every `localStorage.getItem("token")` and
   `localStorage.setItem("token", ...)` call. Guard with a lint rule
   (`eslint-no-localstorage-token`).

### Staged rollout

1. **Release N:** add cookie writing on login/SSO alongside existing
   JSON token return. Middleware accepts either. Frontend unchanged.
2. **Release N+1:** frontend switches to cookie-only. JSON token
   still returned but ignored.
3. **Release N+2:** backend stops returning the raw token in the
   JSON body. Logout hardened.

### CSRF design

- Double-submit pattern: server sets a second, JS-readable cookie
  `ao_csrf` containing a random token tied to the session.
- Frontend reads `ao_csrf` cookie value on mount and includes it as
  `X-CSRF-Token` on every mutating request.
- Backend middleware rejects mutating requests whose header
  doesn't match the session-bound CSRF value.

## Tests

- `tests/integration/test_cookie_auth.py` — login sets cookie,
  `Authorization` header no longer required, logout clears cookie
  AND blacklists.
- `tests/integration/test_csrf.py` — mutating request without header
  returns 403; with matching header passes.
- Playwright: rework `ui/e2e/auth.spec.ts` to not read
  `localStorage.token`.

## Non-goals

- Session lengthening (keep 60-min TTL).
- OAuth/OIDC changes (out of scope — only the wire-format of the
  in-browser session).
