// SPDX-License-Identifier: Apache-2.0
// Screening disposition review on the case screen (PRD §3 US-3, A-9).
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  CASE_REF,
  HIT_ID,
  axiosError,
  caseDetailFixture,
  dispositionFixture,
  screeningResultFixture,
} from "./fixtures/governedCase";
import type { CaseDetail, ScreeningDisposition } from "@/lib/governedCases";

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

function detailWith(disposition: ScreeningDisposition, overrides: Partial<CaseDetail> = {}): CaseDetail {
  return caseDetailFixture({
    screening_dispositions: [disposition],
    screening_results: [screeningResultFixture()],
    ...overrides,
  });
}

function renderCase(detail: CaseDetail) {
  mockGet.mockResolvedValue({ data: detail });
  return render(
    <MemoryRouter initialEntries={[`/dashboard/approvals/cases/${CASE_REF}`]}>
      <Routes>
        <Route path="/dashboard/approvals/cases/:caseRef" element={<GovernedCaseDetail />} />
      </Routes>
    </MemoryRouter>,
  );
}

const REVIEW_PATH = `/governed-cases/${CASE_REF}/screening-dispositions/${HIT_ID}/review`;

describe("screening dispositions: per-identifier comparison (US-3)", () => {
  beforeEach(() => {
    mockGet.mockReset();
    mockPost.mockReset();
  });

  it("shows one comparison row per identifier with both values and the result", async () => {
    renderCase(detailWith(dispositionFixture()));
    const table = await screen.findByTestId("comparison-table");
    for (const identifier of ["Name", "Date of birth", "Nationality", "Address", "Associated entities"]) {
      expect(within(table).getByRole("rowheader", { name: identifier })).toBeInTheDocument();
    }
    const nameRow = within(table).getByRole("rowheader", { name: "Name" }).closest("tr") as HTMLElement;
    expect(nameRow).toHaveTextContent("Ansel Pikeworth");
    expect(nameRow).toHaveTextContent("Ansel Pikworth");
    expect(nameRow).toHaveTextContent("Partial match");
    expect(nameRow).toHaveTextContent("Normalised names are 0.94 similar.");
    const address = within(table).getByRole("rowheader", { name: "Address" }).closest("tr") as HTMLElement;
    expect(address).toHaveTextContent("Not comparable");
  });

  it("shows the hit, the proposed outcome, the confidence band and the cited evidence", async () => {
    renderCase(detailWith(dispositionFixture()));
    const card = await screen.findByTestId("screening-disposition");
    expect(within(card).getByRole("heading", { level: 3 })).toHaveTextContent("Ansel Pikworth");
    expect(card).toHaveTextContent("sanctions");
    expect(card).toHaveTextContent("Example sanctions list");
    expect(within(card).getByTestId("proposed-outcome")).toHaveTextContent("Proposed: False positive");
    expect(card).toHaveTextContent("Confidence band: high");
    expect(card).toHaveTextContent("Proposed outcome: false positive.");
    expect(within(card).getAllByTestId("evidence-entry")[0]).toHaveTextContent("mock:watchlist:wl-0001");
  });
});

describe("screening dispositions: accepting and overriding", () => {
  beforeEach(() => {
    mockGet.mockReset();
    mockPost.mockReset();
  });

  it("accepts the proposal with the proposed outcome and no identity in the request", async () => {
    const disposition = dispositionFixture();
    renderCase(detailWith(disposition));
    mockPost.mockResolvedValue({ data: { ...disposition, review: null } });
    fireEvent.click(await screen.findByRole("button", { name: "Record review" }));
    await waitFor(() => expect(mockPost).toHaveBeenCalledTimes(1));
    expect(mockPost).toHaveBeenCalledWith(REVIEW_PATH, { action: "accepted", final_outcome: "false_positive" });
    // The case is reloaded so the recorded review comes from the server.
    await waitFor(() => expect(mockGet).toHaveBeenCalledTimes(2));
  });

  it("refuses an override without a reason and sends nothing", async () => {
    renderCase(detailWith(dispositionFixture()));
    fireEvent.click(await screen.findByRole("radio", { name: "Override it" }));
    const submit = screen.getByRole("button", { name: "Record review" });
    expect(submit).toBeDisabled();
    fireEvent.submit(screen.getByTestId("disposition-review-form"));
    expect(await screen.findByRole("alert")).toHaveTextContent("An override needs a written reason.");
    expect(mockPost).not.toHaveBeenCalled();
  });

  it("sends an override with its outcome and the analyst's written reason", async () => {
    const disposition = dispositionFixture();
    renderCase(detailWith(disposition));
    fireEvent.click(await screen.findByRole("radio", { name: "Override it" }));
    fireEvent.change(screen.getByLabelText("Outcome"), { target: { value: "true_match" } });
    fireEvent.change(screen.getByLabelText("Reason for the override (required)"), {
      target: { value: "  Date of birth in the filing matches the list entry.  " },
    });
    mockPost.mockResolvedValue({ data: disposition });
    fireEvent.click(screen.getByRole("button", { name: "Record review" }));
    await waitFor(() =>
      expect(mockPost).toHaveBeenCalledWith(REVIEW_PATH, {
        action: "overridden",
        final_outcome: "true_match",
        reason: "Date of birth in the filing matches the list entry.",
      }),
    );
  });

  it("offers only outcomes other than the proposed one for an override", async () => {
    renderCase(detailWith(dispositionFixture()));
    fireEvent.click(await screen.findByRole("radio", { name: "Override it" }));
    const options = within(screen.getByLabelText("Outcome")).getAllByRole("option").map((o) => o.textContent);
    expect(options).toEqual(["True match", "Insufficient information"]);
  });

  it("surfaces the API's refusal rather than pretending the review was recorded", async () => {
    renderCase(detailWith(dispositionFixture()));
    mockPost.mockRejectedValue(axiosError(409, { error: { reason: "already_reviewed", detail: "" } }));
    fireEvent.click(await screen.findByRole("button", { name: "Record review" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("already been reviewed");
    expect(await screen.findByRole("alert")).toHaveTextContent("already_reviewed");
  });
});

describe("screening dispositions: a review is written once and only while the case waits", () => {
  beforeEach(() => {
    mockGet.mockReset();
    mockPost.mockReset();
  });

  it("shows a recorded review with its analyst, time and reason, and offers no second review", async () => {
    renderCase(
      detailWith(
        dispositionFixture({
          review: {
            action: "overridden",
            final_outcome: "true_match",
            analyst_id: "user:00000000-0000-0000-0000-000000000001",
            reviewed_at: "2026-09-01T10:00:00Z",
            reason: "Date of birth in the filing matches the list entry.",
          },
        }),
      ),
    );
    const review = await screen.findByTestId("disposition-review");
    expect(review).toHaveTextContent("Overridden");
    expect(review).toHaveTextContent("True match");
    expect(review).toHaveTextContent("user:00000000-0000-0000-0000-000000000001");
    expect(review).toHaveTextContent("Date of birth in the filing matches the list entry.");
    expect(review).toHaveTextContent("does not close the hit in any system");
    expect(screen.queryByTestId("disposition-review-form")).not.toBeInTheDocument();
  });

  it("offers no review form once the case has left awaiting_decision", async () => {
    const base = caseDetailFixture();
    renderCase(
      detailWith(dispositionFixture(), {
        case: { ...base.case, state: "decided", decision: { outcome: "approve", approvers: [], decided_at: "2026-09-02T10:00:00Z" } },
      }),
    );
    expect(await screen.findByTestId("review-not-open")).toHaveTextContent(
      "only while the case is awaiting a decision",
    );
    expect(screen.queryByTestId("disposition-review-form")).not.toBeInTheDocument();
  });
});
