// SPDX-License-Identifier: Apache-2.0
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import CustomDomains from "../pages/CustomDomains";

const apiMock = vi.hoisted(() => ({
  get: vi.fn(),
  add: vi.fn(),
  check: vi.fn(),
  remove: vi.fn(),
  operatorQueue: vi.fn(),
  markMapped: vi.fn(),
  markFailed: vi.fn(),
}));

vi.mock("../lib/api", () => ({
  customDomainsApi: apiMock,
  extractApiError: (_e: unknown, fallback: string) => fallback,
}));

const HOST = "agent.merchant.example";

function domain(overrides: Record<string, unknown> = {}) {
  return {
    id: "domain-1",
    host: HOST,
    status: "pending_dns",
    steps: [
      { key: "dns", state: "current" },
      { key: "ownership", state: "current" },
      { key: "mapping", state: "pending" },
      { key: "certificate", state: "pending" },
      { key: "live", state: "pending" },
    ],
    records: [
      { purpose: "routing", type: "CNAME", name: HOST, value: "ghs.googlehosted.com.", note: "DNS only (not proxied)." },
      { purpose: "ownership", type: "TXT", name: `_agenticorg-ucp.${HOST}`, value: "agenticorg-ucp-verify=synthetic" },
    ],
    ownership_verified: false,
    search_console: {
      account: "mapper@platform.example",
      property: "merchant.example",
      instructions: "Add the mapping account as a verified owner.",
      confirmed: false,
    },
    error: null,
    can_retry: true,
    profile_url: `https://${HOST}/.well-known/ucp`,
    last_checked_at: null,
    live_at: null,
    ...overrides,
  };
}

function notFound() {
  return Object.assign(new Error("not found"), { response: { status: 404, data: { detail: "Not Found" } } });
}

async function loadMerchant() {
  fireEvent.change(screen.getByLabelText("Merchant ID"), { target: { value: "merchant-1" } });
  fireEvent.click(screen.getByRole("button", { name: "Load" }));
}

describe("CustomDomains", () => {
  beforeEach(() => {
    Object.values(apiMock).forEach((fn) => fn.mockReset());
    apiMock.operatorQueue.mockRejectedValue(notFound());
  });

  it("adds a subdomain and shows the exact CNAME and TXT records", async () => {
    apiMock.get.mockResolvedValueOnce({ data: { enabled: true, cname_target: "ghs.googlehosted.com", domain: null } });
    apiMock.add.mockResolvedValueOnce({ data: domain() });
    render(<CustomDomains />);
    await loadMerchant();

    fireEvent.change(await screen.findByLabelText("Domain"), { target: { value: HOST } });
    fireEvent.click(screen.getByRole("button", { name: "Add domain" }));

    await screen.findByRole("heading", { name: HOST });
    expect(apiMock.add).toHaveBeenCalledWith("merchant-1", { host: HOST });
    expect(screen.getByText("ghs.googlehosted.com.")).toBeTruthy();
    expect(screen.getByText(`_agenticorg-ucp.${HOST}`)).toBeTruthy();
    expect(screen.getByText("agenticorg-ucp-verify=synthetic")).toBeTruthy();
    expect(screen.getByRole("link", { name: "apex domain guide" }).getAttribute("href")).toContain("merchant-apex-domain.md");
    // The operator queue is hidden for a merchant (404).
    expect(screen.queryByText("Operator queue")).toBeNull();
  });

  it("verifies with the Search Console confirmation and shows a clear error to retry", async () => {
    apiMock.get.mockResolvedValueOnce({ data: { enabled: true, domain: domain() } });
    apiMock.check.mockResolvedValueOnce({
      data: domain({
        error: { code: "cname_wrong_target", message: "The CNAME record points somewhere else.", detail: "found shops.example" },
        steps: [
          { key: "dns", state: "error" },
          { key: "ownership", state: "done" },
          { key: "mapping", state: "pending" },
          { key: "certificate", state: "pending" },
          { key: "live", state: "pending" },
        ],
      }),
    });
    render(<CustomDomains />);
    await loadMerchant();

    fireEvent.click(await screen.findByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: /Verify records/ }));

    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText("The CNAME record points somewhere else.")).toBeTruthy();
    expect(within(alert).getByText("found shops.example")).toBeTruthy();
    expect(apiMock.check).toHaveBeenCalledWith("merchant-1", { search_console_owner_added: true });
    const states = Array.from(screen.getByLabelText("Domain progress").querySelectorAll("li")).map((li) => li.getAttribute("data-state"));
    expect(states).toEqual(["error", "done", "pending", "pending", "pending"]);
  });

  it("shows the live profile URL and a retry for a failed domain", async () => {
    apiMock.get.mockResolvedValueOnce({ data: { enabled: true, domain: domain({ status: "failed", error: { code: "mapping_failed", message: "Could not connect.", detail: "Search Console owner missing" } }) } });
    apiMock.check.mockResolvedValueOnce({ data: domain({ status: "live", can_retry: false, steps: domain().steps.map((s) => ({ ...s, state: "done" })) }) });
    render(<CustomDomains />);
    await loadMerchant();

    expect(await screen.findByText("Search Console owner missing")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Retry/ }));
    expect(await screen.findByRole("link", { name: `https://${HOST}/.well-known/ucp` })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Retry|Check status/ })).toBeNull();
  });

  it("says when custom domains are not enabled", async () => {
    apiMock.get.mockRejectedValueOnce(notFound());
    render(<CustomDomains />);
    await loadMerchant();
    expect(await screen.findByText("Custom domains are not enabled for this deployment yet.")).toBeTruthy();
  });

  it("lets the operator copy the command and mark a domain mapped", async () => {
    const item = {
      id: "domain-1",
      tenant_id: "tenant-1",
      merchant_id: "merchant-1",
      host: HOST,
      status: "pending_mapping",
      search_console_owner_confirmed: true,
      commands: {
        create: `gcloud beta run domain-mappings create --service=agenticorg-api --domain=${HOST} --project=example-project --region=asia-southeast1`,
        describe: "describe",
        delete: "delete",
      },
      error: null,
      mapping_requested_at: null,
    };
    apiMock.operatorQueue.mockReset();
    apiMock.operatorQueue.mockResolvedValueOnce({ data: { items: [item] } }).mockResolvedValueOnce({ data: { items: [] } });
    apiMock.markMapped.mockResolvedValueOnce({ data: { ...item, status: "pending_certificate" } });
    render(<CustomDomains />);

    const row = await screen.findByTestId("operator-queue-item");
    expect(within(row).getByText(item.commands.create)).toBeTruthy();
    expect(within(row).getByText("Search Console owner confirmed")).toBeTruthy();
    fireEvent.click(within(row).getByRole("button", { name: "Mark mapped" }));
    await waitFor(() => expect(apiMock.markMapped).toHaveBeenCalledWith("domain-1"));
    expect(await screen.findByText("Nothing is waiting.")).toBeTruthy();
  });
});
