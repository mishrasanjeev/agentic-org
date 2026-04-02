/**
 * CFODashboard Component Tests
 *
 * Tests rendering states, KPI cards, charts, bank balances, and tax calendar
 * using mocked API responses that match the real CFOKPIData interface.
 */
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { vi, describe, it, expect, beforeEach } from "vitest";
import { HelmetProvider } from "react-helmet-async";

// ---------------------------------------------------------------------------
// Mock API module
// ---------------------------------------------------------------------------

const mockGet = vi.fn();

vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    post: vi.fn(),
    interceptors: {
      request: { use: vi.fn() },
      response: { use: vi.fn() },
    },
  },
}));

// ---------------------------------------------------------------------------
// Import component under test (after mocks are set up)
// ---------------------------------------------------------------------------

import CFODashboard from "@/pages/CFODashboard";

// ---------------------------------------------------------------------------
// Test data matching CFOKPIData interface
// ---------------------------------------------------------------------------

const MOCK_CFO_DATA = {
  demo: true,
  company_id: "comp-001",
  cash_runway_months: 18,
  cash_runway_trend: 2.5,
  burn_rate: 4500000,
  burn_rate_trend: -3.2,
  dso_days: 42,
  dso_trend: -1.8,
  dpo_days: 35,
  dpo_trend: 0.5,
  ar_aging: {
    "0_30": 2500000,
    "31_60": 1800000,
    "61_90": 900000,
    "90_plus": 400000,
  },
  ap_aging: {
    "0_30": 1200000,
    "31_60": 800000,
    "61_90": 300000,
    "90_plus": 100000,
  },
  monthly_pl: [
    {
      month: "2026-01",
      revenue: 12000000,
      cogs: 5000000,
      gross_margin: 7000000,
      opex: 4000000,
      net_income: 3000000,
    },
    {
      month: "2026-02",
      revenue: 13500000,
      cogs: 5500000,
      gross_margin: 8000000,
      opex: 4200000,
      net_income: 3800000,
    },
    {
      month: "2026-03",
      revenue: 14200000,
      cogs: 5800000,
      gross_margin: 8400000,
      opex: 4300000,
      net_income: 4100000,
    },
  ],
  bank_balances: [
    { account: "HDFC Current", balance: 8500000, currency: "INR" },
    { account: "SBI Savings", balance: 3200000, currency: "INR" },
    { account: "Wise USD", balance: 45000, currency: "USD" },
  ],
  pending_approvals_count: 7,
  tax_calendar: [
    { filing: "GST-3B March", due_date: "2026-04-20", status: "upcoming" },
    { filing: "TDS Q4", due_date: "2026-04-30", status: "pending" },
    { filing: "GST-3B February", due_date: "2026-03-20", status: "filed" },
  ],
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function renderCFO() {
  return render(
    <HelmetProvider>
      <MemoryRouter initialEntries={["/dashboard/cfo"]}>
        <CFODashboard />
      </MemoryRouter>
    </HelmetProvider>,
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("CFODashboard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // ── Loading State ──────────────────────────────────────────────────────

  it("renders loading state initially", () => {
    // Make the API call hang indefinitely
    mockGet.mockReturnValue(new Promise(() => {}));

    renderCFO();

    expect(screen.getByText("CFO Dashboard")).toBeInTheDocument();
    expect(screen.getByText("Loading finance data...")).toBeInTheDocument();
  });

  // ── Success State: KPI Cards ───────────────────────────────────────────

  it("renders KPI cards after data loads", async () => {
    mockGet.mockResolvedValue({ data: MOCK_CFO_DATA });

    renderCFO();

    await waitFor(() => {
      expect(screen.getByText("Cash Runway")).toBeInTheDocument();
    });

    expect(screen.getByText("Monthly Burn Rate")).toBeInTheDocument();
    expect(screen.getByText("DSO (Days)")).toBeInTheDocument();
    expect(screen.getByText("DPO (Days)")).toBeInTheDocument();
  });

  it("displays Cash Runway with months unit", async () => {
    mockGet.mockResolvedValue({ data: MOCK_CFO_DATA });

    renderCFO();

    await waitFor(() => {
      expect(screen.getByText("18 mo")).toBeInTheDocument();
    });
  });

  it("displays DSO with days unit", async () => {
    mockGet.mockResolvedValue({ data: MOCK_CFO_DATA });

    renderCFO();

    await waitFor(() => {
      expect(screen.getByText("42d")).toBeInTheDocument();
    });
  });

  it("displays DPO with days unit", async () => {
    mockGet.mockResolvedValue({ data: MOCK_CFO_DATA });

    renderCFO();

    await waitFor(() => {
      expect(screen.getByText("35d")).toBeInTheDocument();
    });
  });

  it("shows trend indicators on KPI cards", async () => {
    mockGet.mockResolvedValue({ data: MOCK_CFO_DATA });

    renderCFO();

    await waitFor(() => {
      expect(screen.getByText("Cash Runway")).toBeInTheDocument();
    });

    // Positive trend: 2.5%
    expect(screen.getByText(/2\.5%/)).toBeInTheDocument();
    // Negative trend: 3.2%
    expect(screen.getByText(/3\.2%/)).toBeInTheDocument();
  });

  it("shows Demo Data badge when data is demo", async () => {
    mockGet.mockResolvedValue({ data: MOCK_CFO_DATA });

    renderCFO();

    await waitFor(() => {
      expect(screen.getByText("Demo Data")).toBeInTheDocument();
    });
  });

  it("does not show Demo Data badge when data is not demo", async () => {
    mockGet.mockResolvedValue({
      data: { ...MOCK_CFO_DATA, demo: false },
    });

    renderCFO();

    await waitFor(() => {
      expect(screen.getByText("Cash Runway")).toBeInTheDocument();
    });

    expect(screen.queryByText("Demo Data")).not.toBeInTheDocument();
  });

  // ── Charts ─────────────────────────────────────────────────────────────

  it("renders AR Aging chart section", async () => {
    mockGet.mockResolvedValue({ data: MOCK_CFO_DATA });

    renderCFO();

    await waitFor(() => {
      expect(
        screen.getByText("Accounts Receivable Aging (INR Lakhs)"),
      ).toBeInTheDocument();
    });
  });

  it("renders AP Aging chart section", async () => {
    mockGet.mockResolvedValue({ data: MOCK_CFO_DATA });

    renderCFO();

    await waitFor(() => {
      expect(
        screen.getByText("Accounts Payable Aging (INR Lakhs)"),
      ).toBeInTheDocument();
    });
  });

  it("renders Monthly P&L Summary table", async () => {
    mockGet.mockResolvedValue({ data: MOCK_CFO_DATA });

    renderCFO();

    await waitFor(() => {
      expect(screen.getByText("Monthly P&L Summary")).toBeInTheDocument();
    });

    // Check column headers
    expect(screen.getByText("Revenue")).toBeInTheDocument();
    expect(screen.getByText("COGS")).toBeInTheDocument();
    expect(screen.getByText("Gross Margin")).toBeInTheDocument();
    expect(screen.getByText("OPEX")).toBeInTheDocument();
    expect(screen.getByText("Net Income")).toBeInTheDocument();
  });

  it("renders P&L Trend chart section", async () => {
    mockGet.mockResolvedValue({ data: MOCK_CFO_DATA });

    renderCFO();

    await waitFor(() => {
      expect(screen.getByText("P&L Trend (Lakhs)")).toBeInTheDocument();
    });
  });

  // ── Bank Balances ──────────────────────────────────────────────────────

  it("renders bank balances section with all accounts", async () => {
    mockGet.mockResolvedValue({ data: MOCK_CFO_DATA });

    renderCFO();

    await waitFor(() => {
      expect(screen.getByText("Bank Balances")).toBeInTheDocument();
    });

    expect(screen.getByText("HDFC Current")).toBeInTheDocument();
    expect(screen.getByText("SBI Savings")).toBeInTheDocument();
    expect(screen.getByText("Wise USD")).toBeInTheDocument();
  });

  it("displays currency labels for each bank account", async () => {
    mockGet.mockResolvedValue({ data: MOCK_CFO_DATA });

    renderCFO();

    await waitFor(() => {
      expect(screen.getByText("HDFC Current")).toBeInTheDocument();
    });

    // INR accounts
    const inrLabels = screen.getAllByText("INR");
    expect(inrLabels.length).toBe(2);

    // USD account
    expect(screen.getByText("USD")).toBeInTheDocument();
  });

  // ── Pending Approvals ──────────────────────────────────────────────────

  it("shows pending approvals count", async () => {
    mockGet.mockResolvedValue({ data: MOCK_CFO_DATA });

    renderCFO();

    await waitFor(() => {
      expect(screen.getByText("7")).toBeInTheDocument();
    });

    expect(screen.getByText("items awaiting review")).toBeInTheDocument();
  });

  // ── Tax Calendar ───────────────────────────────────────────────────────

  it("renders tax calendar with filing names", async () => {
    mockGet.mockResolvedValue({ data: MOCK_CFO_DATA });

    renderCFO();

    await waitFor(() => {
      expect(
        screen.getByText("Tax & Compliance Calendar"),
      ).toBeInTheDocument();
    });

    expect(screen.getByText("GST-3B March")).toBeInTheDocument();
    expect(screen.getByText("TDS Q4")).toBeInTheDocument();
    expect(screen.getByText("GST-3B February")).toBeInTheDocument();
  });

  it("shows status badges on tax filings", async () => {
    mockGet.mockResolvedValue({ data: MOCK_CFO_DATA });

    renderCFO();

    await waitFor(() => {
      expect(screen.getByText("upcoming")).toBeInTheDocument();
    });

    expect(screen.getByText("pending")).toBeInTheDocument();
    expect(screen.getByText("filed")).toBeInTheDocument();
  });

  // ── Error State ────────────────────────────────────────────────────────

  it("shows error message when API returns 500", async () => {
    mockGet.mockRejectedValue(new Error("Internal Server Error"));

    renderCFO();

    await waitFor(() => {
      expect(screen.getByText("Failed to load CFO KPIs")).toBeInTheDocument();
    });
  });

  it("shows error message when Promise.allSettled rejects the request", async () => {
    mockGet.mockImplementation(() =>
      Promise.reject(new Error("Network Error")),
    );

    renderCFO();

    await waitFor(() => {
      expect(screen.getByText("Failed to load CFO KPIs")).toBeInTheDocument();
    });
  });

  // ── Empty / Zero Data State ────────────────────────────────────────────

  it("handles zero KPI values gracefully", async () => {
    const zeroData = {
      ...MOCK_CFO_DATA,
      cash_runway_months: 0,
      burn_rate: 0,
      dso_days: 0,
      dpo_days: 0,
      pending_approvals_count: 0,
      bank_balances: [],
      tax_calendar: [],
      monthly_pl: [],
    };

    mockGet.mockResolvedValue({ data: zeroData });

    renderCFO();

    await waitFor(() => {
      expect(screen.getByText("0 mo")).toBeInTheDocument();
    });

    expect(screen.getByText("0d")).toBeInTheDocument();
    // Check that it renders without crashing even with empty arrays
    expect(screen.getByText("Bank Balances")).toBeInTheDocument();
    expect(screen.getByText("Tax & Compliance Calendar")).toBeInTheDocument();
  });

  // ── API Call Correctness ───────────────────────────────────────────────

  it("calls /kpis/cfo endpoint on mount", async () => {
    mockGet.mockResolvedValue({ data: MOCK_CFO_DATA });

    renderCFO();

    await waitFor(() => {
      expect(mockGet).toHaveBeenCalledWith("/kpis/cfo");
    });
  });

  // ── Monthly P&L MoM Calculations ──────────────────────────────────────

  it("shows MoM percentage changes in P&L table", async () => {
    mockGet.mockResolvedValue({ data: MOCK_CFO_DATA });

    renderCFO();

    await waitFor(() => {
      expect(screen.getByText("Monthly P&L Summary")).toBeInTheDocument();
    });

    // Revenue MoM: (13500000 - 12000000) / 12000000 * 100 = 12.5%
    expect(screen.getByText("+12.5%")).toBeInTheDocument();
  });
});
