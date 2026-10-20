// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
import { render, screen } from "@testing-library/react";
import { HelmetProvider } from "react-helmet-async";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import ProductOwnership from "../components/ProductOwnership";
import Terms from "../pages/legal/Terms";

describe("PolyForm Noncommercial licence notice", () => {
  it("states the licence, the free self use and the written-agreement requirement", () => {
    render(<HelmetProvider><MemoryRouter><Terms /></MemoryRouter></HelmetProvider>);
    const notice = screen.getByRole("heading", { name: "Software licence" }).nextElementSibling;
    expect(notice).toHaveTextContent("source-available under the PolyForm Noncommercial License 1.0.0");
    expect(notice).toHaveTextContent("Only self use is free");
    expect(notice).toHaveTextContent(
      "Commercial use of any sort needs prior approval and a written agreement with Orchestrum Technologies LLP",
    );
    expect(notice).toHaveTextContent("internal business use");
    expect(screen.getByRole("link", { name: "licensing and commercial use" })).toHaveAttribute(
      "href",
      "/docs/licensing",
    );
    expect(screen.getAllByRole("link", { name: "sanjeev@orchestrum.in" })).toSatisfy(
      (links: HTMLElement[]) => links.every((link) => link.getAttribute("href") === "mailto:sanjeev@orchestrum.in"),
    );
  });

  it("covers existing users and earlier versions without claiming to revoke the earlier grant", () => {
    render(<HelmetProvider><MemoryRouter><Terms /></MemoryRouter></HelmetProvider>);
    const change = screen.getByText(/applies to every version published on or after 21 October 2026/);
    expect(change).toHaveTextContent("including all later updates");
    expect(change).toHaveTextContent(
      "Versions published before that date remain available under the Apache License 2.0 they were released with",
    );
    expect(change).toHaveTextContent("Existing and new users alike need a written agreement");
    expect(change.textContent ?? "").not.toMatch(/revok|withdraw/i);
    expect(screen.getByText(/are trademarks of Orchestrum/)).toHaveTextContent(
      "needs prior written approval with any version",
    );
  });

  it("puts the licence in every public footer through the ownership disclosure", () => {
    render(<ProductOwnership />);
    const licence = screen.getByTestId("product-licence");
    expect(licence).toHaveTextContent("Licence: PolyForm Noncommercial 1.0.0");
    expect(licence).toHaveTextContent("Self use is free; commercial use needs a written agreement");
    expect(screen.getByRole("link", { name: "Licensing and commercial use" })).toHaveAttribute(
      "href",
      "/docs/licensing",
    );
  });
});
