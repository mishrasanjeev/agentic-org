import { useEffect, useState } from "react";
import { CreditCard, ReceiptText, RefreshCw, Send } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import api, { extractApiError } from "@/lib/api";

const SAMPLE_ITEMS = [
  { description: "Monthly GST and bookkeeping retainer", quantity: "1", unit_price: "15000", tax_rate_percent: "18" },
];

interface ClientInvoice {
  id: string;
  company_id: string;
  invoice_number: string;
  issue_date: string;
  due_date: string;
  currency: string;
  total: string;
  balance_due: string;
  status: string;
}

interface ServicePlan {
  id: string;
  name: string;
  currency: string;
  default_fee: string;
  billing_cycle: string;
}

function parseLineItems(value: string): unknown[] {
  const parsed = JSON.parse(value);
  if (!Array.isArray(parsed)) throw new Error("Line items must be a JSON array.");
  return parsed;
}

export default function CABilling() {
  const [companyId, setCompanyId] = useState("");
  const [plans, setPlans] = useState<ServicePlan[]>([]);
  const [invoices, setInvoices] = useState<ClientInvoice[]>([]);
  const [planName, setPlanName] = useState("Monthly Compliance Retainer");
  const [planFee, setPlanFee] = useState("15000");
  const [lineItems, setLineItems] = useState(JSON.stringify(SAMPLE_ITEMS, null, 2));
  const [paymentAmount, setPaymentAmount] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  const load = async () => {
    setError("");
    try {
      const params = companyId ? { company_id: companyId } : undefined;
      const [planRes, invoiceRes] = await Promise.all([
        api.get("/ca-billing/service-plans").catch(() => ({ data: [] })),
        api.get("/ca-billing/invoices", { params }).catch(() => ({ data: [] })),
      ]);
      setPlans(Array.isArray(planRes.data) ? planRes.data : []);
      setInvoices(Array.isArray(invoiceRes.data) ? invoiceRes.data : []);
    } catch (err) {
      setError(extractApiError(err, "Failed to load CA billing data."));
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const createPlan = async () => {
    setBusy("plan");
    setError("");
    try {
      await api.post("/ca-billing/service-plans", {
        name: planName,
        default_fee: planFee,
        currency: "INR",
        billing_cycle: "monthly",
        tax_rate_percent: "18",
      });
      await load();
    } catch (err) {
      setError(extractApiError(err, "Failed to create service plan."));
    } finally {
      setBusy("");
    }
  };

  const createInvoice = async () => {
    setBusy("invoice");
    setError("");
    try {
      const items = parseLineItems(lineItems);
      await api.post("/ca-billing/invoices", {
        company_id: companyId,
        line_items: items,
        currency: "INR",
        tax_rate_percent: "18",
        send_immediately: true,
      });
      await load();
    } catch (err) {
      setError(extractApiError(err, "Failed to create invoice."));
    } finally {
      setBusy("");
    }
  };

  const sendInvoice = async (invoiceId: string) => {
    setBusy(invoiceId);
    setError("");
    try {
      await api.post(`/ca-billing/invoices/${invoiceId}/send`);
      await load();
    } catch (err) {
      setError(extractApiError(err, "Failed to send invoice."));
    } finally {
      setBusy("");
    }
  };

  const recordPayment = async (invoiceId: string) => {
    setBusy(`pay-${invoiceId}`);
    setError("");
    try {
      await api.post(`/ca-billing/invoices/${invoiceId}/payments`, {
        amount: paymentAmount,
        method: "bank_transfer",
        reference: "manual-entry",
      });
      setPaymentAmount("");
      await load();
    } catch (err) {
      setError(extractApiError(err, "Failed to record payment."));
    } finally {
      setBusy("");
    }
  };

  const firstOpen = invoices.find((item) => item.status !== "paid" && item.status !== "void");

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h2 className="text-2xl font-bold">CA Client Billing</h2>
          <p className="text-sm text-muted-foreground">Service plans, client invoices, and manual payment tracking.</p>
        </div>
        <Button variant="outline" onClick={load}>
          <RefreshCw className="mr-2 h-4 w-4" />
          Refresh
        </Button>
      </div>

      {error && <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">{error}</div>}

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-lg">Service Plan</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_120px]">
              <input className="h-9 rounded-md border px-3 text-sm" value={planName} onChange={(e) => setPlanName(e.target.value)} />
              <input className="h-9 rounded-md border px-3 text-sm" value={planFee} onChange={(e) => setPlanFee(e.target.value)} />
            </div>
            <Button variant="outline" disabled={!planName || !planFee || busy === "plan"} onClick={createPlan}>
              <ReceiptText className="mr-2 h-4 w-4" />
              {busy === "plan" ? "Saving..." : "Create Plan"}
            </Button>
            <div className="space-y-2">
              {plans.slice(0, 4).map((plan) => (
                <div key={plan.id} className="flex items-center justify-between gap-3 rounded-md border px-3 py-2 text-sm">
                  <span className="truncate font-medium">{plan.name}</span>
                  <Badge variant="secondary">{plan.currency} {plan.default_fee}</Badge>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-lg">Create Invoice</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <input className="h-9 w-full rounded-md border px-3 text-sm" placeholder="Company ID" value={companyId} onChange={(e) => setCompanyId(e.target.value)} />
            <textarea className="min-h-36 w-full rounded-md border px-3 py-2 font-mono text-xs" value={lineItems} onChange={(e) => setLineItems(e.target.value)} data-testid="ca-billing-line-items" />
            <Button disabled={!companyId || busy === "invoice"} onClick={createInvoice}>
              <Send className="mr-2 h-4 w-4" />
              {busy === "invoice" ? "Creating..." : "Create & Send"}
            </Button>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Invoices</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {invoices.length === 0 && <p className="text-sm text-muted-foreground">No client invoices yet.</p>}
          {invoices.map((item) => (
            <div key={item.id} className="grid gap-2 rounded-md border px-3 py-3 text-sm md:grid-cols-[minmax(0,1fr)_120px_120px_180px] md:items-center">
              <div className="min-w-0">
                <p className="truncate font-medium">{item.invoice_number}</p>
                <p className="text-xs text-muted-foreground">Due {item.due_date} &middot; balance {item.currency} {item.balance_due}</p>
              </div>
              <Badge variant={item.status === "paid" ? "success" : item.status === "overdue" ? "destructive" : "warning"}>{item.status}</Badge>
              <span className="font-medium">{item.currency} {item.total}</span>
              <div className="flex gap-2">
                <Button size="sm" variant="outline" disabled={busy === item.id} onClick={() => sendInvoice(item.id)}>Send</Button>
                <Button size="sm" variant="ghost" disabled={!paymentAmount || busy === `pay-${item.id}`} onClick={() => recordPayment(item.id)}>
                  <CreditCard className="h-4 w-4" />
                </Button>
              </div>
            </div>
          ))}
          {firstOpen && (
            <div className="flex max-w-sm gap-2">
              <input className="h-9 flex-1 rounded-md border px-3 text-sm" placeholder={`Payment for ${firstOpen.invoice_number}`} value={paymentAmount} onChange={(e) => setPaymentAmount(e.target.value)} />
              <Button variant="outline" disabled={!paymentAmount} onClick={() => recordPayment(firstOpen.id)}>Record</Button>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
