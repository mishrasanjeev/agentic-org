import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockGet = vi.fn();
const mockPost = vi.fn();

vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
    interceptors: {
      request: { use: vi.fn() },
      response: { use: vi.fn() },
    },
  },
  extractApiError: (_err: unknown, fallback: string) => fallback,
}));

import CMOVendorSandboxConnectors from "@/pages/CMOVendorSandboxConnectors";

function renderPage() {
  render(
    <MemoryRouter>
      <CMOVendorSandboxConnectors />
    </MemoryRouter>,
  );
}

describe("CMOVendorSandboxConnectors", () => {
  beforeEach(() => {
    mockGet.mockReset();
    mockPost.mockReset();
    mockGet.mockResolvedValue({
      data: {
        categories: [],
        missing_categories: ["CRM", "Ads", "Analytics", "Email"],
      },
    });
    mockPost.mockResolvedValue({
      data: {
        status: "ready",
        message: "CMO vendor-sandbox ConnectorConfig rows configured.",
      },
    });
  });

  it("renders the guided CMO vendor-sandbox setup flow", async () => {
    renderPage();

    expect(await screen.findByText("CMO Vendor Sandbox Connectors")).toBeInTheDocument();
    expect(screen.getByText("Current DB Preflight Rows")).toBeInTheDocument();
    expect(screen.getByLabelText("CRM Access Token")).toBeInTheDocument();
    expect(screen.getByLabelText("Ads Developer Token")).toBeInTheDocument();
    expect(screen.getByLabelText("Analytics Property ID")).toBeInTheDocument();
    expect(screen.getByLabelText("Email API Key")).toBeInTheDocument();
  });

  it("submits all four categories to the CMO vendor-sandbox endpoint", async () => {
    renderPage();

    await screen.findByText("CMO Vendor Sandbox Connectors");

    fireEvent.change(screen.getByLabelText("CRM Access Token"), { target: { value: "hubspot-token" } });
    fireEvent.change(screen.getByLabelText("CRM Account ID optional"), { target: { value: "246292667" } });

    fireEvent.change(screen.getByLabelText("Ads Developer Token"), { target: { value: "ads-dev-token" } });
    fireEvent.change(screen.getByLabelText("Ads Refresh Token"), { target: { value: "ads-refresh-token" } });
    fireEvent.change(screen.getByLabelText("Ads Customer ID"), { target: { value: "1234567890" } });
    fireEvent.change(screen.getByLabelText("Ads Client ID"), { target: { value: "ads-client-id" } });
    fireEvent.change(screen.getByLabelText("Ads Client Secret"), { target: { value: "ads-client-secret" } });

    fireEvent.change(screen.getByLabelText("Analytics Property ID"), { target: { value: "properties/123" } });
    fireEvent.change(screen.getByLabelText("Analytics Refresh Token"), { target: { value: "ga4-refresh-token" } });
    fireEvent.change(screen.getByLabelText("Analytics Client ID"), { target: { value: "ga4-client-id" } });
    fireEvent.change(screen.getByLabelText("Analytics Client Secret"), { target: { value: "ga4-client-secret" } });

    fireEvent.change(screen.getByLabelText("Email API Key"), { target: { value: "sendgrid-key" } });
    fireEvent.change(screen.getByLabelText("Email Sender Identity"), { target: { value: "sender@agenticorg.ai" } });

    fireEvent.click(screen.getByRole("button", { name: /Save CMO Sandbox Connectors/i }));

    await waitFor(() => expect(mockPost).toHaveBeenCalledTimes(1));
    expect(mockPost).toHaveBeenCalledWith(
      "/connectors/cmo-vendor-sandbox",
      expect.objectContaining({
        connectors: expect.objectContaining({
          CRM: expect.objectContaining({
            connector_name: "hubspot",
            credentials: expect.objectContaining({ access_token: "hubspot-token" }),
            config: expect.objectContaining({ account_id: "246292667" }),
          }),
          Ads: expect.objectContaining({
            connector_name: "google_ads",
            credentials: expect.objectContaining({ customer_id: "1234567890" }),
          }),
          Analytics: expect.objectContaining({
            connector_name: "ga4",
            credentials: expect.objectContaining({ property_id: "properties/123" }),
          }),
          Email: expect.objectContaining({
            connector_name: "sendgrid",
            credentials: expect.objectContaining({ sender_identity: "sender@agenticorg.ai" }),
          }),
        }),
      }),
    );
  });
});
