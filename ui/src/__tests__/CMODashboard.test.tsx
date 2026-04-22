/**
 * CMODashboard Component Tests
 *
 * Tests rendering states and KPI cards using mocked API responses
 * that match the current CMOKPIData interface (basic metrics shape).
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
// Test data matching current CMOKPIData interface (basic metrics shape)
// ---------------------------------------------------------------------------

const MOCK_CMO_DATA = {
  demo: true,
  company_id: "comp-001",
  agent_count: 5,
  total_tasks_30d: 89,
  success_rate: 91.2,
  hitl_interventions: 3,
  total_cost_usd: 15.80,
  domain_breakdown: [
    { domain: "marketing", total: 54, completed: 49, failed: 5, avg_confidence: 0.91 },
    { domain: "sales", total: 35, completed: 32, failed: 3, avg_confidence: 0.87 },
  ],
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

  it("renders loading state initially", () => {
    mockGet.mockReturnValue(new Promise(() => {}));
    renderCMO();
    expect(screen.getByText("CMO Dashboard")).toBeInTheDocument();
  });

  it("renders all KPI cards after data loads", async () => {
    mockGet.mockResolvedValue({ data: MOCK_CMO_DATA });
    renderCMO();
    await waitFor(() => {
      expect(screen.getByText("Agents")).toBeInTheDocument();
    });
    expect(screen.getByText("Total Tasks (30d)")).toBeInTheDocument();
    expect(screen.getByText("Success Rate")).toBeInTheDocument();
    expect(screen.getByText("HITL Interventions")).toBeInTheDocument();
    expect(screen.getByText("Total Cost (USD)")).toBeInTheDocument();
  });

  it("displays agent count correctly", async () => {
    mockGet.mockResolvedValue({ data: MOCK_CMO_DATA });
    renderCMO();
    await waitFor(() => {
      expect(screen.getByText("Agents")).toBeInTheDocument();
    });
    // Agent count of 5 rendered somewhere in the document
    expect(document.body.textContent).toContain("5");
  });

  it("displays success rate as percentage", async () => {
    mockGet.mockResolvedValue({ data: MOCK_CMO_DATA });
    renderCMO();
    await waitFor(() => {
      expect(screen.getByText("91.2%")).toBeInTheDocument();
    });
  });

  it("shows Demo Data badge when demo is true", async () => {
    mockGet.mockResolvedValue({ data: MOCK_CMO_DATA });
    renderCMO();
    await waitFor(() => {
      expect(screen.getByText("Demo Data")).toBeInTheDocument();
    });
  });

  it("does not show Demo Data badge when demo is false", async () => {
    mockGet.mockResolvedValue({ data: { ...MOCK_CMO_DATA, demo: false } });
    renderCMO();
    await waitFor(() => {
      expect(screen.getByText("Agents")).toBeInTheDocument();
    });
    expect(screen.queryByText("Demo Data")).not.toBeInTheDocument();
  });

  it("renders domain breakdown when data exists", async () => {
    mockGet.mockResolvedValue({ data: MOCK_CMO_DATA });
    renderCMO();
    await waitFor(() => {
      expect(screen.getByText("Domain Breakdown")).toBeInTheDocument();
    });
    expect(screen.getByText("marketing")).toBeInTheDocument();
  });

  it("shows error message when API fails", async () => {
    mockGet.mockRejectedValue(new Error("Internal Server Error"));
    renderCMO();
    await waitFor(() => {
      expect(screen.getByText("Failed to load CMO KPIs")).toBeInTheDocument();
    });
  });

  it("shows 'No data available' when response data is null", async () => {
    mockGet.mockResolvedValue({ data: null });
    renderCMO();
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
    renderCMO();
    await waitFor(() => {
      expect(screen.getByText("Agents")).toBeInTheDocument();
    });
    expect(screen.getByText("0.0%")).toBeInTheDocument();
  });

  it("calls /kpis/cmo endpoint on mount", async () => {
    mockGet.mockResolvedValue({ data: MOCK_CMO_DATA });
    renderCMO();
    await waitFor(() => {
      // Codex 2026-04-22 multi-company fix: the dashboard now passes a
      // params object (empty when no company is selected) so KPI helpers
      // can filter by company_id.
      expect(mockGet).toHaveBeenCalledWith("/kpis/cmo", { params: {} });
    });
  });
});
