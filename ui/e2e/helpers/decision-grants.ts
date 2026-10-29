// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
/**
 * Helpers for the suites that take a real approval on the Grantex auth service's own approval
 * page (`make e2e-decisions`, `make e2e-onboarding-desk`): the approver identity provider, the
 * approval itself, the console's decision request and the watcher that no decision grant ever
 * reaches the console's browser.
 */
import { expect, type APIRequestContext, type Browser, type BrowserContext, type Page, type Request } from "@playwright/test";

/**
 * Required, with no default: a missing one is a broken run, not a reason to pass quietly. The
 * config requires BASE_URL the same way.
 */
export function required(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is required: run this suite with \`make e2e-decisions\``);
  return value;
}

export const ISSUER_ORIGIN = required("GRANTEX_APPROVAL_ORIGIN");
export const ADMIN_API_KEY = required("GRANTEX_ADMIN_API_KEY");
export const API_KEY = required("GRANTEX_API_KEY");
export const APPROVER_ISSUER = required("GRANTEX_APPROVER_ISSUER");
export const APPROVER_CLIENT_ID = "grantex-decision-approvers-dev";
export const APPROVER_CLIENT_SECRET = "agenticorg-dev-only-decision-approver-secret";
export const STEP_UP_ACR = "urn:agenticorg:acr:step-up";

/** The two fixture people in tools/oidc_stub/config.approvers.dev.json. */
export const APPROVER_ONE = "case-approver-a";
export const APPROVER_TWO = "case-approver-b";

/**
 * A decision grant is `typ: "decision+jwt"`; its header decodes to a JSON
 * object naming that type. Nothing the console receives may contain one.
 */
export function looksLikeADecisionGrant(body: string): boolean {
  if (!body.includes("eyJ")) return false;
  for (const candidate of body.match(/eyJ[A-Za-z0-9_-]{10,}/g) ?? []) {
    try {
      const header = JSON.parse(Buffer.from(candidate, "base64url").toString("utf-8")) as { typ?: string };
      if (header.typ === "decision+jwt") return true;
    } catch {
      // Not a JWS header; the console carries plenty of other base64.
    }
  }
  return false;
}

/** The console's own API, where a decision grant would travel if one ever reached the browser. */
export function isConsoleApi(url: string): boolean {
  return new URL(url).pathname.startsWith("/api/");
}

/**
 * Watches everything this browser context is served - the console's own requests and the test's
 * `page.request` calls alike - for a decision grant. Bodies are read asynchronously, so the caller
 * awaits `settled()` before asserting, otherwise a response that arrived late would be inspected
 * after the assertion had already passed.
 *
 * A body is read once its request has finished, not as soon as its response arrives. When the test
 * navigates while the console is still loading (`openCase` right after `signIn`, or a reload), the
 * navigation cuts off script chunks mid-download, and Chromium can drop the end of such a request:
 * neither `requestfinished` nor `requestfailed` fires, and `response.text()` on it never settles,
 * which held `settled()` until the test timed out. A cut-off download never delivered its body to
 * the page, so it carries nothing to inspect; every console API response, though, must finish and
 * be read, and `settled()` fails naming any that did not.
 */
export function decisionGrantWatcher(
  context: BrowserContext,
): { settled: () => Promise<{ seen: string[]; inspected: number }> } {
  const seen: string[] = [];
  const reads: Promise<void>[] = [];
  // Responses that have arrived but whose request has neither finished nor failed yet.
  const open = new Map<Request, string>();
  context.on("response", (response) => open.set(response.request(), response.url()));
  context.on("requestfailed", (request) => open.delete(request));
  context.on("requestfinished", (request) => {
    open.delete(request);
    reads.push(
      request
        .response()
        .then((response) => (response ? response.text() : ""))
        .then((body) => {
          if (looksLikeADecisionGrant(body)) seen.push(request.url());
        })
        .catch(() => {
          // A body that is gone - a redirect, a request the page abandoned - carried nothing.
        }),
    );
  });
  return {
    settled: async () => {
      await expect
        .poll(() => [...open.values()].filter(isConsoleApi), {
          message: "console API responses that never finished, so were never inspected",
          timeout: 15_000,
        })
        .toEqual([]);
      await Promise.all(reads);
      // `inspected` is the watcher's own positive control: a listener that is
      // not attached, or attached to the wrong thing, reads nothing and would
      // otherwise report "no decision grant seen" for the happiest of reasons.
      return { seen, inspected: reads.length };
    },
  };
}

/**
 * Allow-list the identity provider approvers sign in with. This is the
 * *service administrator's* action (`ADMIN_API_KEY`); the platform's own
 * developer key cannot do it, and the test asserts that too, because the
 * separation is the point of the feature.
 */
export async function allowListApproverIdp(request: APIRequestContext): Promise<{ developerId: string; idpId: string }> {
  const me = await request.get(`${ISSUER_ORIGIN}/v1/me`, { headers: { authorization: `Bearer ${API_KEY}` } });
  expect(me.ok(), await me.text()).toBeTruthy();
  const developerId = ((await me.json()) as { developerId: string }).developerId;

  const body = {
    issuer: APPROVER_ISSUER,
    clientId: APPROVER_CLIENT_ID,
    clientSecret: APPROVER_CLIENT_SECRET,
    acrValues: [STEP_UP_ACR],
    requireVerifiedEmail: true,
    displayName: "Development approvers",
    actor: "local development operator",
  };
  const path = `${ISSUER_ORIGIN}/v1/admin/developers/${developerId}/decision-approver-idps`;

  // The platform's developer key must not be able to add an approver identity
  // provider: if it could, a platform could approve its own decisions.
  const asPlatform = await request.post(path, { headers: { authorization: `Bearer ${API_KEY}` }, data: body });
  expect(asPlatform.status(), "a developer key must not configure approvers").toBe(401);

  const listed = await request.get(path, { headers: { authorization: `Bearer ${ADMIN_API_KEY}` } });
  expect(listed.ok(), await listed.text()).toBeTruthy();
  const active = ((await listed.json()) as { approverIdps: { id: string; issuer: string; clientId: string; status: string }[] })
    .approverIdps.find((i) => i.issuer === APPROVER_ISSUER && i.clientId === APPROVER_CLIENT_ID && i.status === "active");
  if (active) return { developerId, idpId: active.id };

  const created = await request.post(path, { headers: { authorization: `Bearer ${ADMIN_API_KEY}` }, data: body });
  expect(created.status(), await created.text()).toBe(201);
  return { developerId, idpId: ((await created.json()) as { id: string }).id };
}

/**
 * One approval, taken the way a person takes it: a browser of their own, a
 * sign-in at the identity provider with a second factor, the memo and the
 * exact action on the auth service's page, then one click.
 *
 * Returns the page it ended on, so the caller can assert what the service
 * answered - an approval, or a refusal such as the same person twice.
 */
export async function approveOnTheIssuer(
  browser: Browser,
  requestId: string,
  sub: string,
  options: { minDwellMs?: number } = {},
): Promise<{ title: string; body: string }> {
  // A fresh context per approver: an approver's session is a browser session
  // on the auth service's origin, and four eyes means two of them.
  const context = await browser.newContext();
  try {
    const page = await context.newPage();
    const opened = await page.goto(`${ISSUER_ORIGIN}/decisions/${requestId}`);
    expect(opened, "the approval page answered").not.toBeNull();

    if (await page.getByRole("link", { name: /Sign in with/ }).count()) {
      await page.getByRole("link", { name: /Sign in with/ }).first().click();
      // The identity provider's own sign-in page, on its own origin.
      await expect(page).toHaveURL(new RegExp(`^${APPROVER_ISSUER.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}/authorize`));
      await expect(page.getByText("Step-up required.")).toBeVisible();
      const form = page.locator(`form:has(button[data-sub="${sub}"])`);
      // The second factor is what makes this step-up; without it the provider
      // re-renders the page and the auth service refuses the session.
      await form.locator('input[name="second_factor"]').check();
      await form.locator("button[type=submit]").click();
    }

    await page.waitForURL(new RegExp(`/decisions/${requestId}$`));
    // The document title is what names the outcome; the heading on a refusal
    // page is still "Review and approve this decision".
    if (!(await page.getByRole("button", { name: /^Approve: / }).count())) {
      // A refusal page: closed, expired, or this person has already approved.
      return { title: (await page.title()).trim(), body: (await page.locator("main").innerText()).trim() };
    }

    // The dwell time in the grant is measured by the service, from when it
    // rendered this page to when it receives the form post. Approving faster
    // than its minimum is refused, so wait past it deliberately.
    await page.waitForTimeout(options.minDwellMs ?? 2_500);
    await page.getByRole("button", { name: /^Approve: / }).click();
    return { title: (await page.title()).trim(), body: (await page.locator("main").innerText()).trim() };
  } finally {
    await context.close();
  }
}

/** The decision request the console just created, from the API's own answer. */
export async function requestDecisionInTheConsole(
  page: Page,
  outcome: "approve" | "decline",
  reason: string,
): Promise<{ requestId: string; approvalsRequired: number; caseVersion: string }> {
  // A case may already carry an open request from an earlier run; asking
  // again is what a person does, and it is what the panel offers.
  if (await page.getByTestId("ask-again").count()) await page.getByTestId("ask-again").click();
  await expect(page.getByTestId("decision-request-form")).toBeVisible();
  await page.locator(`input[name="decision-outcome"][value="${outcome}"]`).check();
  if (await page.locator("#decision-override-reason").count()) {
    await page.locator("#decision-override-reason").fill(reason);
  }
  const [response] = await Promise.all([
    page.waitForResponse((r) => r.url().includes("/decision-requests") && r.request().method() === "POST"),
    page.getByTestId("request-decision").click(),
  ]);
  expect(response.status(), await response.text()).toBe(201);
  const created = (await response.json()) as { request_id: string; approvals_required: number; case_version: string };
  await expect(page.getByTestId("decision-request")).toBeVisible();
  return { requestId: created.request_id, approvalsRequired: created.approvals_required, caseVersion: created.case_version };
}

/**
 * Cancel any decision request this case already carries, at the issuer, with
 * the platform's developer key. A request is bound to the case and its
 * version, so asking again while one is open returns that same request - and
 * with it whatever approvals it already has. Each test wants a request of its
 * own, and cancelling is the one thing a platform key may do to close one.
 */
export async function cancelOpenRequests(page: Page, issuer: APIRequestContext, caseRef: string): Promise<void> {
  const state = await page.request.get(`/api/v1/governed-cases/${caseRef}`);
  expect(state.ok(), await state.text()).toBeTruthy();
  const recorded = ((await state.json()) as { decision_requests?: { request_id: string }[] }).decision_requests ?? [];
  for (const record of recorded) {
    // 404 when it is already closed, which is the state we want anyway.
    await issuer.post(`${ISSUER_ORIGIN}/v1/decisions/requests/${record.request_id}/cancel`, {
      headers: { authorization: `Bearer ${API_KEY}` },
    });
  }
  if (recorded.length > 0) {
    await page.reload();
    await expect(page.getByTestId("case-state")).toBeVisible({ timeout: 20_000 });
  }
}

/**
 * The console's own credentials for a direct API call from the test: the
 * session is an HttpOnly cookie the request context already carries, and a
 * mutation also needs the CSRF token from its readable companion cookie.
 */
export async function consoleHeaders(page: Page): Promise<Record<string, string>> {
  const csrf = (await page.context().cookies()).find((c) => c.name === "agenticorg_csrf");
  return csrf ? { "X-CSRF-Token": csrf.value } : {};
}

export async function refreshStatus(page: Page): Promise<void> {
  await Promise.all([
    page.waitForResponse((r) => r.url().includes("/decision-requests/") && r.request().method() === "GET"),
    page.getByRole("button", { name: "Refresh status" }).click(),
  ]);
}
