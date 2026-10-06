// SPDX-License-Identifier: Apache-2.0
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createMemoryRouter, MemoryRouter, Route, RouterProvider, Routes } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockGet = vi.fn();
const mockPost = vi.fn();
const mockNavigate = vi.fn();

vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => ({ user: { role: "admin", tenant_id: "tenant-1" } }),
}));
vi.mock("react-router", async () => {
  const actual = await vi.importActual<typeof import("react-router")>("react-router");
  return { ...actual, useNavigate: () => mockNavigate };
});
vi.mock("@/lib/api", () => ({
  default: { get: (...args: unknown[]) => mockGet(...args), post: (...args: unknown[]) => mockPost(...args) },
  extractApiError: (_err: unknown, fallback: string) => fallback,
}));

import ConnectorCreate from "@/pages/ConnectorCreate";

const registry = {
  items: [
    { name: "whatsapp", display_name: "WhatsApp", category: "comms", auth_type: "meta_business", base_url: "https://graph.facebook.com" },
    { name: "twilio", display_name: "Twilio", category: "comms", auth_type: "api_key_secret" },
    { name: "gmail", display_name: "Gmail", category: "comms", auth_type: "oauth2" },
    { name: "pinelabs_plural", display_name: "Pine Labs Plural", category: "finance", auth_type: "oauth2" },
  ],
};

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes><Route path="/dashboard/connectors/new" element={<ConnectorCreate />} /></Routes>
    </MemoryRouter>,
  );
}

describe("native connector registration", () => {
  beforeEach(() => {
    vi.stubEnv("VITE_NATIVE_CONNECTOR_PREFILL_ENABLED", "true");
    mockGet.mockReset();
    mockPost.mockReset();
    mockNavigate.mockReset();
    mockGet.mockResolvedValue({ data: registry });
    mockPost.mockResolvedValue({ data: { connector_id: "new-id" } });
  });

  it.each([
    ["whatsapp", "comms", "https://graph.facebook.com", "meta_business", "Enter access token", "access_token"],
    ["twilio", "comms", "", "api_key_secret", "Enter auth token", "auth_token"],
    ["gmail", "comms", "", "oauth2", "Enter refresh token", "refresh_token"],
    ["pinelabs_plural", "finance", "", "oauth2", "Enter merchant ID", "merchant_id"],
  ])("registers %s using the registry identity and expected credential key", async (name, category, baseUrl, authType, placeholder, authKey) => {
    renderAt(`/dashboard/connectors/new?type=${name}`);
    const nameField = await screen.findByDisplayValue(name) as HTMLInputElement;
    expect(nameField).toHaveAttribute("readonly");
    expect(screen.getByTestId("provider-select")).toHaveValue(name);
    expect(screen.getByLabelText("Category")).toBeDisabled();
    expect(screen.getByLabelText("Auth Type")).toBeDisabled();
    expect(screen.queryByText("Zoho Books regions are inferred from this URL.")).not.toBeInTheDocument();
    expect(screen.getByPlaceholderText("{}")).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText(placeholder), { target: { value: "synthetic-test-value" } });
    fireEvent.click(screen.getByRole("button", { name: "Register Connector" }));
    await waitFor(() => expect(mockPost).toHaveBeenCalledWith("/connectors", expect.objectContaining({
      name, category, base_url: baseUrl || undefined, auth_type: authType,
      auth_config: expect.objectContaining({ [authKey]: "synthetic-test-value" }),
    })));
  });

  it("refuses a stale or invented native type", async () => {
    renderAt("/dashboard/connectors/new?type=unknown_provider");
    expect(await screen.findByRole("alert")).toHaveTextContent("not in the current registry");
    expect(screen.getByRole("button", { name: "Register Connector" })).toBeDisabled();
    expect(mockPost).not.toHaveBeenCalled();
  });

  it("refuses registration when the native registry cannot be loaded", async () => {
    mockGet.mockRejectedValue(new Error("registry unavailable"));
    renderAt("/dashboard/connectors/new?type=whatsapp");
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load the native connector registry");
    expect(screen.getByRole("button", { name: "Register Connector" })).toBeDisabled();
    expect(mockPost).not.toHaveBeenCalled();
  });

  it("preserves the existing generic form when the rollout flag is off", () => {
    vi.stubEnv("VITE_NATIVE_CONNECTOR_PREFILL_ENABLED", "false");
    renderAt("/dashboard/connectors/new?type=whatsapp");
    expect(screen.getByTestId("provider-select")).toHaveValue("custom");
    expect(mockGet).not.toHaveBeenCalled();
  });

  it("clears credentials and extra config when switching native providers", async () => {
    const router = createMemoryRouter(
      [{ path: "/dashboard/connectors/new", element: <ConnectorCreate /> }],
      { initialEntries: ["/dashboard/connectors/new?type=whatsapp"] },
    );
    render(<RouterProvider router={router} />);
    await screen.findByDisplayValue("whatsapp");
    fireEvent.change(screen.getByPlaceholderText("Enter access token"), { target: { value: "first-provider-secret" } });
    fireEvent.change(screen.getByPlaceholderText("{}"), { target: { value: '{"first":"provider"}' } });
    fireEvent.change(screen.getByPlaceholderText("e.g. gcp://projects/my-project/secrets/my-secret/versions/latest"), {
      target: { value: "first-provider-secret-ref" },
    });

    await router.navigate("/dashboard/connectors/new?type=twilio");
    await screen.findByDisplayValue("twilio");
    expect(screen.getByPlaceholderText("Enter auth token")).toHaveValue("");
    expect(screen.getByPlaceholderText("{}")).toHaveValue("");
    expect(screen.getByPlaceholderText("e.g. gcp://projects/my-project/secrets/my-secret/versions/latest")).toHaveValue("");
  });
});
