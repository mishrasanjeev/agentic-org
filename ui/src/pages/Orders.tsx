// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
import { useCallback, useEffect, useState } from "react";
import { Package, RefreshCw } from "lucide-react";
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
  surface: string;
  permalink_url: string | null;
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

/** Every order buyers placed through AgenticOrg, newest first. */
export default function Orders() {
  const [merchantId, setMerchantId] = useState("");
  const [filter, setFilter] = useState("");
  const [orders, setOrders] = useState<Order[]>([]);
  const [offset, setOffset] = useState(0);
  const [nextOffset, setNextOffset] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

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
            admin.
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
            <p className="mt-3 text-xs text-muted-foreground">Merchant {order.merchant_id}</p>
          </li>
        ))}
      </ul>

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
