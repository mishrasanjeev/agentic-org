/**
 * Module 16: Email System — TC-EMAIL-001 through TC-EMAIL-006.
 *
 * Most of Module 16 lives in the validate_email_domain pure
 * function (source-pinned in
 * tests/unit/test_module_16_email_system.py). The Playwright
 * coverage focuses on the boundary where the validation
 * surfaces to the user — the invite endpoint, which calls
 * send_email() under the hood and reports rejection back to
 * the caller.
 */
import { expect, test } from "@playwright/test";

import { APP, E2E_TOKEN, requireAuth } from "./helpers/auth";

test.describe("Module 16: Email System @qa @email", () => {
  test.beforeEach(() => {
    requireAuth();
  });

  // -------------------------------------------------------------------------
  // TC-EMAIL-001: Blocked domain
  // -------------------------------------------------------------------------

  test("TC-EMAIL-001 invite to mailinator.com is silently dropped (no capture)", async ({
    request,
  }) => {
    // Disposable-mail domains pass schema validation (the email
    // is well-formed) but the underlying send_email refuses to
    // dispatch. The /invite route succeeds at the user
    // creation step but no mail is sent. Either 201 or 400 is
    // valid here; the contract under test is "the request
    // doesn't 5xx".
    const ts = Date.now();
    const resp = await request.post(`${APP}/api/v1/invite`, {
      headers: {
        Authorization: `Bearer ${E2E_TOKEN}`,
        "Content-Type": "application/json",
      },
      data: {
        email: `qa-blocked-${ts}@mailinator.com`,
        name: "QA Blocked",
        role: "analyst",
      },
      failOnStatusCode: false,
    });
    // Foundation #8: blocked domain must NOT crash the server.
    expect(resp.status()).toBeLessThan(500);
  });

  // -------------------------------------------------------------------------
  // TC-EMAIL-002: Fake domain (no MX records)
  // -------------------------------------------------------------------------

  test("TC-EMAIL-002 invite to a no-MX domain doesn't 5xx", async ({
    request,
  }) => {
    // .invalid is RFC-2606 reserved — it MUST NOT have MX
    // records. The domain validation rejects; the server stays
    // up.
    const ts = Date.now();
    const resp = await request.post(`${APP}/api/v1/invite`, {
      headers: {
        Authorization: `Bearer ${E2E_TOKEN}`,
        "Content-Type": "application/json",
      },
      data: {
        email: `qa-no-mx-${ts}@nonexistent-domain-no-mx-${ts}.invalid`,
        name: "QA No MX",
        role: "analyst",
      },
      failOnStatusCode: false,
    });
    expect(resp.status()).toBeLessThan(500);
  });

  // -------------------------------------------------------------------------
  // TC-EMAIL-004: Test-domain blocked (.local / .test TLDs)
  // -------------------------------------------------------------------------

  test("TC-EMAIL-004 invite to .local TLD doesn't 5xx", async ({ request }) => {
    const ts = Date.now();
    const resp = await request.post(`${APP}/api/v1/invite`, {
      headers: {
        Authorization: `Bearer ${E2E_TOKEN}`,
        "Content-Type": "application/json",
      },
      data: {
        email: `qa-local-tld-${ts}@something.local`,
        name: "QA Local",
        role: "analyst",
      },
      failOnStatusCode: false,
    });
    expect(resp.status()).toBeLessThan(500);
  });

  test("TC-EMAIL-004b invite to .test TLD doesn't 5xx", async ({ request }) => {
    const ts = Date.now();
    const resp = await request.post(`${APP}/api/v1/invite`, {
      headers: {
        Authorization: `Bearer ${E2E_TOKEN}`,
        "Content-Type": "application/json",
      },
      data: {
        email: `qa-test-tld-${ts}@example.test`,
        name: "QA Test TLD",
        role: "analyst",
      },
      failOnStatusCode: false,
    });
    expect(resp.status()).toBeLessThan(500);
  });

  // -------------------------------------------------------------------------
  // TC-EMAIL-005: Welcome email on signup (boundary smoke)
  // -------------------------------------------------------------------------

  test("TC-EMAIL-005 signup endpoint exists and accepts well-formed input", async ({
    request,
  }) => {
    // Foundation #6 burndown: the signup → welcome-email path
    // requires a fresh user creation, which can't be cleanly
    // automated in a shared E2E tenant. We pin the endpoint's
    // existence + 4xx-on-bad-input contract so the seam
    // doesn't silently disappear.
    const resp = await request.post(`${APP}/api/v1/auth/signup`, {
      headers: { "Content-Type": "application/json" },
      data: {}, // empty body — schema rejection
      failOnStatusCode: false,
    });
    // 422 (Pydantic) or 400 (handler) is expected. NOT 404
    // (which would mean the route disappeared) and NOT 500
    // (which would mean an unhandled exception path).
    expect([400, 422]).toContain(resp.status());
  });

  // -------------------------------------------------------------------------
  // TC-EMAIL-006: Invite email
  // -------------------------------------------------------------------------

  test("TC-EMAIL-006 invite endpoint requires email field", async ({
    request,
  }) => {
    // No email in body → 422.
    const resp = await request.post(`${APP}/api/v1/invite`, {
      headers: {
        Authorization: `Bearer ${E2E_TOKEN}`,
        "Content-Type": "application/json",
      },
      data: { name: "no email here", role: "analyst" },
      failOnStatusCode: false,
    });
    expect(resp.status()).toBe(422);
  });

  test("TC-EMAIL-006b invite endpoint requires admin auth", async ({
    request,
  }) => {
    const resp = await request.post(`${APP}/api/v1/invite`, {
      headers: { "Content-Type": "application/json" },
      data: { email: "x@example.com", role: "analyst" },
      failOnStatusCode: false,
    });
    expect([401, 403]).toContain(resp.status());
  });
});
