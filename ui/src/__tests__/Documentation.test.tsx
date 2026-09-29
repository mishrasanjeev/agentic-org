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
    ).toHaveLength(29);
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
