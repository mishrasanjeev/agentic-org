/**
 * CFODashboard Component Tests
 *
 * Tests rendering states and KPI cards using mocked API responses
 * that match the current CFOKPIData interface (basic metrics shape).
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
// Test data matching current CFOKPIData interface (basic metrics shape)
// ---------------------------------------------------------------------------

const MOCK_CFO_DATA = {
  demo: true,
  company_id: "comp-001",
  agent_count: 8,
  total_tasks_30d: 142,
  success_rate: 87.3,
  hitl_interventions: 5,
  total_cost_usd: 23.45,
  domain_breakdown: [
    { domain: "finance", total: 95, completed: 82, failed: 13, avg_confidence: 0.89 },
    { domain: "hr", total: 47, completed: 41, failed: 6, avg_confidence: 0.85 },
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

  it("renders loading state initially", () => {
    mockGet.mockReturnValue(new Promise(() => {}));
    renderCFO();
    expect(screen.getByText("CFO Dashboard")).toBeInTheDocument();
  });

  it("renders KPI cards after data loads", async () => {
    mockGet.mockResolvedValue({ data: MOCK_CFO_DATA });
    renderCFO();
    await waitFor(() => {
      expect(screen.getByText("Agents")).toBeInTheDocument();
    });
    expect(screen.getByText("Total Tasks (30d)")).toBeInTheDocument();
    expect(screen.getByText("Success Rate")).toBeInTheDocument();
    expect(screen.getByText("HITL Interventions")).toBeInTheDocument();
    expect(screen.getByText("Total Cost (USD)")).toBeInTheDocument();
  });

  it("displays agent count", async () => {
    mockGet.mockResolvedValue({ data: MOCK_CFO_DATA });
    renderCFO();
    await waitFor(() => {
      expect(screen.getByText("8")).toBeInTheDocument();
    });
  });

  it("displays success rate as percentage", async () => {
    mockGet.mockResolvedValue({ data: MOCK_CFO_DATA });
    renderCFO();
    await waitFor(() => {
      expect(screen.getByText("87.3%")).toBeInTheDocument();
    });
  });

  it("shows Demo Data badge when demo is true", async () => {
    mockGet.mockResolvedValue({ data: MOCK_CFO_DATA });
    renderCFO();
    await waitFor(() => {
      expect(screen.getByText("Demo Data")).toBeInTheDocument();
    });
  });

  it("does not show Demo Data badge when demo is false", async () => {
    mockGet.mockResolvedValue({ data: { ...MOCK_CFO_DATA, demo: false } });
    renderCFO();
    await waitFor(() => {
      expect(screen.getByText("Agents")).toBeInTheDocument();
    });
    expect(screen.queryByText("Demo Data")).not.toBeInTheDocument();
  });

  it("renders domain breakdown table when data exists", async () => {
    mockGet.mockResolvedValue({ data: MOCK_CFO_DATA });
    renderCFO();
    await waitFor(() => {
      expect(screen.getByText("Domain Breakdown")).toBeInTheDocument();
    });
    expect(screen.getByText("finance")).toBeInTheDocument();
  });

  it("shows error message when API fails", async () => {
    mockGet.mockRejectedValue(new Error("Internal Server Error"));
    renderCFO();
    await waitFor(() => {
      expect(screen.getByText("Failed to load CFO KPIs")).toBeInTheDocument();
    });
  });

  it("shows 'No data available' when response data is null", async () => {
    mockGet.mockResolvedValue({ data: null });
    renderCFO();
    await waitFor(() => {
      expect(screen.getByText("No data available")).toBeInTheDocument();
    });
  });

  it("handles missing/null KPI fields without crashing", async () => {
    const partialData = {
      demo: false,
      company_id: "comp-001",
      agent_count: null,
      total_tasks_30d: null,
      success_rate: null,
      hitl_interventions: null,
      total_cost_usd: null,
      domain_breakdown: [],
    };
    mockGet.mockResolvedValue({ data: partialData });
    renderCFO();
    await waitFor(() => {
      expect(screen.getByText("Agents")).toBeInTheDocument();
    });
    // Should render 0 values instead of crashing
    expect(screen.getByText("0.0%")).toBeInTheDocument();
  });

  it("calls /kpis/cfo endpoint on mount", async () => {
    mockGet.mockResolvedValue({ data: MOCK_CFO_DATA });
    renderCFO();
    await waitFor(() => {
      // Codex 2026-04-22 multi-company fix: the dashboard now passes a
      // params object (empty when no company is selected) so KPI helpers
      // can filter by company_id.
      expect(mockGet).toHaveBeenCalledWith("/kpis/cfo", { params: {} });
    });
  });
});
