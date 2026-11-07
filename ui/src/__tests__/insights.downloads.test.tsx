// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
/**
 * Insights background exports: the export menu offers to prepare a file in
 * the background when the server says an inline export is too large, sends
 * the metric and filters, and asks for raw personal data only for roles that
 * may have it; the Downloads page lists files, polls while one is being made,
 * and downloads a ready file through the API.
 */
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HelmetProvider } from "react-helmet-async";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mockGet = vi.fn();
const mockPost = vi.fn();
let mockRole = "cfo";

vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
  },
  extractApiError: () => null,
}));
vi.mock("@/contexts/AuthContext", () => ({
  useOptionalRole: () => mockRole,
  useAuth: () => ({ user: { role: mockRole }, isAuthenticated: true, isHydrating: false }),
}));

import i18n from "@/i18n";
import ExportMenu from "@/components/insights/ExportMenu";
import InsightsDownloads, { POLL_MS } from "@/pages/InsightsDownloads";

function job(overrides: Record<string, unknown> = {}) {
  return {
    id: "j1",
    kind: "explorer",
    format: "csv",
    key: "runs",
    period: { from: "2026-10-01", to: "2026-10-31" },
    include_pii: false,
    status: "ready",
    error: null,
    row_count: 120000,
    size_bytes: 2_400_000,
    sha256: "ab".repeat(32),
    filename: "insights-explore-runs-2026-10-01-2026-10-31.csv",
    created_at: "2026-11-03T05:00:00+00:00",
    finished_at: "2026-11-03T05:01:00+00:00",
    expires_at: "2026-11-10T05:00:00+00:00",
    signed_at: null,
    signature: null,
    download: "/insights/exports/j1/download",
    ...overrides,
  };
}

function tooLarge() {
  const body = new Blob([
    JSON.stringify({ detail: { error: "export_too_large", hint: "Export over 50,000 rows.", rows: 120000, background: true } }),
  ]);
  return { response: { status: 413, data: body } };
}

describe("ExportMenu background exports", () => {
  beforeEach(async () => {
    mockGet.mockReset();
    mockPost.mockReset();
    mockRole = "cfo";
    await i18n.changeLanguage("en");
  });

  it("offers to prepare a too-large export in the background and sends the metric with its filters", async () => {
    const user = userEvent.setup();
    mockGet.mockRejectedValue(tooLarge());
    mockPost.mockResolvedValue({ data: { export: job({ status: "queued", kind: "metric", key: "cost.spend" }) } });
    render(
      <MemoryRouter>
        <ExportMenu metricKey="cost.spend" title="Spend" filters={{ from: "2025-01-01", to: "2026-10-31" }} />
      </MemoryRouter>,
    );
    await user.click(screen.getByRole("button", { name: "Export Spend" }));
    // Not an administrator or auditor: no raw-data choice.
    expect(screen.queryByRole("checkbox")).toBeNull();
    await user.click(screen.getByRole("menuitem", { name: "CSV" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Export over 50,000 rows.");
    await user.click(screen.getByTestId("export-background"));
    await waitFor(() =>
      expect(mockPost).toHaveBeenCalledWith("/insights/exports", {
        include_pii: false,
        kind: "metric",
        format: "csv",
        params: { key: "cost.spend", from: "2025-01-01", to: "2026-10-31" },
      }),
    );
    expect(await screen.findByText(/It is being prepared/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open Downloads" })).toHaveAttribute("href", "/dashboard/insights/downloads");
  });

  it("lets an auditor ask for raw personal data, on the inline export too", async () => {
    const user = userEvent.setup();
    mockRole = "auditor";
    mockGet.mockResolvedValue({ data: new Blob(["a,b"]) });
    Object.assign(URL, { createObjectURL: vi.fn(() => "blob:x"), revokeObjectURL: vi.fn() });
    render(
      <MemoryRouter>
        <ExportMenu metricKey="compliance.change_log" title="Changes" filters={{ from: "2026-10-01" }} />
      </MemoryRouter>,
    );
    await user.click(screen.getByRole("button", { name: "Export Changes" }));
    await user.click(screen.getByRole("checkbox", { name: /Include raw personal data/ }));
    await user.click(screen.getByRole("menuitem", { name: "CSV" }));
    await waitFor(() => expect(mockGet).toHaveBeenCalled());
    expect(mockGet.mock.calls[0][1].params).toEqual({ format: "csv", from: "2026-10-01", include_pii: "true" });
  });
});

describe("InsightsDownloads", () => {
  beforeEach(async () => {
    mockGet.mockReset();
    mockPost.mockReset();
    await i18n.changeLanguage("en");
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  function renderPage() {
    return render(
      <HelmetProvider>
        <MemoryRouter>
          <InsightsDownloads />
        </MemoryRouter>
      </HelmetProvider>,
    );
  }

  it("lists files and downloads a ready one through the API", async () => {
    const created: Blob[] = [];
    Object.assign(URL, {
      createObjectURL: vi.fn((b: Blob) => {
        created.push(b);
        return "blob:x";
      }),
      revokeObjectURL: vi.fn(),
    });
    mockGet.mockImplementation((url: string) => {
      if (url === "/insights/exports")
        return Promise.resolve({
          data: {
            exports: [job(), job({ id: "j2", status: "failed", error: "export_too_large: 900,000 rows", download: null })],
            storage: true,
          },
        });
      if (url === "/insights/exports/j1/download") return Promise.resolve({ data: new Blob(["x"]) });
      return Promise.reject(new Error(`unexpected ${url}`));
    });
    renderPage();
    const row = await screen.findByTestId("download-j1");
    expect(within(row).getByText("Ready")).toBeInTheDocument();
    expect(within(row).getByText("120,000")).toBeInTheDocument();
    expect(within(row).getByText("2.3 MB")).toBeInTheDocument();
    expect(within(screen.getByTestId("download-j2")).getByText("export_too_large: 900,000 rows")).toBeInTheDocument();
    expect(within(screen.getByTestId("download-j2")).queryByRole("button")).toBeNull();
    fireEvent.click(within(row).getByRole("button", { name: /Download insights-explore-runs/ }));
    expect(await within(row).findByText("Saved.")).toBeInTheDocument();
    expect(created).toHaveLength(1);
    expect(document.body.textContent).not.toMatch(/NaN|undefined|\[object Object\]/);
  });

  it("polls while a file is being prepared and stops when it is ready", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    let calls = 0;
    mockGet.mockImplementation(() => {
      calls += 1;
      return Promise.resolve({ data: { exports: [job({ status: calls === 1 ? "running" : "ready" })], storage: true } });
    });
    // Flush the first response and the effect that schedules the polling interval.
    await act(async () => { renderPage(); });
    expect(screen.getByText("Preparing")).toBeInTheDocument();
    expect(screen.getByText(/still being prepared/)).toBeInTheDocument();
    await act(() => vi.advanceTimersByTimeAsync(POLL_MS - 1));
    expect(calls).toBe(1);
    await act(() => vi.advanceTimersByTimeAsync(1));
    expect(screen.getByText("Ready")).toBeInTheDocument();
    expect(calls).toBe(2);
    await act(() => vi.advanceTimersByTimeAsync(POLL_MS * 3));
    expect(calls).toBe(2);
  });

  it("says when background files are not set up", async () => {
    mockGet.mockResolvedValue({ data: { exports: [], storage: false } });
    renderPage();
    expect(await screen.findByText(/Background files are not set up/)).toBeInTheDocument();
  });
});

describe("chart pictures and view files", () => {
  beforeEach(async () => {
    mockGet.mockReset();
    mockPost.mockReset();
    await i18n.changeLanguage("en");
  });

  it("serialises a chart with its title, period and data quality burned in, colours resolved", async () => {
    const { chartSvg } = await import("@/lib/insights/chartImage");
    document.documentElement.style.setProperty("--chart-1", "243 75% 59%");
    const host = document.createElement("figure");
    host.innerHTML = '<svg width="400" height="200"><rect width="10" height="10" fill="hsl(var(--chart-1))"/></svg>';
    document.body.appendChild(host);
    const markup = chartSvg(host, { title: "Spend <INR>", subtitle: "2026-10-01 to 2026-10-31", quality: "Measured" });
    expect(markup).toContain("Spend &lt;INR&gt;");
    expect(markup).toContain("2026-10-01 to 2026-10-31");
    expect(markup).toContain("Measured");
    expect(markup).toContain('fill="hsl(243 75% 59%)"');
    expect(markup).not.toContain("var(--");
    expect(chartSvg(document.createElement("div"), { title: "x", subtitle: "", quality: "" })).toBeNull();
    host.remove();
  });

  it("saves the chart as SVG from the export menu", async () => {
    const user = userEvent.setup();
    const created: Blob[] = [];
    Object.assign(URL, {
      createObjectURL: vi.fn((b: Blob) => {
        created.push(b);
        return "blob:x";
      }),
      revokeObjectURL: vi.fn(),
    });
    render(
      <MemoryRouter>
        <ExportMenu metricKey="cost.spend" title="Spend" filters={{}} picture={() => "<svg>chart</svg>"} />
      </MemoryRouter>,
    );
    await user.click(screen.getByRole("button", { name: "Export Spend" }));
    await user.click(screen.getByRole("menuitem", { name: "Chart (SVG)" }));
    await waitFor(() => expect(created).toHaveLength(1));
    expect(created[0].type).toBe("image/svg+xml;charset=utf-8");
    expect(await created[0].text()).toBe("<svg>chart</svg>");
  });

  it("asks for the view PDF, and the packs only on their views for their roles", async () => {
    const { default: ViewFiles } = await import("@/components/insights/ViewFiles");
    mockPost.mockResolvedValue({ data: { export: job({ status: "queued", kind: "view_pdf" }) } });
    const filters = { from: "2026-10-01", to: "2026-10-31", compare: "previous" as const };
    const { rerender } = render(
      <MemoryRouter>
        <ViewFiles view="finance" filters={filters} role="cfo" />
      </MemoryRouter>,
    );
    expect(screen.getByTestId("view-file-finance")).toBeInTheDocument();
    expect(screen.queryByTestId("view-file-evidence")).toBeNull();
    fireEvent.click(screen.getByTestId("view-file-pdf"));
    await waitFor(() =>
      expect(mockPost).toHaveBeenCalledWith("/insights/exports", {
        include_pii: false,
        kind: "view_pdf",
        format: "pdf",
        params: { view: "finance", compare: "previous", from: "2026-10-01", to: "2026-10-31", company_id: undefined },
      }),
    );
    expect(await screen.findByText(/It is being prepared/)).toBeInTheDocument();
    rerender(
      <MemoryRouter>
        <ViewFiles view="compliance" filters={filters} role="auditor" />
      </MemoryRouter>,
    );
    expect(screen.queryByTestId("view-file-finance")).toBeNull();
    fireEvent.click(screen.getByTestId("view-file-evidence"));
    await waitFor(() =>
      expect(mockPost).toHaveBeenLastCalledWith("/insights/exports", {
        include_pii: false,
        kind: "evidence_pack",
        format: "zip",
        params: { from: "2026-10-01", to: "2026-10-31", company_id: undefined },
      }),
    );
  });

  it("lets an auditor sign a ready evidence pack once, with a statement", async () => {
    mockRole = "auditor";
    const pack = job({
      id: "p1",
      kind: "evidence_pack",
      format: "zip",
      filename: "insights-evidence-pack-2026-10-01-2026-10-31.zip",
      verification_status: "signatures_verified",
    });
    let signed = false;
    mockGet.mockImplementation(() =>
      Promise.resolve({
        data: {
          exports: [
            signed
              ? {
                  ...pack,
                  signed_at: "2026-11-03T06:00:00+00:00",
                  signature: { by: "Meera", statement: "Reviewed and accepted." },
                }
              : pack,
          ],
          storage: true,
        },
      }),
    );
    mockPost.mockImplementation(() => {
      signed = true;
      return Promise.resolve({ data: { export: { ...pack, signed_at: "2026-11-03T06:00:00+00:00" } } });
    });
    render(
      <HelmetProvider>
        <MemoryRouter>
          <InsightsDownloads />
        </MemoryRouter>
      </HelmetProvider>,
    );
    expect(await screen.findByTestId("verification-p1")).toHaveTextContent("every signature verified");
    fireEvent.click(screen.getByRole("button", { name: "Sign this pack" }));
    const box = screen.getByLabelText("Your sign-off statement");
    expect(box).toHaveValue("I have reviewed the evidence pack for 2026-10-01 to 2026-10-31 and its verification report.");
    fireEvent.change(box, { target: { value: "Reviewed and accepted." } });
    fireEvent.click(screen.getByRole("button", { name: "Sign" }));
    await waitFor(() =>
      expect(mockPost).toHaveBeenCalledWith("/insights/exports/p1/sign", { statement: "Reviewed and accepted." }),
    );
    expect(await screen.findByTestId("signature-p1")).toHaveTextContent("Signed by Meera");
    expect(screen.queryByRole("button", { name: "Sign this pack" })).toBeNull();
    mockRole = "cfo";
  });
});
