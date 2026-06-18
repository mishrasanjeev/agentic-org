import { useEffect, useState } from "react";
import { Calculator, RefreshCw, Send } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import api, { extractApiError } from "@/lib/api";

const SAMPLE_EMPLOYEES = [
  { employee_id: "E001", employee_name: "Asha Rao", gross_salary: "62000", pt_amount: "200" },
  { employee_id: "E002", employee_name: "Ravi Menon", gross_salary: "48000", pt_amount: "200" },
];

interface PTState {
  state_code: string;
  state: string;
  portal_name: string;
  portal_url: string;
  supports_online_return: boolean;
}

interface PTReturn {
  id: string;
  company_id: string;
  state_code: string;
  filing_period: string;
  total_payable: string;
  status: string;
}

function parseJsonArray(value: string): unknown[] {
  const parsed = JSON.parse(value);
  if (!Array.isArray(parsed)) throw new Error("Expected a JSON array.");
  return parsed;
}

function ResultBlock({ value }: { value: unknown }) {
  if (!value) return null;
  return (
    <pre className="max-h-64 overflow-auto rounded-md border bg-slate-950 p-3 text-xs text-slate-50">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

export default function ProfessionalTax() {
  const [states, setStates] = useState<PTState[]>([]);
  const [returns, setReturns] = useState<PTReturn[]>([]);
  const [companyId, setCompanyId] = useState("");
  const [stateCode, setStateCode] = useState("KA");
  const [registrationNumber, setRegistrationNumber] = useState("");
  const [filingPeriod, setFilingPeriod] = useState("2026-06");
  const [employeesJson, setEmployeesJson] = useState(JSON.stringify(SAMPLE_EMPLOYEES, null, 2));
  const [result, setResult] = useState<unknown>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  const load = async () => {
    setError("");
    try {
      const [stateRes, returnRes] = await Promise.all([
        api.get("/professional-tax/states"),
        api.get("/professional-tax/returns").catch(() => ({ data: [] })),
      ]);
      setStates(Array.isArray(stateRes.data?.items) ? stateRes.data.items : []);
      setReturns(Array.isArray(returnRes.data) ? returnRes.data : []);
    } catch (err) {
      setError(extractApiError(err, "Failed to load Professional Tax data."));
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const saveRegistration = async () => {
    setBusy("registration");
    setError("");
    try {
      await api.put(`/professional-tax/companies/${companyId}/registrations/${stateCode}`, {
        company_id: companyId,
        state_code: stateCode,
        registration_number: registrationNumber,
      });
      await load();
    } catch (err) {
      setError(extractApiError(err, "Failed to save PT registration."));
    } finally {
      setBusy("");
    }
  };

  const prepareReturn = async () => {
    setBusy("prepare");
    setError("");
    setResult(null);
    try {
      const employees = parseJsonArray(employeesJson);
      const { data } = await api.post("/professional-tax/returns/prepare", {
        company_id: companyId,
        state_code: stateCode,
        filing_period: filingPeriod,
        registration_number: registrationNumber || undefined,
        employees,
      });
      setResult(data);
      await load();
    } catch (err) {
      setError(extractApiError(err, "Failed to prepare PT return."));
    } finally {
      setBusy("");
    }
  };

  const markManual = async () => {
    const latest = returns[0];
    if (!latest) return;
    setBusy("manual");
    setError("");
    try {
      const { data } = await api.post(`/professional-tax/returns/${latest.id}/submit`, {
        submit_to_portal: false,
      });
      setResult(data);
      await load();
    } catch (err) {
      setError(extractApiError(err, "Failed to update PT return."));
    } finally {
      setBusy("");
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h2 className="text-2xl font-bold">Professional Tax</h2>
          <p className="text-sm text-muted-foreground">State registration, return preparation, and challan handoff.</p>
        </div>
        <Button variant="outline" onClick={load}>
          <RefreshCw className="mr-2 h-4 w-4" />
          Refresh
        </Button>
      </div>

      {error && <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">{error}</div>}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1.1fr)_minmax(320px,0.9fr)]">
        <Card>
          <CardHeader>
            <CardTitle className="text-lg">Prepare Return</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="grid gap-3 md:grid-cols-2">
              <input className="h-9 rounded-md border px-3 text-sm" placeholder="Company ID" value={companyId} onChange={(e) => setCompanyId(e.target.value)} />
              <select className="h-9 rounded-md border px-3 text-sm" value={stateCode} onChange={(e) => setStateCode(e.target.value)}>
                {states.length ? states.map((item) => <option key={item.state_code} value={item.state_code}>{item.state_code} - {item.state}</option>) : <option value="KA">KA - Karnataka</option>}
              </select>
              <input className="h-9 rounded-md border px-3 text-sm" placeholder="PT registration number" value={registrationNumber} onChange={(e) => setRegistrationNumber(e.target.value)} />
              <input className="h-9 rounded-md border px-3 text-sm" placeholder="Filing period" value={filingPeriod} onChange={(e) => setFilingPeriod(e.target.value)} />
            </div>
            <textarea className="min-h-40 w-full rounded-md border px-3 py-2 font-mono text-xs" value={employeesJson} onChange={(e) => setEmployeesJson(e.target.value)} data-testid="pt-employees-json" />
            <div className="flex flex-wrap gap-2">
              <Button variant="outline" disabled={!companyId || !registrationNumber || busy === "registration"} onClick={saveRegistration}>
                Save Registration
              </Button>
              <Button disabled={!companyId || !registrationNumber || busy === "prepare"} onClick={prepareReturn}>
                <Calculator className="mr-2 h-4 w-4" />
                {busy === "prepare" ? "Preparing..." : "Prepare Return"}
              </Button>
              <Button variant="outline" disabled={!returns.length || busy === "manual"} onClick={markManual}>
                <Send className="mr-2 h-4 w-4" />
                Mark Manual Upload
              </Button>
            </div>
            <ResultBlock value={result} />
          </CardContent>
        </Card>

        <div className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-lg">State Portals</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              {states.slice(0, 8).map((item) => (
                <div key={item.state_code} className="flex items-center justify-between gap-3 rounded-md border px-3 py-2 text-sm">
                  <div className="min-w-0">
                    <p className="truncate font-medium">{item.state}</p>
                    <p className="truncate text-xs text-muted-foreground">{item.portal_name}</p>
                  </div>
                  <Badge variant={item.supports_online_return ? "success" : "secondary"}>{item.state_code}</Badge>
                </div>
              ))}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-lg">Recent Returns</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              {returns.length === 0 && <p className="text-sm text-muted-foreground">No PT returns yet.</p>}
              {returns.slice(0, 5).map((item) => (
                <div key={item.id} className="rounded-md border px-3 py-2 text-sm">
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-medium">{item.state_code} {item.filing_period}</span>
                    <Badge variant={item.status === "submitted" ? "success" : "warning"}>{item.status}</Badge>
                  </div>
                  <p className="mt-1 text-xs text-muted-foreground">Total payable: {item.total_payable}</p>
                </div>
              ))}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
