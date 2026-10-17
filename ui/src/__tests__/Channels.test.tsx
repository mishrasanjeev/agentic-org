// SPDX-License-Identifier: Apache-2.0
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

const get = vi.fn();
const post = vi.fn();
const patch = vi.fn();
let role = "admin";
const binding = { id: "binding-1", name: "My inbox", provider: "signed_webhook", target_kind: "agent", target_id: "agent-1", enabled: false, auto_reply: false, callback_url: "https://example.test/callback" };

vi.mock("@/contexts/AuthContext", () => ({ useAuth: () => ({ user: { role } }) }));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/lib/api")>(),
  default: { get: (...args: unknown[]) => get(...args), post: (...args: unknown[]) => post(...args), patch: (...args: unknown[]) => patch(...args) },
}));
import Channels from "@/pages/Channels";

function page() { return render(<MemoryRouter><Channels /></MemoryRouter>); }

describe("BYO channel setup", () => {
  beforeEach(() => {
    role = "admin";
    get.mockReset(); post.mockReset(); patch.mockReset();
    get.mockImplementation((url: string) => {
      if (url === "/channels/catalog") return Promise.resolve({ data: { providers: [{ id: "signed_webhook", label: "Signed gateway", fields: ["webhook_secret"], guide: "/docs/channel-workflows", reply: false, identity_link: true }] } });
      if (url === "/channels") return Promise.resolve({ data: { bindings: [] } });
      if (url.endsWith("/deliveries")) return Promise.resolve({ data: { deliveries: [] } });
      return Promise.resolve({ data: { items: [{ id: "agent-1", name: "Request reviewer" }] } });
    });
  });

  it("saves only selected destination and protected fields, clears credentials after save", async () => {
    page();
    await screen.findByText("No channel connections");
    fireEvent.click(screen.getByRole("button", { name: "Connect channel" }));
    fireEvent.change(screen.getByLabelText("Channel", { selector: "select" }), { target: { value: "signed_webhook" } });
    fireEvent.change(screen.getByLabelText("Connection name"), { target: { value: "My inbox" } });
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    fireEvent.change(screen.getByLabelText("Agent", { selector: "select" }), { target: { value: "agent-1" } });
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    const secretInput = screen.getByLabelText("Webhook Secret");
    expect(secretInput).toHaveAttribute("type", "password");
    fireEvent.change(secretInput, { target: { value: "synthetic-secret-for-local-tests" } });
    post.mockResolvedValue({ data: binding });
    fireEvent.click(screen.getByRole("button", { name: "Save connection" }));
    await waitFor(() => expect(post).toHaveBeenCalledWith("/channels", {
      name: "My inbox", provider: "signed_webhook", target_kind: "agent", target_id: "agent-1",
      credentials: { webhook_secret: "synthetic-secret-for-local-tests" }, auto_reply: false,
    }));
    await screen.findByRole("button", { name: "Replace credentials" });
    expect(screen.queryByDisplayValue("synthetic-secret-for-local-tests")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Link my messaging account" })).not.toBeInTheDocument();
    expect(window.localStorage.getItem("webhook_secret")).toBeNull();
  });

  it("does not offer admin-only workflows or AI credential links to a developer", async () => {
    role = "developer";
    page();
    await screen.findByText("No channel connections");
    expect(get).not.toHaveBeenCalledWith("/workflows", expect.anything());
    expect(screen.queryByRole("link", { name: "AI credentials" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "MCP tools" })).toHaveAttribute("href", "/dashboard/connectors/remote-mcp");
  });

  it("keeps the working connection unchanged when a duplicate bot is refused", async () => {
    const original = { ...binding, id: "original", name: "Working Telegram", provider: "telegram", target_kind: "workflow", target_id: "workflow-1", enabled: true };
    const duplicate = { ...original, id: "duplicate", name: "Unused Telegram", target_kind: "agent", target_id: "agent-1", enabled: false };
    get.mockImplementation((url: string) => {
      if (url === "/channels/catalog") return Promise.resolve({ data: { providers: [{ id: "telegram", label: "Telegram", fields: ["access_token", "webhook_secret"], guide: "/docs/channel-workflows", reply: true, identity_link: true }] } });
      if (url === "/channels") return Promise.resolve({ data: { bindings: [original, duplicate] } });
      if (url === "/workflows") return Promise.resolve({ data: { items: [{ id: "workflow-1", name: "Transport acceptance" }] } });
      if (url.endsWith("/deliveries")) return Promise.resolve({ data: { deliveries: [] } });
      return Promise.resolve({ data: { items: [{ id: "agent-1", name: "Reasoning acceptance" }] } });
    });
    post.mockRejectedValue({ response: { status: 409, data: { detail: "telegram_bot_connected_elsewhere" } } });
    page();
    await screen.findByText("Working Telegram");
    fireEvent.click(screen.getAllByRole("button", { name: "Open" })[1]);
    fireEvent.click(screen.getByRole("button", { name: "Connect Telegram" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("telegram_bot_connected_elsewhere");
    expect(screen.getByLabelText("Enabled", { exact: true })).not.toBeChecked();
    expect(post).toHaveBeenCalledExactlyOnceWith("/channels/bindings/duplicate/connect-telegram");
    expect(patch).not.toHaveBeenCalled();
    fireEvent.click(screen.getAllByRole("button", { name: "Open" })[0]);
    expect(screen.getByLabelText("Enabled", { exact: true })).toBeChecked();
  });
});
