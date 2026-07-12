import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { BrandingProvider, useBranding } from "../contexts/BrandingContext";

function BrandingProbe() {
  const branding = useBranding();
  return (
    <div>
      <span data-testid="product-name">{branding.productName}</span>
      <span data-testid="support-email">{branding.supportEmail}</span>
    </div>
  );
}

describe("BrandingProvider", () => {
  beforeEach(() => {
    document.title = "Enterprise Work, Governed by Design. | AgenticOrg";
    document.documentElement.style.removeProperty("--brand-primary");
    document.documentElement.style.removeProperty("--brand-accent");
    document.head.querySelectorAll("link[rel='icon']").forEach((node) => node.remove());
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        product_name: "Acme Operations",
        logo_url: "https://assets.example.com/logo.svg",
        favicon_url: "https://assets.example.com/favicon.png",
        primary_color: "#123456",
        accent_color: "#abcdef",
        support_email: "support@example.com",
        footer_text: "Acme footer",
      }),
    } as Response));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    document.documentElement.style.removeProperty("--brand-primary");
    document.documentElement.style.removeProperty("--brand-accent");
    document.head.querySelectorAll("link[rel='icon']").forEach((node) => node.remove());
  });

  it("hydrates branding visuals and context without overwriting the route SEO title", async () => {
    render(
      <BrandingProvider>
        <BrandingProbe />
      </BrandingProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("product-name")).toHaveTextContent("Acme Operations");
    });

    expect(fetch).toHaveBeenCalledWith("/api/v1/branding");
    expect(screen.getByTestId("support-email")).toHaveTextContent("support@example.com");
    expect(document.documentElement.style.getPropertyValue("--brand-primary")).toBe("#123456");
    expect(document.documentElement.style.getPropertyValue("--brand-accent")).toBe("#abcdef");
    expect(document.head.querySelector("link[rel='icon']")).toHaveAttribute(
      "href",
      "https://assets.example.com/favicon.png",
    );
    expect(document.title).toBe("Enterprise Work, Governed by Design. | AgenticOrg");
  });
});
