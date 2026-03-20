import { useState, useEffect } from "react";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import SchemaEditor from "@/components/SchemaEditor";

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
      const resp = await fetch("/api/v1/schemas");
      const data = await resp.json();
      setSchemas(data.items || []);
    } catch {
      setSchemas([]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <h2 className="text-2xl font-bold">Schema Registry</h2>
        <Button onClick={() => setShowEditor(true)}>Create Schema</Button>
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
          {(schemas.length > 0 ? schemas : DEFAULT_SCHEMAS.map((name, i) => ({ name, version: "1", is_default: true, field_count: 0, description: "" }))).map((schema) => (
            <Card
              key={schema.name}
              className="cursor-pointer hover:shadow-md transition-shadow"
              onClick={() => setSelectedSchema(schema.name)}
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
        <Card>
          <CardHeader>
            <div className="flex justify-between items-center">
              <CardTitle>{selectedSchema ? `Edit: ${selectedSchema}` : "New Schema"}</CardTitle>
              <Button variant="outline" size="sm" onClick={() => { setShowEditor(false); setSelectedSchema(null); }}>Close</Button>
            </div>
          </CardHeader>
          <CardContent>
            <SchemaEditor />
          </CardContent>
        </Card>
      )}
    </div>
  );
}
