// SPDX-License-Identifier: Apache-2.0
/**
 * The decision action (PRD A-9, G-3) against the local development stack.
 *
 * The first two tests are fully real: the console, the API and the database.
 * The development stack has no decision-grant issuer configured
 * (`AGENTICORG_CASE_DECISION_SERVICE` is unset), so they prove the two
 * refusals that matter - a decision through the API is refused with
 * `decision_required`, and the console says plainly that no issuer is
 * configured rather than offering an approval.
 *
 * The last test drives the console's four-eyes flow with the decision-request
 * endpoints answered at the network boundary (`page.route`), because a real
 * approval can only be made on the Grantex auth service's approval page by a
 * signed-in person stepping up, which the pinned development image does not
 * serve. Everything on this side of that boundary is the real console.
 */
import { expect, test, type Page } from "@playwright/test";
import {
  documentationScreenshot,
  expectNoAccessibilityViolations,
  missingPrerequisite,
  openCase,
  seededCase,
  signIn,
} from "./helpers/governed-cases";

const REQUEST_ID = "dr_e2e00000001";
const APPROVAL_PAGE = `https://auth.grantex.invalid/decisions/${REQUEST_ID}`;

interface Approval {
  approver: string;
  approver_auth: string;
  dwell_ms: number;
  dwell_source: string;
  position: number;
  issued_at: string;
  consumed_at: null;
}

function statusBody(approvals: Approval[]) {
  return {
    request_id: REQUEST_ID,
    status: approvals.length >= 2 ? "approved" : "pending",
    approval_page: APPROVAL_PAGE,
    action: { case_id: "case", action: "case_decision", decision: "decline", subject: "mock:x" },
    action_hash: `sha256:${"1".repeat(64)}`,
    case_version: "4",
    approvals_required: 2,
    approvals_received: approvals.length,
    grants_ready: approvals.length >= 2,
    expires_at: "2026-09-21T10:00:00Z",
    approvals,
    outcome: "decline",
    override_reason: "The applicant withdrew two owners.",
    case_version_now: "4",
    case_changed: false,
  };
}

const APPROVER_A: Approval = {
  approver: "user:9f:approver-a",
  approver_auth: "sso+webauthn",
  dwell_ms: 61_250,
  dwell_source: "server",
  position: 1,
  issued_at: "2026-09-20T10:00:00Z",
  consumed_at: null,
};
const APPROVER_B: Approval = { ...APPROVER_A, approver: "user:9f:approver-b", position: 2, dwell_ms: 42_000 };

/** Answers the decision-request endpoints as the issuer would, through the API's shapes. */
async function stubIssuer(page: Page, state: { approvals: Approval[]; recorded: unknown[] }): Promise<void> {
  await page.route("**/api/v1/governed-cases/*/decision-requests**", async (route) => {
    const method = route.request().method();
    if (method === "POST") {
      await route.fulfill({ status: 201, json: statusBody(state.approvals) });
      return;
    }
    await route.fulfill({ status: 200, json: statusBody(state.approvals) });
  });
  await page.route("**/api/v1/governed-cases/*/decision", async (route) => {
    state.recorded.push(route.request().postDataJSON());
    await route.fulfill({ status: 200, json: { case_ref: "case", state: "decided" } });
  });
}

test.describe("the decision action @dev-stack", () => {
  test.skip(() => missingPrerequisite() !== "", "governed case seed or sign-in password missing");

  test.beforeEach(async ({ page }) => {
    await signIn(page);
  });

  test("the console offers no approval of its own, and the API refuses a decision without a grant", async ({
    page,
  }, testInfo) => {
    const clean = seededCase("gb-clean-brightwater");
    await openCase(page, clean.case_ref);

    const panel = page.getByTestId("decision-panel");
    await expect(panel).toBeVisible();
    await expect(panel).toContainText("This console cannot approve anything");
    await expect(panel.getByRole("button", { name: "Request decision" })).toBeVisible();
    await expect(panel.getByRole("button", { name: /^Approve$/ })).toHaveCount(0);
    await expect(page.getByTestId("record-decision")).toHaveCount(0);
    await documentationScreenshot(page, "governed-case-decision-request");
    await expectNoAccessibilityViolations(page, testInfo);

    // PRD §8.4 step 4: a decision straight through the API is refused.
    const cookies = await page.context().cookies();
    const csrf = cookies.find((c) => c.name === "agenticorg_csrf")?.value ?? "";
    const refused = await page.request.post(`/api/v1/governed-cases/${clean.case_ref}/decision`, {
      headers: { "X-CSRF-Token": csrf, "content-type": "application/json" },
      data: { outcome: "approve" },
    });
    expect(refused.status()).toBe(403);
    expect(await refused.json()).toMatchObject({ error: { reason: "decision_required" } });
  });

  test("with no issuer configured the console says so instead of pretending", async ({ page }) => {
    const clean = seededCase("gb-clean-brightwater");
    await openCase(page, clean.case_ref);

    await page.getByRole("radio", { name: "Decline" }).click();
    await page
      .getByLabel(/Reason for a decision other than the recommendation/)
      .fill("Testing the refusal path: no issuer is configured on this stack.");
    await page.getByRole("button", { name: "Request decision" }).click();

    const alert = page.getByTestId("decision-panel").getByRole("alert");
    await expect(alert).toBeVisible({ timeout: 20_000 });
    await expect(alert).toContainText("decision_service_not_configured");
    await expect(page.getByTestId("record-decision")).toHaveCount(0);
  });

  test("four eyes: the first approval, then a different second approver, then recording", async ({ page }) => {
    const clean = seededCase("gb-clean-brightwater");
    const state = { approvals: [] as Approval[], recorded: [] as unknown[] };
    await stubIssuer(page, state);
    await openCase(page, clean.case_ref);

    await page.getByRole("radio", { name: "Decline" }).click();
    await page
      .getByLabel(/Reason for a decision other than the recommendation/)
      .fill("The applicant withdrew two owners.");
    await page.getByRole("button", { name: "Request decision" }).click();

    const request = page.getByTestId("decision-request");
    await expect(request).toBeVisible({ timeout: 20_000 });
    await expect(request).toContainText("two approvers required");
    await expect(page.getByTestId("record-decision")).toBeDisabled();

    state.approvals = [APPROVER_A];
    await page.getByRole("button", { name: "Refresh status" }).click();
    const waiting = page.getByTestId("four-eyes-waiting");
    await expect(waiting).toBeVisible();
    await expect(waiting).toContainText("user:9f:approver-a");
    await expect(waiting).toContainText("refuses the same person twice");
    await expect(page.getByTestId("decision-approvals")).toContainText("measured by the approval page");
    await expect(page.getByTestId("record-decision")).toBeDisabled();
    await documentationScreenshot(page, "governed-case-decision-four-eyes");

    state.approvals = [APPROVER_A, APPROVER_B];
    await page.getByRole("button", { name: "Refresh status" }).click();
    await expect(page.getByTestId("decision-status")).toContainText("ready to record");
    await expect(page.getByTestId("record-decision")).toBeEnabled();

    await page.getByTestId("record-decision").click();
    await expect.poll(() => state.recorded.length).toBeGreaterThan(0);
    const body = state.recorded[0] as Record<string, unknown>;
    expect(body.decision_request_id).toBe(REQUEST_ID);
    expect(body.outcome).toBe("decline");
    // Advisory console dwell, measured from the case screen rendering.
    expect(typeof body.client_dwell_ms).toBe("number");
    // No decision grant ever passes through the browser.
    expect(JSON.stringify(body)).not.toContain("decision_grants");
  });
});
