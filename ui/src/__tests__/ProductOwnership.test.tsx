import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import ProductOwnership from "../components/ProductOwnership";

describe("ProductOwnership", () => {
  it("renders the canonical company, inventor/owner, and both contact links", () => {
    render(<ProductOwnership tone="light" />);

    const disclosure = screen.getByTestId("product-ownership");
    expect(disclosure).toHaveTextContent("AgenticOrg is owned by Orchestrum Technologies LLP.");
    expect(disclosure).toHaveTextContent("Inventor / Owner: Sanjeev Kumar");
    expect(screen.getByRole("link", { name: "sanjeev@orchestrum.in" })).toHaveAttribute(
      "href",
      "mailto:sanjeev@orchestrum.in",
    );
    expect(screen.getByRole("link", { name: "mishra.sanjeev@gmail.com" })).toHaveAttribute(
      "href",
      "mailto:mishra.sanjeev@gmail.com",
    );
  });
});
