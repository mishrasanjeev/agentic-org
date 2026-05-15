# Post-deploy validation checklist — Uday CA Firms (2026-05-14)

Run this AFTER `deploy-production` lands the new commit on
`https://agenticorg-api-490751771290.asia-southeast1.run.app`.

## Pre-requisites (operator)

1. **Set the env var on the Cloud Run service** so the canonical
   redirect URI is https + the right host:

   ```
   gcloud run services update agenticorg-api \
       --region asia-southeast1 \
       --update-env-vars \
       AGENTICORG_PUBLIC_API_BASE_URL=https://agenticorg-api-490751771290.asia-southeast1.run.app
   ```

   Until this rolls out, the OAuth handler falls back to the proxy-aware
   `X-Forwarded-Proto` + `X-Forwarded-Host` headers Cloud Run sends —
   that's the safety net, not the steady state.

2. **Confirm the Zoho Developer Console has the https redirect URI**
   registered for the India DC client `1000.KN7KOTFZOEO6AEXNB12OT8CNGV3A9Z`:

   ```
   https://agenticorg-api-490751771290.asia-southeast1.run.app/api/v1/oauth/callback
   ```

   (No trailing slash, https only.)

## 1) OAuth — Zoho Books India authorize URL

- Open `https://agenticorg.ai/dashboard/connectors/new`.
- Confirm the provider dropdown contains only **Custom / Generic Connector**.
- Set connector name to `zoho_books`.
- Set Base URL to `https://www.zohoapis.in/books/v3`.
- Set Auth Type to `OAuth2`.
- Fill Client ID / Client Secret.
- Add `organization_id` and `refresh_token` in Extra config JSON.
- Click **Register Connector**.

Expected:
- Browser stays inside AgenticOrg; no Zoho OAuth page is opened.
- The connector is saved with encrypted credentials.
- The region is inferred as India from the base URL.
- Agent activation is blocked unless the connector health status is `healthy`.

If registration returns `zoho_token_material_required`, the payload is
missing refreshable token material. Do not mark the connector fixed or
healthy from Client ID / Client Secret alone.

## 2) OAuth — refresh_token issuance

After consenting on Zoho:
- Zoho redirects back to `/api/v1/oauth/callback?code=…`.
- The success page renders "OAuth connector authorized".
- The Zoho Books connector tile in `/dashboard/connectors` shows
  `status=active`.

If the page shows the structured `code=oauth_refresh_token_missing`
error (HTTP 409) — that's expected when this client has been consented
before. Click **Reconnect** on the connector tile. The framework revokes
the prior grant and re-initiates with `prompt=consent`. A real
refresh_token mint should follow.

## 3) Provider catalog

- Hit `GET https://agenticorg-api-490751771290.asia-southeast1.run.app/api/v1/connectors/oauth/providers`
  (with the Uday Authorization header / session cookie).
- Confirm the JSON lists at least: `gmail`, `google_calendar`, `youtube`,
  `zoho_books`, `banking_aa`, `gstn`.
- `zoho_books.regions` should be `["au","eu","in","jp","us"]`.

## 4) BUG-07..BUG-17 re-verification

The regression suite `tests/regression/test_bugs_uday_14may2026.py`
already pins all eleven prior bug IDs. After deploy, re-run the
Playwright suites that exercise them end-to-end:

```
cd ui
BASE_URL=https://agenticorg.ai \
UDAY_EMAIL=uday.chauhan@edumatica.io \
UDAY_PASSWORD='<from May-14 report>' \
npx playwright test e2e/qa-uday-14may2026.spec.ts e2e/qa-uday-2may2026.spec.ts e2e/qa-cafirms-may03.spec.ts --project=chromium --workers=1
```

## 5) Negative safety checks

- `POST /api/v1/connectors/oauth/initiate` with a missing
  `client_secret` must return **400** with "Missing required fields"
  in the body — not a silently-broken authorize URL.
- `POST /api/v1/connectors/oauth/revoke-and-retry` for a connector that
  has never been authorized must return **400** with "No prior
  authorization attempt found" — not silently re-prompt the user.
- A pasted `redirect_uri: http://...` in the body must be ignored and
  upgraded to the canonical https URL (server never trusts a http
  redirect_uri).
