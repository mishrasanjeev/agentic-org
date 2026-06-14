import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import CommerceRuntimeDemo from "../pages/CommerceRuntimeDemo";

const apiMock = vi.hoisted(() => ({
  createOnboardingPacket: vi.fn(),
  syncShopify: vi.fn(),
  requestGrantexAuthority: vi.fn(),
  cacheArtifacts: vi.fn(),
  askBuyerQuestion: vi.fn(),
  listProducts: vi.fn(),
  verifyPluralPineCapability: vi.fn(),
}));

vi.mock("../lib/api", () => ({
  commerceRuntimeApi: apiMock,
  extractApiError: () => "blocked by missing environment",
}));

describe("CommerceRuntimeDemo", () => {
  it("creates an onboarding packet and answers from cached artifact labels", async () => {
    apiMock.createOnboardingPacket.mockResolvedValueOnce({
      data: { packet: { packet_id: "packet_1" } },
    });
    apiMock.listProducts.mockResolvedValue({
      data: {
        products: [
          {
            product_ref: "product_ref_1",
            title: "Canvas Tote",
            vendor: "Demo Brand",
            product_type: "Bags",
          },
        ],
      },
    });
    apiMock.askBuyerQuestion.mockResolvedValueOnce({
      data: {
        status: "answered",
        answer: "Canvas Tote: price snapshot 1299.00 INR; inventory snapshot 7.",
        source_label: "Source: Shopify via Grantex artifact",
        freshness_label: "Freshness: synced 1m ago",
        matched_products: [],
      },
    });

    render(<CommerceRuntimeDemo />);
    fireEvent.click(screen.getByRole("button", { name: /create/i }));
    await waitFor(() => expect(apiMock.createOnboardingPacket).toHaveBeenCalled());
    expect(await screen.findByText("packet_1")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /ask/i }));
    await waitFor(() => expect(apiMock.askBuyerQuestion).toHaveBeenCalledWith(expect.objectContaining({
      buyer_agent_id: "buyer_agent_demo",
    })));
    expect(await screen.findByText(/Source: Shopify via Grantex artifact/i)).toBeInTheDocument();
    expect(screen.getByText(/inventory snapshot/i)).toBeInTheDocument();
  });

  it("caches Grantex artifacts with the same buyer scope used by buyer questions", async () => {
    apiMock.createOnboardingPacket.mockResolvedValueOnce({
      data: { packet: { packet_id: "packet_3" } },
    });
    apiMock.syncShopify.mockResolvedValueOnce({
      data: { evidence_id: "evidence_1", product_count: 1, variant_count: 1 },
    });
    apiMock.requestGrantexAuthority.mockResolvedValueOnce({
      data: {
        status: "artifact_issuance_ready",
        artifacts: [{ artifact_family: "catalog_snapshot" }],
      },
    });
    apiMock.cacheArtifacts.mockResolvedValueOnce({
      data: { status: "cached", records_stored: 1 },
    });
    apiMock.listProducts.mockResolvedValue({ data: { products: [] } });

    render(<CommerceRuntimeDemo />);
    fireEvent.click(screen.getByRole("button", { name: /create/i }));
    await screen.findByText("packet_3");
    fireEvent.click(screen.getByRole("button", { name: /sync/i }));
    await screen.findByText(/1 products, 1 variants/i);
    fireEvent.click(screen.getByRole("button", { name: /issue/i }));

    await waitFor(() => expect(apiMock.cacheArtifacts).toHaveBeenCalledWith({
      artifacts: [{ artifact_family: "catalog_snapshot" }],
      buyer_agent_id: "buyer_agent_demo",
    }));
  });

  it("surfaces blocked Shopify sync without exposing credentials", async () => {
    apiMock.createOnboardingPacket.mockResolvedValueOnce({
      data: { packet: { packet_id: "packet_2" } },
    });
    apiMock.syncShopify.mockRejectedValueOnce(new Error("missing env"));

    render(<CommerceRuntimeDemo />);
    fireEvent.click(screen.getByRole("button", { name: /create/i }));
    await screen.findByText("packet_2");
    fireEvent.click(screen.getByRole("button", { name: /sync/i }));

    expect(await screen.findByText("Shopify sync blocked")).toBeInTheDocument();
    expect(screen.queryByText(/access_token|client_secret|password/i)).not.toBeInTheDocument();
  });
});
