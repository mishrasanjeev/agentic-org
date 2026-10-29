// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
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
import { expect, test } from "@playwright/test";
import {
  allowListApproverIdp,
  APPROVER_ONE,
  APPROVER_TWO,
  approveOnTheIssuer,
  cancelOpenRequests,
  consoleHeaders,
  decisionGrantWatcher,
  refreshStatus,
  requestDecisionInTheConsole,
} from "./helpers/decision-grants";
import { seededCase, signIn } from "./helpers/governed-cases";

test.describe("governed case decisions with real decision grants", () => {
  test.beforeAll(async ({ playwright }) => {
    const request = await playwright.request.newContext();
    try {
      await allowListApproverIdp(request);
    } finally {
      await request.dispose();
    }
  });

  test("case sign-in does not abandon slow dashboard API responses", async ({ page }) => {
    const watcher = decisionGrantWatcher(page.context());
    const governed = seededCase("gb-clean-brightwater");
    const casePath = `/dashboard/approvals/cases/${governed.case_ref}`;
    const navigations: string[] = [];
    page.on("framenavigated", (frame) => {
      if (frame === page.mainFrame()) navigations.push(new URL(frame.url()).pathname);
    });
    let releaseResponses!: () => void;
    const heldResponses = new Promise<void>((resolve) => { releaseResponses = resolve; });
    let held = 0;
    let delivered = 0;
    // Delay real server responses, not fixture bodies. A hard navigation from
    // the role landing page used to strand these in Chromium's network events.
    await page.route(/\/api\/v1\/(companies(?:\?|$)|approvals\?)/, async (route) => {
      const response = await route.fetch();
      held += 1;
      await heldResponses;
      await route.fulfill({ response });
      delivered += 1;
    });
    try {
      await signIn(page, undefined, casePath);
      await expect(page.getByTestId("case-state")).toBeVisible({ timeout: 20_000 });
      await expect.poll(() => held).toBeGreaterThanOrEqual(2);
      expect(navigations.filter((path) => path !== "/login")).toEqual([casePath]);
    } finally {
      releaseResponses();
    }
    await expect.poll(() => delivered).toBeGreaterThanOrEqual(2);
    const watched = await watcher.settled();
    expect(watched.inspected).toBeGreaterThan(5);
    expect(watched.seen).toEqual([]);
  });

  test("a four-eyes decline is approved by two different people on the issuer's page and recorded here", async ({
    browser,
    page,
    request,
  }) => {
    const watcher = decisionGrantWatcher(page.context());
    const governed = seededCase("gb-clean-brightwater");

    await signIn(page, undefined, `/dashboard/approvals/cases/${governed.case_ref}`);
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

    await signIn(page, undefined, `/dashboard/approvals/cases/${governed.case_ref}`);
    await expect(page.getByTestId("case-state")).toBeVisible({ timeout: 20_000 });
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
