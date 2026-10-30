// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
import { render, screen, within } from "@testing-library/react";
import { HelmetProvider } from "react-helmet-async";
import { MemoryRouter, Route, Routes } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import Documentation, { guideParts } from "@/pages/Documentation";
import DocsFigure from "@/components/docs/OacpGuideFigures";
import manual from "@/content/userDocs.generated.json";

type GuideFigure = { id: string; title: string; caption: string };

function openDocs(path: string) {
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

const oacpGuides = manual.articles.filter((article) => article.slug.startsWith("oacp-"));

beforeEach(() => {
  vi.spyOn(window, "scrollTo").mockImplementation(() => {});
  Element.prototype.scrollIntoView = vi.fn();
});

describe("OACP user guides", () => {
  it("has a diagram for every figure a guide names, each with a title and description", () => {
    const figures = oacpGuides.flatMap((guide) => guide.figures as GuideFigure[]);
    expect(figures.length).toBeGreaterThanOrEqual(14);
    for (const figure of figures) {
      const { container, unmount } = render(<DocsFigure {...figure} />);
      const svgs = container.querySelectorAll("svg[role='img']");
      expect(svgs.length, figure.id).toBeGreaterThan(0);
      for (const svg of svgs) {
        expect(svg.querySelector("title")?.textContent, figure.id).toBeTruthy();
        expect(svg.querySelector("desc")?.textContent, figure.id).toBeTruthy();
      }
      unmount();
    }
  });

  it("renders each figure in place under its own heading, on an adaptive page", () => {
    openDocs("/docs/oacp-how-it-works");
    const main = screen.getByRole("main");
    expect(main.closest(".docs-site")).toHaveClass("docs-adaptive");
    expect(within(main).getByRole("complementary", { name: "In 30 seconds" })).toBeInTheDocument();
    const article = main.querySelector("article.docs-prose")!;
    const order = [...article.querySelectorAll("h2, figure")].map((node) =>
      node.tagName === "FIGURE" ? `figure:${node.getAttribute("data-docs-figure")}` : node.textContent,
    );
    expect(order.indexOf("figure:oacp-docs-flow")).toBe(order.indexOf("The whole flow on one page") + 1);
    expect(order.indexOf("figure:oacp-verify-gate")).toBe(
      order.indexOf("Step 3. AgenticOrg checks before it keeps anything") + 1,
    );
    const figure = within(main).getByRole("figure", { name: "Four checks before a fact is kept" });
    expect(within(figure).getByRole("img", { name: /Four checks before a fact is kept/ })).toBeInTheDocument();
    expect(within(main).getAllByRole("list", { name: "Process map" }).length).toBeGreaterThan(0);
  });

  it("numbers the merchant checklist across its phases", () => {
    openDocs("/docs/oacp-merchant-setup");
    const maps = screen.getAllByRole("list", { name: "Process map" });
    expect(maps.map((map) => map.getAttribute("start"))).toEqual([null, "5", "9", "15", "20"]);
    expect(maps[4]).toHaveTextContent("Sandbox credentials");
    expect(screen.getByRole("figure", { name: "The DNS TXT step" })).toHaveTextContent(
      "_agenticorg-ucp.shop.merchant.example",
    );
  });

  it("keeps every other guide on the light scheme with its original single article body", () => {
    openDocs("/docs/commerce");
    const main = screen.getByRole("main");
    expect(main.closest(".docs-site")).not.toHaveClass("docs-adaptive");
    expect(main.querySelector(".docs-prose-part")).toBeNull();
  });

  it("lists the OACP group on the documentation home with an anchor", () => {
    openDocs("/docs");
    expect(document.getElementById("oacp")).toHaveTextContent("OACP — agentic commerce, made safe");
    expect(screen.getByRole("link", { name: "OACP: sell through AI assistants" })).toHaveAttribute(
      "href",
      "/docs/oacp-start-here",
    );
  });

  it("splits HTML only at known figure placeholders", () => {
    const slot = (id: string) => `<figure class="docs-figure" data-docs-figure="${id}" aria-labelledby="x"><figcaption>c</figcaption></figure>`;
    const parts = guideParts(`<p>a</p>${slot("known")}<p>b</p>${slot("unknown")}`, [
      { id: "known", title: "T", caption: "C" },
    ]);
    expect(parts).toEqual([
      { html: "<p>a</p>" },
      { figure: { id: "known", title: "T", caption: "C" } },
      { html: `<p>b</p>${slot("unknown")}` },
    ]);
  });
});
