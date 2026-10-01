// SPDX-License-Identifier: Apache-2.0
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { HelmetProvider } from "react-helmet-async";
import { MemoryRouter, Route, Routes } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import Documentation, { searchGuides } from "@/pages/Documentation";
import manual from "@/content/userDocs.generated.json";

function open(path = "/docs") {
  return render(
    <HelmetProvider>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/docs" element={<Documentation />} />
          <Route path="/docs/:slug" element={<Documentation />} />
        </Routes>
      </MemoryRouter>
    </HelmetProvider>,
  );
}

beforeEach(() => {
  vi.spyOn(window, "scrollTo").mockImplementation(() => {});
  Element.prototype.scrollIntoView = vi.fn();
});

describe("public documentation", () => {
  it("offers role learning paths and all guide groups without authentication", () => {
    open();
    expect(
      screen.getByRole("heading", {
        level: 1,
        name: "AgenticOrg documentation",
      }),
    ).toBeInTheDocument();
    const directory = screen.getByRole("main");
    for (const group of manual.groups)
      expect(
        within(directory).getByRole("heading", { name: group }),
      ).toBeInTheDocument();
    expect(
      within(directory).getAllByRole("heading", { level: 3 }),
    ).toHaveLength(manual.articles.length);
    const ownership = screen.getByTestId("product-ownership");
    expect(ownership).toHaveTextContent("Orchestrum Technologies LLP");
    expect(ownership).toHaveTextContent("Sanjeev Kumar");
    expect(within(ownership).getByRole("link", { name: "sanjeev@orchestrum.in" }))
      .toHaveAttribute("href", "mailto:sanjeev@orchestrum.in");
  });

  it("searches article body and requires every token, not just guide titles", () => {
    expect(searchGuides("LibreOffice").map((guide) => guide.slug)).toContain(
      "knowledge-and-ocr",
    );
    expect(
      searchGuides("Shopify freshness").map((guide) => guide.slug),
    ).toContain("commerce");
    expect(searchGuides("unfindable-abc")).toEqual([]);
    expect(searchGuides("   ")).toEqual([]);
    open();
    fireEvent.change(screen.getByRole("searchbox"), {
      target: { value: "LibreOffice" },
    });
    expect(
      screen.getByRole("heading", { level: 1, name: /Results for/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("region", { name: "Search results" }),
    ).toHaveTextContent("Knowledge");
    fireEvent.click(screen.getByRole("button", { name: "Clear search" }));
    expect(
      screen.getByRole("heading", {
        level: 1,
        name: "AgenticOrg documentation",
      }),
    ).toBeInTheDocument();
  });

  it("shows an actionable empty search and clears it with Escape", () => {
    open();
    fireEvent.change(screen.getByRole("searchbox"), {
      target: { value: "unfindable-abc" },
    });
    expect(screen.getByText(/No matching guide/)).toBeInTheDocument();
    fireEvent.keyDown(screen.getByRole("searchbox"), { key: "Escape" });
    expect(screen.getByRole("searchbox")).toHaveValue("");
  });

  it("renders real guide content, visual flow and source references with correct metadata", async () => {
    open("/docs/bfsi-business-onboarding");
    expect(screen.getByRole("main")).toHaveTextContent("Example Bank");
    expect(screen.getByRole("list", { name: "Workflow" })).toBeInTheDocument();
    expect(
      screen.getByRole("region", { name: "Implementation references" }),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(document.querySelector('link[rel="canonical"]')).toHaveAttribute(
        "href",
        "https://agenticorg.ai/docs/bfsi-business-onboarding",
      ),
    );
    expect(document.querySelector('meta[name="robots"]')).toHaveAttribute(
      "content",
      "index, follow, max-image-preview:large",
    );
  });

  it("guides an outside A2A buyer without presenting learning progress as live commerce", () => {
    open("/docs/seller-a2a-commerce-journey");
    const main = screen.getByRole("main");
    expect(within(main).getByRole("heading", { level: 1, name: "Seller commerce with an outside A2A buyer" })).toBeInTheDocument();
    expect(within(main).getByRole("list", { name: "Workflow" })).toBeInTheDocument();
    expect(main).toHaveTextContent("Muse, Instinct or Dots");
    expect(main).toHaveTextContent("A2A-Version: 1.0");
    expect(main).toHaveTextContent("cannot create an order, hold stock, collect money or create a Pine Labs Plural mandate");
    expect(main).toHaveTextContent("role can manage merchant configuration, but it cannot perform those admin-only actions");
    const transaction = within(main).getByRole("region", { name: "Third-party buyer transaction map" });
    expect(transaction).toHaveTextContent("Outside buyer");
    fireEvent.change(within(transaction).getByRole("combobox", { name: "Illustrative buyer app" }), {
      target: { value: "Dots-style buyer" },
    });
    expect(transaction).toHaveTextContent("Dots-style buyer");
    fireEvent.click(within(transaction).getByRole("button", { name: "Step 3: Buyer asks to purchase" }));
    expect(transaction).toHaveTextContent("A2A intent is not an order");
    expect(transaction).toHaveTextContent("refuses execution");
    fireEvent.click(within(transaction).getByRole("button", { name: "Step 5: Authorize with the provider" }));
    expect(transaction).toHaveTextContent("not wired into the current external seller A2A route");
    fireEvent.click(within(transaction).getByRole("button", { name: "Step 6: Confirm order and receipt" }));
    expect(transaction).toHaveTextContent("Without both authorities, the result stays pending or blocked");
    const journey = within(main).getByRole("region", { name: "Seller A2A commerce learning journey" });
    expect(within(journey).getByRole("progressbar", { name: "Learning checkpoints reviewed" })).toHaveAttribute("aria-valuenow", "0");
    expect(within(journey).getByRole("button", { name: "Mark reviewed" })).toBeDisabled();
    fireEvent.click(within(journey).getByRole("radio", { name: "Turn on public catalog immediately" }));
    expect(journey).toHaveTextContent("Not yet. Recheck the boundary");
    expect(within(journey).getByRole("button", { name: "Mark reviewed" })).toBeDisabled();
    fireEvent.click(within(journey).getByRole("radio", { name: "Confirm scope and approvals before publishing" }));
    fireEvent.click(within(journey).getByRole("button", { name: "Mark reviewed" }));
    expect(within(journey).getByRole("progressbar", { name: "Learning checkpoints reviewed" })).toHaveAttribute("aria-valuenow", "1");
    expect(journey).toHaveTextContent("not a connection test or launch approval");
    fireEvent.click(within(journey).getByRole("button", { name: "View checkpoint 6: Hand off payment" }));
    expect(journey).toHaveTextContent("does not create a Plural mandate, checkout or payment");
  });

  it("prints the guide and copies only its canonical address", async () => {
    const print = vi.spyOn(window, "print").mockImplementation(() => {});
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    open("/docs/first-agent");
    fireEvent.click(screen.getByRole("button", { name: "Print guide" }));
    expect(print).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole("button", { name: "Copy guide link" }));
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Link copied" }),
      ).toBeInTheDocument(),
    );
    expect(writeText).toHaveBeenCalledWith(
      "https://agenticorg.ai/docs/first-agent",
    );
  });

  it("does not silently present an unknown guide as the home page", async () => {
    open("/docs/not-a-guide");
    expect(
      screen.getByRole("heading", { name: "Guide not found" }),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(document.querySelector('meta[name="robots"]')).toHaveAttribute(
        "content",
        "noindex, nofollow",
      ),
    );
  });

  it("handles malformed hash escapes without throwing", async () => {
    open("/docs/first-agent#%E0%A4%A");
    expect(screen.getByRole("main")).toHaveTextContent(
      "Step 1: prepare the workspace",
    );
    await new Promise((resolve) => setTimeout(resolve, 30));
  });
});
