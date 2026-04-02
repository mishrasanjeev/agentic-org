/**
 * CMODashboard Component Tests
 *
 * Tests rendering states, KPI cards, ROAS chart, social engagement,
 * brand sentiment, and content analytics using mocked API responses
 * that match the real CMOKPIData interface.
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
// Import component under test
// ---------------------------------------------------------------------------

import CMODashboard from "@/pages/CMODashboard";

// ---------------------------------------------------------------------------
// Test data matching CMOKPIData interface
// ---------------------------------------------------------------------------

const MOCK_CMO_DATA = {
  demo: true,
  company_id: "comp-001",
  cac: 3500,
  cac_trend: -8.2,
  mqls: 1240,
  mqls_trend: 15.3,
  sqls: 380,
  sqls_trend: 12.1,
  pipeline_value: 28500000,
  pipeline_trend: 18.7,
  roas_by_channel: {
    "Google Ads": 4.2,
    "Meta Ads": 3.1,
    LinkedIn: 2.8,
    Organic: 8.5,
  },
  email_performance: {
    open_rate: 32.5,
    click_rate: 4.8,
    unsubscribe_rate: 0.3,
  },
  social_engagement: {
    Twitter: 12500,
    LinkedIn: 28000,
    Instagram: 8700,
  },
  website_traffic: {
    sessions: 45200,
    users: 31800,
    bounce_rate: 42.3,
    sessions_trend: [
      { date: "2026-03-01", sessions: 1400 },
      { date: "2026-03-02", sessions: 1520 },
      { date: "2026-03-03", sessions: 1380 },
      { date: "2026-03-04", sessions: 1610 },
      { date: "2026-03-05", sessions: 1450 },
    ],
  },
  content_top_pages: [
    { page: "/blog/ai-finance-automation", views: 8200, avg_time_sec: 245 },
    { page: "/solutions/invoice-processing", views: 6100, avg_time_sec: 180 },
    { page: "/pricing", views: 4500, avg_time_sec: 120 },
  ],
  brand_sentiment_score: 78,
  brand_sentiment_trend: 3.5,
  pending_content_approvals: 4,
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function renderCMO() {
  return render(
    <HelmetProvider>
      <MemoryRouter initialEntries={["/dashboard/cmo"]}>
        <CMODashboard />
      </MemoryRouter>
    </HelmetProvider>,
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("CMODashboard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // ── Loading State ──────────────────────────────────────────────────────

  it("renders loading state initially", () => {
    mockGet.mockReturnValue(new Promise(() => {}));

    renderCMO();

    expect(screen.getByText("CMO Dashboard")).toBeInTheDocument();
    expect(screen.getByText("Loading marketing data...")).toBeInTheDocument();
  });

  // ── Success State: KPI Cards ───────────────────────────────────────────

  it("renders all four KPI cards after data loads", async () => {
    mockGet.mockResolvedValue({ data: MOCK_CMO_DATA });

    renderCMO();

    await waitFor(() => {
      expect(
        screen.getByText("Customer Acquisition Cost"),
      ).toBeInTheDocument();
    });

    expect(screen.getByText("MQLs This Month")).toBeInTheDocument();
    expect(screen.getByText("SQLs This Month")).toBeInTheDocument();
    expect(screen.getByText("Pipeline Value")).toBeInTheDocument();
  });

  it("displays MQL count correctly", async () => {
    mockGet.mockResolvedValue({ data: MOCK_CMO_DATA });

    renderCMO();

    await waitFor(() => {
      expect(screen.getByText("1,240")).toBeInTheDocument();
    });
  });

  it("displays SQL count correctly", async () => {
    mockGet.mockResolvedValue({ data: MOCK_CMO_DATA });

    renderCMO();

    await waitFor(() => {
      expect(screen.getByText("380")).toBeInTheDocument();
    });
  });

  it("shows trend indicators on KPI cards", async () => {
    mockGet.mockResolvedValue({ data: MOCK_CMO_DATA });

    renderCMO();

    await waitFor(() => {
      expect(screen.getByText(/8\.2%/)).toBeInTheDocument();
    });

    expect(screen.getByText(/15\.3%/)).toBeInTheDocument();
  });

  it("shows (good) label for inverted CAC trend when negative", async () => {
    mockGet.mockResolvedValue({ data: MOCK_CMO_DATA });

    renderCMO();

    await waitFor(() => {
      expect(screen.getByText("(good)")).toBeInTheDocument();
    });
  });

  it("shows Demo Data badge when data is demo", async () => {
    mockGet.mockResolvedValue({ data: MOCK_CMO_DATA });

    renderCMO();

    await waitFor(() => {
      expect(screen.getByText("Demo Data")).toBeInTheDocument();
    });
  });

  it("does not show Demo Data badge when data is not demo", async () => {
    mockGet.mockResolvedValue({
      data: { ...MOCK_CMO_DATA, demo: false },
    });

    renderCMO();

    await waitFor(() => {
      expect(
        screen.getByText("Customer Acquisition Cost"),
      ).toBeInTheDocument();
    });

    expect(screen.queryByText("Demo Data")).not.toBeInTheDocument();
  });

  // ── ROAS Chart ─────────────────────────────────────────────────────────

  it("renders ROAS by Channel section", async () => {
    mockGet.mockResolvedValue({ data: MOCK_CMO_DATA });

    renderCMO();

    await waitFor(() => {
      expect(
        screen.getByText("Return on Ad Spend (ROAS) by Channel"),
      ).toBeInTheDocument();
    });
  });

  // ── Email Performance ──────────────────────────────────────────────────

  it("renders Email Performance section", async () => {
    mockGet.mockResolvedValue({ data: MOCK_CMO_DATA });

    renderCMO();

    await waitFor(() => {
      expect(screen.getByText("Email Performance (%)")).toBeInTheDocument();
    });
  });

  // ── Social Engagement ──────────────────────────────────────────────────

  it("renders Social Engagement by Platform section", async () => {
    mockGet.mockResolvedValue({ data: MOCK_CMO_DATA });

    renderCMO();

    await waitFor(() => {
      expect(
        screen.getByText("Social Engagement by Platform"),
      ).toBeInTheDocument();
    });
  });

  // ── Website Traffic ────────────────────────────────────────────────────

  it("renders Website Traffic section with summary stats", async () => {
    mockGet.mockResolvedValue({ data: MOCK_CMO_DATA });

    renderCMO();

    await waitFor(() => {
      expect(screen.getByText("Website Traffic")).toBeInTheDocument();
    });

    expect(screen.getByText("Sessions")).toBeInTheDocument();
    expect(screen.getByText("Users")).toBeInTheDocument();
    expect(screen.getByText("Bounce Rate")).toBeInTheDocument();
    expect(screen.getByText("42.3%")).toBeInTheDocument();
  });

  // ── Top Content Pages ──────────────────────────────────────────────────

  it("renders Top Content Pages table", async () => {
    mockGet.mockResolvedValue({ data: MOCK_CMO_DATA });

    renderCMO();

    await waitFor(() => {
      expect(screen.getByText("Top Content Pages")).toBeInTheDocument();
    });

    expect(
      screen.getByText("/blog/ai-finance-automation"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("/solutions/invoice-processing"),
    ).toBeInTheDocument();
    expect(screen.getByText("/pricing")).toBeInTheDocument();
  });

  it("shows view counts and average time in content table", async () => {
    mockGet.mockResolvedValue({ data: MOCK_CMO_DATA });

    renderCMO();

    await waitFor(() => {
      expect(screen.getByText("8,200")).toBeInTheDocument();
    });

    // 245 seconds = 4m 5s
    expect(screen.getByText("4m 5s")).toBeInTheDocument();
  });

  // ── Brand Sentiment ────────────────────────────────────────────────────

  it("renders Brand Sentiment Score section", async () => {
    mockGet.mockResolvedValue({ data: MOCK_CMO_DATA });

    renderCMO();

    await waitFor(() => {
      expect(screen.getByText("Brand Sentiment Score")).toBeInTheDocument();
    });

    expect(screen.getByText("78")).toBeInTheDocument();
    expect(screen.getByText("out of 100")).toBeInTheDocument();
  });

  // ── Pending Content Approvals ──────────────────────────────────────────

  it("shows pending content approvals count", async () => {
    mockGet.mockResolvedValue({ data: MOCK_CMO_DATA });

    renderCMO();

    await waitFor(() => {
      expect(screen.getByText("4")).toBeInTheDocument();
    });

    expect(
      screen.getByText(
        "blog posts, social posts & campaigns awaiting review",
      ),
    ).toBeInTheDocument();
  });

  // ── Error State ────────────────────────────────────────────────────────

  it("shows error message when API returns 500", async () => {
    mockGet.mockRejectedValue(new Error("Internal Server Error"));

    renderCMO();

    await waitFor(() => {
      expect(screen.getByText("Failed to load CMO KPIs")).toBeInTheDocument();
    });
  });

  it("shows error message when data fetch is rejected", async () => {
    mockGet.mockImplementation(() =>
      Promise.reject(new Error("Network Error")),
    );

    renderCMO();

    await waitFor(() => {
      expect(screen.getByText("Failed to load CMO KPIs")).toBeInTheDocument();
    });
  });

  it("shows 'No data available' when response data is null", async () => {
    // Simulate allSettled fulfilled but with null data
    mockGet.mockResolvedValue({ data: null });

    renderCMO();

    await waitFor(() => {
      expect(screen.getByText("No data available")).toBeInTheDocument();
    });
  });

  // ── Empty / Zero Data State ────────────────────────────────────────────

  it("handles zero KPI values gracefully", async () => {
    const zeroData = {
      ...MOCK_CMO_DATA,
      cac: 0,
      mqls: 0,
      sqls: 0,
      pipeline_value: 0,
      roas_by_channel: {},
      social_engagement: {},
      website_traffic: {
        sessions: 0,
        users: 0,
        bounce_rate: 0,
        sessions_trend: [],
      },
      content_top_pages: [],
      brand_sentiment_score: 0,
      pending_content_approvals: 0,
    };

    mockGet.mockResolvedValue({ data: zeroData });

    renderCMO();

    await waitFor(() => {
      expect(
        screen.getByText("Customer Acquisition Cost"),
      ).toBeInTheDocument();
    });

    // No crash, still renders structural sections
    expect(screen.getByText("Website Traffic")).toBeInTheDocument();
    expect(screen.getByText("Top Content Pages")).toBeInTheDocument();
    expect(screen.getByText("Brand Sentiment Score")).toBeInTheDocument();
  });

  // ── API Call Correctness ───────────────────────────────────────────────

  it("calls /kpis/cmo endpoint on mount", async () => {
    mockGet.mockResolvedValue({ data: MOCK_CMO_DATA });

    renderCMO();

    await waitFor(() => {
      expect(mockGet).toHaveBeenCalledWith("/kpis/cmo");
    });
  });
});
