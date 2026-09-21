// SPDX-License-Identifier: Apache-2.0
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { CASE_REF, axiosError, caseDetailFixture } from "./fixtures/governedCase";
import type { CaseDetail } from "@/lib/governedCases";

const mockGet = vi.fn();

vi.mock("../lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    post: vi.fn(),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  },
  extractApiError: () => "request failed",
}));

import GovernedCaseDetail from "@/pages/GovernedCaseDetail";

function renderCase(detail: CaseDetail = caseDetailFixture()) {
  mockGet.mockResolvedValue({ data: detail });
  return renderRoute();
}

function renderRefusal(error: unknown) {
  mockGet.mockImplementation(() => Promise.reject(error));
  return renderRoute();
}

function renderRoute() {
  return render(
    <MemoryRouter initialEntries={[`/dashboard/approvals/cases/${CASE_REF}`]}>
      <Routes>
        <Route path="/dashboard/approvals/cases/:caseRef" element={<GovernedCaseDetail />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("case detail: memo with citations (US-1)", () => {
  beforeEach(() => {
    mockGet.mockReset();
  });

  it("loads the case by its reference", async () => {
    renderCase();
    await screen.findByRole("heading", { level: 1, name: "Marlpit Orchard Example Ltd" });
    expect(mockGet).toHaveBeenCalledWith(`/governed-cases/${CASE_REF}`);
  });

  it("renders every memo section with each evidence entry's provider, record, field and retrieval time", async () => {
    renderCase();
    const ownership = await screen.findByTestId("memo-section-ownership");
    expect(within(ownership).getByText(/does not appear in the ownership graph/)).toBeInTheDocument();
    const entries = within(ownership).getAllByTestId("evidence-entry");
    expect(entries.length).toBeGreaterThanOrEqual(2);
    for (const entry of entries) {
      expect(entry).toHaveTextContent("mock");
      expect(entry).toHaveTextContent("mock:company:00000001:owners");
      expect(entry).toHaveTextContent("field items");
      expect(entry.querySelector("time")).toHaveAttribute("dateTime", "2026-09-01T09:00:00Z");
    }
  });

  it("links every citation to a cited record that exists on the page", async () => {
    const { container } = renderCase();
    await screen.findByTestId("cited-records");
    const links = container.querySelectorAll<HTMLAnchorElement>('[data-testid="evidence-entry"] a[href^="#cited-record-"]');
    expect(links.length).toBeGreaterThan(0);
    for (const link of links) {
      const target = container.querySelector(link.getAttribute("href") ?? "");
      expect(target).not.toBeNull();
      expect(target).toHaveTextContent(link.textContent ?? "");
    }
  });

  it("links an attached excerpt reference and marks one the memo does not carry", async () => {
    const { container } = renderCase();
    const screening = await screen.findByTestId("memo-section-screening");
    const excerptLink = within(screening).getAllByRole("link", { name: "Excerpt excerpt:mock-watchlist-0007" })[0];
    expect(container.querySelector(excerptLink.getAttribute("href") ?? "")).toHaveTextContent(
      "sha256:0000000000000000000000000000000000000000000000000000000000000002",
    );
    expect(within(screening).getByText(/excerpt:not-attached \(not attached to this memo\)/)).toBeInTheDocument();
  });

  it("renders a not_available section as unchecked, not as a clear result", async () => {
    renderCase();
    const web = await screen.findByTestId("memo-section-web_presence");
    expect(within(web).getByTestId("section-status")).toHaveTextContent("Not available");
    expect(within(web).getByTestId("section-not-available")).toHaveTextContent(
      "The verification provider does not offer this data",
    );
    expect(within(web).getByTestId("section-not-available")).toHaveTextContent("not as a clear result");
    expect(within(web).queryByText("No findings.")).not.toBeInTheDocument();
  });

  it("renders a provider error section with its reason", async () => {
    renderCase();
    const activity = await screen.findByTestId("memo-section-activity");
    expect(within(activity).getByTestId("section-status")).toHaveTextContent("Provider error");
    expect(within(activity).getByRole("note")).toHaveTextContent("The provider did not answer in time.");
  });

  it("shows the recommendation as a proposal needing a human decision, with missing items", async () => {
    renderCase();
    expect(await screen.findByTestId("memo-recommendation")).toHaveTextContent("Refer");
    expect(screen.getByText(/needs a human decision/)).toBeInTheDocument();
    expect(screen.getByTestId("memo-missing-items")).toHaveTextContent("owner_evidence");
  });

  it("treats agent-authored text as text, never markup", async () => {
    const detail = caseDetailFixture();
    detail.memo!.sections[1].findings[0].statement = '<img src=x onerror="alert(1)">';
    const { container } = renderCase(detail);
    await screen.findByTestId("memo-section-ownership");
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText('<img src=x onerror="alert(1)">')).toBeInTheDocument();
  });
});

describe("case detail: policy score with fired rules", () => {
  beforeEach(() => {
    mockGet.mockReset();
  });

  it("shows the policy version, score, tier and every fired rule with its inputs", async () => {
    renderCase();
    const policy = await screen.findByTestId("policy-score");
    expect(within(policy).getByTestId("policy-score-value")).toHaveTextContent("40");
    expect(within(policy).getAllByText("Medium risk").length).toBeGreaterThan(0);
    expect(policy).toHaveTextContent("business_onboarding_uk version 1.2.0");
    const rule = within(policy).getByTestId("policy-rule");
    expect(rule).toHaveTextContent("ownership_reconciled");
    expect(rule).toHaveTextContent("Declared owners do not reconcile with the ownership graph");
    const inputs = within(rule).getByRole("table");
    expect(inputs).toHaveTextContent("ownership.missing_owners");
    expect(inputs).toHaveTextContent("1");
    expect(inputs).toHaveTextContent("verification.status");
    expect(inputs).toHaveTextContent("missing");
  });

  it("warns when the policy is an unreviewed example", async () => {
    renderCase();
    expect(await screen.findByTestId("policy-example-warning")).toHaveTextContent("must not be used for real decisions");
  });
});

describe("case detail: states and refusals", () => {
  beforeEach(() => {
    mockGet.mockReset();
  });

  it("explains a case without a memo by its state", async () => {
    renderCase(caseDetailFixture({ memo: null, policy_result: null, case: { ...caseDetailFixture().case, state: "in_progress" } }));
    expect(await screen.findByTestId("memo-not-ready")).toHaveTextContent("The agents are investigating this case.");
  });

  it("shows the failure reason of a failed investigation", async () => {
    renderCase(
      caseDetailFixture({
        memo: null,
        policy_result: null,
        failure_reason: "tool_refused:grant_missing",
        case: { ...caseDetailFixture().case, state: "failed" },
      }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("tool_refused:grant_missing");
  });

  it("does not reveal whether a case exists in another organisation", async () => {
    renderRefusal(axiosError(404, { error: { reason: "case_not_found", detail: "" } }));
    const alert = await screen.findByTestId("governed-case-error");
    expect(alert).toHaveTextContent("does not exist or belongs to another organisation");
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
  });
});
