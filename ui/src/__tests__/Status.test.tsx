// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
import { render, screen } from "@testing-library/react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import StatusPage from "@/pages/Status";

afterEach(() => vi.unstubAllGlobals());

describe("public service status", () => {
  it("server-renders useful context and support links without claiming live availability", () => {
    const fetcher = vi.fn();
    vi.stubGlobal("fetch", fetcher);
    const element = document.createElement("div");
    element.innerHTML = renderToStaticMarkup(<StatusPage />);

    expect(element.querySelectorAll("h1")).toHaveLength(1);
    expect(element.textContent!.length).toBeGreaterThan(500);
    expect(element.querySelector('a[href="/support"]')).not.toBeNull();
    expect(element.querySelector('a[href="/docs/troubleshooting"]')).not.toBeNull();
    expect(element.textContent).toContain("Availability is not confirmed");
    expect(element.textContent).not.toContain("All systems operational");
    expect(element.textContent).not.toMatch(/\d+\.\d+%/);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("keeps an explicit unconfirmed state while the current report is loading", () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));
    render(<StatusPage />);
    expect(screen.getByRole("status")).toHaveTextContent("Availability is not confirmed");
    expect(screen.getByRole("link", { name: "Contact support" })).toHaveAttribute("href", "/support");
    expect(screen.queryByText("All systems operational")).not.toBeInTheDocument();
  });

  it("retains support and interpretation information when the status endpoint fails", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 503 }));
    render(<StatusPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("HTTP 503");
    expect(screen.getByRole("heading", { name: "Reading the service report" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Contact support" })).toBeInTheDocument();
    expect(screen.queryByText("All systems operational")).not.toBeInTheDocument();
  });

  it("shows operational status only after the endpoint supplies a current report", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        overall: "operational",
        services: [{ name: "API", status: "operational" }],
        active_incidents: [],
        recent_incidents: [],
        uptime_30d_percent: 99.5,
        last_updated: "2026-10-11T02:00:00Z",
      }),
    }));
    render(<StatusPage />);
    expect(await screen.findByText("All systems operational")).toBeInTheDocument();
    expect(screen.getByText("99.50%")).toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Contact support" })).toBeInTheDocument();
  });
});
