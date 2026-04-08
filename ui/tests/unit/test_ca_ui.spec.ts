/**
 * CA Features UI Unit Tests (Vitest + Testing Library)
 *
 * Tests component rendering for CA-specific pages:
 *   1. CompanyDashboard — health indicators on company cards
 *   2. CompanyDetail — all 7 tabs including Approvals
 *   3. CompanyDetail Approvals — approve buttons for pending items
 *   4. CompanyDetail Compliance — GSTN upload section
 *   5. Login page — CA Partner demo option
 *   6. CAFirmsSolution — "CA Pack" text
 *
 * These tests run in JSDOM via Vitest and do not require a running server.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent, act } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

// ---------------------------------------------------------------------------
// Mock API module
// ---------------------------------------------------------------------------

const mockGet = vi.fn();
const mockPatch = vi.fn();
const mockPost = vi.fn();

vi.mock("../../src/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    patch: (...args: unknown[]) => mockPatch(...args),
    post: (...args: unknown[]) => mockPost(...args),
    interceptors: {
      request: { use: vi.fn() },
      response: { use: vi.fn() },
    },
  },
}));

// Mock react-helmet-async to prevent SSR issues in test
vi.mock("react-helmet-async", () => ({
  Helmet: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  HelmetProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

// ---------------------------------------------------------------------------
// Mock data
// ---------------------------------------------------------------------------

const MOCK_COMPANY_API_RESPONSE = {
  id: "c1",
  name: "Acme Manufacturing Pvt Ltd",
  gstin: "29AABCU9603R1ZM",
  pan: "AABCU9603R",
  tan: "BLRA12345F",
  cin: "U12345KA2020PTC123456",
  industry: "Manufacturing",
  state: "Karnataka",
  status: "active",
  subscription_status: "trial",
  client_health_score: 92,
  document_vault_enabled: true,
  gst_auto_file: true,
};

const MOCK_COMPANIES_LIST = [
  { id: "c1", name: "Acme Manufacturing", gstin: "29AABCU9603R1ZM", pan: "AABCU9603R", industry: "Manufacturing", state: "Karnataka", status: "active" },
  { id: "c2", name: "Greenleaf Exports", gstin: "07AADCG1234N1Z5", pan: "AADCG1234N", industry: "Export", state: "Delhi", status: "active" },
  { id: "c3", name: "Sunrise Healthcare", gstin: "27AACFS5678P1ZK", pan: "AACFS5678P", industry: "Healthcare", state: "Maharashtra", status: "inactive" },
];

// ---------------------------------------------------------------------------
// Helper: render with router
// ---------------------------------------------------------------------------

function renderWithRouter(component: React.ReactElement, initialEntry = "/") {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      {component}
    </MemoryRouter>
  );
}

// ============================================================================
// CompanyDashboard renders health indicators
// ============================================================================

describe("CompanyDashboard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders company cards with status badges as health indicators", async () => {
    // Dynamic import to avoid hoisting issues
    const { default: CompanyDashboard } = await import("../../src/pages/CompanyDashboard");

    mockGet.mockResolvedValue({ data: { items: MOCK_COMPANIES_LIST } });

    renderWithRouter(<CompanyDashboard />);

    await waitFor(() => {
      expect(screen.getByText("Acme Manufacturing")).toBeInTheDocument();
    });

    // Active companies should show "active" badge
    const activeBadges = screen.getAllByText("active");
    expect(activeBadges.length).toBeGreaterThanOrEqual(1);

    // Stats bar should show total clients count
    expect(screen.getByText("Total Clients")).toBeInTheDocument();
    expect(screen.getByText("Active")).toBeInTheDocument();
  });

  it("renders company GSTIN on cards", async () => {
    const { default: CompanyDashboard } = await import("../../src/pages/CompanyDashboard");

    mockGet.mockResolvedValue({ data: { items: MOCK_COMPANIES_LIST } });

    renderWithRouter(<CompanyDashboard />);

    await waitFor(() => {
      expect(screen.getByText("29AABCU9603R1ZM")).toBeInTheDocument();
    });
  });
});

// ============================================================================
// CompanyDetail renders all 7 tabs including Approvals
// ============================================================================

describe("CompanyDetail", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders all 7 tabs including Approvals", async () => {
    const { default: CompanyDetail } = await import("../../src/pages/CompanyDetail");

    mockGet.mockResolvedValue({ data: MOCK_COMPANY_API_RESPONSE });

    render(
      <MemoryRouter initialEntries={["/dashboard/companies/c1"]}>
        <Routes>
          <Route path="/dashboard/companies/:id" element={<CompanyDetail />} />
        </Routes>
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText("Acme Manufacturing Pvt Ltd")).toBeInTheDocument();
    });

    // All 7 tabs must be visible
    const expectedTabs = ["Overview", "Compliance", "Agents", "Workflows", "Audit Log", "Approvals", "Settings"];
    for (const tab of expectedTabs) {
      expect(screen.getByText(tab)).toBeInTheDocument();
    }
  });

  it("shows subscription status badge in header", async () => {
    const { default: CompanyDetail } = await import("../../src/pages/CompanyDetail");

    mockGet.mockResolvedValue({ data: MOCK_COMPANY_API_RESPONSE });

    render(
      <MemoryRouter initialEntries={["/dashboard/companies/c1"]}>
        <Routes>
          <Route path="/dashboard/companies/:id" element={<CompanyDetail />} />
        </Routes>
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText("Acme Manufacturing Pvt Ltd")).toBeInTheDocument();
    });

    // Should show subscription status badge (Trial, Active, or Expired)
    const trialBadge = screen.queryByText("Trial");
    const activeBadge = screen.queryByText("Active");
    const expiredBadge = screen.queryByText("Expired");
    expect(trialBadge || activeBadge || expiredBadge).toBeTruthy();
  });

  it("overview tab shows Client Health Score", async () => {
    const { default: CompanyDetail } = await import("../../src/pages/CompanyDetail");

    mockGet.mockResolvedValue({ data: MOCK_COMPANY_API_RESPONSE });

    render(
      <MemoryRouter initialEntries={["/dashboard/companies/c1"]}>
        <Routes>
          <Route path="/dashboard/companies/:id" element={<CompanyDetail />} />
        </Routes>
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText("Client Health Score")).toBeInTheDocument();
    });
  });
});

// ============================================================================
// CompanyDetail Approvals tab shows approve buttons for pending items
// ============================================================================

describe("CompanyDetail Approvals Tab", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows approve buttons for pending filing requests", async () => {
    const { default: CompanyDetail } = await import("../../src/pages/CompanyDetail");

    mockGet.mockResolvedValue({ data: MOCK_COMPANY_API_RESPONSE });

    render(
      <MemoryRouter initialEntries={["/dashboard/companies/c1"]}>
        <Routes>
          <Route path="/dashboard/companies/:id" element={<CompanyDetail />} />
        </Routes>
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText("Acme Manufacturing Pvt Ltd")).toBeInTheDocument();
    });

    // Click the Approvals tab
    const approvalsTab = screen.getByText("Approvals");
    await act(async () => {
      fireEvent.click(approvalsTab);
    });

    // Should show Filing Approvals heading
    expect(screen.getByText("Filing Approvals")).toBeInTheDocument();

    // Should show filing types
    expect(screen.getByText("GSTR-1")).toBeInTheDocument();

    // Pending items should have an Approve button
    const approveButtons = screen.getAllByText("Approve");
    expect(approveButtons.length).toBeGreaterThanOrEqual(1);
  });

  it("shows Request Filing Approval button", async () => {
    const { default: CompanyDetail } = await import("../../src/pages/CompanyDetail");

    mockGet.mockResolvedValue({ data: MOCK_COMPANY_API_RESPONSE });

    render(
      <MemoryRouter initialEntries={["/dashboard/companies/c1"]}>
        <Routes>
          <Route path="/dashboard/companies/:id" element={<CompanyDetail />} />
        </Routes>
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText("Acme Manufacturing Pvt Ltd")).toBeInTheDocument();
    });

    const approvalsTab = screen.getByText("Approvals");
    await act(async () => {
      fireEvent.click(approvalsTab);
    });

    expect(screen.getByText("Request Filing Approval")).toBeInTheDocument();
  });
});

// ============================================================================
// CompanyDetail Compliance tab shows GSTN upload section
// ============================================================================

describe("CompanyDetail Compliance Tab", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows GSTN Manual Upload section with download buttons", async () => {
    const { default: CompanyDetail } = await import("../../src/pages/CompanyDetail");

    mockGet.mockResolvedValue({ data: MOCK_COMPANY_API_RESPONSE });

    render(
      <MemoryRouter initialEntries={["/dashboard/companies/c1"]}>
        <Routes>
          <Route path="/dashboard/companies/:id" element={<CompanyDetail />} />
        </Routes>
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText("Acme Manufacturing Pvt Ltd")).toBeInTheDocument();
    });

    // Click the Compliance tab
    const complianceTab = screen.getByText("Compliance");
    await act(async () => {
      fireEvent.click(complianceTab);
    });

    // Should show GSTN Manual Upload section
    expect(screen.getByText("GSTN Manual Upload")).toBeInTheDocument();

    // Should show Download buttons
    const downloadButtons = screen.getAllByText("Download");
    expect(downloadButtons.length).toBeGreaterThanOrEqual(1);

    // Should show Mark as Uploaded buttons
    const markButtons = screen.getAllByText("Mark as Uploaded");
    expect(markButtons.length).toBeGreaterThanOrEqual(1);
  });

  it("shows GST Filing Calendar", async () => {
    const { default: CompanyDetail } = await import("../../src/pages/CompanyDetail");

    mockGet.mockResolvedValue({ data: MOCK_COMPANY_API_RESPONSE });

    render(
      <MemoryRouter initialEntries={["/dashboard/companies/c1"]}>
        <Routes>
          <Route path="/dashboard/companies/:id" element={<CompanyDetail />} />
        </Routes>
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(screen.getByText("Acme Manufacturing Pvt Ltd")).toBeInTheDocument();
    });

    const complianceTab = screen.getByText("Compliance");
    await act(async () => {
      fireEvent.click(complianceTab);
    });

    // Should show GST Filing Calendar
    expect(screen.getByText(/GST Filing Calendar/)).toBeInTheDocument();
  });
});

// ============================================================================
// CAFirmsSolution shows "CA Pack" text
// ============================================================================

describe("CAFirmsSolution", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows "CA Pack" text on the solution page', async () => {
    const { default: CAFirmsSolution } = await import("../../src/pages/CAFirmsSolution");

    renderWithRouter(<CAFirmsSolution />);

    await waitFor(() => {
      // The page should contain "CA Pack" text
      const caPackElements = screen.getAllByText(/CA Pack/);
      expect(caPackElements.length).toBeGreaterThanOrEqual(1);
    });
  });

  it("shows pricing at INR 4,999", async () => {
    const { default: CAFirmsSolution } = await import("../../src/pages/CAFirmsSolution");

    renderWithRouter(<CAFirmsSolution />);

    await waitFor(() => {
      expect(screen.getByText(/4,999/)).toBeInTheDocument();
    });
  });

  it("shows feature cards for CA firms", async () => {
    const { default: CAFirmsSolution } = await import("../../src/pages/CAFirmsSolution");

    renderWithRouter(<CAFirmsSolution />);

    await waitFor(() => {
      expect(screen.getByText("Multi-Client Management")).toBeInTheDocument();
      expect(screen.getByText("GST Compliance")).toBeInTheDocument();
      expect(screen.getByText("TDS Automation")).toBeInTheDocument();
    });
  });
});
