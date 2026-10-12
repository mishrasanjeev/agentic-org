// SPDX-License-Identifier: Apache-2.0
import { render, screen } from "@testing-library/react";
import { HelmetProvider } from "react-helmet-async";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import Terms from "../pages/legal/Terms";

describe("future-release licensing notice", () => {
  it("records policy publication approval without inventing software adoption", () => {
    render(<HelmetProvider><MemoryRouter><Terms /></MemoryRouter></HelmetProvider>);
    const notice = screen.getByRole("heading", { name: "Prospective licensing policy" }).nextElementSibling;
    expect(notice).toHaveTextContent("Publication approved; software adoption pending");
    expect(notice).toHaveTextContent("personal/internal use would be free");
    expect(notice).toHaveTextContent("agreement with Orchestrum Technologies LLP");
    expect(notice).toHaveTextContent("Current Apache-2.0 rights remain unchanged");
    expect(notice).toHaveTextContent("hosted plans, infrastructure and provider costs are separate");
    expect(screen.getByRole("link", { name: "licensing policy" })).toHaveAttribute("href", "/docs/licensing");
    expect(screen.getAllByRole("link", { name: "sanjeev@orchestrum.in" })).toSatisfy(
      (links: HTMLElement[]) => links.every((link) => link.getAttribute("href") === "mailto:sanjeev@orchestrum.in"),
    );
  });
});
