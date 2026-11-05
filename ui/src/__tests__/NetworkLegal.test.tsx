// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
import { render, screen, within } from "@testing-library/react";
import { HelmetProvider } from "react-helmet-async";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import NetworkTerms, { NETWORK_TERMS_VERSION } from "../pages/legal/NetworkTerms";
import Privacy from "../pages/legal/Privacy";
import publicSite from "../content/publicSite.json";

function renderPage(page: React.ReactElement) {
  render(
    <HelmetProvider>
      <MemoryRouter>{page}</MemoryRouter>
    </HelmetProvider>,
  );
}

describe("Privacy notice: shopping network buyers", () => {
  it("keeps the draft label and describes what the MCP server and Telegram bot handle", () => {
    renderPage(<Privacy />);
    expect(screen.getByText(/Draft policy notice - legal and privacy review required/)).toBeInTheDocument();
    const section = screen.getByRole("region", { name: "Shopping network buyers" });
    const text = section.textContent ?? "";
    expect(text).toContain("https://mcp.agenticorg.ai/mcp");
    expect(text).toContain("never receives card details");
    expect(text).toContain("logs each request's method, result status and duration, not its content");
    expect(text).toContain("one-way hash (SHA-256) of the Telegram chat id");
    expect(text).toContain("last six exchanges");
    expect(text).toContain("seven days after the last message");
    expect(text).toContain("30 minutes");
    expect(text).toContain("/stop deletes the stored chat");
    expect(text).toContain("24 hours");
    expect(within(section).getByRole("link", { name: "network terms" })).toHaveAttribute("href", "/network-terms");
  });
});

describe("Shopping network terms page", () => {
  it("is a draft, names the version shops accept and the text of the Sell everywhere checkbox", () => {
    renderPage(<NetworkTerms />);
    expect(screen.getByRole("heading", { level: 1, name: "Shopping network terms" })).toBeInTheDocument();
    expect(screen.getByText(/Draft terms - legal review required/)).toBeInTheDocument();
    expect(NETWORK_TERMS_VERSION).toBe("2026-10-25");
    expect(screen.getAllByText(new RegExp(`version ${NETWORK_TERMS_VERSION}`, "i")).length).toBeGreaterThan(0);
    expect(
      screen.getByText(/AgenticOrg lists my shop and opens checkouts for buyers who find it\. I remain the seller and take\s+payment myself\./),
    ).toBeInTheDocument();
    expect(screen.getByText(/Each shop is the seller of its products/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "privacy notice" })).toHaveAttribute("href", "/privacy#shopping-network-buyers");
  });

  it("is listed for the sitemap", () => {
    const page = publicSite.pages.find((entry: { path: string }) => entry.path === "/network-terms");
    expect(page).toBeDefined();
    expect(page?.index).toBe(true);
  });
});
