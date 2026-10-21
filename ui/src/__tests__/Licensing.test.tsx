// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
import { render, screen } from "@testing-library/react";
import { HelmetProvider } from "react-helmet-async";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import ProductOwnership from "../components/ProductOwnership";
import Terms from "../pages/legal/Terms";

describe("Orchestrum Personal Use licence notice", () => {
  it("states the licence, the free self use and the written-agreement requirement", () => {
    render(<HelmetProvider><MemoryRouter><Terms /></MemoryRouter></HelmetProvider>);
    const notice = screen.getByRole("heading", { name: "Software licence" }).nextElementSibling;
    expect(notice).toHaveTextContent("source-available under the Orchestrum Personal Use License 1.0");
    expect(notice).toHaveTextContent("Only an individual using it for their own personal purposes is free");
    expect(notice).toHaveTextContent(
      "Any organisation needs prior written permission in a signed agreement with Orchestrum Technologies LLP",
    );
    expect(notice).toHaveTextContent("charities, schools, universities, research bodies and government");
    expect(notice).toHaveTextContent("evaluation, testing, pilots");
    expect(screen.getByText(/SDKs \(Python and TypeScript\) and the MCP server/)).toHaveTextContent(
      "open source under the Apache License 2.0, free for any use including commercial",
    );
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
    expect(change).toHaveTextContent("Existing and new users alike need a signed agreement");
    expect(change.textContent ?? "").not.toMatch(/revok|withdraw/i);
    expect(screen.getByText(/are trademarks of Orchestrum/)).toHaveTextContent(
      "needs prior written approval with any version",
    );
  });

  it("puts the licence in every public footer through the ownership disclosure", () => {
    render(<ProductOwnership />);
    const licence = screen.getByTestId("product-licence");
    expect(licence).toHaveTextContent("Licence: platform Orchestrum Personal Use 1.0");
    expect(licence).toHaveTextContent("Only individuals' personal use is free; any organisation needs a signed agreement");
    expect(licence).toHaveTextContent("SDKs and MCP server: Apache-2.0, free for any use.");
    expect(screen.getByRole("link", { name: "Licensing and commercial use" })).toHaveAttribute(
      "href",
      "/docs/licensing",
    );
  });
});
