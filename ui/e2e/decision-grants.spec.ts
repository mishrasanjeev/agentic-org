// SPDX-License-Identifier: Apache-2.0
/**
 * The governed-case decision, end to end across both systems (PRD 8.4 steps 5
 * and 6, PRD G-3, `spec/decision-grant.md`).
 *
 * Nothing here is stubbed. The console, the API and the database are the
 * development stack's; the decision requests, the approval page, the
 * identity provider, the step-up, the dwell measurement, the four-eyes rule
 * and the decision grants are the Grantex auth service's. The only thing the
 * test does that a person would not is click the buttons.
 *
 * What each test proves:
 *
 * 1. **A four-eyes decline** (PRD 8.4 step 6): the console asks for a decline
 *    on a case in `awaiting_decision`; two different people sign in on the
 *    auth service's own approval page with step-up and approve there; the
 *    same person is refused the second approval; the console records the
 *    decision and the case carries both approvers and both grant ids.
 * 2. **A single approval with step-up** (PRD 8.4 step 5), and the refusal
 *    that matters: when the case changes after the approval, recording the
 *    decision is refused with `case_changed` and the grants are spent on
 *    nothing.
 *
 * And throughout: no decision grant ever reaches this browser. Every response
 * the console receives is inspected for one.
 *
 * Run with `make e2e-decisions` (the runner shares the auth service's network
 * namespace, see docker-compose.dev.yml).
 */
import { expect, test, type APIRequestContext, type Browser, type BrowserContext, type Page } from "@playwright/test";
import { openCase, seededCase, signIn } from "./helpers/governed-cases";

/**
 * Required, with no default: a missing one is a broken run, not a reason to pass quietly. The
 * config requires BASE_URL the same way.
 */
function required(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is required: run this suite with \`make e2e-decisions\``);
  return value;
}

const ISSUER_ORIGIN = required("GRANTEX_APPROVAL_ORIGIN");
const ADMIN_API_KEY = required("GRANTEX_ADMIN_API_KEY");
const API_KEY = required("GRANTEX_API_KEY");
const APPROVER_ISSUER = required("GRANTEX_APPROVER_ISSUER");
const APPROVER_CLIENT_ID = "grantex-decision-approvers-dev";
const APPROVER_CLIENT_SECRET = "agenticorg-dev-only-decision-approver-secret";
const STEP_UP_ACR = "urn:agenticorg:acr:step-up";

/** The two fixture people in tools/oidc_stub/config.approvers.dev.json. */
const APPROVER_ONE = "case-approver-a";
const APPROVER_TWO = "case-approver-b";

/**
 * A decision grant is `typ: "decision+jwt"`; its header decodes to a JSON
 * object naming that type. Nothing the console receives may contain one.
 */
function looksLikeADecisionGrant(body: string): boolean {
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

/**
 * Watches everything this browser context is served - the console's own requests and the test's
 * `page.request` calls alike - for a decision grant. Bodies are read asynchronously, so the caller
 * awaits `settled()` before asserting, otherwise a response that arrived late would be inspected
 * after the assertion had already passed.
 */
function decisionGrantWatcher(
  context: BrowserContext,
): { settled: () => Promise<{ seen: string[]; inspected: number }> } {
  const seen: string[] = [];
  const reads: Promise<void>[] = [];
  context.on("response", (response) => {
    reads.push(
      response
        .text()
        .then((body) => {
          if (looksLikeADecisionGrant(body)) seen.push(response.url());
        })
        .catch(() => {
          // A body that is gone - a redirect, an aborted request - carried nothing.
        }),
    );
  });
  return {
    settled: async () => {
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
async function allowListApproverIdp(request: APIRequestContext): Promise<{ developerId: string; idpId: string }> {
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
async function approveOnTheIssuer(
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
async function requestDecisionInTheConsole(
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
async function cancelOpenRequests(page: Page, issuer: APIRequestContext, caseRef: string): Promise<void> {
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
async function consoleHeaders(page: Page): Promise<Record<string, string>> {
  const csrf = (await page.context().cookies()).find((c) => c.name === "agenticorg_csrf");
  return csrf ? { "X-CSRF-Token": csrf.value } : {};
}

async function refreshStatus(page: Page): Promise<void> {
  await Promise.all([
    page.waitForResponse((r) => r.url().includes("/decision-requests/") && r.request().method() === "GET"),
    page.getByRole("button", { name: "Refresh status" }).click(),
  ]);
}

test.describe("governed case decisions with real decision grants", () => {
  test.beforeAll(async ({ playwright }) => {
    const request = await playwright.request.newContext();
    try {
      await allowListApproverIdp(request);
    } finally {
      await request.dispose();
    }
  });

  test("a four-eyes decline is approved by two different people on the issuer's page and recorded here", async ({
    browser,
    page,
    request,
  }) => {
    const watcher = decisionGrantWatcher(page.context());
    const governed = seededCase("gb-clean-brightwater");

    await signIn(page);
    await openCase(page, governed.case_ref);
    await expect(page.getByTestId("case-state")).toContainText("Awaiting decision");
    await cancelOpenRequests(page, request, governed.case_ref);

    const created = await requestDecisionInTheConsole(
      page,
      "decline",
      "Two beneficial owners were withdrawn after the memo was written.",
    );
    // `decline` is in AGENTICORG_CASE_DECISION_FOUR_EYES_ON, so the issuer
    // bound the request to two approvals.
    expect(created.approvalsRequired).toBe(2);
    await expect(page.getByTestId("decision-approvals")).toContainText("0 of 2 approvals");

    // The first approver: their own browser, their own step-up.
    const first = await approveOnTheIssuer(browser, created.requestId, APPROVER_ONE);
    expect(first.title, first.body).toBe("Approved");
    expect(first.body).toContain("needs one more approval from a different person");

    await refreshStatus(page);
    await expect(page.getByTestId("decision-approvals")).toContainText("1 of 2 approvals");
    // The dwell in the grant is the service's own measurement, not ours.
    await expect(page.getByTestId("decision-approvals")).toContainText("measured by the approval page");
    await expect(page.getByTestId("four-eyes-waiting")).toBeVisible();
    await expect(page.getByTestId("record-decision")).toBeDisabled();

    // The same person again, in a clean browser: four eyes is on the
    // (issuer, subject) pair, not on the browser session.
    const twice = await approveOnTheIssuer(browser, created.requestId, APPROVER_ONE);
    expect(twice.title, twice.body).toBe("Already approved");
    expect(twice.body).toContain("needs a different second approver");
    await refreshStatus(page);
    await expect(page.getByTestId("decision-approvals")).toContainText("1 of 2 approvals");

    // A different person completes it.
    const second = await approveOnTheIssuer(browser, created.requestId, APPROVER_TWO);
    expect(second.title, second.body).toBe("Approved");
    expect(second.body).toContain("You can close this page");

    await refreshStatus(page);
    await expect(page.getByTestId("decision-approvals")).toContainText("2 of 2 approvals");
    await expect(page.getByTestId("decision-status")).toContainText(/Approved|Ready/i);
    await expect(page.getByTestId("record-decision")).toBeEnabled();

    const [recorded] = await Promise.all([
      page.waitForResponse((r) => /\/decision$/.test(r.url()) && r.request().method() === "POST"),
      page.getByTestId("record-decision").click(),
    ]);
    expect(recorded.status(), await recorded.text()).toBe(200);
    expect((await recorded.json()) as { state: string }).toMatchObject({ state: "decided" });

    const decided = await page.request.get(`/api/v1/governed-cases/${governed.case_ref}`);
    expect(decided.ok(), await decided.text()).toBeTruthy();
    const decision = ((await decided.json()) as {
      case: { state: string };
      decision: { outcome: string; approvers: { approver: string; decision_grant_id: string }[] };
    }).decision;

    expect(decision.outcome).toBe("decline");
    expect(decision.approvers).toHaveLength(2);
    // Two different people, and each one named with the single-use grant they
    // spent: the record has to say which credential was consumed.
    const subjects = decision.approvers.map((a) => a.approver);
    const grantIds = decision.approvers.map((a) => a.decision_grant_id);
    expect(new Set(subjects).size).toBe(2);
    expect(new Set(grantIds).size).toBe(2);
    for (const id of grantIds) expect(id).toMatch(/^dgnt_[0-9A-HJKMNP-TV-Z]{26}$/);
    for (const subject of subjects) expect(subject).toMatch(/^user:[A-Za-z0-9_-]{22}:case-approver-[ab]$/);

    await expect(page.getByTestId("case-state")).toContainText("Decided");
    await expect(page.getByTestId("case-decision")).toContainText(grantIds[0]!);

    const watched = await watcher.settled();
    expect(watched.inspected, "the decision-grant watcher saw no traffic at all").toBeGreaterThan(5);
    expect(watched.seen, "a decision grant reached the console's browser").toEqual([]);
  });

  test("a case that changed after the approval cannot be decided on it (case_changed)", async ({
    browser,
    page,
    request,
  }) => {
    const watcher = decisionGrantWatcher(page.context());
    // This fixture has a screening disposition a human can review, which is a
    // material change to the case and bumps its version.
    const governed = seededCase("us-false-positive-oakhollow");

    await signIn(page);
    await openCase(page, governed.case_ref);
    await cancelOpenRequests(page, request, governed.case_ref);

    // `approve` is not in the four-eyes list, so one approver is enough here:
    // that is PRD 8.4 step 5, a human approving with step-up.
    const created = await requestDecisionInTheConsole(
      page,
      "approve",
      "The screening hit is a name collision; the registry record and the owner list match the application.",
    );
    expect(created.approvalsRequired).toBe(1);

    const approval = await approveOnTheIssuer(browser, created.requestId, APPROVER_ONE);
    expect(approval.title, approval.body).toBe("Approved");

    await refreshStatus(page);
    await expect(page.getByTestId("decision-approvals")).toContainText("1 of 1 approvals");
    await expect(page.getByTestId("decision-approvals")).toContainText("measured by the approval page");

    // Now the case changes: an analyst reviews a screening hit. AgenticOrg
    // registers the new case version with the issuer, which supersedes the
    // open request and revokes the grant that was minted for the old one.
    const caseBefore = await page.request.get(`/api/v1/governed-cases/${governed.case_ref}`);
    expect(caseBefore.ok(), await caseBefore.text()).toBeTruthy();
    const disposition = ((await caseBefore.json()) as {
      screening_dispositions: { hit_id: string; proposed_outcome: string }[];
    }).screening_dispositions[0]!;
    const reviewed = await page.request.post(
      `/api/v1/governed-cases/${governed.case_ref}/screening-dispositions/${disposition.hit_id}/review`,
      { data: { action: "accepted", final_outcome: disposition.proposed_outcome }, headers: await consoleHeaders(page) },
    );
    expect(reviewed.ok(), await reviewed.text()).toBeTruthy();

    await page.reload();
    await expect(page.getByTestId("case-state")).toBeVisible();

    // The console refuses to offer the recording at all, and the API refuses
    // it if asked anyway.
    const refused = await page.request.post(`/api/v1/governed-cases/${governed.case_ref}/decision`, {
      data: { outcome: "approve", decision_request_id: created.requestId },
      headers: await consoleHeaders(page),
    });
    expect(refused.status(), await refused.text()).toBe(409);
    expect(await refused.text()).toContain("case_changed");

    // The case did not move, and nothing was recorded on it.
    const state = await page.request.get(`/api/v1/governed-cases/${governed.case_ref}`);
    const body = (await state.json()) as { case: { state: string }; decision: unknown };
    expect(body.case.state).toBe("awaiting_decision");
    expect(body.decision).toBeFalsy();

    const watched = await watcher.settled();
    expect(watched.inspected, "the decision-grant watcher saw no traffic at all").toBeGreaterThan(5);
    expect(watched.seen, "a decision grant reached the console's browser").toEqual([]);
  });
});
