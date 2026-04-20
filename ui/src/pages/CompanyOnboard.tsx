import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Helmet } from "react-helmet-async";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import api from "@/lib/api";
import { INDIAN_STATES, stateNameFromCode } from "@/lib/indianStates";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface CompanyForm {
  /* Step 1: Basic Info */
  name: string;
  gstin: string;
  pan: string;
  tan: string;
  cin: string;
  state: string;
  industry: string;
  address: string;
  /* Step 2: Compliance */
  pf_reg: string;
  esi_reg: string;
  pt_reg: string;
  fy_start: string;
  fy_end: string;
  /* Step 3: Signatory */
  signatory_name: string;
  signatory_designation: string;
  signatory_email: string;
  dsc_serial: string;
  dsc_holder: string;
  dsc_expiry: string;
  /* Step 4: Banking */
  bank_name: string;
  account_number: string;
  ifsc: string;
  branch: string;
  /* Step 5: Tally Connection */
  tally_bridge_url: string;
  tally_bridge_id: string;
  tally_company_name: string;
  /* Step 6: Review — includes GST Config */
  gst_auto_file: boolean;
  compliance_email: string;
}

const INITIAL_FORM: CompanyForm = {
  name: "", gstin: "", pan: "", tan: "", cin: "", state: "", industry: "", address: "",
  pf_reg: "", esi_reg: "", pt_reg: "", fy_start: "2026-04-01", fy_end: "2027-03-31",
  signatory_name: "", signatory_designation: "", signatory_email: "", dsc_serial: "", dsc_holder: "", dsc_expiry: "",
  bank_name: "", account_number: "", ifsc: "", branch: "",
  tally_bridge_url: "", tally_bridge_id: "", tally_company_name: "",
  gst_auto_file: false, compliance_email: "",
};

const STEPS = [
  "Basic Info",
  "Compliance",
  "Signatory",
  "Banking",
  "Tally Connection",
  "Review & Confirm",
];

const INDUSTRIES = [
  "Manufacturing", "IT Services", "Healthcare", "Export", "Retail",
  "Textile", "Logistics", "Education", "Construction", "Real Estate",
  "FMCG", "Agriculture", "Pharmaceuticals", "Automotive", "Other",
];

/* ------------------------------------------------------------------ */
/*  Validation helpers                                                 */
/* ------------------------------------------------------------------ */

function validateGSTIN(gstin: string): string | null {
  if (!gstin) return "GSTIN is required";
  const re = /^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$/;
  if (!re.test(gstin)) return "Invalid GSTIN format (e.g., 29AABCU9603R1ZM)";
  return null;
}

function validatePAN(pan: string): string | null {
  if (!pan) return "PAN is required";
  const re = /^[A-Z]{5}[0-9]{4}[A-Z]{1}$/;
  if (!re.test(pan)) return "Invalid PAN format (e.g., AABCU9603R)";
  return null;
}

function validateEmail(email: string): string | null {
  if (!email) return null; // optional
  const re = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
  if (!re.test(email)) return "Invalid email format";
  return null;
}

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export default function CompanyOnboard() {
  const navigate = useNavigate();
  const [step, setStep] = useState(0);
  const [form, setForm] = useState<CompanyForm>(INITIAL_FORM);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState("");

  const update = (field: keyof CompanyForm, value: string | boolean) => {
    setForm((prev) => ({ ...prev, [field]: value }));
    setErrors((prev) => { const next = { ...prev }; delete next[field]; return next; });
  };

  const fieldClass = (field: string) =>
    `w-full h-9 rounded-md border ${errors[field] ? "border-red-500" : "border-input"} bg-background px-3 text-sm focus:outline-none focus:ring-1 ${errors[field] ? "focus:ring-red-500" : "focus:ring-primary"}`;

  const labelClass = "block text-sm font-medium mb-1";

  const [tallyTesting, setTallyTesting] = useState(false);
  const [tallyTestResult, setTallyTestResult] = useState<string | null>(null);
  const [tallyDetecting, setTallyDetecting] = useState(false);
  const [tallyDetectResult, setTallyDetectResult] = useState<{ company_name: string; gstin: string; pan: string } | null>(null);
  const [tallyDetectMsg, setTallyDetectMsg] = useState<string | null>(null);

  const autoDetectFromTally = async () => {
    setTallyDetecting(true);
    setTallyDetectResult(null);
    setTallyDetectMsg(null);
    try {
      // BUG-005: the request shape was `bridge_url`/`bridge_id` but the
      // backend model was `tally_bridge_url`/`tally_bridge_id`, so the
      // Auto-Detect call failed silently the moment after Test
      // Connection succeeded. Backend now accepts either alias; we keep
      // using the shorter keys so they match /test-tally.
      const res = await api.post("/companies/tally-detect", {
        bridge_url: form.tally_bridge_url,
        bridge_id: form.tally_bridge_id,
      });
      const data = res.data as {
        detected?: boolean;
        company_name?: string;
        gstin?: string;
        pan?: string;
        address?: string;
      };
      // BUG-005 / BUG-008: honour the `detected` flag. On failure the
      // backend puts the user-friendly error in `address` — surface
      // that instead of the generic "Auto-detect failed" fallback.
      if (data?.detected && data.company_name) {
        setTallyDetectResult({
          company_name: data.company_name,
          gstin: data.gstin || "",
          pan: data.pan || "",
        });
      } else {
        setTallyDetectMsg(
          data?.address ||
            "Could not detect company info from Tally. Verify the bridge URL and try Test Connection first.",
        );
      }
    } catch (err: unknown) {
      const detail = (err as {
        response?: { data?: { detail?: string } };
      })?.response?.data?.detail;
      setTallyDetectMsg(
        typeof detail === "string"
          ? `Auto-detect failed: ${detail}`
          : "Auto-detect failed. Ensure the Tally bridge is running and reachable.",
      );
    } finally {
      setTallyDetecting(false);
    }
  };

  const applyTallyDetect = () => {
    if (!tallyDetectResult) return;
    setForm((prev) => ({
      ...prev,
      name: tallyDetectResult.company_name || prev.name,
      gstin: tallyDetectResult.gstin || prev.gstin,
      pan: tallyDetectResult.pan || prev.pan,
    }));
    setTallyDetectMsg("Company data auto-detected from Tally");
    setTallyDetectResult(null);
  };

  const [tallyTestMessage, setTallyTestMessage] = useState<string>("");

  const testTallyConnection = async () => {
    setTallyTesting(true);
    setTallyTestResult(null);
    setTallyTestMessage("");
    try {
      // BUG-008: the backend already returns a user-friendly reason in
      // TallyTestResponse.message (e.g. "Could not reach bridge at X —
      // verify the URL and that the AgenticOrg Tally Bridge is
      // running."). The wizard used to swallow the response and render
      // only "Connection failed. Check bridge URL..." regardless.
      // Surface the backend message verbatim when present.
      const res = await api.post("/companies/test-tally", {
        bridge_url: form.tally_bridge_url,
        bridge_id: form.tally_bridge_id,
        company_name: form.tally_company_name,
      });
      const data = res.data as { success?: boolean; message?: string; bridge_version?: string };
      if (data?.success) {
        setTallyTestResult("success");
        setTallyTestMessage(
          data.bridge_version
            ? `Bridge reachable (version ${data.bridge_version})`
            : data.message || "Bridge reachable",
        );
      } else {
        setTallyTestResult("error");
        setTallyTestMessage(
          data?.message ||
            "Connection failed. Check the bridge URL and try again.",
        );
      }
    } catch (err: unknown) {
      const detail = (err as {
        response?: { data?: { detail?: string } };
      })?.response?.data?.detail;
      setTallyTestResult("error");
      setTallyTestMessage(
        typeof detail === "string"
          ? `Connection failed: ${detail}`
          : "Could not reach the bridge. Verify the URL and that the AgenticOrg Tally Bridge is running.",
      );
    } finally {
      setTallyTesting(false);
    }
  };

  const validateStep = (): boolean => {
    const errs: Record<string, string> = {};

    if (step === 0) {
      if (!form.name.trim()) errs.name = "Company name is required";
      const gstinErr = validateGSTIN(form.gstin);
      if (gstinErr) errs.gstin = gstinErr;
      const panErr = validatePAN(form.pan);
      if (panErr) errs.pan = panErr;
      if (!form.state) errs.state = "State is required";
    } else if (step === 1) {
      if (!form.fy_start) errs.fy_start = "FY start is required";
      if (!form.fy_end) errs.fy_end = "FY end is required";
    } else if (step === 2) {
      if (!form.signatory_name.trim()) errs.signatory_name = "Signatory name is required";
      const emailErr = validateEmail(form.signatory_email);
      if (emailErr) errs.signatory_email = emailErr;
    }

    setErrors(errs);
    return Object.keys(errs).length === 0;
  };

  const handleNext = () => {
    if (!validateStep()) return;
    setStep((s) => Math.min(s + 1, STEPS.length - 1));
  };

  const handleBack = () => setStep((s) => Math.max(s - 1, 0));

  const handleSubmit = async () => {
    setSubmitError("");
    setSubmitting(true);
    try {
      // Map UI field names to backend schema field names.
      // BUG-004: persist the Tally Connection fields captured on Step 5
      // (previously dropped on the floor, so a subsequent 422 looked
      // like a mysterious reset). tally_config is the documented
      // carrier on /companies/onboard.
      const tallyConfig =
        form.tally_bridge_url || form.tally_bridge_id || form.tally_company_name
          ? {
              bridge_url: form.tally_bridge_url || undefined,
              bridge_id: form.tally_bridge_id || undefined,
              company_name: form.tally_company_name || undefined,
            }
          : undefined;
      const payload = {
        name: form.name,
        gstin: form.gstin || undefined,
        pan: form.pan,
        tan: form.tan || undefined,
        cin: form.cin || undefined,
        state_code: form.state || undefined,
        industry: form.industry || undefined,
        registered_address: form.address || undefined,
        signatory_name: form.signatory_name || undefined,
        signatory_designation: form.signatory_designation || undefined,
        signatory_email: form.signatory_email || undefined,
        compliance_email: form.compliance_email || undefined,
        dsc_serial: form.dsc_serial || undefined,
        dsc_expiry: form.dsc_expiry || undefined,
        bank_name: form.bank_name || undefined,
        bank_account_number: form.account_number || undefined,
        bank_ifsc: form.ifsc || undefined,
        pf_registration: form.pf_reg || undefined,
        esi_registration: form.esi_reg || undefined,
        pt_registration: form.pt_reg || undefined,
        gst_auto_file: form.gst_auto_file,
        tally_config: tallyConfig,
      };
      await api.post("/companies/onboard", payload);
      navigate("/dashboard/companies");
    } catch (e: unknown) {
      // BUG-004: the old handler set submitError but didn't keep the
      // user on Step 6 — form state reset to Step 0 next render, so
      // the error banner was invisible and the form appeared to
      // silently lose data. Stay on the current step and flatten
      // Pydantic 422 detail arrays into a readable message.
      const detail = (e as {
        response?: { status?: number; data?: { detail?: unknown } };
      })?.response?.data?.detail;
      let msg = "Failed to onboard company. Please try again.";
      if (typeof detail === "string") {
        msg = detail;
      } else if (Array.isArray(detail)) {
        msg = (detail as { loc?: unknown[]; msg?: string }[])
          .map((d) => {
            const field = Array.isArray(d.loc) ? d.loc.slice(1).join(".") : "";
            return field ? `${field}: ${d.msg}` : d.msg;
          })
          .filter(Boolean)
          .join("; ");
      }
      setSubmitError(msg);
      // Keep the user on the review step (Step 6) so they can see
      // the message and their entered data remains intact.
      setStep(STEPS.length - 1);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <Helmet>
        <title>Onboard Client | AgenticOrg</title>
      </Helmet>

      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold">Onboard New Client</h2>
          <p className="text-sm text-muted-foreground mt-1">Step {step + 1} of {STEPS.length}: {STEPS[step]}</p>
        </div>
        <Button variant="outline" onClick={() => navigate("/dashboard/companies")}>Cancel</Button>
      </div>

      {/* Step indicator */}
      <div className="flex items-center gap-1">
        {STEPS.map((s, i) => (
          <div key={s} className="flex items-center flex-1">
            <div className={`flex items-center justify-center w-8 h-8 rounded-full text-xs font-bold ${
              i < step ? "bg-primary text-primary-foreground" :
              i === step ? "bg-primary text-primary-foreground ring-2 ring-primary/30" :
              "bg-muted text-muted-foreground"
            }`}>
              {i < step ? (
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                </svg>
              ) : (
                i + 1
              )}
            </div>
            {i < STEPS.length - 1 && (
              <div className={`flex-1 h-0.5 mx-1 ${i < step ? "bg-primary" : "bg-muted"}`} />
            )}
          </div>
        ))}
      </div>

      {/* Form Card */}
      <Card>
        <CardHeader>
          <CardTitle>{STEPS[step]}</CardTitle>
        </CardHeader>
        <CardContent>
          {/* Step 0: Basic Info */}
          {step === 0 && (
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="sm:col-span-2">
                <label className={labelClass}>Company Name <span className="text-red-500">*</span></label>
                <input value={form.name} onChange={(e) => update("name", e.target.value)} className={fieldClass("name")} placeholder="e.g., Acme Manufacturing Pvt Ltd" />
                {errors.name && <p className="text-xs text-red-500 mt-1">{errors.name}</p>}
              </div>
              <div>
                <label className={labelClass}>GSTIN <span className="text-red-500">*</span></label>
                <input value={form.gstin} onChange={(e) => update("gstin", e.target.value.toUpperCase())} className={fieldClass("gstin")} placeholder="29AABCU9603R1ZM" maxLength={15} />
                {errors.gstin && <p className="text-xs text-red-500 mt-1">{errors.gstin}</p>}
              </div>
              <div>
                <label className={labelClass}>PAN <span className="text-red-500">*</span></label>
                <input value={form.pan} onChange={(e) => update("pan", e.target.value.toUpperCase())} className={fieldClass("pan")} placeholder="AABCU9603R" maxLength={10} />
                {errors.pan && <p className="text-xs text-red-500 mt-1">{errors.pan}</p>}
              </div>
              <div>
                <label className={labelClass}>TAN</label>
                <input value={form.tan} onChange={(e) => update("tan", e.target.value.toUpperCase())} className={fieldClass("tan")} placeholder="BLRA12345F" maxLength={10} />
              </div>
              <div>
                <label className={labelClass}>CIN</label>
                <input value={form.cin} onChange={(e) => update("cin", e.target.value.toUpperCase())} className={fieldClass("cin")} placeholder="U12345KA2020PTC123456" />
              </div>
              <div>
                <label className={labelClass}>State <span className="text-red-500">*</span></label>
                <select value={form.state} onChange={(e) => update("state", e.target.value)} className={fieldClass("state")}>
                  <option value="">Select state</option>
                  {INDIAN_STATES.map((s) => <option key={s.code} value={s.code}>{s.name}</option>)}
                </select>
                {errors.state && <p className="text-xs text-red-500 mt-1">{errors.state}</p>}
              </div>
              <div>
                <label className={labelClass}>Industry</label>
                <select value={form.industry} onChange={(e) => update("industry", e.target.value)} className={fieldClass("industry")}>
                  <option value="">Select industry</option>
                  {INDUSTRIES.map((ind) => <option key={ind} value={ind}>{ind}</option>)}
                </select>
              </div>
              <div className="sm:col-span-2">
                <label className={labelClass}>Address</label>
                <input value={form.address} onChange={(e) => update("address", e.target.value)} className={fieldClass("address")} placeholder="Registered address" />
              </div>
            </div>
          )}

          {/* Step 1: Compliance */}
          {step === 1 && (
            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <label className={labelClass}>PF Registration No.</label>
                <input value={form.pf_reg} onChange={(e) => update("pf_reg", e.target.value)} className={fieldClass("pf_reg")} placeholder="KA/BLR/12345/000/12345" />
              </div>
              <div>
                <label className={labelClass}>ESI Registration No.</label>
                <input value={form.esi_reg} onChange={(e) => update("esi_reg", e.target.value)} className={fieldClass("esi_reg")} placeholder="12-34-567890-123-4567" />
              </div>
              <div>
                <label className={labelClass}>PT Registration No.</label>
                <input value={form.pt_reg} onChange={(e) => update("pt_reg", e.target.value)} className={fieldClass("pt_reg")} placeholder="Professional Tax Reg No." />
              </div>
              <div className="sm:col-span-2 border-t pt-4 mt-2">
                <p className="text-sm font-medium mb-3">Financial Year</p>
              </div>
              <div>
                <label className={labelClass}>FY Start <span className="text-red-500">*</span></label>
                <input type="date" value={form.fy_start} onChange={(e) => update("fy_start", e.target.value)} className={fieldClass("fy_start")} />
                {errors.fy_start && <p className="text-xs text-red-500 mt-1">{errors.fy_start}</p>}
              </div>
              <div>
                <label className={labelClass}>FY End <span className="text-red-500">*</span></label>
                <input type="date" value={form.fy_end} onChange={(e) => update("fy_end", e.target.value)} className={fieldClass("fy_end")} />
                {errors.fy_end && <p className="text-xs text-red-500 mt-1">{errors.fy_end}</p>}
              </div>
            </div>
          )}

          {/* Step 2: Signatory */}
          {step === 2 && (
            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <label className={labelClass}>Signatory Name <span className="text-red-500">*</span></label>
                <input value={form.signatory_name} onChange={(e) => update("signatory_name", e.target.value)} className={fieldClass("signatory_name")} placeholder="Full name of authorized signatory" />
                {errors.signatory_name && <p className="text-xs text-red-500 mt-1">{errors.signatory_name}</p>}
              </div>
              <div>
                <label className={labelClass}>Designation</label>
                <input value={form.signatory_designation} onChange={(e) => update("signatory_designation", e.target.value)} className={fieldClass("signatory_designation")} placeholder="e.g., Director, Partner, Proprietor" />
              </div>
              <div>
                <label className={labelClass}>Email</label>
                <input type="email" value={form.signatory_email} onChange={(e) => update("signatory_email", e.target.value)} className={fieldClass("signatory_email")} placeholder="signatory@company.com" />
                {errors.signatory_email && <p className="text-xs text-red-500 mt-1">{errors.signatory_email}</p>}
              </div>
              <div>
                <label className={labelClass}>DSC Serial Number</label>
                <input value={form.dsc_serial} onChange={(e) => update("dsc_serial", e.target.value)} className={fieldClass("dsc_serial")} placeholder="DSC certificate serial" />
              </div>
              <div>
                <label className={labelClass}>DSC Holder Name</label>
                <input value={form.dsc_holder} onChange={(e) => update("dsc_holder", e.target.value)} className={fieldClass("dsc_holder")} placeholder="Name on DSC" />
              </div>
              <div>
                <label className={labelClass}>DSC Expiry Date</label>
                <input type="date" value={form.dsc_expiry} onChange={(e) => update("dsc_expiry", e.target.value)} className={fieldClass("dsc_expiry")} />
              </div>
            </div>
          )}

          {/* Step 3: Banking */}
          {step === 3 && (
            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <label className={labelClass}>Bank Name</label>
                <input value={form.bank_name} onChange={(e) => update("bank_name", e.target.value)} className={fieldClass("bank_name")} placeholder="e.g., State Bank of India" />
              </div>
              <div>
                <label className={labelClass}>Account Number</label>
                <input value={form.account_number} onChange={(e) => update("account_number", e.target.value)} className={fieldClass("account_number")} placeholder="Account number" />
              </div>
              <div>
                <label className={labelClass}>IFSC Code</label>
                <input value={form.ifsc} onChange={(e) => update("ifsc", e.target.value.toUpperCase())} className={fieldClass("ifsc")} placeholder="SBIN0001234" maxLength={11} />
              </div>
              <div>
                <label className={labelClass}>Branch</label>
                <input value={form.branch} onChange={(e) => update("branch", e.target.value)} className={fieldClass("branch")} placeholder="Branch name" />
              </div>
            </div>
          )}

          {/* Step 4: Tally Connection */}
          {step === 4 && (
            <div className="grid gap-4">
              <div className="bg-muted/50 rounded-lg p-4 mb-2">
                <p className="text-sm text-muted-foreground">
                  Connect to Tally Prime via the AgenticOrg Tally Bridge. Install the bridge on the same machine as Tally, then provide the connection details below.
                </p>
              </div>
              <div>
                <label className={labelClass}>Tally Bridge URL</label>
                <input value={form.tally_bridge_url} onChange={(e) => update("tally_bridge_url", e.target.value)} className={fieldClass("tally_bridge_url")} placeholder="http://localhost:9100" />
              </div>
              <div>
                <label className={labelClass}>Bridge ID</label>
                <input value={form.tally_bridge_id} onChange={(e) => update("tally_bridge_id", e.target.value)} className={fieldClass("tally_bridge_id")} placeholder="Auto-generated on bridge install" />
              </div>
              <div>
                <label className={labelClass}>Company Name in Tally</label>
                <input value={form.tally_company_name} onChange={(e) => update("tally_company_name", e.target.value)} className={fieldClass("tally_company_name")} placeholder="Exact company name as in Tally" />
              </div>
              <div className="pt-2">
                <Button
                  variant="outline"
                  onClick={testTallyConnection}
                  disabled={tallyTesting || !form.tally_bridge_url}
                >
                  {tallyTesting ? "Testing..." : "Test Connection"}
                </Button>
                {tallyTestResult === "success" && (
                  <p className="text-xs text-emerald-600 mt-2">
                    {tallyTestMessage || "Connection successful."}
                  </p>
                )}
                {tallyTestResult === "error" && (
                  <p className="text-xs text-red-500 mt-2">
                    {tallyTestMessage ||
                      "Connection failed. Check the bridge URL and ensure Tally is running."}
                  </p>
                )}
                <Button
                  variant="outline"
                  className="ml-2"
                  onClick={autoDetectFromTally}
                  disabled={tallyDetecting || !form.tally_bridge_url}
                >
                  {tallyDetecting ? "Detecting..." : "Auto-Detect Company Info"}
                </Button>
              </div>

              {/* Tally Auto-Detect confirmation dialog */}
              {tallyDetectResult && (
                <div className="bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-lg p-4">
                  <p className="text-sm font-medium text-blue-800 dark:text-blue-200 mb-2">
                    Detected: {tallyDetectResult.company_name}, GSTIN: {tallyDetectResult.gstin || "N/A"}, PAN: {tallyDetectResult.pan || "N/A"}. Apply to form?
                  </p>
                  <div className="flex items-center gap-2">
                    <Button size="sm" onClick={applyTallyDetect}>Apply</Button>
                    <Button size="sm" variant="outline" onClick={() => setTallyDetectResult(null)}>Dismiss</Button>
                  </div>
                </div>
              )}

              {/* Auto-detect info message */}
              {tallyDetectMsg && !tallyDetectResult && (
                <div className="bg-emerald-50 dark:bg-emerald-900/20 border border-emerald-200 dark:border-emerald-800 rounded-lg p-3">
                  <p className="text-xs text-emerald-700 dark:text-emerald-300">{tallyDetectMsg}</p>
                </div>
              )}
            </div>
          )}

          {/* Step 5: Review & Confirm */}
          {step === 5 && (
            <div className="space-y-6">
              {/* Basic Info */}
              <div>
                <h4 className="text-sm font-semibold mb-2 flex items-center gap-2">
                  <Badge variant="outline">1</Badge> Basic Info
                </h4>
                <div className="grid sm:grid-cols-2 gap-2 text-sm">
                  <div><span className="text-muted-foreground">Name:</span> {form.name || "—"}</div>
                  <div><span className="text-muted-foreground">GSTIN:</span> <span className="font-mono">{form.gstin || "—"}</span></div>
                  <div><span className="text-muted-foreground">PAN:</span> <span className="font-mono">{form.pan || "—"}</span></div>
                  <div><span className="text-muted-foreground">TAN:</span> <span className="font-mono">{form.tan || "—"}</span></div>
                  <div><span className="text-muted-foreground">State:</span> {stateNameFromCode(form.state) || "—"}</div>
                  <div><span className="text-muted-foreground">Industry:</span> {form.industry || "—"}</div>
                  <div className="sm:col-span-2"><span className="text-muted-foreground">Address:</span> {form.address || "—"}</div>
                </div>
              </div>

              {/* Compliance */}
              <div>
                <h4 className="text-sm font-semibold mb-2 flex items-center gap-2">
                  <Badge variant="outline">2</Badge> Compliance
                </h4>
                <div className="grid sm:grid-cols-2 gap-2 text-sm">
                  <div><span className="text-muted-foreground">PF Reg:</span> {form.pf_reg || "—"}</div>
                  <div><span className="text-muted-foreground">ESI Reg:</span> {form.esi_reg || "—"}</div>
                  <div><span className="text-muted-foreground">PT Reg:</span> {form.pt_reg || "—"}</div>
                  <div><span className="text-muted-foreground">FY:</span> {form.fy_start} to {form.fy_end}</div>
                </div>
              </div>

              {/* Signatory */}
              <div>
                <h4 className="text-sm font-semibold mb-2 flex items-center gap-2">
                  <Badge variant="outline">3</Badge> Signatory
                </h4>
                <div className="grid sm:grid-cols-2 gap-2 text-sm">
                  <div><span className="text-muted-foreground">Name:</span> {form.signatory_name || "—"}</div>
                  <div><span className="text-muted-foreground">Designation:</span> {form.signatory_designation || "—"}</div>
                  <div><span className="text-muted-foreground">Email:</span> {form.signatory_email || "—"}</div>
                  <div><span className="text-muted-foreground">DSC Serial:</span> {form.dsc_serial || "—"}</div>
                  <div><span className="text-muted-foreground">DSC Holder:</span> {form.dsc_holder || "—"}</div>
                  <div><span className="text-muted-foreground">DSC Expiry:</span> {form.dsc_expiry || "—"}</div>
                </div>
              </div>

              {/* Banking */}
              <div>
                <h4 className="text-sm font-semibold mb-2 flex items-center gap-2">
                  <Badge variant="outline">4</Badge> Banking
                </h4>
                <div className="grid sm:grid-cols-2 gap-2 text-sm">
                  <div><span className="text-muted-foreground">Bank:</span> {form.bank_name || "—"}</div>
                  <div><span className="text-muted-foreground">Account:</span> {form.account_number ? "****" + form.account_number.slice(-4) : "—"}</div>
                  <div><span className="text-muted-foreground">IFSC:</span> <span className="font-mono">{form.ifsc || "—"}</span></div>
                  <div><span className="text-muted-foreground">Branch:</span> {form.branch || "—"}</div>
                </div>
              </div>

              {/* Tally */}
              <div>
                <h4 className="text-sm font-semibold mb-2 flex items-center gap-2">
                  <Badge variant="outline">5</Badge> Tally Connection
                </h4>
                <div className="grid sm:grid-cols-2 gap-2 text-sm">
                  <div><span className="text-muted-foreground">Bridge URL:</span> {form.tally_bridge_url || "—"}</div>
                  <div><span className="text-muted-foreground">Company:</span> {form.tally_company_name || "—"}</div>
                </div>
              </div>

              {/* GST Auto-File Toggle */}
              <div className="border-t pt-4">
                <label className="flex items-center gap-3 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={form.gst_auto_file}
                    onChange={(e) => update("gst_auto_file", e.target.checked)}
                    className="w-4 h-4 rounded border-input"
                  />
                  <span className="text-sm font-medium">Enable GST Auto-Filing</span>
                </label>
                {form.gst_auto_file && (
                  <div className="mt-3 bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 rounded-lg p-3">
                    <p className="text-sm text-amber-800 dark:text-amber-200 font-medium">Warning: Auto-filing enabled</p>
                    <p className="text-xs text-amber-700 dark:text-amber-300 mt-1">
                      GSTR-1 and GSTR-3B will be automatically filed after agent validation. Ensure DSC details are configured and the authorized signatory has approved auto-filing for this client.
                    </p>
                  </div>
                )}
                {!form.gst_auto_file && (
                  <div className="mt-3 bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-lg p-3">
                    <p className="text-sm text-blue-800 dark:text-blue-200 font-medium">Manual GSTN Upload Mode</p>
                    <p className="text-xs text-blue-700 dark:text-blue-300 mt-1">
                      Agents will generate the GSTR JSON files. You can download them and manually upload to the GSTN portal. The partner can self-approve filings before upload.
                    </p>
                  </div>
                )}
              </div>

              {/* Compliance Alerts Email */}
              <div className="border-t pt-4">
                <label className={labelClass}>Compliance Alerts Email</label>
                <input
                  type="email"
                  value={form.compliance_email}
                  onChange={(e) => update("compliance_email", e.target.value)}
                  className={fieldClass("compliance_email")}
                  placeholder="alerts@cafirm.com (receives filing deadlines & alerts)"
                />
              </div>

              {submitError && (
                <div className="bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg p-3">
                  <p className="text-sm text-red-700 dark:text-red-300">{submitError}</p>
                </div>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Navigation buttons */}
      <div className="flex justify-between">
        <Button variant="outline" onClick={handleBack} disabled={step === 0}>
          Back
        </Button>
        {step < STEPS.length - 1 ? (
          <Button onClick={handleNext}>
            Next
          </Button>
        ) : (
          <Button onClick={handleSubmit} disabled={submitting}>
            {submitting ? "Submitting..." : "Confirm & Onboard"}
          </Button>
        )}
      </div>
    </div>
  );
}
