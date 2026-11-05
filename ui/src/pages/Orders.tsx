// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
import { useCallback, useEffect, useState } from "react";
import { Package, RefreshCw, Undo2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { extractApiError, ordersApi } from "../lib/api";

interface OrderItem {
  title: string | null;
  quantity: number | null;
  unit_price: string;
  status: string | null;
}

interface Delivery {
  method: string;
  option: string | null;
  address: string[];
}

interface Refund {
  id: string;
  status: string;
  amount: string;
  occurred_at: string | null;
}

interface ShortLine {
  title: string;
  quantity: number;
  in_stock: number;
}

interface Order {
  id: string;
  merchant_id: string;
  checkout_id: string;
  created_at: string | null;
  currency: string;
  total_minor_units: number;
  total: string;
  items: OrderItem[];
  buyer: { first_name?: string; last_name?: string; email?: string; phone_number?: string };
  delivery?: Delivery[];
  shipping?: string | null;
  payment: { status: string; provider: string; provider_order_ref_present: boolean };
  refunds?: Refund[];
  refunded_minor_units?: number;
  refundable_minor_units?: number;
  refund_available?: boolean;
  stock_short?: ShortLine[] | null;
  refund_checkable?: boolean;
  surface: string;
  permalink_url: string | null;
}

interface UnmatchedPayment {
  id: string;
  merchant_id: string;
  checkout_id: string;
  order_id: string | null;
  reason: string;
  reason_text: string;
  amount: string | null;
  refund_status: string;
  refund_error: string | null;
  refundable: boolean;
  refund_checkable?: boolean;
  created_at: string | null;
}

interface RefundAnswer {
  status: string;
  reason: string;
  decision?: { reason?: string } | null;
}

const PAGE_SIZE = 25;
const PROVIDERS: Record<string, string> = { plural: "Plural" };

function when(value: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function buyerLines(buyer: Order["buyer"]): string[] {
  const name = [buyer.first_name, buyer.last_name].filter(Boolean).join(" ");
  return [name, buyer.email, buyer.phone_number].filter((v): v is string => Boolean(v));
}

/** The method and the option the buyer chose; the fee is the order's, shown once (shippingFeeLine). */
function deliveryHeading(delivery: Delivery): string {
  const parts = [delivery.method === "pickup" ? "Store pickup" : "Ship", delivery.option ?? ""];
  return parts.filter(Boolean).join(" · ");
}

/** The order's one shipping fee (all its shipping deliveries together), or "" when nothing ships. */
function shippingFeeLine(deliveries: Delivery[], shipping: string | null | undefined): string {
  const shipped = deliveries.filter((delivery) => delivery.method !== "pickup").length;
  if (!shipping || shipped === 0) return "";
  return `${shipped > 1 ? "Shipping fee for all deliveries" : "Shipping fee"}: ${shipping}`;
}

/** Minor units from a typed amount in major units ("449.50" -> 44950); null when it is not a positive amount. */
function minorUnits(value: string): number | null {
  const text = value.trim();
  if (!/^\d+(\.\d{1,2})?$/.test(text)) return null;
  const amount = Math.round(Number(text) * 100);
  return amount > 0 ? amount : null;
}

function majorUnits(minor: number): string {
  return (minor / 100).toFixed(2);
}

function newRequestId(orderId: string): string {
  const random = typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `${Date.now()}`;
  return `ui-${orderId}-${random}`.replace(/[^A-Za-z0-9_.:-]/g, "").slice(0, 80);
}

const REFUND_RESULTS: Record<string, string> = {
  succeeded: "Refunded. Plural returns the money to the buyer.",
  pending: "Refund requested. Plural has not confirmed it yet; it shows as pending.",
  refused: "The refund was refused",
  failed: "Plural did not take the refund",
};

function refundMessage(answer: RefundAnswer): string {
  const text = REFUND_RESULTS[answer.status] ?? `Refund ${answer.status}`;
  // A refusal by the arbiter names the failing rule in reason and the exact cause in the decision.
  const why = answer.decision?.reason || answer.reason;
  return answer.status === "refused" || answer.status === "failed" ? `${text} (${why}).` : text;
}

function OrderRefund({ order, onDone }: { order: Order; onDone: () => void }) {
  const refundable = order.refundable_minor_units ?? 0;
  const [open, setOpen] = useState(false);
  const [amount, setAmount] = useState("");
  const [reason, setReason] = useState("");
  const [requestId, setRequestId] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  if (!order.refund_available || (refundable <= 0 && !message)) return null;

  const start = () => {
    setAmount(majorUnits(refundable));
    setReason("");
    // One request id per confirmation: a repeated click returns the same refund, never a second one.
    setRequestId(newRequestId(order.id));
    setMessage(null);
    setOpen(true);
  };
  const minor = minorUnits(amount);
  const invalid = minor === null || minor > refundable;
  const confirm = async () => {
    if (minor === null || invalid) return;
    setBusy(true);
    try {
      const { data } = await ordersApi.refund(order.id, {
        amount: minor,
        request_id: requestId,
        ...(reason.trim() ? { reason: reason.trim() } : {}),
      });
      setMessage(refundMessage(data as RefundAnswer));
      setOpen(false);
      onDone();
    } catch (e) {
      setMessage(extractApiError(e, "The refund could not be sent. Try again."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mt-3 text-sm">
      {!open ? (
        <Button variant="outline" onClick={start} disabled={refundable <= 0}>
          <Undo2 className="mr-2 h-4 w-4" /> Refund
        </Button>
      ) : (
        <div className="space-y-2 rounded-lg border p-3" role="group" aria-label={`Refund order ${order.id}`}>
          <p>
            Refund up to {order.currency === "INR" ? "Rs " : `${order.currency} `}
            {majorUnits(refundable)} to the buyer through your Plural account. This cannot be undone.
          </p>
          <div className="flex flex-wrap items-end gap-3">
            <label className="flex flex-col gap-1">
              Amount ({order.currency})
              <input
                className="h-11 w-36 rounded-md border px-3 sm:h-10"
                inputMode="decimal"
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
              />
            </label>
            <label className="flex min-w-0 flex-1 flex-col gap-1">
              Reason (optional)
              <input className="h-11 rounded-md border px-3 sm:h-10" maxLength={200} value={reason} onChange={(e) => setReason(e.target.value)} />
            </label>
          </div>
          {invalid ? <p className="text-destructive">Enter an amount up to {majorUnits(refundable)}.</p> : null}
          <div className="flex gap-2">
            <Button onClick={() => void confirm()} disabled={busy || invalid}>
              Confirm refund
            </Button>
            <Button variant="outline" onClick={() => setOpen(false)} disabled={busy}>
              Cancel
            </Button>
          </div>
        </div>
      )}
      {message ? (
        <p className="mt-2 text-muted-foreground" role="status">
          {message}
        </p>
      ) : null}
    </div>
  );
}

function UnmatchedRefund({ payment, onDone }: { payment: UnmatchedPayment; onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const confirm = async () => {
    setBusy(true);
    try {
      const { data } = await ordersApi.refundUnmatched(payment.id, {});
      setMessage(refundMessage(data as RefundAnswer));
      setOpen(false);
      onDone();
    } catch (e) {
      setMessage(extractApiError(e, "The refund could not be sent. Try again."));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="mt-2 text-sm">
      {payment.refundable && !open ? (
        <Button variant="outline" onClick={() => setOpen(true)}>
          <Undo2 className="mr-2 h-4 w-4" /> Refund
        </Button>
      ) : null}
      {open ? (
        <div className="space-y-2 rounded-lg border p-3" role="group" aria-label={`Refund payment ${payment.id}`}>
          <p>Refund the full {payment.amount ?? "amount"} to the buyer through your Plural account? This cannot be undone.</p>
          <div className="flex gap-2">
            <Button onClick={() => void confirm()} disabled={busy}>
              Confirm refund
            </Button>
            <Button variant="outline" onClick={() => setOpen(false)} disabled={busy}>
              Cancel
            </Button>
          </div>
        </div>
      ) : null}
      {message ? (
        <p className="mt-2 text-muted-foreground" role="status">
          {message}
        </p>
      ) : null}
    </div>
  );
}

const CHECK_RESULTS: Record<string, string> = {
  succeeded: "Plural confirmed the refund.",
  failed: "Plural reports the refund failed. You can refund again.",
  still_unknown: "Plural has not confirmed the refund yet. It still counts as refunded; check again later.",
  status_unavailable: "Plural could not be reached. Check again later.",
  credentials_missing: "Your Plural credentials are missing, so the refund cannot be checked.",
  too_recent: "The refund was asked for less than 5 minutes ago. Check again in a few minutes.",
  nothing_to_check: "No refund is waiting for Plural.",
};

function checkMessage(results: Record<string, number> | string): string {
  const key = typeof results === "string" ? results : Object.keys(results)[0] ?? "nothing_to_check";
  return CHECK_RESULTS[key] ?? key;
}

/** Ask Plural again about a refund it has not confirmed; shows what it said. */
function CheckRefund({ check, onDone }: { check: () => Promise<{ data: any }>; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const run = async () => {
    setBusy(true);
    try {
      const { data } = await check();
      setMessage(checkMessage(data.results ?? data.result));
      onDone();
    } catch (e) {
      setMessage(extractApiError(e, "The refund could not be checked. Try again."));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="mt-2 text-sm">
      <Button variant="outline" onClick={() => void run()} disabled={busy}>
        <RefreshCw className="mr-2 h-4 w-4" /> Check refund again
      </Button>
      {message ? (
        <p className="mt-2 text-muted-foreground" role="status">
          {message}
        </p>
      ) : null}
    </div>
  );
}

const REFUND_STATUS: Record<string, string> = {
  none: "Not refunded",
  requested: "Refund in progress",
  pending: "Refund pending at Plural",
  refunded: "Refunded",
  failed: "Last refund failed",
};

/** Every order buyers placed through AgenticOrg, newest first. */
export default function Orders() {
  const [merchantId, setMerchantId] = useState("");
  const [filter, setFilter] = useState("");
  const [orders, setOrders] = useState<Order[]>([]);
  const [offset, setOffset] = useState(0);
  const [nextOffset, setNextOffset] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [unmatched, setUnmatched] = useState<UnmatchedPayment[]>([]);

  const load = useCallback(async (merchant: string, from: number) => {
    setLoading(true);
    setError(null);
    try {
      const { data } = await ordersApi.list({ limit: PAGE_SIZE, offset: from, ...(merchant ? { merchant_id: merchant } : {}) });
      setOrders(data.orders ?? []);
      setOffset(from);
      setNextOffset(data.next_offset ?? null);
    } catch (e) {
      setError(extractApiError(e, "Orders could not be loaded. Try again."));
    } finally {
      setLoading(false);
    }
    try {
      const { data } = await ordersApi.unmatched({ limit: PAGE_SIZE, ...(merchant ? { merchant_id: merchant } : {}) });
      setUnmatched(data.payments ?? []);
    } catch {
      setUnmatched([]);
    }
  }, []);

  useEffect(() => {
    void load(filter, 0);
  }, [load, filter]);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="flex items-center gap-2 text-2xl font-bold">
            <Package className="h-6 w-6" /> Orders
          </h2>
          <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
            Orders buyers paid for through AgenticOrg checkouts. Orders paid in your Shopify checkout appear in your Shopify
            admin, and you refund them there.
          </p>
        </div>
        <Button variant="outline" disabled={loading} onClick={() => void load(filter, offset)}>
          <RefreshCw className="mr-2 h-4 w-4" /> Refresh
        </Button>
      </div>

      <form
        className="flex flex-wrap items-end gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          setFilter(merchantId.trim());
        }}
      >
        <label className="flex flex-col gap-1 text-sm">
          Merchant ID (optional)
          <input className="h-11 rounded-md border px-3 sm:h-10" value={merchantId} onChange={(e) => setMerchantId(e.target.value)} />
        </label>
        <Button type="submit" variant="outline" disabled={loading}>
          Show
        </Button>
      </form>

      {error ? (
        <div className="rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive" role="alert">
          {error}
        </div>
      ) : null}

      {!loading && !error && orders.length === 0 ? (
        <div className="rounded-lg border bg-muted/40 px-4 py-6 text-sm" role="status">
          No orders yet. When a buyer pays on your payment page, the order shows here and you get an email.
        </div>
      ) : null}

      <ul className="space-y-3" aria-label="Orders">
        {orders.map((order) => (
          <li key={order.id} className="rounded-xl border p-4">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div>
                <p className="font-medium">{order.total}</p>
                <p className="break-all text-xs text-muted-foreground">
                  {order.id} · {when(order.created_at)}
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                <Badge variant="success">Paid{PROVIDERS[order.payment.provider] ? ` via ${PROVIDERS[order.payment.provider]}` : ""}</Badge>
                <Badge variant="secondary">{order.surface}</Badge>
              </div>
            </div>
            <div className="mt-3 grid gap-4 text-sm sm:grid-cols-3">
              <div>
                <p className="font-medium">Items</p>
                <ul className="mt-1 space-y-1 text-muted-foreground">
                  {order.items.map((item, index) => (
                    <li key={`${order.id}-${index}`}>
                      {item.title} × {item.quantity}
                      {item.unit_price ? ` at ${item.unit_price}` : ""}
                    </li>
                  ))}
                </ul>
              </div>
              <div>
                <p className="font-medium">Delivery (as given)</p>
                {order.delivery?.length ? (
                  order.delivery.map((delivery, index) => (
                    <div key={`${order.id}-delivery-${index}`} className="mt-1 text-muted-foreground">
                      <p>{deliveryHeading(delivery)}</p>
                      <ul className="break-words">
                        {delivery.address.map((line, lineIndex) => (
                          <li key={lineIndex}>{line}</li>
                        ))}
                      </ul>
                    </div>
                  ))
                ) : (
                  <p className="mt-1 text-muted-foreground">The buyer gave no delivery details.</p>
                )}
                {shippingFeeLine(order.delivery ?? [], order.shipping) ? (
                  <p className="mt-2 text-muted-foreground">{shippingFeeLine(order.delivery ?? [], order.shipping)}</p>
                ) : null}
              </div>
              <div>
                <p className="font-medium">Buyer contact (as given)</p>
                <ul className="mt-1 space-y-1 break-all text-muted-foreground">
                  {buyerLines(order.buyer).length ? (
                    buyerLines(order.buyer).map((line) => <li key={line}>{line}</li>)
                  ) : (
                    <li>The buyer gave no contact details.</li>
                  )}
                </ul>
              </div>
            </div>
            {order.stock_short?.length ? (
              <div className="mt-3 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm" role="note">
                <p className="font-medium">Stock was short when the payment arrived</p>
                <ul className="mt-1 text-muted-foreground">
                  {order.stock_short.map((line, index) => (
                    <li key={`${order.id}-short-${index}`}>
                      {line.title}: ordered {line.quantity}, in stock {line.in_stock}
                    </li>
                  ))}
                </ul>
                <p className="mt-1 text-muted-foreground">Ship what you can and refund the rest, or refund the order.</p>
              </div>
            ) : null}
            {order.refunds?.length ? (
              <ul className="mt-3 space-y-1 text-sm text-muted-foreground" aria-label={`Refunds of ${order.id}`}>
                {order.refunds.map((refund) => (
                  <li key={refund.id}>
                    Refund {refund.amount} · {refund.status === "completed" ? "refunded" : refund.status}
                  </li>
                ))}
              </ul>
            ) : null}
            <OrderRefund order={order} onDone={() => void load(filter, offset)} />
            {order.refund_checkable ? (
              <CheckRefund check={() => ordersApi.checkRefunds(order.id)} onDone={() => void load(filter, offset)} />
            ) : null}
            <p className="mt-3 text-xs text-muted-foreground">Merchant {order.merchant_id}</p>
          </li>
        ))}
      </ul>

      {unmatched.length ? (
        <section className="space-y-3" aria-label="Payments with no order">
          <div>
            <h3 className="text-lg font-semibold">Payments with no order</h3>
            <p className="text-sm text-muted-foreground">
              A buyer paid on your Plural page but no order matches the payment, or stock was short. Refund it here or in
              your Plural dashboard.
            </p>
          </div>
          <ul className="space-y-3">
            {unmatched.map((payment) => (
              <li key={payment.id} className="rounded-xl border p-4 text-sm">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div>
                    <p className="font-medium">{payment.amount ?? "Amount not reported"}</p>
                    <p className="break-all text-xs text-muted-foreground">
                      Checkout {payment.checkout_id}
                      {payment.order_id ? ` · Order ${payment.order_id}` : ""} · {when(payment.created_at)}
                    </p>
                  </div>
                  <Badge variant="secondary">{REFUND_STATUS[payment.refund_status] ?? payment.refund_status}</Badge>
                </div>
                <p className="mt-2 text-muted-foreground">Why: {payment.reason_text}.</p>
                <UnmatchedRefund payment={payment} onDone={() => void load(filter, offset)} />
                {payment.refund_checkable ? (
                  <CheckRefund check={() => ordersApi.checkUnmatched(payment.id)} onDone={() => void load(filter, offset)} />
                ) : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {offset > 0 || nextOffset !== null ? (
        <div className="flex gap-2">
          <Button variant="outline" disabled={loading || offset === 0} onClick={() => void load(filter, Math.max(0, offset - PAGE_SIZE))}>
            Newer
          </Button>
          <Button variant="outline" disabled={loading || nextOffset === null} onClick={() => nextOffset !== null && void load(filter, nextOffset)}>
            Older
          </Button>
        </div>
      ) : null}
    </div>
  );
}
