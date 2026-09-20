// SPDX-License-Identifier: Apache-2.0
// The decision action (PRD A-9): approving without step-up is impossible in the UI, the four-eyes
// state is visible, the second approver cannot be the first, and dwell is recorded render-to-submit.
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { CASE_REF, axiosError, caseDetailFixture } from "./fixtures/governedCase";
import type { CaseDetail, DecisionRequestView } from "@/lib/governedCases";

const mockGet = vi.fn();
const mockPost = vi.fn();

vi.mock("../lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  },
  extractApiError: () => "request failed",
}));

vi.mock("../contexts/AuthContext", () => ({
  useAuth: () => ({ user: { email: "approver.a@example.com", user_id: "u-1", role: "domain_lead" } }),
}));

import GovernedCaseDetail from "@/pages/GovernedCaseDetail";

const REQUEST_PATH = `/governed-cases/${CASE_REF}/decision-requests`;
const DECISION_PATH = `/governed-cases/${CASE_REF}/decision`;

function requestView(overrides: Partial<DecisionRequestView> = {}): DecisionRequestView {
  return {
    request_id: "dr_00000001",
    status: "pending",
    approval_page: "https://auth.grantex.invalid/decisions/dr_00000001",
    action: { case_id: CASE_REF, action: "case_decision", decision: "decline", subject: "mock:mock-gb-00000001" },
    action_hash: `sha256:${"1".repeat(64)}`,
    case_version: "4",
    approvals_required: 2,
    approvals_received: 0,
    grants_ready: false,
    expires_at: "2026-09-21T10:00:00Z",
    approvals: [],
    outcome: "decline",
    override_reason: "The applicant withdrew two owners.",
    case_version_now: "4",
    case_changed: false,
    ...overrides,
  };
}

const FIRST_APPROVAL = {
  approver: "user:9f:approver-a",
  approver_auth: "sso+webauthn",
  dwell_ms: 61_250,
  dwell_source: "server",
  position: 1,
  issued_at: "2026-09-20T10:00:00Z",
  consumed_at: null,
};

/** What the status endpoint answers while the test runs. */
function setStatus(view: DecisionRequestView, detail: CaseDetail = caseDetailFixture()) {
  mockGet.mockImplementation((url: string) =>
    Promise.resolve({ data: url.includes("/decision-requests/") ? view : detail }),
  );
}

function renderCase(detail: CaseDetail = caseDetailFixture(), status: DecisionRequestView = requestView()) {
  setStatus(status, detail);
  return render(
    <MemoryRouter initialEntries={[`/dashboard/approvals/cases/${CASE_REF}`]}>
      <Routes>
        <Route path="/dashboard/approvals/cases/:caseRef" element={<GovernedCaseDetail />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("decision action: the console cannot approve", () => {
  beforeEach(() => {
    mockGet.mockReset();
    mockPost.mockReset();
  });

  it("offers no approve control at all, only a request for a decision", async () => {
    renderCase();
    const panel = await screen.findByTestId("decision-panel");
    expect(within(panel).getByRole("button", { name: "Request decision" })).toBeInTheDocument();
    expect(within(panel).queryByRole("button", { name: /^Approve$/ })).not.toBeInTheDocument();
    expect(within(panel).queryByTestId("record-decision")).not.toBeInTheDocument();
    expect(panel).toHaveTextContent("This console cannot approve anything");
    expect(panel).toHaveTextContent("steps up");
  });

  it("sends the outcome, the override reason and the advisory console dwell", async () => {
    renderCase();
    await screen.findByTestId("decision-panel");
    fireEvent.click(screen.getByRole("radio", { name: "Decline" }));
    fireEvent.change(screen.getByLabelText(/Reason for a decision other than the recommendation/), {
      target: { value: "  The applicant withdrew two owners.  " },
    });
    mockPost.mockResolvedValue({ data: requestView() });
    fireEvent.click(screen.getByRole("button", { name: "Request decision" }));

    await waitFor(() => expect(mockPost).toHaveBeenCalledTimes(1));
    const [path, body] = mockPost.mock.calls[0];
    expect(path).toBe(REQUEST_PATH);
    expect(body.outcome).toBe("decline");
    expect(body.override_reason).toBe("The applicant withdrew two owners.");
    expect(typeof body.client_dwell_ms).toBe("number");
    expect(body.client_dwell_ms).toBeGreaterThanOrEqual(0);
  });

  it("will not ask for a decision that differs from the recommendation without a reason", async () => {
    renderCase();
    await screen.findByTestId("decision-panel");
    fireEvent.click(screen.getByRole("radio", { name: "Decline" }));
    expect(screen.getByRole("button", { name: "Request decision" })).toBeDisabled();
    fireEvent.submit(screen.getByTestId("decision-request-form"));
    expect(await screen.findByRole("alert")).toHaveTextContent("needs a written reason");
    expect(mockPost).not.toHaveBeenCalled();
  });

  it("surfaces a deployment without a decision-grant issuer instead of pretending", async () => {
    renderCase();
    await screen.findByTestId("decision-panel");
    mockPost.mockRejectedValue(
      axiosError(503, { error: { reason: "decision_service_not_configured", detail: "" } }),
    );
    fireEvent.click(screen.getByRole("radio", { name: "Approve" }));
    fireEvent.change(screen.getByLabelText(/Reason for a decision other than the recommendation/), {
      target: { value: "The owner evidence arrived." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Request decision" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("No decision-grant issuer is configured");
    expect(alert).toHaveTextContent("decision_service_not_configured");
  });
});

describe("decision action: approval page, four eyes and recording", () => {
  beforeEach(() => {
    mockGet.mockReset();
    mockPost.mockReset();
  });

  async function requestDecision(view: DecisionRequestView = requestView()) {
    renderCase(caseDetailFixture(), view);
    await screen.findByTestId("decision-panel");
    fireEvent.click(screen.getByRole("radio", { name: "Decline" }));
    fireEvent.change(screen.getByLabelText(/Reason for a decision other than the recommendation/), {
      target: { value: "The applicant withdrew two owners." },
    });
    mockPost.mockResolvedValueOnce({ data: view });
    fireEvent.click(screen.getByRole("button", { name: "Request decision" }));
    return screen.findByTestId("decision-request");
  }

  it("opens the issuer's approval page in a new window, never in this app", async () => {
    const open = vi.spyOn(window, "open").mockReturnValue(null);
    await requestDecision();
    fireEvent.click(screen.getByTestId("open-approval-page"));
    expect(open).toHaveBeenCalledWith(
      "https://auth.grantex.invalid/decisions/dr_00000001",
      "_blank",
      "noopener,noreferrer",
    );
    open.mockRestore();
  });

  it("refuses an approval page address that is not an http(s) URL", async () => {
    const open = vi.spyOn(window, "open").mockReturnValue(null);
    await requestDecision(requestView({ approval_page: "javascript:alert(1)" }));
    fireEvent.click(screen.getByTestId("open-approval-page"));
    expect(open).not.toHaveBeenCalled();
    expect(await screen.findByRole("alert")).toHaveTextContent("not return a usable approval page");
    open.mockRestore();
  });

  it("shows the four-eyes state: the first approval, its server-measured dwell and who cannot be second", async () => {
    await requestDecision();
    setStatus(requestView({ approvals: [FIRST_APPROVAL], approvals_received: 1 }));
    fireEvent.click(screen.getByRole("button", { name: "Refresh status" }));

    const approvals = await screen.findByTestId("decision-approvals");
    await waitFor(() => expect(approvals).toHaveTextContent("1 of 2 approvals"));
    expect(approvals).toHaveTextContent("First approver: user:9f:approver-a");
    expect(approvals).toHaveTextContent("sso+webauthn");
    expect(approvals).toHaveTextContent("61 s");
    expect(approvals).toHaveTextContent("measured by the approval page");
    const waiting = screen.getByTestId("four-eyes-waiting");
    expect(waiting).toHaveTextContent("Waiting for a second approver");
    expect(waiting).toHaveTextContent("user:9f:approver-a");
    expect(waiting).toHaveTextContent("refuses the same person twice");
    expect(screen.getByTestId("record-decision")).toBeDisabled();
  });

  it("records the decision only once both grants exist, and reloads the case", async () => {
    await requestDecision();
    const approved = requestView({
      status: "approved",
      grants_ready: true,
      approvals_received: 2,
      approvals: [FIRST_APPROVAL, { ...FIRST_APPROVAL, approver: "user:9f:approver-b", position: 2, dwell_ms: 30_000 }],
    });
    setStatus(approved);
    fireEvent.click(screen.getByRole("button", { name: "Refresh status" }));
    await waitFor(() => expect(screen.getByTestId("record-decision")).toBeEnabled());

    mockPost.mockResolvedValueOnce({ data: { case_ref: CASE_REF, state: "decided" } });
    fireEvent.click(screen.getByTestId("record-decision"));
    await waitFor(() => expect(mockPost).toHaveBeenCalledTimes(2));
    const [path, body] = mockPost.mock.calls[1];
    expect(path).toBe(DECISION_PATH);
    expect(body).toMatchObject({ outcome: "decline", decision_request_id: "dr_00000001" });
    expect(typeof body.client_dwell_ms).toBe("number");
    // No decision grant is ever handled by the browser.
    expect(JSON.stringify(body)).not.toContain("decision_grants");
  });

  it("surfaces decision_required and a same-approver refusal from the API", async () => {
    await requestDecision(requestView({ status: "approved", grants_ready: true, approvals_received: 2 }));
    mockPost.mockRejectedValueOnce(axiosError(403, { error: { reason: "decision_required", detail: "" } }));
    fireEvent.click(screen.getByTestId("record-decision"));
    expect(await screen.findByRole("alert")).toHaveTextContent("No decision grant proves a person decided this");

    mockPost.mockRejectedValueOnce(
      axiosError(403, { error: { reason: "decision_invalid", detail: "same_approver" } }),
    );
    fireEvent.click(screen.getByTestId("record-decision"));
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("must come from a different person than the first"),
    );
    expect(screen.getByRole("alert")).toHaveTextContent("same_approver");
  });

  it("stops the decision when the case changed after the approval", async () => {
    await requestDecision(
      requestView({ status: "approved", grants_ready: true, approvals_received: 2, case_changed: true, case_version_now: "5" }),
    );
    expect(screen.getByTestId("decision-case-changed")).toHaveTextContent("now version 5");
    expect(screen.getByTestId("record-decision")).toBeDisabled();
  });
});

describe("decision action: a decided case", () => {
  beforeEach(() => {
    mockGet.mockReset();
    mockPost.mockReset();
  });

  it("shows the approvers and the decision grants that were consumed, and no controls", async () => {
    const base = caseDetailFixture();
    const decision = {
      outcome: "decline" as const,
      approvers: [
        { approver: "user:9f:approver-a", decision_grant_id: "j-1" },
        { approver: "user:9f:approver-b", decision_grant_id: "j-2" },
      ],
      decided_at: "2026-09-20T11:00:00Z",
    };
    renderCase({ ...base, case: { ...base.case, state: "decided", decision }, decision });
    const record = await screen.findByTestId("case-decision");
    expect(record).toHaveTextContent("Decline");
    expect(record).toHaveTextContent("user:9f:approver-a");
    expect(record).toHaveTextContent("j-2");
    expect(screen.queryByTestId("decision-request-form")).not.toBeInTheDocument();
    expect(screen.queryByTestId("record-decision")).not.toBeInTheDocument();
  });
});
