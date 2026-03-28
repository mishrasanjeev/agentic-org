import { useState, useEffect } from "react";
import { Helmet } from "react-helmet-async";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import SchemaEditor from "@/components/SchemaEditor";
import api from "@/lib/api";

interface SchemaEntry {
  name: string;
  version: string;
  description?: string;
  is_default: boolean;
  field_count: number;
}

const DEFAULT_SCHEMAS = [
  "Invoice", "Payment", "Order", "Employee", "Contract", "Campaign",
  "Ticket", "Vendor", "Lead", "Product", "Asset", "Incident",
  "ComplianceFiling", "JobRequisition", "JournalEntry", "PayrollRun",
  "TrainingRecord", "CustomFieldsExtension",
];

const SCHEMA_DEFINITIONS: Record<string, object> = {
  Invoice: {
    $schema: "https://json-schema.org/draft/2020-12/schema",
    title: "Invoice", type: "object", required: ["invoice_id", "vendor_id", "amount", "currency", "due_date"],
    properties: {
      invoice_id: { type: "string", description: "Unique invoice identifier" },
      vendor_id: { type: "string", description: "Vendor/supplier identifier" },
      po_number: { type: "string", description: "Purchase order reference" },
      amount: { type: "number", minimum: 0, description: "Invoice amount" },
      currency: { type: "string", enum: ["INR", "USD", "EUR", "GBP"], default: "INR" },
      tax_amount: { type: "number", minimum: 0 },
      gst_number: { type: "string", pattern: "^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$" },
      due_date: { type: "string", format: "date" },
      line_items: { type: "array", items: { type: "object", properties: { description: { type: "string" }, quantity: { type: "integer" }, unit_price: { type: "number" } } } },
      status: { type: "string", enum: ["draft", "pending", "approved", "paid", "disputed"] },
    },
  },
  Payment: {
    $schema: "https://json-schema.org/draft/2020-12/schema",
    title: "Payment", type: "object", required: ["payment_id", "invoice_id", "amount", "method"],
    properties: {
      payment_id: { type: "string" }, invoice_id: { type: "string" }, amount: { type: "number", minimum: 0 },
      currency: { type: "string", enum: ["INR", "USD", "EUR", "GBP"], default: "INR" },
      method: { type: "string", enum: ["bank_transfer", "upi", "neft", "rtgs", "cheque", "card"] },
      reference_number: { type: "string" }, paid_at: { type: "string", format: "date-time" },
      status: { type: "string", enum: ["initiated", "processing", "completed", "failed", "reversed"] },
    },
  },
  Order: {
    $schema: "https://json-schema.org/draft/2020-12/schema",
    title: "Order", type: "object", required: ["order_id", "customer_id", "items"],
    properties: {
      order_id: { type: "string" }, customer_id: { type: "string" },
      items: { type: "array", items: { type: "object", properties: { product_id: { type: "string" }, quantity: { type: "integer" }, price: { type: "number" } } } },
      total: { type: "number" }, status: { type: "string", enum: ["pending", "confirmed", "shipped", "delivered", "cancelled"] },
      created_at: { type: "string", format: "date-time" },
    },
  },
  Employee: {
    $schema: "https://json-schema.org/draft/2020-12/schema",
    title: "Employee", type: "object", required: ["employee_id", "name", "email", "department"],
    properties: {
      employee_id: { type: "string" }, name: { type: "string" }, email: { type: "string", format: "email" },
      department: { type: "string" }, designation: { type: "string" }, reporting_to: { type: "string" },
      pan_number: { type: "string" }, uan_number: { type: "string", description: "EPFO Universal Account Number" },
      date_of_joining: { type: "string", format: "date" },
      status: { type: "string", enum: ["active", "on_leave", "notice_period", "exited"] },
    },
  },
  Contract: {
    $schema: "https://json-schema.org/draft/2020-12/schema",
    title: "Contract", type: "object", required: ["contract_id", "parties", "start_date", "value"],
    properties: {
      contract_id: { type: "string" }, title: { type: "string" },
      parties: { type: "array", items: { type: "string" } },
      value: { type: "number" }, currency: { type: "string", default: "INR" },
      start_date: { type: "string", format: "date" }, end_date: { type: "string", format: "date" },
      auto_renew: { type: "boolean", default: false },
      status: { type: "string", enum: ["draft", "active", "expired", "terminated"] },
    },
  },
  Campaign: {
    $schema: "https://json-schema.org/draft/2020-12/schema",
    title: "Campaign", type: "object", required: ["campaign_id", "name", "channel"],
    properties: {
      campaign_id: { type: "string" }, name: { type: "string" },
      channel: { type: "string", enum: ["email", "social", "search", "display", "sms"] },
      budget: { type: "number" }, spend: { type: "number" },
      impressions: { type: "integer" }, clicks: { type: "integer" }, conversions: { type: "integer" },
      start_date: { type: "string", format: "date" }, end_date: { type: "string", format: "date" },
      status: { type: "string", enum: ["draft", "active", "paused", "completed"] },
    },
  },
  Ticket: {
    $schema: "https://json-schema.org/draft/2020-12/schema",
    title: "Ticket", type: "object", required: ["ticket_id", "subject", "priority"],
    properties: {
      ticket_id: { type: "string" }, subject: { type: "string" }, description: { type: "string" },
      priority: { type: "string", enum: ["critical", "high", "normal", "low"] },
      category: { type: "string" }, assigned_to: { type: "string" },
      sla_deadline: { type: "string", format: "date-time" },
      status: { type: "string", enum: ["open", "in_progress", "waiting", "resolved", "closed"] },
    },
  },
  Vendor: {
    $schema: "https://json-schema.org/draft/2020-12/schema",
    title: "Vendor", type: "object", required: ["vendor_id", "name", "category"],
    properties: {
      vendor_id: { type: "string" }, name: { type: "string" }, category: { type: "string" },
      gst_number: { type: "string" }, pan_number: { type: "string" },
      payment_terms: { type: "string" }, rating: { type: "number", minimum: 0, maximum: 5 },
      status: { type: "string", enum: ["active", "under_review", "blacklisted", "inactive"] },
    },
  },
  Lead: {
    $schema: "https://json-schema.org/draft/2020-12/schema",
    title: "Lead", type: "object", required: ["lead_id", "contact_name", "source"],
    properties: {
      lead_id: { type: "string" }, contact_name: { type: "string" }, company: { type: "string" },
      email: { type: "string", format: "email" }, phone: { type: "string" },
      source: { type: "string", enum: ["website", "referral", "campaign", "cold_outreach", "event"] },
      score: { type: "integer", minimum: 0, maximum: 100 },
      status: { type: "string", enum: ["new", "contacted", "qualified", "proposal", "won", "lost"] },
    },
  },
  Product: {
    $schema: "https://json-schema.org/draft/2020-12/schema",
    title: "Product", type: "object", required: ["product_id", "name", "category"],
    properties: {
      product_id: { type: "string" }, name: { type: "string" }, category: { type: "string" },
      sku: { type: "string" }, price: { type: "number" }, hsn_code: { type: "string", description: "HSN/SAC code for GST" },
      stock_quantity: { type: "integer" },
      status: { type: "string", enum: ["active", "discontinued", "out_of_stock"] },
    },
  },
  Asset: {
    $schema: "https://json-schema.org/draft/2020-12/schema",
    title: "Asset", type: "object", required: ["asset_id", "name", "category"],
    properties: {
      asset_id: { type: "string" }, name: { type: "string" }, category: { type: "string" },
      serial_number: { type: "string" }, assigned_to: { type: "string" },
      purchase_date: { type: "string", format: "date" }, purchase_value: { type: "number" },
      depreciation_rate: { type: "number" },
      status: { type: "string", enum: ["active", "maintenance", "retired", "disposed"] },
    },
  },
  Incident: {
    $schema: "https://json-schema.org/draft/2020-12/schema",
    title: "Incident", type: "object", required: ["incident_id", "title", "severity"],
    properties: {
      incident_id: { type: "string" }, title: { type: "string" }, description: { type: "string" },
      severity: { type: "string", enum: ["critical", "high", "medium", "low"] },
      affected_systems: { type: "array", items: { type: "string" } },
      root_cause: { type: "string" }, resolution: { type: "string" },
      status: { type: "string", enum: ["detected", "investigating", "mitigated", "resolved", "postmortem"] },
    },
  },
  ComplianceFiling: {
    $schema: "https://json-schema.org/draft/2020-12/schema",
    title: "ComplianceFiling", type: "object", required: ["filing_id", "filing_type", "period", "due_date"],
    properties: {
      filing_id: { type: "string" },
      filing_type: { type: "string", enum: ["gstr1", "gstr3b", "tds_return", "epf_return", "esi_return", "roc_filing"] },
      period: { type: "string" }, due_date: { type: "string", format: "date" },
      filed_date: { type: "string", format: "date" }, acknowledgement_number: { type: "string" },
      status: { type: "string", enum: ["pending", "filed", "accepted", "rejected", "revised"] },
    },
  },
  JobRequisition: {
    $schema: "https://json-schema.org/draft/2020-12/schema",
    title: "JobRequisition", type: "object", required: ["req_id", "title", "department"],
    properties: {
      req_id: { type: "string" }, title: { type: "string" }, department: { type: "string" },
      positions: { type: "integer", minimum: 1 }, experience_range: { type: "string" },
      budget_ctc: { type: "number" }, hiring_manager: { type: "string" },
      status: { type: "string", enum: ["draft", "approved", "sourcing", "interviewing", "filled", "cancelled"] },
    },
  },
  JournalEntry: {
    $schema: "https://json-schema.org/draft/2020-12/schema",
    title: "JournalEntry", type: "object", required: ["entry_id", "date", "lines"],
    properties: {
      entry_id: { type: "string" }, date: { type: "string", format: "date" },
      description: { type: "string" }, reference: { type: "string" },
      lines: { type: "array", items: { type: "object", properties: { account: { type: "string" }, debit: { type: "number" }, credit: { type: "number" } } } },
      status: { type: "string", enum: ["draft", "posted", "reversed"] },
    },
  },
  PayrollRun: {
    $schema: "https://json-schema.org/draft/2020-12/schema",
    title: "PayrollRun", type: "object", required: ["run_id", "period", "employee_count"],
    properties: {
      run_id: { type: "string" }, period: { type: "string" },
      employee_count: { type: "integer" }, gross_total: { type: "number" }, net_total: { type: "number" },
      tds_total: { type: "number" }, epf_total: { type: "number" }, esi_total: { type: "number" },
      status: { type: "string", enum: ["draft", "computed", "approved", "paid", "filed"] },
    },
  },
  TrainingRecord: {
    $schema: "https://json-schema.org/draft/2020-12/schema",
    title: "TrainingRecord", type: "object", required: ["record_id", "employee_id", "program"],
    properties: {
      record_id: { type: "string" }, employee_id: { type: "string" }, program: { type: "string" },
      provider: { type: "string" }, start_date: { type: "string", format: "date" },
      completion_date: { type: "string", format: "date" }, score: { type: "number" },
      certificate_url: { type: "string" },
      status: { type: "string", enum: ["enrolled", "in_progress", "completed", "expired"] },
    },
  },
  CustomFieldsExtension: {
    $schema: "https://json-schema.org/draft/2020-12/schema",
    title: "CustomFieldsExtension", type: "object", description: "Extend any schema with tenant-specific custom fields",
    required: ["target_schema", "fields"],
    properties: {
      target_schema: { type: "string", description: "Name of the schema to extend" },
      fields: { type: "object", additionalProperties: { type: "object", properties: { type: { type: "string" }, required: { type: "boolean" }, label: { type: "string" } } } },
    },
  },
};

export default function Schemas() {
  const [schemas, setSchemas] = useState<SchemaEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedSchema, setSelectedSchema] = useState<string | null>(null);
  const [showEditor, setShowEditor] = useState(false);

  useEffect(() => {
    fetchSchemas();
  }, []);

  async function fetchSchemas() {
    setLoading(true);
    try {
      const { data } = await api.get("/schemas");
      const items = Array.isArray(data) ? data : Array.isArray(data?.items) ? data.items : [];
      setSchemas(items);
    } catch {
      setSchemas([]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-6">
      <Helmet><title>Schemas — AgenticOrg</title></Helmet>
      <div className="flex justify-between items-center">
        <h2 className="text-2xl font-bold">Schema Registry</h2>
        <Button onClick={() => { setSelectedSchema(null); setShowEditor(true); setTimeout(() => document.getElementById("schema-editor")?.scrollIntoView({ behavior: "smooth" }), 100); }}>Create Schema</Button>
      </div>

      <div className="grid grid-cols-4 gap-2">
        <Card>
          <CardHeader><CardTitle className="text-sm text-muted-foreground">Total Schemas</CardTitle></CardHeader>
          <CardContent><p className="text-3xl font-bold">{schemas.length || DEFAULT_SCHEMAS.length}</p></CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle className="text-sm text-muted-foreground">Platform Default</CardTitle></CardHeader>
          <CardContent><p className="text-3xl font-bold">18</p></CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle className="text-sm text-muted-foreground">Custom</CardTitle></CardHeader>
          <CardContent><p className="text-3xl font-bold">{Math.max(0, schemas.length - 18)}</p></CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle className="text-sm text-muted-foreground">Version</CardTitle></CardHeader>
          <CardContent><p className="text-3xl font-bold">v1</p></CardContent>
        </Card>
      </div>

      {loading ? (
        <p className="text-muted-foreground">Loading schemas...</p>
      ) : (
        <div className="grid grid-cols-3 gap-4">
          {(schemas.length > 0 ? schemas : DEFAULT_SCHEMAS.map((name) => ({ name, version: "1", is_default: true, field_count: 0, description: "" }))).map((schema) => (
            <Card
              key={schema.name}
              className="cursor-pointer hover:shadow-md transition-shadow"
              onClick={() => { setSelectedSchema(schema.name); setShowEditor(false); setTimeout(() => document.getElementById("schema-editor")?.scrollIntoView({ behavior: "smooth" }), 100); }}
            >
              <CardHeader>
                <div className="flex justify-between items-center">
                  <CardTitle className="text-base">{schema.name}</CardTitle>
                  <Badge variant={schema.is_default ? "secondary" : "default"}>
                    {schema.is_default ? "Default" : "Custom"}
                  </Badge>
                </div>
              </CardHeader>
              <CardContent>
                <div className="text-sm text-muted-foreground">
                  Version: {schema.version}
                  {schema.description && <p className="mt-1">{schema.description}</p>}
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {(showEditor || selectedSchema) && (
        <Card id="schema-editor">
          <CardHeader>
            <div className="flex justify-between items-center">
              <CardTitle>{selectedSchema ? `Edit: ${selectedSchema}` : "New Schema"}</CardTitle>
              <Button variant="outline" size="sm" onClick={() => { setShowEditor(false); setSelectedSchema(null); }}>Close</Button>
            </div>
          </CardHeader>
          <CardContent>
            <SchemaEditor schema={selectedSchema ? SCHEMA_DEFINITIONS[selectedSchema] : { $schema: "https://json-schema.org/draft/2020-12/schema", title: "NewSchema", type: "object", required: [], properties: {} }} />
          </CardContent>
        </Card>
      )}
    </div>
  );
}
