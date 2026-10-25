// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

const get = vi.fn();
const put = vi.fn();

vi.mock("@/lib/api", () => ({ default: {} }));
vi.mock("../lib/api", () => ({
  sellEverywhereApi: { get: (...a: unknown[]) => get(...a), put: (...a: unknown[]) => put(...a) },
  extractApiError: (_e: unknown, fallback: string) => fallback,
}));

import SellEverywhere from "@/pages/SellEverywhere";

const surfaces = (overrides: Record<string, unknown> = {}) => ({
  web: { status: "not_started", host: null },
  mcp: { status: "off", server_url: "https://mcp.agenticorg.ai/mcp" },
  telegram: { status: "off", link: null },
  google: { status: "registration_pending" },
  microsoft: { status: "registration_pending" },
  perplexity: { status: "registration_pending" },
  payments: { status: "off" },
  ...overrides,
});

const view = (overrides: Record<string, unknown> = {}) => ({
  terms_version: "2026-10-25",
  suggested_slug: "tea-corner",
  listing: null,
  surfaces: surfaces(),
  platform_subdomains: { apex: "agenticorg.ai", in_use: 3, cap: 20 },
  ...overrides,
});

function load() {
  render(
    <MemoryRouter>
      <SellEverywhere />
    </MemoryRouter>,
  );
  fireEvent.change(screen.getByLabelText("Merchant ID"), { target: { value: "merchant-1" } });
  fireEvent.click(screen.getByRole("button", { name: "Load" }));
}

describe("Sell everywhere", () => {
  beforeEach(() => {
    get.mockReset();
    put.mockReset();
  });

  it("turns the network on only after the terms are accepted and shows every surface", async () => {
    get.mockResolvedValue({ data: view() });
    put.mockResolvedValue({
      data: view({
        listing: { slug: "tea-corner", status: "listed", display_name: "Tea Corner", serving_host: null, subdomain_host: "tea-corner.agenticorg.ai", terms_version: "2026-10-25", opted_in_at: "2026-10-25T00:00:00Z" },
        surfaces: surfaces({
          web: { status: "pending_mapping", host: "tea-corner.agenticorg.ai" },
          mcp: { status: "waiting_for_publication", server_url: "https://mcp.agenticorg.ai/mcp" },
          telegram: { status: "coming_soon", link: "https://t.me/ExampleShopBot?start=tea-corner" },
        }),
      }),
    });
    load();

    const slug = await screen.findByLabelText("Shop name on the network");
    expect(slug).toHaveValue("tea-corner");
    const turnOn = screen.getByRole("button", { name: "Turn on Sell everywhere" });
    expect(turnOn).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(turnOn);

    await waitFor(() =>
      expect(put).toHaveBeenCalledWith("merchant-1", { listed: true, accept_terms: true, slug: "tea-corner" }),
    );
    expect(await screen.findByText("Setting up")).toBeInTheDocument();
    expect(screen.getByText(/https:\/\/tea-corner\.agenticorg\.ai \(AI search/)).toBeInTheDocument();
    expect(screen.getByText("https://t.me/ExampleShopBot?start=tea-corner")).toBeInTheDocument();
    expect(screen.getAllByText("Coming: registration in progress")).toHaveLength(3);
    expect(screen.getByRole("button", { name: "Pause" })).toBeInTheDocument();
    expect(screen.getByLabelText("Shop name on the network")).toBeDisabled();
  }, 20000);

  it("shows the reason when no platform address could be given", async () => {
    get.mockResolvedValue({ data: view({ subdomain_note: { code: "platform_subdomain_cap_reached", message: "All platform addresses are taken for now; connect your own domain instead." } }) });
    load();
    expect(await screen.findByText(/All platform addresses are taken/)).toBeInTheDocument();
  }, 20000);

  it("says when the deployment has not turned it on", async () => {
    get.mockRejectedValue({ response: { status: 404, data: { detail: "Not Found" } } });
    load();
    expect(await screen.findByText("Sell everywhere is not enabled for this deployment yet.")).toBeInTheDocument();
  }, 20000);
});
