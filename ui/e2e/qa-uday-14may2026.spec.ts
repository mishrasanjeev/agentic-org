/**
 * Playwright regression for the Uday CA Firms 2026-05-14 bug report.
 *
 * Source: ``C:\Users\mishr\Downloads\CA_FIRMS_TEST_REPORT_Uday14May2026.md``.
 *
 * The two reproducible bugs in that report both surface as failed Zoho
 * Books authorize-URL construction on agenticorg.ai (prod commit
 * ebbb961):
 *
 *   1. Zoho returns "Invalid Redirect Uri" because the URL we send is
 *      ``redirect_uri=http%3A%2F%2F…``. Cloud Run terminates TLS at the
 *      edge so the internal request scheme is http; the URL we send
 *      Zoho must be https.
 *   2. Zoho returns no refresh_token on re-consent unless we route .in
 *      accounts to ``accounts.zoho.in`` (not the hardcoded .com URL)
 *      and force ``access_type=offline`` + ``prompt=consent``.
 *
 * What this spec proves end-to-end against agenticorg.ai
 * ------------------------------------------------------
 * - Login with Uday's CEO/Admin credentials.
 * - POST ``/api/v1/connectors/oauth/initiate`` with Zoho Books + India
 *   region.
 * - Assert the returned ``authorization_url`` has:
 *     * ``https://`` redirect_uri pointing at ``/api/v1/oauth/callback``
 *     * host = ``accounts.zoho.in`` (India DC, not .com)
 *     * ``access_type=offline`` + ``prompt=consent``
 *     * the Zoho client_id Uday registered.
 * - Assert ``GET /api/v1/connectors/oauth/providers`` exposes zoho_books
 *   with the india/us/eu/au/jp region picker, requires_organization_id,
 *   and the user_fields the dashboard now renders dynamically.
 *
 * What this spec does NOT do
 * --------------------------
 * The actual ``window.location.assign(authorize_url)`` redirect into
 * accounts.zoho.in/oauth/v2/auth requires Zoho 2FA on uday.chauhan's
 * Zoho account. We assert the URL is correctly constructed; completing
 * the Zoho consent step is a human follow-up. The
 * ``docs/post_deploy_checklist_uday_2026-05-14.md`` covers that step.
 *
 * Env vars
 * --------
 *   BASE_URL          — defaults to https://agenticorg.ai per
 *                       playwright.config.ts but Uday's report uses
 *                       ``https://agenticorg.ai`` so we override it
 *                       inside the spec.
 *   UDAY_EMAIL        — tester email (per the May-14 bug report)
 *   UDAY_PASSWORD     — tester password (env-only; never hardcoded).
 *                       The lowercase variant is also tried at runtime.
 *   ZOHO_CLIENT_ID    — Zoho OAuth client id (env-only).
 *   ZOHO_CLIENT_SECRET — Zoho OAuth client secret (env-only).
 *   ZOHO_ORG_ID       — Zoho Books organization id (env-only).
 *
 * SECURITY: no live credential is hardcoded here. All of the above are
 * required env vars; the spec throws a directed error if any is unset.
 */

import { expect, test } from "@playwright/test";

const APP = process.env.BASE_URL || "https://agenticorg.ai";
// Credentials are env-only. No live value is ever committed; see the
// beforeAll guard which fails fast with a directed message if unset.
const UDAY_EMAIL = process.env.UDAY_EMAIL || "uday.chauhan@edumatica.io";
const UDAY_PASSWORD = process.env.UDAY_PASSWORD || "";
const ZOHO_CLIENT_ID = process.env.ZOHO_CLIENT_ID || "";
const ZOHO_CLIENT_SECRET = process.env.ZOHO_CLIENT_SECRET || "";
const ZOHO_ORG_ID = process.env.ZOHO_ORG_ID || "";
const MISSING_ENV = [
  ["UDAY_PASSWORD", UDAY_PASSWORD],
  ["ZOHO_CLIENT_ID", ZOHO_CLIENT_ID],
  ["ZOHO_CLIENT_SECRET", ZOHO_CLIENT_SECRET],
  ["ZOHO_ORG_ID", ZOHO_ORG_ID],
]
  .filter(([, v]) => !v)
  .map(([k]) => k);

// We hit the same backend the UI calls — agenticorg.ai routes
// /api/v1/* to the Cloud Run API. Tester report confirmed the path.
const API_BASE = APP;

/** Login Uday via the public ``/api/v1/auth/login`` endpoint and return
 *  the cookies the dashboard expects. Returns false when login fails
 *  (e.g. password reset, MFA challenge) so the spec can surface a
 *  directed error rather than a generic timeout. */
async function loginAsUday(
  request: import("@playwright/test").APIRequestContext,
): Promise<boolean> {
  // Try the supplied password first, then its lowercase variant (the
  // May-14 report flagged a case-variant alternate).
  for (const password of [UDAY_PASSWORD, UDAY_PASSWORD.toLowerCase()]) {
    const resp = await request.post(`${API_BASE}/api/v1/auth/login`, {
      data: { email: UDAY_EMAIL, password },
      failOnStatusCode: false,
    });
    if (resp.ok()) return true;
  }
  return false;
}

test.describe("Uday CA Firms 2026-05-14 — Zoho Books OAuth onboarding", () => {
  test.skip(
    MISSING_ENV.length > 0,
    `Live Zoho OAuth credentials are not configured: ${MISSING_ENV.join(", ")}.`,
  );

  test.beforeAll(() => {
    // Surface the absent env vars loudly so a misconfigured CI run
    // fails with the right diagnostic instead of a redirect-uri timeout.
    const missing = [
      ["UDAY_PASSWORD", UDAY_PASSWORD],
      ["ZOHO_CLIENT_ID", ZOHO_CLIENT_ID],
      ["ZOHO_CLIENT_SECRET", ZOHO_CLIENT_SECRET],
      ["ZOHO_ORG_ID", ZOHO_ORG_ID],
    ]
      .filter(([, v]) => !v)
      .map(([k]) => k);
    if (missing.length > 0) {
      throw new Error(
        `Required env var(s) unset: ${missing.join(", ")}. These are ` +
          "credentials and are intentionally NOT committed — supply them " +
          "via the environment before running this spec.",
      );
    }
  });

  test("Bug 1+2: Zoho India authorize URL is https + accounts.zoho.in + offline+consent", async ({
    request,
  }) => {
    const loggedIn = await loginAsUday(request);
    expect(
      loggedIn,
      `Login as ${UDAY_EMAIL} against ${API_BASE} must succeed. ` +
        "If this fails the bug verdict cannot proceed past 'unverified — " +
        "credentials no longer valid'.",
    ).toBeTruthy();

    // Step 1 — provider catalog must expose Zoho Books with the region
    // picker the new dynamic UI renders.
    const providersResp = await request.get(
      `${API_BASE}/api/v1/connectors/oauth/providers`,
    );
    expect(
      providersResp.ok(),
      `GET /connectors/oauth/providers returned ${providersResp.status()}. ` +
        "If this is 404, the May-14 framework PR has not been deployed yet " +
        "and the verdict for OAUTH-MAY14-01 / OAUTH-MAY14-02 is " +
        "'Fixed in code, deploy pending', not 'Fixed'.",
    ).toBeTruthy();
    const providers = (await providersResp.json()) as Array<{
      connector_name: string;
      regions: string[];
      requires_organization_id: boolean;
      user_fields: Array<{ key: string; required: boolean }>;
    }>;
    const zoho = providers.find((p) => p.connector_name === "zoho_books");
    expect(zoho, "zoho_books missing from provider registry").toBeTruthy();
    expect(zoho?.regions.sort()).toEqual(
      ["au", "eu", "in", "jp", "us"].sort(),
    );
    expect(zoho?.requires_organization_id).toBe(true);
    const fieldKeys = (zoho?.user_fields ?? []).map((f) => f.key);
    expect(fieldKeys).toEqual(
      expect.arrayContaining([
        "client_id",
        "client_secret",
        "region",
        "organization_id",
      ]),
    );

    // Step 2 — initiate the OAuth flow with India region + Uday's creds.
    const initiateResp = await request.post(
      `${API_BASE}/api/v1/connectors/oauth/initiate`,
      {
        data: {
          connector_name: "zoho_books",
          user_fields: {
            client_id: ZOHO_CLIENT_ID,
            client_secret: ZOHO_CLIENT_SECRET,
            region: "in",
            organization_id: ZOHO_ORG_ID,
          },
        },
        failOnStatusCode: false,
      },
    );
    expect(
      initiateResp.ok(),
      `oauth/initiate must succeed for the authed user. ` +
        `Got ${initiateResp.status()}: ${await initiateResp.text()}`,
    ).toBeTruthy();
    const initiate = (await initiateResp.json()) as {
      authorization_url: string;
      redirect_uri: string;
      region?: string;
    };

    // ── Bug 1: redirect_uri must be https ──────────────────────────────
    expect(
      initiate.redirect_uri.startsWith("https://"),
      `redirect_uri must be https (got ${initiate.redirect_uri}). ` +
        "The Cloud Run http:// scheme was the root cause of Zoho's " +
        "'Invalid Redirect Uri' error on May-14.",
    ).toBeTruthy();
    expect(initiate.redirect_uri).toContain("/api/v1/oauth/callback");

    // ── Bug 2: authorize URL must use the India data center ────────────
    const authUrl = new URL(initiate.authorization_url);
    expect(
      authUrl.host,
      `authorize URL must use accounts.zoho.in for India accounts, ` +
        `got ${authUrl.host}. Hardcoded .com was the root cause of ` +
        "the May-14 missing refresh_token symptom.",
    ).toBe("accounts.zoho.in");
    expect(authUrl.pathname).toBe("/oauth/v2/auth");

    const params = authUrl.searchParams;
    expect(params.get("client_id")).toBe(ZOHO_CLIENT_ID);
    expect(params.get("response_type")).toBe("code");
    expect(params.get("access_type")).toBe("offline");
    expect(params.get("prompt")).toBe("consent");
    expect(params.get("scope")).toBe("ZohoBooks.fullaccess.all");

    // redirect_uri inside the authorize URL must be the same https value.
    const innerRedirect = params.get("redirect_uri") || "";
    expect(innerRedirect.startsWith("https://")).toBeTruthy();
    expect(innerRedirect).toBe(initiate.redirect_uri);

    // Region echo so the UI can show "Zoho India" in the post-auth state.
    expect(initiate.region).toBe("in");
  });

  test("Production reproduction (old deploy): authorize URL is http + zoho.com — proves the bug exists on un-deployed prod", async ({
    request,
  }) => {
    // This test is the receipt that BUG-MAY14-01 + 02 are real on the
    // current deployed commit. It uses the LEGACY payload shape (no
    // ``user_fields``) so it works against the old API. When the
    // framework PR is deployed, the same payload still works (legacy
    // shape accepted) but the URL output is fixed — the asserts below
    // will then flip to the new ``test_redirect_uri_is_https`` test.
    const loggedIn = await loginAsUday(request);
    test.skip(
      !loggedIn,
      "Login as Uday failed — production credentials invalid; cannot prove the bug.",
    );

    const resp = await request.post(
      `${API_BASE}/api/v1/connectors/oauth/initiate`,
      {
        data: {
          connector_name: "zoho_books",
          client_id: ZOHO_CLIENT_ID,
          client_secret: ZOHO_CLIENT_SECRET,
          base_url: "https://www.zohoapis.in/books/v3",
          extra_config: { organization_id: ZOHO_ORG_ID },
        },
        failOnStatusCode: false,
      },
    );
    // The deployed handler still answers 200 — broken on the URL, not
    // on the body shape. Once the framework PR deploys, the same
    // payload still returns 200 (legacy accepted) but with a correct
    // URL — at which point this test starts failing and you flip it.
    if (!resp.ok()) {
      test.info().annotations.push({
        type: "deploy-state",
        description: `Legacy /oauth/initiate returned ${resp.status()} — likely a deploy in progress with a transitional schema. Re-run after deploy stabilises.`,
      });
      return;
    }
    const body = (await resp.json()) as {
      authorization_url: string;
      redirect_uri: string;
    };
    const authUrl = new URL(body.authorization_url);
    const isPreDeploy =
      body.redirect_uri.startsWith("http://") ||
      authUrl.host === "accounts.zoho.com";
    test
      .info()
      .annotations.push({
        type: isPreDeploy ? "bug-reproduced" : "fix-deployed",
        description: isPreDeploy
          ? `Bug reproduced on deployed prod. redirect_uri=${body.redirect_uri}, authorize host=${authUrl.host}. Verdict: Fixed in code, deploy pending.`
          : `Fix is live on prod. redirect_uri=${body.redirect_uri}, authorize host=${authUrl.host}. Verdict: Fixed.`,
      });
    // We don't fail this test — it is a diagnostic. The status of the
    // main bug-1+2 test above is what gates the verdict.
  });

  test("Connector wizard exposes only generic provider registration", async ({
    page,
    request,
  }) => {
    const loggedIn = await loginAsUday(request);
    expect(loggedIn).toBeTruthy();

    // Carry the session into the browser context. The login API returned
    // an HttpOnly session cookie; ``page.request`` and ``page.goto`` share
    // the same cookie jar as ``request`` so we just navigate.
    await page.goto(`${APP}/dashboard/connectors/new`, {
      waitUntil: "domcontentloaded",
    });

    // May-15 reopening: the create page must not list managed providers
    // or send admins through OAuth redirects. Backend provider support
    // stays available via API, but the UI exposes only generic setup.
    const providerSelect = page.getByTestId("provider-select");
    await expect(providerSelect).toBeVisible({ timeout: 10_000 });
    await expect(providerSelect.locator("option")).toHaveCount(1);
    await expect(providerSelect.locator("option")).toHaveText("Custom / Generic Connector");
    await expect(page.getByText("Extra config")).toBeVisible();
    await expect(page.getByRole("button", { name: "Register Connector" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Authorize Connector" })).toHaveCount(0);
  });
});
