// SPDX-License-Identifier: Apache-2.0
// Citations a reviewer can check (PRD §3 US-1, A-6): the passage behind a citation can be read,
// and a cited record the run never fetched is called out rather than presented as traced.
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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

const EXCERPT_REF = "excerpt:mock-watchlist-0007";
const EXCERPT_PATH = `/governed-cases/${CASE_REF}/excerpts/${encodeURIComponent(EXCERPT_REF)}`;
const PASSAGE = '{"aliases":["A. Pikworth"],"matched_name":"Ansel Pikworth"}';

function renderCase(detail: CaseDetail = caseDetailFixture(), excerpt: unknown = null) {
  mockGet.mockImplementation((url: string) => {
    if (url.includes("/excerpts/")) {
      return excerpt instanceof Error ? Promise.reject(excerpt) : Promise.resolve({ data: excerpt });
    }
    return Promise.resolve({ data: detail });
  });
  return render(
    <MemoryRouter initialEntries={[`/dashboard/approvals/cases/${CASE_REF}`]}>
      <Routes>
        <Route path="/dashboard/approvals/cases/:caseRef" element={<GovernedCaseDetail />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("citations are checked against the run's own tool calls", () => {
  beforeEach(() => {
    mockGet.mockReset();
  });

  it("says which cited records the run fetched", async () => {
    renderCase();
    const records = await screen.findByTestId("cited-records");
    const traced = within(records).getAllByTestId("record-traced");
    expect(traced.length).toBeGreaterThan(0);
    for (const note of traced) expect(note).toHaveTextContent("This run fetched this record.");
    expect(screen.queryByTestId("evidence-untraced")).not.toBeInTheDocument();
  });

  it("calls out a citation naming a record the run never returned", async () => {
    const detail = caseDetailFixture();
    detail.tool_calls[0].record_ids = ["mock:company:00000001:profile"];
    renderCase(detail);
    const untraced = await screen.findAllByTestId("evidence-untraced");
    expect(untraced[0]).toHaveTextContent("not in this run's tool calls");
    const records = screen.getAllByTestId("record-traced");
    expect(records.some((r) => r.textContent?.includes("never returned this record"))).toBe(true);
  });

  it("says the citations cannot be checked when the case carries no tool calls", async () => {
    // Failing open here would render a forged citation as an ordinary, legitimate one.
    renderCase(caseDetailFixture({ tool_calls: [] }));
    expect(await screen.findByTestId("citations-uncheckable")).toHaveTextContent(
      "cannot be checked against it",
    );
    expect(screen.queryByTestId("record-traced")).not.toBeInTheDocument();
    expect(screen.queryByTestId("evidence-untraced")).not.toBeInTheDocument();
  });

  it("does not accept a real record id claimed under another provider", async () => {
    const detail = caseDetailFixture();
    detail.tool_calls[0].provider = "another_provider";
    renderCase(detail);
    const untraced = await screen.findAllByTestId("evidence-untraced");
    expect(untraced.length).toBeGreaterThan(0);
  });
});

describe("the passage behind a citation", () => {
  beforeEach(() => {
    mockGet.mockReset();
  });

  it("is fetched only when the reviewer asks, and rendered as text", async () => {
    renderCase(caseDetailFixture(), {
      excerpt_ref: EXCERPT_REF,
      provider: "mock",
      record_id: "mock:watchlist:entry:0007",
      media_type: "application/json",
      sha256: `sha256:${"2".repeat(64)}`,
      text: PASSAGE,
    });
    const show = await screen.findByTestId("show-passage");
    expect(mockGet).not.toHaveBeenCalledWith(EXCERPT_PATH);

    fireEvent.click(show);
    const passage = await screen.findByTestId("excerpt-passage");
    expect(mockGet).toHaveBeenCalledWith(EXCERPT_PATH);
    expect(passage).toHaveTextContent("Ansel Pikworth");
    expect(passage).toHaveTextContent("The record as mock returned it, captured by the platform as application/json");
    expect(passage).toHaveTextContent("re-hashed when it was read back");
  });

  it("renders provider content as text, never as markup", async () => {
    const { container } = renderCase(caseDetailFixture(), {
      excerpt_ref: EXCERPT_REF,
      provider: "mock",
      record_id: "mock:watchlist:entry:0007",
      media_type: "application/json",
      sha256: `sha256:${"2".repeat(64)}`,
      text: '<img src=x onerror="alert(1)">',
    });
    fireEvent.click(await screen.findByTestId("show-passage"));
    await waitFor(() => expect(screen.getByTestId("excerpt-passage")).toBeInTheDocument());
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText('<img src=x onerror="alert(1)">')).toBeInTheDocument();
  });

  it("says so when the case does not hold the passage", async () => {
    renderCase(caseDetailFixture({ excerpts: [] }));
    expect(await screen.findByTestId("excerpt-not-held")).toHaveTextContent("does not hold this passage");
    expect(screen.queryByTestId("show-passage")).not.toBeInTheDocument();
  });

  it("surfaces a refusal instead of showing nothing", async () => {
    renderCase(caseDetailFixture(), axiosError(404, { error: { reason: "excerpt_not_found", detail: "" } }) as Error);
    fireEvent.click(await screen.findByTestId("show-passage"));
    expect(await screen.findByRole("alert")).toHaveTextContent("does not hold the passage");
    expect(screen.queryByTestId("excerpt-passage")).not.toBeInTheDocument();
  });

  it("shows nothing at all when the stored passage no longer matches its digest", async () => {
    renderCase(
      caseDetailFixture(),
      axiosError(409, { error: { reason: "excerpt_integrity_failed", detail: "" } }) as Error,
    );
    fireEvent.click(await screen.findByTestId("show-passage"));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("no longer matches the digest");
    expect(alert).toHaveTextContent("Treat this case as suspect");
    expect(screen.queryByTestId("excerpt-passage")).not.toBeInTheDocument();
  });
});
