// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
/**
 * Insights budgets page: the list with spend, limit and state; adding a budget
 * (with the form's own checks before anything is sent); editing; deleting only
 * after a confirmation; a duplicate name explained; read-only for auditors;
 * the forbidden state; and the client's normalisation.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { HelmetProvider } from "react-helmet-async";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockGet = vi.fn();
const mockPost = vi.fn();
const mockPatch = vi.fn();
const mockDelete = vi.fn();

vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
    patch: (...args: unknown[]) => mockPatch(...args),
    delete: (...args: unknown[]) => mockDelete(...args),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  },
}));

import i18n from "@/i18n";
import InsightsBudgets from "@/pages/InsightsBudgets";
import { normaliseList, validateBudget } from "@/lib/insights/budgets";

const MONTH = {
  id: "b-1",
  name: "AI spend",
  period: "monthly",
  threshold_usd: "100.00",
  warn_at_percent: 80,
  company_id: null,
  cost_center_id: null,
  notify_channels: "email",
  spend_usd: "85.50",
  used_pct: 85.5,
  state: "warn",
  forecast_alert: true,
  last_triggered_at: null,
};

function listBody(overrides: Record<string, unknown> = {}) {
  return { budgets: [MONTH], can_edit: true, periods: ["daily", "weekly", "monthly"], max_threshold_usd: "10000000", ...overrides };
}

function httpError(status: number, detail: unknown) {
  return Object.assign(new Error(`HTTP ${status}`), { response: { status, data: { detail } } });
}

function renderPage() {
  return render(
    <HelmetProvider>
      <MemoryRouter initialEntries={["/dashboard/insights/budgets"]}>
        <InsightsBudgets />
      </MemoryRouter>
    </HelmetProvider>,
  );
}

beforeEach(async () => {
  mockGet.mockReset();
  mockPost.mockReset();
  mockPatch.mockReset();
  mockDelete.mockReset();
  await i18n.changeLanguage("en");
});

describe("InsightsBudgets", () => {
  it("lists each budget with its spend, limit, percent used and state", async () => {
    mockGet.mockResolvedValue({ data: listBody() });
    renderPage();
    const row = await screen.findByTestId("budget-b-1");
    expect(within(row).getByText("AI spend")).toBeInTheDocument();
    expect(within(row).getByText("$85.50")).toBeInTheDocument();
    expect(within(row).getByText("$100.00")).toBeInTheDocument();
    expect(within(row).getByText("85.5%")).toBeInTheDocument();
    expect(within(row).getByText("At 80% or more")).toBeInTheDocument();
    expect(within(row).getByText(/Whole organisation · checked against the forecast/)).toBeInTheDocument();
    expect(mockGet).toHaveBeenCalledWith("/insights/budgets", { params: undefined });
  });

  it("checks the form before sending and adds a budget", async () => {
    mockGet.mockResolvedValue({ data: listBody({ budgets: [] }) });
    mockPost.mockResolvedValue({ data: { budget: { ...MONTH, id: "b-2", name: "Daily cap" } } });
    renderPage();
    expect(await screen.findByText("No budget is set")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("budget-add"));
    fireEvent.change(screen.getByTestId("budget-name"), { target: { value: "Daily cap" } });
    fireEvent.change(screen.getByTestId("budget-limit"), { target: { value: "0" } });
    fireEvent.click(screen.getByTestId("budget-save"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Enter a limit from 0.01");
    expect(mockPost).not.toHaveBeenCalled();

    fireEvent.change(screen.getByTestId("budget-limit"), { target: { value: "25" } });
    fireEvent.change(screen.getByTestId("budget-period"), { target: { value: "daily" } });
    fireEvent.click(screen.getByTestId("budget-save"));
    await waitFor(() =>
      expect(mockPost).toHaveBeenCalledWith("/insights/budgets", {
        name: "Daily cap",
        period: "daily",
        threshold_usd: "25",
        warn_at_percent: 80,
      }),
    );
    expect(await screen.findByText("Budget Daily cap added.")).toBeInTheDocument();
  });

  it("explains a duplicate name", async () => {
    mockGet.mockResolvedValue({ data: listBody() });
    mockPost.mockRejectedValue(httpError(409, { error: "duplicate_name", message: "a budget with this name exists" }));
    renderPage();
    fireEvent.click(await screen.findByTestId("budget-add"));
    fireEvent.change(screen.getByTestId("budget-name"), { target: { value: "AI spend" } });
    fireEvent.change(screen.getByTestId("budget-limit"), { target: { value: "10" } });
    fireEvent.click(screen.getByTestId("budget-save"));
    expect(await screen.findByRole("alert")).toHaveTextContent("A budget with this name exists.");
  });

  it("edits a budget and deletes one only after confirming", async () => {
    mockGet.mockResolvedValue({ data: listBody() });
    mockPatch.mockResolvedValue({ data: { budget: { ...MONTH, threshold_usd: "120.00" } } });
    mockDelete.mockResolvedValue({ data: { deleted: "b-1" } });
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Edit budget AI spend" }));
    fireEvent.change(screen.getByTestId("budget-limit"), { target: { value: "120" } });
    fireEvent.click(screen.getByTestId("budget-save"));
    await waitFor(() =>
      expect(mockPatch).toHaveBeenCalledWith("/insights/budgets/b-1", {
        name: "AI spend",
        period: "monthly",
        threshold_usd: "120",
        warn_at_percent: 80,
      }),
    );

    fireEvent.click(await screen.findByRole("button", { name: "Delete budget AI spend" }));
    expect(mockDelete).not.toHaveBeenCalled();
    const dialog = screen.getByRole("alertdialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete budget" }));
    await waitFor(() => expect(mockDelete).toHaveBeenCalledWith("/insights/budgets/b-1"));
    expect(await screen.findByText("Budget AI spend deleted.")).toBeInTheDocument();
  });

  it("is read-only for an auditor", async () => {
    mockGet.mockResolvedValue({ data: listBody({ can_edit: false }) });
    renderPage();
    await screen.findByTestId("budget-b-1");
    expect(screen.queryByTestId("budget-add")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Edit budget/ })).not.toBeInTheDocument();
  });

  it("says who may see budgets when the API refuses", async () => {
    mockGet.mockRejectedValue(httpError(403, "insights_budgets_forbidden"));
    renderPage();
    expect(await screen.findByText("Budgets are for administrators, the CFO and auditors")).toBeInTheDocument();
  });

  it("renders in Hindi", async () => {
    await i18n.changeLanguage("hi");
    mockGet.mockResolvedValue({ data: listBody() });
    renderPage();
    expect(await screen.findByRole("heading", { name: "बजट" })).toBeInTheDocument();
    expect(screen.getByText("बजट जोड़ें")).toBeInTheDocument();
  });
});

describe("budgets client", () => {
  it("normalises the list and drops rows without an id", () => {
    const list = normaliseList({ budgets: [MONTH, { name: "no id" }, { ...MONTH, id: "b-9", period: "yearly", state: "odd" }] });
    expect(list.budgets.map((b) => b.id)).toEqual(["b-1", "b-9"]);
    expect(list.budgets[1].period).toBe("monthly");
    expect(list.budgets[1].state).toBe("ok");
    expect(list.can_edit).toBe(false);
    expect(list.periods).toEqual(["daily", "weekly", "monthly"]);
    expect(normaliseList(null).budgets).toEqual([]);
  });

  it("validates like the API", () => {
    const ok = { name: "A", period: "daily" as const, threshold_usd: "5", warn_at_percent: 80 };
    expect(validateBudget(ok, "10000000")).toBeNull();
    expect(validateBudget({ ...ok, name: "  " }, "10000000")).toBe("name");
    expect(validateBudget({ ...ok, name: "x".repeat(101) }, "10000000")).toBe("name");
    expect(validateBudget({ ...ok, threshold_usd: "abc" }, "10000000")).toBe("threshold");
    expect(validateBudget({ ...ok, threshold_usd: "20000000" }, "10000000")).toBe("threshold");
    expect(validateBudget({ ...ok, warn_at_percent: 0 }, "10000000")).toBe("warnAt");
    expect(validateBudget({ ...ok, warn_at_percent: 50.5 }, "10000000")).toBe("warnAt");
  });
});
