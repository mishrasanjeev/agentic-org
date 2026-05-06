import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockGet = vi.fn();

vi.mock("../lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
  },
}));

vi.mock("react-helmet-async", () => ({
  Helmet: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

import "@/i18n";
import PartnerDashboard from "@/pages/PartnerDashboard";

const dashboardFixture = {
  total_clients: 2,
  active_clients: 1,
  inactive_clients: 1,
  metrics_scope: "active_clients_only",
  avg_health_score: 75,
  total_pending_filings: 1,
  total_overdue: 0,
  revenue_per_month_inr: 4999,
  clients: [
    {
      id: "active-co",
      name: "Active Retail Pvt Ltd",
      health_score: 75,
      pending_filings: 1,
      overdue_filings: 0,
      is_active: true,
      status: "active",
      subscription_status: "trial",
      trial_days_remaining: 5,
      metrics_included: true,
    },
    {
      id: "inactive-co",
      name: "Dormant LLP",
      health_score: null,
      pending_filings: 9,
      overdue_filings: 3,
      is_active: false,
      status: "inactive",
      subscription_status: "trial",
      trial_days_remaining: 5,
      metrics_included: false,
    },
  ],
  upcoming_deadlines: [
    {
      id: "deadline-20",
      company_id: "active-co",
      deadline_type: "GSTR-3B",
      filing_period: "Apr 2026",
      company_name: "Active Retail Pvt Ltd",
      due_date: "2099-01-20",
      days_remaining: 20,
      company_status: "active",
    },
    {
      id: "deadline-3",
      company_id: "active-co",
      deadline_type: "TDS 26Q",
      filing_period: "Q4 FY26",
      company_name: "Active Retail Pvt Ltd",
      due_date: "2099-01-03",
      days_remaining: 3,
      company_status: "active",
    },
    {
      id: "inactive-deadline",
      company_id: "inactive-co",
      deadline_type: "Dormant GST",
      filing_period: "Apr 2026",
      company_name: "Dormant LLP",
      due_date: "2099-01-04",
      days_remaining: 4,
      company_status: "inactive",
    },
  ],
};

function renderDashboard() {
  return render(
    <MemoryRouter>
      <PartnerDashboard />
    </MemoryRouter>,
  );
}

describe("PartnerDashboard Aishwarya 2026-05-06 regressions", () => {
  beforeEach(() => {
    mockGet.mockReset();
    mockGet.mockResolvedValue({ data: dashboardFixture });
  });

  it("renders trial expiry, inactive N/A health, active-only workload, and all active deadline day badges", async () => {
    renderDashboard();

    await waitFor(() => expect(mockGet).toHaveBeenCalledWith("/partner-dashboard"));

    expect(screen.getAllByText("Trial - 5 days left")).toHaveLength(2);
    expect(screen.getByText("N/A")).toBeInTheDocument();
    expect(screen.getAllByText("Active clients with no pending filings").length).toBeGreaterThan(0);
    expect(screen.getByText("20 days left")).toBeInTheDocument();
    expect(screen.getByText("3 days left")).toBeInTheDocument();
    expect(screen.queryByText("Dormant GST - Apr 2026")).not.toBeInTheDocument();
  });
});
