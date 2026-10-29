// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
import { render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { HelmetProvider } from "react-helmet-async";
import i18n from "i18next";
import { beforeEach, describe, expect, it, vi } from "vitest";
import "@/i18n";
import Dashboard from "@/pages/Dashboard";

const get = vi.fn();
const listAll = vi.fn();
vi.mock("@/lib/api", () => ({
  default: { get: (...args: unknown[]) => get(...args) },
  agentsApi: { listAll: (...args: unknown[]) => listAll(...args) },
}));
vi.mock("@/lib/productFacts", () => ({
  useProductFacts: () => ({ facts: { agent_count: 1, connector_count: 1, tool_count: 1, version: "test" } }),
}));

function renderDashboard() {
  return render(<HelmetProvider><MemoryRouter><Dashboard /></MemoryRouter></HelmetProvider>);
}

function responses(pending: number, approved: number, rejected: number) {
  get.mockImplementation((path: string, options?: { params?: { status?: string } }) => {
    const total = options?.params?.status === "approved" ? approved
      : options?.params?.status === "rejected" ? rejected : pending;
    return Promise.resolve({ data: { items: [], total: path === "/approvals" ? total : 0 } });
  });
}

describe("Dashboard approval text encoding", () => {
  beforeEach(async () => {
    get.mockReset();
    listAll.mockReset();
    listAll.mockResolvedValue([]);
    responses(0, 0, 0);
    await i18n.changeLanguage("en");
  });

  it.each(["en", "hi"])("renders the actual empty-state dash correctly in %s", async (language) => {
    await i18n.changeLanguage(language);
    const { container } = renderDashboard();
    const heading = await screen.findByRole("heading", { name: i18n.t("dashboard.approvalsResolved") });
    const card = within(heading.parentElement!.parentElement!);
    expect(card.getByText("\u2014", { exact: true })).toBeInTheDocument();
    expect(card.getByText(i18n.t("dashboard.noApprovalsLogged"))).toBeInTheDocument();
    expect(container.textContent).not.toMatch(/\u00e2\u20ac|\ufffd/);
    await waitFor(() => expect(document.title).toBe(`${i18n.t("nav.dashboard")} \u2014 AgenticOrg`));
  });

  it.each([
    [4, 0, 0, "0%"],
    [0, 3, 1, "100%"],
    [2, 1, 1, "50%"],
    [1, 1, 0, "50%"],
  ])("preserves real approval arithmetic for %i pending, %i approved, %i rejected", async (pending, approved, rejected, expected) => {
    responses(pending, approved, rejected);
    renderDashboard();
    const heading = await screen.findByRole("heading", { name: "Approvals Resolved" });
    expect(within(heading.parentElement!.parentElement!).getByText(expected, { exact: true })).toBeInTheDocument();
  });

  it("keeps unavailable data explicit without corrupting the placeholder", async () => {
    get.mockRejectedValue(new Error("unavailable"));
    renderDashboard();
    const heading = await screen.findByRole("heading", { name: "Approvals Resolved" });
    expect(within(heading.parentElement!.parentElement!).getByText("\u2014", { exact: true })).toBeInTheDocument();
    expect(screen.getByText(/Resolved approval counts could not be loaded/)).toBeInTheDocument();
  });
});
