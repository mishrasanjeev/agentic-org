import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import CommerceRuntimeDemo from "../pages/CommerceRuntimeDemo";

const apiMock = vi.hoisted(() => ({
  createOnboardingPacket: vi.fn(),
  upsertShopifyCredentials: vi.fn(),
  getShopifyStatus: vi.fn(),
  syncShopify: vi.fn(),
  requestGrantexAuthority: vi.fn(),
  cacheArtifacts: vi.fn(),
  askBuyerQuestion: vi.fn(),
  getBridgeSurfaces: vi.fn(),
  getProtocolAdapters: vi.fn(),
  preparePurchase: vi.fn(),
  getOfflinePosReadiness: vi.fn(),
  createOfflinePosHandoff: vi.fn(),
  simulateOfflinePosConfirmation: vi.fn(),
  listProducts: vi.fn(),
  verifyPluralPineCapability: vi.fn(),
}));

vi.mock("../lib/api", () => ({
  commerceRuntimeApi: apiMock,
  extractApiError: () => "blocked by missing environment",
}));

describe("CommerceRuntimeDemo", () => {
  it("stores Shopify connector credentials through the runtime API without rendering secrets", async () => {
    apiMock.upsertShopifyCredentials.mockResolvedValueOnce({
      data: {
        status: "shopify_connector_configured",
        shop_domain: "mgx0n6-22.myshopify.com",
        credential_values_redacted: true,
      },
    });

    render(<CommerceRuntimeDemo />);
    fireEvent.change(screen.getByLabelText("Shopify Admin API token"), {
      target: { value: "fixture-admin-token" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() => expect(apiMock.upsertShopifyCredentials).toHaveBeenCalledWith(expect.objectContaining({
      admin_access_token: "fixture-admin-token",
      merchant_id: "merchant_demo",
      shop_domain: "mgx0n6-22.myshopify.com",
    })));
    await waitFor(() => expect(screen.getAllByText("shopify_connector_configured").length).toBeGreaterThan(0));
    expect(screen.queryByText("fixture-admin-token")).not.toBeInTheDocument();
  });

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

  it("shows bridge readiness, protocol adapters, and safe purchase-prep blockers", async () => {
    apiMock.getBridgeSurfaces.mockResolvedValueOnce({
      data: {
        surfaces: [
          { surface: "web", status: "bridge_ready" },
          { surface: "telegram", status: "config_missing" },
        ],
      },
    });
    apiMock.getProtocolAdapters.mockResolvedValueOnce({
      data: {
        status: "adapter_payloads_ready",
        source_label: "Source: Shopify via Grantex artifact",
        freshness_label: "Freshness: synced 1m ago",
      },
    });
    apiMock.listProducts.mockResolvedValueOnce({
      data: {
        products: [
          {
            product_ref: "shopify:product:1",
            title: "Canvas Tote",
          },
        ],
      },
    });
    apiMock.preparePurchase.mockResolvedValueOnce({
      data: {
        status: "blocked",
        source_label: "Source: Shopify via Grantex artifact",
        freshness_label: "Freshness: synced 1m ago",
        blocker: {
          code: "plural_pine_capability_missing_or_stale",
          action: "Configure PLURAL_PINE_CLIENT_ID and rerun capability verification.",
        },
      },
    });

    render(<CommerceRuntimeDemo />);
    fireEvent.click(screen.getByRole("button", { name: /channels/i }));
    expect(await screen.findByText("1 channel configs missing")).toBeInTheDocument();
    expect(screen.getByText("1/2 ready")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /adapters/i }));
    await waitFor(() => expect(apiMock.getProtocolAdapters).toHaveBeenCalledWith(expect.objectContaining({
      buyer_agent_id: "buyer_agent_demo",
      merchant_id: "merchant_demo",
      seller_agent_id: "seller_agent_demo",
    })));
    expect((await screen.findAllByText("adapter_payloads_ready")).length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole("button", { name: /products/i }));
    expect(await screen.findByText("Canvas Tote")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /prepare/i }));
    await waitFor(() => expect(apiMock.preparePurchase).toHaveBeenCalledWith(expect.objectContaining({
      live_execution_approved: false,
      product_ref_or_query: "shopify:product:1",
    })));
    expect(await screen.findByText(/plural_pine_capability_missing_or_stale/i)).toBeInTheDocument();
    expect(screen.queryByText(/capture payment|paid successfully|order created/i)).not.toBeInTheDocument();
  });

  it("creates an Offline POS handoff and simulator confirmation without fake paid-state claims", async () => {
    apiMock.getOfflinePosReadiness.mockResolvedValueOnce({
      data: {
        status: "offline_pos_bridge_foundation_ready",
        simulator: { status: "ready" },
        real_pos_provider: { status: "blocked_missing_credential" },
      },
    });
    apiMock.listProducts.mockResolvedValueOnce({
      data: {
        products: [
          {
            product_ref: "shopify:product:1",
            title: "Canvas Tote",
          },
        ],
      },
    });
    apiMock.createOfflinePosHandoff.mockResolvedValueOnce({
      data: {
        status: "pos_handoff_packet_ready",
        pos_handoff_created: true,
        packet: {
          packet_id: "offline_pos_handoff_1",
        },
      },
    });
    apiMock.simulateOfflinePosConfirmation.mockResolvedValueOnce({
      data: {
        status: "pos_simulator_reconciled",
        reconciliation: {
          buyer_safe_status: "POS accepted the handoff. Staff must confirm final price and payment at the store.",
          seller_operator_status: "handoff_accepted_waiting_for_pos_staff_payment_step",
        },
      },
    });

    render(<CommerceRuntimeDemo />);
    fireEvent.click(screen.getByRole("button", { name: /^pos$/i }));
    expect(await screen.findByText("ready")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /^products$/i }));
    expect(await screen.findByText("Canvas Tote")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /pos handoff/i }));
    await waitFor(() => expect(apiMock.createOfflinePosHandoff).toHaveBeenCalledWith(expect.objectContaining({
      buyer_session_ref: "buyer_session_demo",
      store_id: "store_demo",
      product_ref_or_query: "shopify:product:1",
    })));
    expect(await screen.findByText("offline_pos_handoff_1")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /pos confirm/i }));
    await waitFor(() => expect(apiMock.simulateOfflinePosConfirmation).toHaveBeenCalledWith({
      packet_id: "offline_pos_handoff_1",
      confirmation_status: "accepted",
    }));
    expect(await screen.findByText(/Staff must confirm final price and payment/i)).toBeInTheDocument();
    expect(screen.queryByText(/paid successfully|order created|capture payment/i)).not.toBeInTheDocument();
  });
});
