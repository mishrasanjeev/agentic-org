/**
 * CompanySwitcher Component Tests
 *
 * Tests company list rendering, single-company mode (no dropdown),
 * multi-company dropdown, selection persistence to localStorage,
 * and loading state.
 */
import { render, screen, waitFor, fireEvent, act } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { vi, describe, it, expect, beforeEach } from "vitest";

// ---------------------------------------------------------------------------
// Mock API module
// ---------------------------------------------------------------------------

const mockGet = vi.fn();

vi.mock("../lib/api", () => ({
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

import CompanySwitcher from "@/components/CompanySwitcher";

// ---------------------------------------------------------------------------
// Test data
// ---------------------------------------------------------------------------

const SINGLE_COMPANY = [
  { id: "comp-001", name: "Edumatica Pvt Ltd", gstin: "29ABCDE1234F1Z5" },
];

const MULTI_COMPANIES = [
  { id: "comp-001", name: "Edumatica Pvt Ltd", gstin: "29ABCDE1234F1Z5" },
  { id: "comp-002", name: "Acme Corp", gstin: "07XYZAB5678C2D6" },
  { id: "comp-003", name: "TechVentures Inc" },
];

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("CompanySwitcher", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    // Reset location.reload mock
    Object.defineProperty(window, "location", {
      writable: true,
      value: { ...window.location, reload: vi.fn() },
    });
  });

  // ── Loading State ──────────────────────────────────────────────────────

  it("renders loading skeleton initially", () => {
    mockGet.mockReturnValue(new Promise(() => {}));

    render(<MemoryRouter><CompanySwitcher /></MemoryRouter>);

    // Should show the pulse skeleton
    const skeleton = document.querySelector(".animate-pulse");
    expect(skeleton).toBeInTheDocument();
  });

  // ── Single Company: No Dropdown ────────────────────────────────────────

  it("renders company name without dropdown for single company", async () => {
    mockGet.mockResolvedValue({ data: SINGLE_COMPANY });
    localStorage.setItem("company_id", "comp-001");

    render(<MemoryRouter><CompanySwitcher /></MemoryRouter>);

    await waitFor(() => {
      expect(screen.getByText("Edumatica Pvt Ltd")).toBeInTheDocument();
    });

    // Should be a plain span, not a button
    const companyText = screen.getByText("Edumatica Pvt Ltd");
    expect(companyText.tagName.toLowerCase()).toBe("span");
  });

  it("shows 'No Company' when single company has no match", async () => {
    mockGet.mockResolvedValue({ data: SINGLE_COMPANY });
    localStorage.setItem("company_id", "nonexistent-id");

    render(<MemoryRouter><CompanySwitcher /></MemoryRouter>);

    await waitFor(() => {
      expect(screen.getByText("No Company")).toBeInTheDocument();
    });
  });

  // ── Multiple Companies: Dropdown ───────────────────────────────────────

  it("renders dropdown toggle for multiple companies", async () => {
    mockGet.mockResolvedValue({ data: MULTI_COMPANIES });
    localStorage.setItem("company_id", "comp-001");

    render(<MemoryRouter><CompanySwitcher /></MemoryRouter>);

    await waitFor(() => {
      expect(screen.getByText("Edumatica Pvt Ltd")).toBeInTheDocument();
    });

    // Should be a button (clickable to open dropdown)
    const button = screen.getByRole("button");
    expect(button).toBeInTheDocument();
  });

  it("clicking toggle shows all companies in dropdown", async () => {
    mockGet.mockResolvedValue({ data: MULTI_COMPANIES });
    localStorage.setItem("company_id", "comp-001");

    render(<MemoryRouter><CompanySwitcher /></MemoryRouter>);

    await waitFor(() => {
      expect(screen.getByText("Edumatica Pvt Ltd")).toBeInTheDocument();
    });

    // Click the toggle button
    const toggle = screen.getByRole("button");
    await act(async () => {
      fireEvent.click(toggle);
    });

    // All companies should be visible in dropdown
    // Note: "Edumatica Pvt Ltd" appears in both the button and dropdown
    const edumaticaItems = screen.getAllByText("Edumatica Pvt Ltd");
    expect(edumaticaItems.length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("Acme Corp")).toBeInTheDocument();
    expect(screen.getByText("TechVentures Inc")).toBeInTheDocument();
  });

  it("shows GSTIN for companies that have it", async () => {
    mockGet.mockResolvedValue({ data: MULTI_COMPANIES });
    localStorage.setItem("company_id", "comp-001");

    render(<MemoryRouter><CompanySwitcher /></MemoryRouter>);

    await waitFor(() => {
      expect(screen.getByText("Edumatica Pvt Ltd")).toBeInTheDocument();
    });

    const toggle = screen.getByRole("button");
    await act(async () => {
      fireEvent.click(toggle);
    });

    expect(screen.getByText("GSTIN: 29ABCDE1234F1Z5")).toBeInTheDocument();
    expect(screen.getByText("GSTIN: 07XYZAB5678C2D6")).toBeInTheDocument();
  });

  it("highlights the currently selected company", async () => {
    mockGet.mockResolvedValue({ data: MULTI_COMPANIES });
    localStorage.setItem("company_id", "comp-001");

    render(<MemoryRouter><CompanySwitcher /></MemoryRouter>);

    await waitFor(() => {
      expect(screen.getByText("Edumatica Pvt Ltd")).toBeInTheDocument();
    });

    const toggle = screen.getByRole("button");
    await act(async () => {
      fireEvent.click(toggle);
    });

    // The selected company button should have a different style (primary color)
    const dropdownButtons = screen.getAllByRole("button");
    // Find the one matching current company in dropdown
    const activeButton = dropdownButtons.find(
      (btn) =>
        btn.textContent?.includes("Edumatica Pvt Ltd") &&
        btn.className.includes("primary"),
    );
    expect(activeButton).toBeDefined();
  });

  // ── Selection & Persistence ────────────────────────────────────────────

  it("selecting a company stores company_id in localStorage", async () => {
    mockGet.mockResolvedValue({ data: MULTI_COMPANIES });
    localStorage.setItem("company_id", "comp-001");

    render(<MemoryRouter><CompanySwitcher /></MemoryRouter>);

    await waitFor(() => {
      expect(screen.getByText("Edumatica Pvt Ltd")).toBeInTheDocument();
    });

    const toggle = screen.getByRole("button");
    await act(async () => {
      fireEvent.click(toggle);
    });

    const acmeOption = screen.getByText("Acme Corp");
    await act(async () => {
      fireEvent.click(acmeOption);
    });

    expect(localStorage.setItem).toHaveBeenCalledWith(
      "company_id",
      "comp-002",
    );
  });

  it("selecting a company triggers page reload", async () => {
    mockGet.mockResolvedValue({ data: MULTI_COMPANIES });
    localStorage.setItem("company_id", "comp-001");

    render(<MemoryRouter><CompanySwitcher /></MemoryRouter>);

    await waitFor(() => {
      expect(screen.getByText("Edumatica Pvt Ltd")).toBeInTheDocument();
    });

    const toggle = screen.getByRole("button");
    await act(async () => {
      fireEvent.click(toggle);
    });

    const acmeOption = screen.getByText("Acme Corp");
    await act(async () => {
      fireEvent.click(acmeOption);
    });

    expect(window.location.reload).toHaveBeenCalled();
  });

  // ── Auto-select First Company ──────────────────────────────────────────

  it("auto-selects first company when none is stored", async () => {
    mockGet.mockResolvedValue({ data: MULTI_COMPANIES });
    // No company_id in localStorage

    render(<MemoryRouter><CompanySwitcher /></MemoryRouter>);

    await waitFor(() => {
      expect(localStorage.setItem).toHaveBeenCalledWith(
        "company_id",
        "comp-001",
      );
    });
  });

  // ── Dropdown Close Behavior ────────────────────────────────────────────

  it("closes dropdown when clicking the backdrop", async () => {
    mockGet.mockResolvedValue({ data: MULTI_COMPANIES });
    localStorage.setItem("company_id", "comp-001");

    render(<MemoryRouter><CompanySwitcher /></MemoryRouter>);

    await waitFor(() => {
      expect(screen.getByText("Edumatica Pvt Ltd")).toBeInTheDocument();
    });

    const toggle = screen.getByRole("button");
    await act(async () => {
      fireEvent.click(toggle);
    });

    // Dropdown should be open
    expect(screen.getByText("Acme Corp")).toBeInTheDocument();

    // Click the backdrop (fixed inset-0 div)
    const backdrop = document.querySelector(".fixed.inset-0");
    expect(backdrop).toBeInTheDocument();
    await act(async () => {
      fireEvent.click(backdrop!);
    });

    // Dropdown should close
    expect(screen.queryByText("Acme Corp")).not.toBeInTheDocument();
  });

  // ── Error Handling ─────────────────────────────────────────────────────

  it("handles API error gracefully without crashing", async () => {
    mockGet.mockRejectedValue(new Error("Network Error"));

    render(<MemoryRouter><CompanySwitcher /></MemoryRouter>);

    // Should finish loading without crashing
    await waitFor(() => {
      const skeleton = document.querySelector(".animate-pulse");
      expect(skeleton).not.toBeInTheDocument();
    });
  });

  // ── API Call ───────────────────────────────────────────────────────────

  it("calls /companies endpoint on mount", async () => {
    mockGet.mockResolvedValue({ data: SINGLE_COMPANY });
    localStorage.setItem("company_id", "comp-001");

    render(<MemoryRouter><CompanySwitcher /></MemoryRouter>);

    await waitFor(() => {
      expect(mockGet).toHaveBeenCalledWith("/companies");
    });
  });
});
