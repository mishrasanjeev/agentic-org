// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

const list = vi.fn();
const refund = vi.fn();
const unmatched = vi.fn();
const refundUnmatched = vi.fn();

vi.mock("@/lib/api", () => ({ default: {} }));
vi.mock("../lib/api", () => ({
  ordersApi: {
    list: (...a: unknown[]) => list(...a),
    refund: (...a: unknown[]) => refund(...a),
    unmatched: (...a: unknown[]) => unmatched(...a),
    refundUnmatched: (...a: unknown[]) => refundUnmatched(...a),
  },
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
  beforeEach(() => {
    list.mockReset();
    refund.mockReset();
    unmatched.mockReset();
    refundUnmatched.mockReset();
    unmatched.mockResolvedValue({ data: { payments: [], next_offset: null } });
  });

  it("lists each order with its items, total, delivery, buyer contact, payment status and surface", async () => {
    list.mockResolvedValue({ data: { orders: [order()], limit: 25, offset: 0, next_offset: null } });
    show();
    expect(await screen.findByText("Rs 449.00")).toBeInTheDocument();
    expect(screen.getByText(/Synthetic Masala Chai × 2/)).toBeInTheDocument();
    expect(screen.getByText("buyer@merchant.example")).toBeInTheDocument();
    expect(screen.getByText("Asha")).toBeInTheDocument();
    expect(screen.getByText("Ship · Standard delivery")).toBeInTheDocument();
    expect(screen.getByText("Shipping fee: Rs 49.00")).toBeInTheDocument();
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
    expect(screen.queryByText(/Shipping fee/)).not.toBeInTheDocument();
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
    expect(screen.queryByText(/Shipping fee/)).not.toBeInTheDocument();
  });

  it("shows the order's one shipping fee once when it ships to two places", async () => {
    list.mockResolvedValueOnce({
      data: {
        orders: [
          order({
            delivery: [
              { method: "shipping", option: "Standard", address: ["Asha Rao", "12 MG Road"] },
              { method: "shipping", option: "Express", address: ["Ravi Rao", "4 Park Street"] },
            ],
            shipping: "Rs 98.00",
          }),
        ],
        next_offset: null,
      },
    });
    show();
    expect(await screen.findByText("Ship · Standard")).toBeInTheDocument();
    expect(screen.getByText("Ship · Express")).toBeInTheDocument();
    expect(screen.getAllByText(/Rs 98\.00/)).toHaveLength(1);
    expect(screen.getByText("Shipping fee for all deliveries: Rs 98.00")).toBeInTheDocument();
  });

  it("refunds a Plural order after a confirmation step and shows the result", async () => {
    const refundable = order({ refund_available: true, refundable_minor_units: 44900, refunds: [] });
    list.mockResolvedValue({ data: { orders: [refundable], next_offset: null } });
    show();
    await screen.findByText("Rs 449.00");
    fireEvent.click(screen.getByRole("button", { name: /Refund/ }));
    // Nothing is sent before the merchant confirms.
    expect(refund).not.toHaveBeenCalled();
    expect(screen.getByText(/This cannot be undone/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Amount (INR)"), { target: { value: "500.00" } });
    expect(screen.getByText("Enter an amount up to 449.00.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Confirm refund" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Amount (INR)"), { target: { value: "100.50" } });
    fireEvent.change(screen.getByLabelText("Reason (optional)"), { target: { value: "Damaged box" } });
    refund.mockResolvedValueOnce({ data: { status: "succeeded", reason: "refund_succeeded" } });
    list.mockResolvedValue({
      data: {
        orders: [
          order({
            refund_available: true,
            refundable_minor_units: 34850,
            refunds: [{ id: "adj_1", status: "completed", amount: "Rs 100.50", occurred_at: null }],
          }),
        ],
        next_offset: null,
      },
    });
    fireEvent.click(screen.getByRole("button", { name: "Confirm refund" }));
    await waitFor(() => expect(refund).toHaveBeenCalledTimes(1));
    const [orderId, body] = refund.mock.calls[0];
    expect(orderId).toBe("ord_example_1");
    expect(body).toMatchObject({ amount: 10050, reason: "Damaged box" });
    expect(body.request_id).toMatch(/^ui-ord_example_1-/);
    expect(await screen.findByText("Refunded. Plural returns the money to the buyer.")).toBeInTheDocument();
    expect(await screen.findByText(/Refund Rs 100.50/)).toBeInTheDocument();
  });

  it("cancels a refund without sending it and shows a refusal reason", async () => {
    list.mockResolvedValue({ data: { orders: [order({ refund_available: true, refundable_minor_units: 44900 })], next_offset: null } });
    show();
    await screen.findByText("Rs 449.00");
    fireEvent.click(screen.getByRole("button", { name: /Refund/ }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(refund).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /Refund/ }));
    refund.mockResolvedValueOnce({ data: { status: "refused", reason: "refund_exceeds_captured" } });
    fireEvent.click(screen.getByRole("button", { name: "Confirm refund" }));
    expect(await screen.findByText("The refund was refused (refund_exceeds_captured).")).toBeInTheDocument();
  });

  it("offers no refund for an order paid elsewhere or already fully refunded", async () => {
    list.mockResolvedValue({
      data: {
        orders: [
          order({ id: "ord_a", refund_available: false, refundable_minor_units: 44900 }),
          order({ id: "ord_b", total: "Rs 1.00", refund_available: true, refundable_minor_units: 0 }),
        ],
        next_offset: null,
      },
    });
    show();
    await screen.findByText("Rs 1.00");
    expect(screen.queryByRole("button", { name: /Refund/ })).not.toBeInTheDocument();
  });

  it("lists payments with no order, shows short stock, and refunds an unmatched payment once confirmed", async () => {
    list.mockResolvedValue({
      data: {
        orders: [order({ stock_short: [{ title: "Synthetic Masala Chai", quantity: 2, in_stock: 1 }] })],
        next_offset: null,
      },
    });
    unmatched.mockResolvedValue({
      data: {
        payments: [
          {
            id: "11111111-1111-4111-8111-111111111111",
            merchant_id: "tea-corner",
            checkout_id: "chk_example_9",
            order_id: null,
            reason: "amount_mismatch",
            reason_text: "the amount paid is not the amount of the checkout",
            amount: "Rs 1.00",
            refund_status: "none",
            refund_error: null,
            refundable: true,
            created_at: "2026-10-29T10:00:00Z",
          },
        ],
        next_offset: null,
      },
    });
    show();
    expect(await screen.findByText("Payments with no order")).toBeInTheDocument();
    expect(screen.getByText("Stock was short when the payment arrived")).toBeInTheDocument();
    expect(screen.getByText("Synthetic Masala Chai: ordered 2, in stock 1")).toBeInTheDocument();
    expect(screen.getByText("Why: the amount paid is not the amount of the checkout.")).toBeInTheDocument();
    expect(screen.getByText("Not refunded")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Refund/ }));
    expect(refundUnmatched).not.toHaveBeenCalled();
    expect(screen.getByText(/Refund the full Rs 1.00/)).toBeInTheDocument();
    refundUnmatched.mockResolvedValueOnce({ data: { status: "pending", reason: "refund_unknown" } });
    fireEvent.click(screen.getByRole("button", { name: "Confirm refund" }));
    await waitFor(() => expect(refundUnmatched).toHaveBeenCalledWith("11111111-1111-4111-8111-111111111111", {}));
    expect(await screen.findByText(/Refund requested/)).toBeInTheDocument();
  });
});
