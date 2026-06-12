import { useEffect, useState } from "react";
import { Helmet } from "react-helmet-async";
import { AlertTriangle, FileCheck2, ReceiptText, RefreshCw } from "lucide-react";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import api, { extractApiError } from "@/lib/api";

const SAMPLE_BOOKS_ROWS = [
  {
    id: "BILL-001",
    deductee_pan: "AABCU9603R",
    section: "194C",
    transaction_date: "2026-04-15",
    challan_serial: "10244",
    bsr_code: "0510002",
    tds_amount: 2500,
  },
];

const SAMPLE_TRACES_ROWS = [
  {
    certificate_number: "TRC-001",
    deductee_pan: "AABCU9603R",
    section: "194C",
    transaction_date: "2026-04-15",
    challan_serial: "10244",
    bsr_code: "0510002",
    tds_amount: 2500,
  },
];

const SAMPLE_EWAY_ROWS = [
  {
    client_reference: "INV-001",
    supply_type: "outward",
    sub_supply_type: "supply",
    document_type: "tax_invoice",
    document_number: "INV-001",
    document_date: "2026-06-12",
    from_gstin: "29AABCU9603R1ZM",
    from_pin_code: 560001,
    from_state_code: 29,
    to_gstin: "27AABCU9603R1ZV",
    to_pin_code: 400001,
    to_state_code: 27,
    product_name: "Machine parts",
    hsn_code: "8483",
    quantity: 10,
    unit: "NOS",
    taxable_amount: 100000,
    total_invoice_value: 118000,
    transport_mode: "road",
    distance_km: 980,
    vehicle_number: "KA01AB1234",
  },
];

interface Capability {
  id: string;
  label: string;
  status: string;
  evidence?: string[];
  residual?: string;
}

function parseJsonRows(value: string, label: string): Record<string, unknown>[] {
  const parsed = JSON.parse(value);
  if (!Array.isArray(parsed)) {
    throw new Error(`${label} must be a JSON array`);
  }
  return parsed as Record<string, unknown>[];
}

function ResultBlock({ result }: { result: unknown }) {
  if (!result) return null;
  return (
    <pre className="max-h-80 overflow-auto rounded-md border bg-slate-950 p-3 text-xs text-slate-50">
      {JSON.stringify(result, null, 2)}
    </pre>
  );
}

export default function CAOperations() {
  const [booksRows, setBooksRows] = useState(JSON.stringify(SAMPLE_BOOKS_ROWS, null, 2));
  const [tracesRows, setTracesRows] = useState(JSON.stringify(SAMPLE_TRACES_ROWS, null, 2));
  const [ewayRows, setEwayRows] = useState(JSON.stringify(SAMPLE_EWAY_ROWS, null, 2));
  const [tracesResult, setTracesResult] = useState<unknown>(null);
  const [ewayResult, setEwayResult] = useState<unknown>(null);
  const [capabilities, setCapabilities] = useState<Capability[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<string | null>(null);

  const loadCapabilities = async () => {
    try {
      const { data } = await api.get("/ca-capabilities/status");
      setCapabilities(Array.isArray(data?.items) ? data.items : []);
    } catch (err) {
      setError(extractApiError(err, "Failed to load CA capability status."));
    }
  };

  useEffect(() => {
    void loadCapabilities();
  }, []);

  const reconcileTraces = async () => {
    setBusy("traces");
    setError("");
    setTracesResult(null);
    try {
      const expected_deductions = parseJsonRows(booksRows, "Books rows");
      const traces_statement = parseJsonRows(tracesRows, "TRACES rows");
      const { data } = await api.post("/connectors/traces/reconcile", {
        expected_deductions,
        traces_statement,
        tolerance_rupees: 1,
      });
      setTracesResult(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : extractApiError(err, "TRACES reconciliation failed."));
    } finally {
      setBusy(null);
    }
  };

  const validateEwayBatch = async () => {
    setBusy("eway");
    setError("");
    setEwayResult(null);
    try {
      const invoices = parseJsonRows(ewayRows, "Invoice rows");
      const { data } = await api.post("/connectors/gstn/eway-bills/bulk-generate", {
        invoices,
        submit_to_gstn: false,
      });
      setEwayResult(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : extractApiError(err, "E-way bill batch validation failed."));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="space-y-6">
      <Helmet>
        <title>CA Operations | AgenticOrg</title>
      </Helmet>

      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h2 className="text-2xl font-bold">CA Operations</h2>
          <p className="mt-1 text-sm text-muted-foreground">TRACES, e-way bills, and shipped capability status</p>
        </div>
        <Button variant="outline" onClick={loadCapabilities}>
          <RefreshCw className="mr-2 h-4 w-4" />
          Refresh
        </Button>
      </div>

      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          {error}
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-lg">
              <FileCheck2 className="h-5 w-5" />
              TRACES Reconciliation
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="grid gap-3 md:grid-cols-2">
              <label className="space-y-1">
                <span className="text-sm font-medium">Books TDS Rows</span>
                <textarea
                  value={booksRows}
                  onChange={(e) => setBooksRows(e.target.value)}
                  className="h-48 w-full rounded-md border bg-background p-2 font-mono text-xs"
                  data-testid="books-tds-json"
                />
              </label>
              <label className="space-y-1">
                <span className="text-sm font-medium">TRACES Statement Rows</span>
                <textarea
                  value={tracesRows}
                  onChange={(e) => setTracesRows(e.target.value)}
                  className="h-48 w-full rounded-md border bg-background p-2 font-mono text-xs"
                  data-testid="traces-json"
                />
              </label>
            </div>
            <Button onClick={reconcileTraces} disabled={busy === "traces"}>
              {busy === "traces" ? "Reconciling..." : "Reconcile"}
            </Button>
            <ResultBlock result={tracesResult} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-lg">
              <ReceiptText className="h-5 w-5" />
              E-Way Bill Batch
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <label className="space-y-1">
              <span className="text-sm font-medium">Invoice Rows</span>
              <textarea
                value={ewayRows}
                onChange={(e) => setEwayRows(e.target.value)}
                className="h-48 w-full rounded-md border bg-background p-2 font-mono text-xs"
                data-testid="eway-json"
              />
            </label>
            <Button onClick={validateEwayBatch} disabled={busy === "eway"}>
              {busy === "eway" ? "Validating..." : "Validate Batch"}
            </Button>
            <ResultBlock result={ewayResult} />
          </CardContent>
        </Card>
      </div>

      <div>
        <h3 className="mb-3 text-lg font-semibold">Capability Status</h3>
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {capabilities.map((capability) => (
            <Card key={capability.id}>
              <CardContent className="space-y-3 pt-4">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <p className="font-medium">{capability.label}</p>
                    <p className="mt-1 text-xs text-muted-foreground">{capability.residual}</p>
                  </div>
                  <Badge variant={capability.status.includes("not_shipped") ? "warning" : "success"}>
                    {capability.status.replace(/_/g, " ")}
                  </Badge>
                </div>
                {capability.status.includes("not_shipped") && (
                  <div className="flex items-center gap-2 text-xs text-amber-700">
                    <AlertTriangle className="h-4 w-4" />
                    Roadmap scope
                  </div>
                )}
              </CardContent>
            </Card>
          ))}
        </div>
      </div>
    </div>
  );
}
