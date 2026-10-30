// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

const list = vi.fn();

vi.mock("@/lib/api", () => ({ default: {} }));
vi.mock("../lib/api", () => ({
  ordersApi: { list: (...a: unknown[]) => list(...a) },
  extractApiError: (_e: unknown, fallback: string) => fallback,
}));

import Orders from "@/pages/Orders";

const order = (overrides: Record<string, unknown> = {}) => ({
  id: "ord_example_1",
  merchant_id: "tea-corner",
  checkout_id: "chk_example_1",
  created_at: "2026-10-29T10:00:00Z",
  currency: "INR",
  total_minor_units: 44900,
  total: "Rs 449.00",
  items: [{ title: "Synthetic Masala Chai", quantity: 2, unit_price: "Rs 224.50", status: "processing" }],
  buyer: { first_name: "Asha", email: "buyer@merchant.example", phone_number: "+919800000000" },
  delivery: [
    { method: "shipping", option: "Standard delivery", address: ["Asha Rao", "12 MG Road", "Bengaluru, KA, 560001", "IN"] },
  ],
  shipping: "Rs 49.00",
  payment: { status: "paid", provider: "plural", provider_order_ref_present: true },
  surface: "Telegram",
  permalink_url: "https://shop.merchant.example/ucp/orders/ord_example_1",
  ...overrides,
});

function show() {
  render(
    <MemoryRouter>
      <Orders />
    </MemoryRouter>,
  );
}

describe("Orders", () => {
  beforeEach(() => list.mockReset());

  it("lists each order with its items, total, delivery, buyer contact, payment status and surface", async () => {
    list.mockResolvedValue({ data: { orders: [order()], limit: 25, offset: 0, next_offset: null } });
    show();
    expect(await screen.findByText("Rs 449.00")).toBeInTheDocument();
    expect(screen.getByText(/Synthetic Masala Chai × 2/)).toBeInTheDocument();
    expect(screen.getByText("buyer@merchant.example")).toBeInTheDocument();
    expect(screen.getByText("Asha")).toBeInTheDocument();
    expect(screen.getByText("Ship · Standard delivery · Rs 49.00")).toBeInTheDocument();
    expect(screen.getByText("12 MG Road")).toBeInTheDocument();
    expect(screen.getByText("Bengaluru, KA, 560001")).toBeInTheDocument();
    expect(screen.getByText("Paid via Plural")).toBeInTheDocument();
    expect(screen.getByText("Telegram")).toBeInTheDocument();
    expect(list).toHaveBeenCalledWith({ limit: 25, offset: 0 });
  });

  it("filters by merchant and pages through older orders", async () => {
    list.mockResolvedValueOnce({ data: { orders: [order()], limit: 25, offset: 0, next_offset: 25 } });
    show();
    await screen.findByText("Rs 449.00");
    list.mockResolvedValueOnce({ data: { orders: [order({ id: "ord_example_2", total: "Rs 10.00" })], next_offset: null } });
    fireEvent.click(screen.getByRole("button", { name: "Older" }));
    expect(await screen.findByText("Rs 10.00")).toBeInTheDocument();
    expect(list).toHaveBeenLastCalledWith({ limit: 25, offset: 25 });

    list.mockResolvedValueOnce({ data: { orders: [], next_offset: null } });
    fireEvent.change(screen.getByLabelText("Merchant ID (optional)"), { target: { value: "tea-corner" } });
    fireEvent.click(screen.getByRole("button", { name: "Show" }));
    await waitFor(() => expect(list).toHaveBeenLastCalledWith({ limit: 25, offset: 0, merchant_id: "tea-corner" }));
    expect(await screen.findByRole("status")).toHaveTextContent("No orders yet");
  });

  it("says when the buyer gave no contact or delivery details and shows a load error", async () => {
    list.mockResolvedValueOnce({ data: { orders: [order({ buyer: {}, delivery: undefined })], next_offset: null } });
    show();
    expect(await screen.findByText("The buyer gave no contact details.")).toBeInTheDocument();
    expect(screen.getByText("The buyer gave no delivery details.")).toBeInTheDocument();
    list.mockRejectedValueOnce(new Error("down"));
    fireEvent.click(screen.getByRole("button", { name: /Refresh/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Orders could not be loaded");
  });

  it("names a store pickup without a shipping fee", async () => {
    list.mockResolvedValueOnce({
      data: { orders: [order({ delivery: [{ method: "pickup", option: "MG Road store", address: [] }] })], next_offset: null },
    });
    show();
    expect(await screen.findByText("Store pickup · MG Road store")).toBeInTheDocument();
  });
});
