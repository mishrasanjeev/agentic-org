/**
 * ReportScheduler Component Tests
 *
 * Tests schedule list rendering, create/edit form, toggle active/inactive,
 * delete, run now, empty state, loading state, and form validation.
 */
import { render, screen, waitFor, fireEvent, act } from "@testing-library/react";
import { vi, describe, it, expect, beforeEach, afterEach } from "vitest";

// ---------------------------------------------------------------------------
// Mock global fetch (ReportScheduler uses fetch directly, not axios)
// ---------------------------------------------------------------------------

const mockFetch = vi.fn();

// Store original
const originalFetch = globalThis.fetch;

// ---------------------------------------------------------------------------
// Import component under test
// ---------------------------------------------------------------------------

import ReportScheduler from "@/pages/ReportScheduler";

// ---------------------------------------------------------------------------
// Test data matching ReportSchedule interface
// ---------------------------------------------------------------------------

const MOCK_SCHEDULES = [
  {
    id: "sched-001",
    report_type: "cfo_daily",
    cron_expression: "daily",
    delivery_channels: [
      { type: "email", target: "cfo@company.com" },
      { type: "slack", target: "C01ABC23DEF" },
    ],
    format: "pdf",
    is_active: true,
    company_id: "comp-001",
    params: {},
    tenant_id: "tenant-001",
    last_run_at: "2026-03-29T08:00:00Z",
    next_run_at: "2026-03-30T08:00:00Z",
    created_at: "2026-03-01T00:00:00Z",
  },
  {
    id: "sched-002",
    report_type: "cmo_weekly",
    cron_expression: "weekly",
    delivery_channels: [{ type: "email", target: "cmo@company.com" }],
    format: "excel",
    is_active: false,
    company_id: "comp-001",
    params: {},
    tenant_id: "tenant-001",
    last_run_at: null,
    next_run_at: "2026-04-05T09:00:00Z",
    created_at: "2026-03-15T00:00:00Z",
  },
  {
    id: "sched-003",
    report_type: "aging_report",
    cron_expression: "monthly",
    delivery_channels: [
      { type: "whatsapp", target: "+919876543210" },
    ],
    format: "both",
    is_active: true,
    company_id: "comp-001",
    params: {},
    tenant_id: "tenant-001",
    last_run_at: "2026-03-01T10:00:00Z",
    next_run_at: "2026-04-01T10:00:00Z",
    created_at: "2026-02-01T00:00:00Z",
  },
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function setupFetch(schedules = MOCK_SCHEDULES) {
  mockFetch.mockImplementation(async (url: string, options?: RequestInit) => {
    // GET schedules list
    if (url.endsWith("/report-schedules") && (!options || options.method === undefined || options.method === "GET")) {
      return {
        ok: true,
        status: 200,
        json: async () => schedules,
      };
    }
    // POST create
    if (url.endsWith("/report-schedules") && options?.method === "POST") {
      return {
        ok: true,
        status: 201,
        json: async () => ({
          id: "sched-new",
          ...JSON.parse(options.body as string),
        }),
      };
    }
    // PATCH update
    if (url.includes("/report-schedules/") && options?.method === "PATCH") {
      return {
        ok: true,
        status: 200,
        json: async () => ({}),
      };
    }
    // DELETE
    if (url.includes("/report-schedules/") && options?.method === "DELETE") {
      return {
        ok: true,
        status: 204,
        json: async () => ({}),
      };
    }
    // POST run-now
    if (url.includes("/run-now") && options?.method === "POST") {
      return {
        ok: true,
        status: 200,
        json: async () => ({ task_id: "task-abc-123" }),
      };
    }
    return { ok: false, status: 404, json: async () => ({}) };
  });
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("ReportScheduler", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    globalThis.fetch = mockFetch;
    // SEC-002 (PR-F): cookie-first. The component no longer reads a
    // bearer token from localStorage; the HttpOnly session cookie is
    // shipped via credentials: "include". Set a CSRF cookie so the
    // mutation paths attach the X-CSRF-Token header for parity with
    // the production CSRF middleware.
    document.cookie = "agenticorg_csrf=test-csrf-token; path=/";
    // Mock window.confirm
    vi.spyOn(window, "confirm").mockReturnValue(true);
    // Mock window.alert
    vi.spyOn(window, "alert").mockImplementation(() => {});
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  // ── Loading State ──────────────────────────────────────────────────────

  it("shows loading state while fetching schedules", () => {
    mockFetch.mockReturnValue(new Promise(() => {}));

    render(<ReportScheduler />);

    expect(screen.getByText("Loading schedules...")).toBeInTheDocument();
  });

  // ── Schedule List Rendering ────────────────────────────────────────────

  it("renders schedule list after loading", async () => {
    setupFetch();

    render(<ReportScheduler />);

    await waitFor(() => {
      expect(screen.getByText("CFO Daily Briefing")).toBeInTheDocument();
    });

    expect(screen.getByText("CMO Weekly Report")).toBeInTheDocument();
    expect(screen.getByText("AR/AP Aging")).toBeInTheDocument();
  });

  it("shows report types with human-readable labels", async () => {
    setupFetch();

    render(<ReportScheduler />);

    await waitFor(() => {
      expect(screen.getByText("CFO Daily Briefing")).toBeInTheDocument();
    });

    expect(screen.getByText("CMO Weekly Report")).toBeInTheDocument();
    expect(screen.getByText("AR/AP Aging")).toBeInTheDocument();
  });

  it("shows delivery channel types under report names", async () => {
    setupFetch();

    render(<ReportScheduler />);

    await waitFor(() => {
      expect(screen.getByText("email, slack")).toBeInTheDocument();
    });

    expect(screen.getByText("whatsapp")).toBeInTheDocument();
  });

  it("shows Active/Paused status badges", async () => {
    setupFetch();

    render(<ReportScheduler />);

    await waitFor(() => {
      const activeButtons = screen.getAllByText("Active");
      expect(activeButtons.length).toBe(2);
    });

    expect(screen.getByText("Paused")).toBeInTheDocument();
  });

  // ── Empty State ────────────────────────────────────────────────────────

  it("shows empty state when no schedules exist", async () => {
    setupFetch([]);

    render(<ReportScheduler />);

    await waitFor(() => {
      expect(
        screen.getByText(/No report schedules yet/),
      ).toBeInTheDocument();
    });
  });

  // ── Header & New Schedule Button ───────────────────────────────────────

  it("renders header and New Schedule button", async () => {
    setupFetch();

    render(<ReportScheduler />);

    expect(screen.getByText("Report Schedules")).toBeInTheDocument();
    expect(screen.getByText("+ New Schedule")).toBeInTheDocument();
  });

  it("clicking New Schedule opens the create form", async () => {
    setupFetch();

    render(<ReportScheduler />);

    await waitFor(() => {
      expect(screen.getByText("CFO Daily Briefing")).toBeInTheDocument();
    });

    await act(async () => {
      fireEvent.click(screen.getByText("+ New Schedule"));
    });

    expect(screen.getAllByText("Create Schedule").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("Report Type").length).toBeGreaterThanOrEqual(1);
    // Form is now open — verify structure exists
    expect(document.body.textContent).toContain("Report Type");
    expect(document.body.textContent).toContain("Format");
  });

  // ── Create Form ────────────────────────────────────────────────────────

  it("create form has all report type options", async () => {
    setupFetch();

    render(<ReportScheduler />);

    await waitFor(() => {
      expect(screen.getByText("CFO Daily Briefing")).toBeInTheDocument();
    });

    await act(async () => {
      fireEvent.click(screen.getByText("+ New Schedule"));
    });

    // Check select options exist
    const reportTypeSelect = screen.getAllByRole("combobox")[0];
    expect(reportTypeSelect).toBeInTheDocument();

    // Verify options
    const options = reportTypeSelect.querySelectorAll("option");
    const optionValues = Array.from(options).map((o) => o.textContent);
    expect(optionValues).toContain("CFO Daily Briefing");
    expect(optionValues).toContain("CMO Weekly Report");
    expect(optionValues).toContain("P&L Monthly");
    expect(optionValues).toContain("AR/AP Aging");
    expect(optionValues).toContain("Campaign Performance");
  });

  it("create form has schedule presets", async () => {
    setupFetch();

    render(<ReportScheduler />);

    await waitFor(() => {
      expect(screen.getByText("CFO Daily Briefing")).toBeInTheDocument();
    });

    await act(async () => {
      fireEvent.click(screen.getByText("+ New Schedule"));
    });

    const scheduleSelect = screen.getAllByRole("combobox")[1];
    const options = scheduleSelect.querySelectorAll("option");
    const optionValues = Array.from(options).map((o) => o.textContent);
    expect(optionValues).toContain("Daily");
    expect(optionValues).toContain("Weekly");
    expect(optionValues).toContain("Monthly");
  });

  it("create form has format options", async () => {
    setupFetch();

    render(<ReportScheduler />);

    await waitFor(() => {
      expect(screen.getByText("CFO Daily Briefing")).toBeInTheDocument();
    });

    await act(async () => {
      fireEvent.click(screen.getByText("+ New Schedule"));
    });

    const formatSelect = screen.getAllByRole("combobox")[2];
    const options = formatSelect.querySelectorAll("option");
    const optionValues = Array.from(options).map((o) => o.textContent);
    expect(optionValues).toContain("PDF");
    expect(optionValues).toContain("Excel");
    expect(optionValues).toContain("Both");
  });

  it("create form shows delivery channel checkboxes", async () => {
    setupFetch();

    render(<ReportScheduler />);

    await waitFor(() => {
      expect(screen.getByText("CFO Daily Briefing")).toBeInTheDocument();
    });

    await act(async () => {
      fireEvent.click(screen.getByText("+ New Schedule"));
    });

    expect(screen.getByText("Delivery Channels")).toBeInTheDocument();
    expect(screen.getByLabelText("Email")).toBeInTheDocument();
    expect(screen.getByLabelText("Slack")).toBeInTheDocument();
    expect(screen.getByLabelText("WhatsApp")).toBeInTheDocument();
  });

  it("email input appears when Email checkbox is checked", async () => {
    setupFetch();

    render(<ReportScheduler />);

    await waitFor(() => {
      expect(screen.getByText("CFO Daily Briefing")).toBeInTheDocument();
    });

    await act(async () => {
      fireEvent.click(screen.getByText("+ New Schedule"));
    });

    // Email is checked by default, so the email input should be visible
    const emailInput = screen.getByPlaceholderText("recipient@company.com");
    expect(emailInput).toBeInTheDocument();
  });

  it("submitting create form calls POST API", async () => {
    setupFetch();

    render(<ReportScheduler />);

    await waitFor(() => {
      expect(screen.getByText("CFO Daily Briefing")).toBeInTheDocument();
    });

    await act(async () => {
      fireEvent.click(screen.getByText("+ New Schedule"));
    });

    // Fill email
    const emailInput = screen.getByPlaceholderText("recipient@company.com");
    await act(async () => {
      fireEvent.change(emailInput, { target: { value: "test@company.com" } });
    });

    // Submit — multiple "Create Schedule" texts exist (card title + button)
    const submitBtns = screen.getAllByText("Create Schedule");
    const submitBtn = submitBtns[submitBtns.length - 1]; // Last one is the button
    await act(async () => {
      fireEvent.click(submitBtn);
    });

    // Verify POST was called
    await waitFor(() => {
      const postCalls = mockFetch.mock.calls.filter(
        (call: unknown[]) =>
          (call[0] as string).endsWith("/report-schedules") &&
          (call[1] as RequestInit)?.method === "POST",
      );
      expect(postCalls.length).toBeGreaterThanOrEqual(1);
    });
  });

  it("Cancel button closes the form", async () => {
    setupFetch();

    render(<ReportScheduler />);

    await waitFor(() => {
      expect(screen.getByText("CFO Daily Briefing")).toBeInTheDocument();
    });

    await act(async () => {
      fireEvent.click(screen.getByText("+ New Schedule"));
    });

    expect(screen.getAllByText("Create Schedule").length).toBeGreaterThanOrEqual(1);

    await act(async () => {
      fireEvent.click(screen.getByText("Cancel"));
    });

    // Form should be closed - "Create Schedule" as form title should be gone
    // but "Create Schedule" as submit button is also gone
    expect(screen.queryByText("Report Type")).not.toBeInTheDocument();
  });

  // ── Toggle Active/Inactive ─────────────────────────────────────────────

  it("clicking Active/Paused badge calls PATCH API to toggle", async () => {
    setupFetch();

    render(<ReportScheduler />);

    await waitFor(() => {
      expect(screen.getByText("CFO Daily Briefing")).toBeInTheDocument();
    });

    // Click the Paused badge (sched-002)
    const pausedBadge = screen.getByText("Paused");
    await act(async () => {
      fireEvent.click(pausedBadge);
    });

    await waitFor(() => {
      const patchCalls = mockFetch.mock.calls.filter(
        (call: unknown[]) =>
          (call[0] as string).includes("/report-schedules/sched-002") &&
          (call[1] as RequestInit)?.method === "PATCH",
      );
      expect(patchCalls.length).toBe(1);

      // Should toggle to active
      const body = JSON.parse((patchCalls[0][1] as RequestInit).body as string);
      expect(body.is_active).toBe(true);
    });
  });

  // ── Run Now ────────────────────────────────────────────────────────────

  it("Run button calls run-now API endpoint", async () => {
    setupFetch();

    render(<ReportScheduler />);

    await waitFor(() => {
      expect(screen.getByText("CFO Daily Briefing")).toBeInTheDocument();
    });

    // Click the first "Run" button
    const runButtons = screen.getAllByTitle("Run now");
    await act(async () => {
      fireEvent.click(runButtons[0]);
    });

    await waitFor(() => {
      const runCalls = mockFetch.mock.calls.filter(
        (call: unknown[]) =>
          (call[0] as string).includes("/run-now") &&
          (call[1] as RequestInit)?.method === "POST",
      );
      expect(runCalls.length).toBe(1);
    });

    // Should show alert with task ID
    expect(window.alert).toHaveBeenCalledWith(
      expect.stringContaining("task-abc-123"),
    );
  });

  // ── Delete ─────────────────────────────────────────────────────────────

  it("Del button calls DELETE API after confirmation", async () => {
    setupFetch();

    render(<ReportScheduler />);

    await waitFor(() => {
      expect(screen.getByText("CFO Daily Briefing")).toBeInTheDocument();
    });

    // Click the first "Del" button
    const delButtons = screen.getAllByTitle("Delete");
    await act(async () => {
      fireEvent.click(delButtons[0]);
    });

    expect(window.confirm).toHaveBeenCalled();

    await waitFor(() => {
      const deleteCalls = mockFetch.mock.calls.filter(
        (call: unknown[]) =>
          (call[0] as string).includes("/report-schedules/sched-001") &&
          (call[1] as RequestInit)?.method === "DELETE",
      );
      expect(deleteCalls.length).toBe(1);
    });
  });

  // ── Edit ───────────────────────────────────────────────────────────────

  it("Edit button opens form pre-filled with schedule data", async () => {
    setupFetch();

    render(<ReportScheduler />);

    await waitFor(() => {
      expect(screen.getByText("CFO Daily Briefing")).toBeInTheDocument();
    });

    // Click the first "Edit" button
    const editButtons = screen.getAllByTitle("Edit");
    await act(async () => {
      fireEvent.click(editButtons[0]);
    });

    // Form should show "Edit Schedule" title (not "Create Schedule")
    expect(screen.getByText("Edit Schedule")).toBeInTheDocument();

    // Submit button should say "Update Schedule"
    expect(screen.getByText("Update Schedule")).toBeInTheDocument();
  });

  // ── Error Handling ─────────────────────────────────────────────────────

  it("shows error banner when fetch fails", async () => {
    mockFetch.mockRejectedValue(new Error("Network Error"));

    render(<ReportScheduler />);

    await waitFor(() => {
      expect(screen.getByText("Network Error")).toBeInTheDocument();
    });

    // Should have dismiss button
    expect(screen.getByText("Dismiss")).toBeInTheDocument();
  });

  it("dismiss button clears error banner", async () => {
    mockFetch.mockRejectedValue(new Error("Network Error"));

    render(<ReportScheduler />);

    await waitFor(() => {
      expect(screen.getByText("Network Error")).toBeInTheDocument();
    });

    await act(async () => {
      fireEvent.click(screen.getByText("Dismiss"));
    });

    expect(screen.queryByText("Network Error")).not.toBeInTheDocument();
  });

  // ── Table Structure ────────────────────────────────────────────────────

  it("renders table with correct column headers", async () => {
    setupFetch();

    render(<ReportScheduler />);

    await waitFor(() => {
      expect(screen.getByText("Report")).toBeInTheDocument();
    });

    expect(screen.getByText("Status")).toBeInTheDocument();
    expect(screen.getByText("Actions")).toBeInTheDocument();
  });

  // ── QA regressions (TC_001 / TC_002) ───────────────────────────────────

  it("blocks submit and shows inline error when email recipient is invalid (TC_001)", async () => {
    setupFetch();
    render(<ReportScheduler />);
    await waitFor(() => {
      expect(screen.getByText("CFO Daily Briefing")).toBeInTheDocument();
    });
    await act(async () => {
      fireEvent.click(screen.getByText("+ New Schedule"));
    });
    // Email is enabled by default; type something that fails our regex.
    const emailInput = screen.getByPlaceholderText("recipient@company.com");
    await act(async () => {
      fireEvent.change(emailInput, { target: { value: "not-an-email" } });
    });
    // Submit by submitting the form element itself (avoid HTML5
    // email-type validation intercepting the submit button click).
    const form = emailInput.closest("form")!;
    await act(async () => {
      fireEvent.submit(form);
    });
    // Inline validation should fire; no POST should be made.
    const postCalls = mockFetch.mock.calls.filter(
      (c: unknown[]) =>
        (c[0] as string).endsWith("/report-schedules") &&
        (c[1] as RequestInit)?.method === "POST",
    );
    expect(postCalls.length).toBe(0);
    expect(
      await screen.findByText(/Invalid email address/),
    ).toBeInTheDocument();
  });

  it("exposes execution time + day-of-week + advanced cron controls (TC_002)", async () => {
    setupFetch();
    render(<ReportScheduler />);
    await waitFor(() => {
      expect(screen.getByText("CFO Daily Briefing")).toBeInTheDocument();
    });
    await act(async () => {
      fireEvent.click(screen.getByText("+ New Schedule"));
    });
    // Execution time is always visible.
    const timeInput = screen.getByLabelText(/Execution Time/);
    expect(timeInput).toBeInTheDocument();
    // Day of Week appears when weekly is selected.
    const freqSelect = screen.getByLabelText(/Frequency/);
    await act(async () => {
      fireEvent.change(freqSelect, { target: { value: "weekly" } });
    });
    expect(screen.getByLabelText(/Day of Week/)).toBeInTheDocument();
    // Advanced mode reveals a cron textbox.
    const advancedToggle = screen.getByText(/Advanced — write CRON/);
    await act(async () => {
      fireEvent.click(advancedToggle);
    });
    expect(
      screen.getByLabelText(/Custom cron expression/i),
    ).toBeInTheDocument();
  });

  it("composes a real cron string from frequency + time before POST (TC_002)", async () => {
    setupFetch();
    render(<ReportScheduler />);
    await waitFor(() => {
      expect(screen.getByText("CFO Daily Briefing")).toBeInTheDocument();
    });
    await act(async () => {
      fireEvent.click(screen.getByText("+ New Schedule"));
    });
    const emailInput = screen.getByPlaceholderText("recipient@company.com");
    await act(async () => {
      fireEvent.change(emailInput, { target: { value: "ops@example.com" } });
    });
    // Set time to 14:30 — cron must be "30 14 * * *".
    const timeInput = screen.getByLabelText(/Execution Time/) as HTMLInputElement;
    await act(async () => {
      fireEvent.change(timeInput, { target: { value: "14:30" } });
    });
    const submitBtns = screen.getAllByText("Create Schedule");
    const submitBtn = submitBtns[submitBtns.length - 1];
    await act(async () => {
      fireEvent.click(submitBtn);
    });
    await waitFor(() => {
      const postCall = mockFetch.mock.calls.find(
        (c: unknown[]) =>
          (c[0] as string).endsWith("/report-schedules") &&
          (c[1] as RequestInit)?.method === "POST",
      );
      expect(postCall).toBeDefined();
      const body = JSON.parse((postCall![1] as RequestInit).body as string);
      expect(body.cron_expression).toBe("30 14 * * *");
      expect(body.delivery_channels).toEqual([
        { type: "email", target: "ops@example.com" },
      ]);
    });
  });
});
