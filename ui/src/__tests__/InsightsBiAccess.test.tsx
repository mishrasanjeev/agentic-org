// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
/**
 * Insights BI access page: the login list and connection details, a created
 * password shown once with a copy button and gone after "I have saved it",
 * revoke only after a confirmation, the limit and disabled states, and the
 * Insights header link for administrators while BI access is on.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { HelmetProvider } from "react-helmet-async";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mockGet = vi.fn();
const mockPost = vi.fn();
const mockDelete = vi.fn();

vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
    delete: (...args: unknown[]) => mockDelete(...args),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  },
}));

let mockRole = "admin";
vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => ({
    user: { user_id: "u1", email: "x@example.com", name: "X", role: mockRole, domain: "all", tenant_id: "t1" },
    isAuthenticated: true,
    isHydrating: false,
  }),
}));

import i18n from "@/i18n";
import InsightsBiAccess from "@/pages/InsightsBiAccess";
import InsightsDashboard from "@/pages/InsightsDashboard";
import { normaliseList } from "@/lib/insights/biAccess";

const PASSWORD = "Zx9-q_secretPasswordShownOnce012";
const CONNECTION = {
  host: "10.20.0.7",
  port: 5432,
  database: "agenticorg",
  sslmode: "verify-ca",
  channel_binding: "require",
  schema: "insights_bi",
  views: ["insights_bi.run_facts", "insights_bi.daily", "insights_bi.spend_daily", "insights_bi.agents", "insights_bi.approvals"],
};

function listBody(overrides: Record<string, unknown> = {}) {
  return {
    logins: [
      { role_name: "bi_000000b1_abcdefghij", created_at: "2026-11-01T09:00:00Z", created_by: "u1", revoked_at: null },
      { role_name: "bi_000000b1_klmnopqrst", created_at: "2026-10-01T09:00:00Z", created_by: null, revoked_at: "2026-10-02T09:00:00Z" },
    ],
    connection: CONNECTION,
    max_logins: 3,
    active_logins: 1,
    ...overrides,
  };
}

function httpError(status: number, detail: unknown) {
  return Object.assign(new Error(`HTTP ${status}`), { response: { status, data: { detail } } });
}

function renderPage() {
  return render(
    <HelmetProvider>
      <MemoryRouter initialEntries={["/dashboard/insights/bi-access"]}>
        <InsightsBiAccess />
      </MemoryRouter>
    </HelmetProvider>,
  );
}

const writeText = vi.fn();

beforeEach(async () => {
  mockRole = "admin";
  mockGet.mockReset();
  mockPost.mockReset();
  mockDelete.mockReset();
  writeText.mockReset();
  writeText.mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
  await i18n.changeLanguage("en");
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("InsightsBiAccess", () => {
  it("lists logins and the connection details", async () => {
    mockGet.mockResolvedValue({ data: listBody() });
    renderPage();
    expect(await screen.findByTestId("bi-logins")).toBeTruthy();
    expect(screen.getByTestId("bi-logins").parentElement).toHaveClass("relative", "overflow-x-auto");
    expect(mockGet).toHaveBeenCalledWith("/insights/bi-access");
    expect(screen.getByTestId("bi-host").textContent).toBe("10.20.0.7");
    expect(screen.getByText("insights_bi.spend_daily")).toBeTruthy();
    expect(screen.getByText("verify-ca")).toBeTruthy();
    expect(screen.getByText("require")).toBeTruthy();
    expect(screen.getByText(/root certificate \(sslrootcert\)/)).toBeTruthy();
    expect(screen.getByTestId("bi-usage").textContent).toBe("1 of 3 active logins");
    const active = screen.getByTestId("bi-login-bi_000000b1_abcdefghij");
    expect(within(active).getByText("Active")).toBeTruthy();
    expect(within(active).getByRole("button", { name: "Revoke bi_000000b1_abcdefghij" })).toBeTruthy();
    const revoked = screen.getByTestId("bi-login-bi_000000b1_klmnopqrst");
    expect(within(revoked).queryByRole("button")).toBeNull();
    expect(within(revoked).getByText(/^Revoked /)).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/undefined|NaN/);
  });

  it("shows a created password once, copies it, and forgets it after 'I have saved it'", async () => {
    mockGet.mockResolvedValue({ data: listBody() });
    mockPost.mockResolvedValue({
      data: {
        role_name: "bi_000000b1_newnewnewn",
        password: PASSWORD,
        connection_string: `postgresql://bi_000000b1_newnewnewn:${PASSWORD}@10.20.0.7:5432/agenticorg?sslmode=verify-ca&channel_binding=require`,
        created_at: "2026-11-01T10:00:00Z",
        ...CONNECTION,
      },
    });
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Create login" }));
    expect(await screen.findByTestId("bi-secret")).toBeTruthy();
    expect(mockPost).toHaveBeenCalledWith("/insights/bi-access");
    expect(screen.getByTestId("bi-password").textContent).toBe(PASSWORD);
    expect(screen.getByText("Save this password now")).toBeTruthy();
    // No second login while the password is on screen.
    expect((screen.getByRole("button", { name: "Create login" }) as HTMLButtonElement).disabled).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: "Copy password" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(PASSWORD));
    expect(await screen.findByRole("button", { name: "Copied" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Copy connection string" }));
    await waitFor(() => expect(writeText).toHaveBeenLastCalledWith(expect.stringContaining("sslmode=verify-ca&channel_binding=require")));

    fireEvent.click(screen.getByRole("button", { name: "I have saved it" }));
    await waitFor(() => expect(screen.queryByTestId("bi-secret")).toBeNull());
    expect(document.body.innerHTML).not.toContain(PASSWORD);
    // The list was reloaded after the create; nothing kept the password.
    expect(mockGet.mock.calls.length).toBeGreaterThanOrEqual(2);
    expect(JSON.stringify(localStorage)).not.toContain(PASSWORD);
    expect(JSON.stringify(sessionStorage)).not.toContain(PASSWORD);
  });

  it("revokes only after a confirmation, and cancel does nothing", async () => {
    mockGet.mockResolvedValue({ data: listBody() });
    mockDelete.mockResolvedValue({ data: { role_name: "bi_000000b1_abcdefghij", revoked: true, result: "dropped" } });
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Revoke bi_000000b1_abcdefghij" }));
    const dialog = screen.getByRole("alertdialog", { name: "Revoke this login?" });
    expect(within(dialog).getByText(/stops working at once/)).toBeTruthy();
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(mockDelete).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Revoke bi_000000b1_abcdefghij" }));
    fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Yes, revoke" }));
    await waitFor(() => expect(mockDelete).toHaveBeenCalledWith("/insights/bi-access/bi_000000b1_abcdefghij"));
    expect(await screen.findByText("bi_000000b1_abcdefghij was revoked. It can no longer connect.")).toBeTruthy();
  });

  it("says so at the limit, and turns a 409 into the limit message", async () => {
    mockGet.mockResolvedValue({ data: listBody({ active_logins: 3 }) });
    renderPage();
    const button = (await screen.findByRole("button", { name: "Create login" })) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(screen.getByText(/The limit of active logins is reached/)).toBeTruthy();
    expect(mockPost).not.toHaveBeenCalled();
  });

  it("maps a 409 on create to the limit message", async () => {
    mockGet.mockResolvedValue({ data: listBody({ active_logins: 2 }) });
    mockPost.mockRejectedValue(httpError(409, { error: "insights_bi_limit_reached", max_logins: 3 }));
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Create login" }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/maximum number of active logins/);
    expect(screen.queryByTestId("bi-secret")).toBeNull();
  });

  it("shows the disabled and forbidden states", async () => {
    mockGet.mockRejectedValue(httpError(404, "insights_bi_disabled"));
    const { unmount } = renderPage();
    expect(await screen.findByText("BI database access is not available")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Create login" })).toBeNull();
    unmount();
    mockGet.mockRejectedValue(httpError(403, "A human tenant administrator is required"));
    renderPage();
    expect(await screen.findByText("Administrators only")).toBeTruthy();
  });

  it("renders in Hindi", async () => {
    await i18n.changeLanguage("hi");
    mockGet.mockResolvedValue({ data: listBody() });
    renderPage();
    expect(await screen.findByRole("heading", { level: 1, name: "BI डेटाबेस एक्सेस" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "लॉगिन बनाएँ" })).toBeTruthy();
  });

  it("normalises a partial response without inventing logins", () => {
    expect(normaliseList({})).toEqual({
      logins: [],
      connection: { host: "", port: 5432, database: "", sslmode: "verify-ca", channel_binding: "require", schema: "insights_bi", views: [] },
      max_logins: 0,
      active_logins: 0,
    });
    expect(normaliseList({ logins: [{ role_name: 5 }, null, { role_name: "bi_x" }] }).logins).toEqual([
      { role_name: "bi_x", created_at: null, created_by: null, revoked_at: null },
    ]);
  });
});

describe("Insights header link to BI access", () => {
  const SETTINGS = { timezone: "Asia/Kolkata", fiscal_year_start_month: 4, currency: "INR" };

  function renderDashboard(status: Record<string, unknown>) {
    mockGet.mockImplementation((url: string) => {
      if (url === "/insights/status") return Promise.resolve({ data: status });
      return Promise.resolve({
        data: { view: "executive", period: { from: "2026-10-03", to: "2026-11-01" }, tiles: [], charts: [], needs_you: [] },
      });
    });
    return render(
      <HelmetProvider>
        <MemoryRouter initialEntries={["/dashboard/insights"]}>
          <InsightsDashboard />
        </MemoryRouter>
      </HelmetProvider>,
    );
  }

  it("is shown to an administrator when the status says BI access is on", async () => {
    renderDashboard({ enabled: true, views: ["executive", "finance"], platform_operator: false, bi_access: true, settings: SETTINGS });
    const link = await screen.findByTestId("insights-bi-access-link");
    expect(link.getAttribute("href")).toBe("/dashboard/insights/bi-access");
    expect(link.textContent).toBe("BI database access");
  });

  it("is hidden when BI access is off", async () => {
    renderDashboard({ enabled: true, views: ["executive", "finance"], platform_operator: false, bi_access: false, settings: SETTINGS });
    await screen.findByRole("button", { name: "Refresh" });
    expect(screen.queryByTestId("insights-bi-access-link")).toBeNull();
  });

  it("is hidden from a CFO even if the status says on", async () => {
    mockRole = "cfo";
    renderDashboard({ enabled: true, views: ["finance"], platform_operator: false, bi_access: true, settings: SETTINGS });
    await screen.findByRole("button", { name: "Refresh" });
    expect(screen.queryByTestId("insights-bi-access-link")).toBeNull();
  });
});
