// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const get = vi.fn();
const setMode = vi.fn();
const saveCredentials = vi.fn();
const testCredentials = vi.fn();

vi.mock("@/lib/api", () => ({ default: {} }));
vi.mock("../lib/api", () => ({
  paymentsApi: {
    get: (...a: unknown[]) => get(...a),
    setMode: (...a: unknown[]) => setMode(...a),
    saveCredentials: (...a: unknown[]) => saveCredentials(...a),
    testCredentials: (...a: unknown[]) => testCredentials(...a),
  },
  extractApiError: (_e: unknown, fallback: string) => fallback,
}));

import PaymentSettings from "@/components/commerce/PaymentSettings";

const view = (overrides: Record<string, unknown> = {}) => ({
  merchant_id: "tea-corner",
  mode: "off",
  shopify: { shop_domain: "tea-corner.myshopify.com", ready: true },
  plural: { configured: false },
  links_available: true,
  ...overrides,
});

describe("PaymentSettings", () => {
  beforeEach(() => {
    for (const fn of [get, setMode, saveCredentials, testCredentials]) fn.mockReset();
  });

  it("saves the Shopify checkout mode for the merchant", async () => {
    get.mockResolvedValue({ data: view() });
    setMode.mockResolvedValue({ data: view({ mode: "shopify_cart" }) });
    render(<PaymentSettings merchantId="tea-corner" />);
    expect(await screen.findByText("Connected: tea-corner.myshopify.com")).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText(/My Shopify checkout/));
    fireEvent.click(screen.getByRole("button", { name: "Save payment method" }));
    await waitFor(() => expect(setMode).toHaveBeenCalledWith("tea-corner", "shopify_cart"));
    expect(await screen.findByRole("status")).toHaveTextContent("Payment settings saved.");
  });

  it("saves Plural credentials without echoing secrets, then tests them", async () => {
    get.mockResolvedValue({ data: view() });
    const saved = view({
      plural: { configured: true, environment: "sandbox", webhook_secret_set: true, webhook_url: "https://api.example/api/v1/payments/plural/webhooks/t/k" },
    });
    saveCredentials.mockResolvedValue({ data: saved });
    render(<PaymentSettings merchantId="tea-corner" />);
    await screen.findByText("Not saved");
    const save = screen.getByRole("button", { name: "Save Plural credentials" });
    expect(save).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Client ID"), { target: { value: "client-example" } });
    fireEvent.change(screen.getByLabelText("Client secret"), { target: { value: "secret-example" } });
    fireEvent.change(screen.getByLabelText("Webhook secret"), { target: { value: "hook-example" } });
    fireEvent.click(save);
    await waitFor(() =>
      expect(saveCredentials).toHaveBeenCalledWith("tea-corner", {
        environment: "sandbox",
        client_id: "client-example",
        client_secret: "secret-example",
        webhook_secret: "hook-example",
      }),
    );
    expect(await screen.findByText(/stored encrypted/)).toBeInTheDocument();
    expect((screen.getByLabelText("Client secret") as HTMLInputElement).value).toBe("");
    expect(screen.getByText("https://api.example/api/v1/payments/plural/webhooks/t/k")).toBeInTheDocument();

    testCredentials.mockResolvedValue({ data: { ok: true, code: "token_issued" } });
    get.mockResolvedValue({ data: { ...saved, plural: { ...saved.plural, last_test: { ok: true, code: "token_issued", at: "x" } } } });
    fireEvent.click(screen.getByRole("button", { name: "Test" }));
    expect(await screen.findByText("Plural accepted the credentials.")).toBeInTheDocument();
    expect(await screen.findByText("Test passed")).toBeInTheDocument();
  });

  it("shows why a mode cannot be saved", async () => {
    get.mockResolvedValue({ data: view({ shopify: { shop_domain: null, ready: false } }) });
    setMode.mockRejectedValue({ response: { data: { detail: { code: "plural_credentials_missing", message: "Save your Plural credentials first." } } } });
    render(<PaymentSettings merchantId="tea-corner" />);
    expect(await screen.findByText("Connect your Shopify store first")).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText(/My Plural account/));
    fireEvent.click(screen.getByRole("button", { name: "Save payment method" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Save your Plural credentials first.");
  });
});
