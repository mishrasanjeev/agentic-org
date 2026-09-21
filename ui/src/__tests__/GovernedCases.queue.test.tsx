// SPDX-License-Identifier: Apache-2.0
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { axiosError, summaryFixture } from "./fixtures/governedCase";

const mockGet = vi.fn();

vi.mock("../lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    post: vi.fn(),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  },
  extractApiError: () => "request failed",
}));

import GovernedCases from "@/pages/GovernedCases";

const STATS = {
  cases_by_state: { submitted: 0, in_progress: 1, awaiting_decision: 1, decided: 2, withdrawn: 0, failed: 0 },
};

function renderQueue() {
  return render(
    <MemoryRouter initialEntries={["/dashboard/approvals/cases"]}>
      <GovernedCases />
    </MemoryRouter>,
  );
}

describe("governed case queue", () => {
  beforeEach(() => {
    mockGet.mockReset();
  });

  it("lists the cases awaiting a decision with state, policy tier and recommendation", async () => {
    mockGet.mockImplementation((url: string) =>
      Promise.resolve({ data: url.endsWith("/stats") ? STATS : { cases: [summaryFixture] } }),
    );
    renderQueue();

    const row = await screen.findByTestId("governed-case-row");
    expect(within(row).getByRole("link", { name: "Marlpit Orchard Example Ltd" })).toHaveAttribute(
      "href",
      `/dashboard/approvals/cases/${summaryFixture.case_ref}`,
    );
    expect(within(row).getByText("Awaiting decision")).toBeInTheDocument();
    expect(within(row).getByText("Medium risk")).toBeInTheDocument();
    expect(within(row).getByText("Refer")).toBeInTheDocument();
    expect(mockGet).toHaveBeenCalledWith("/governed-cases", { params: { state: "awaiting_decision", limit: 100 } });
    expect(screen.getByRole("button", { name: "Decided (2)" })).toHaveAttribute("aria-pressed", "false");
  });

  it("filters by state through the API", async () => {
    mockGet.mockImplementation((url: string) =>
      Promise.resolve({ data: url.endsWith("/stats") ? STATS : { cases: [] } }),
    );
    renderQueue();
    fireEvent.click(await screen.findByRole("button", { name: "Decided (2)" }));
    await waitFor(() =>
      expect(mockGet).toHaveBeenCalledWith("/governed-cases", { params: { state: "decided", limit: 100 } }),
    );
    expect(await screen.findByTestId("governed-cases-empty")).toHaveTextContent("No cases are decided.");
  });

  it("explains a tenant without governed cases instead of showing an empty queue", async () => {
    mockGet.mockRejectedValue(axiosError(404, { error: { reason: "governed_cases_disabled", detail: "" } }));
    renderQueue();
    const alert = await screen.findByTestId("governed-cases-error");
    expect(alert).toHaveTextContent("Governed cases are not enabled for this organisation.");
    expect(screen.queryByTestId("governed-cases-empty")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
  });

  it("offers a retry when the load fails for another reason", async () => {
    mockGet.mockRejectedValueOnce(axiosError(503, {})).mockImplementation((url: string) =>
      Promise.resolve({ data: url.endsWith("/stats") ? STATS : { cases: [summaryFixture] } }),
    );
    renderQueue();
    fireEvent.click(await screen.findByRole("button", { name: "Retry" }));
    expect(await screen.findByTestId("governed-case-row")).toBeInTheDocument();
  });
});
