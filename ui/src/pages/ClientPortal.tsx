import { useEffect, useState } from "react";
import { FileText, KeyRound, RefreshCw, Upload } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import api, { extractApiError } from "@/lib/api";

interface Invite {
  id: string;
  company_id: string;
  client_email: string;
  client_name?: string;
  status: string;
  expires_at: string;
  invite_token?: string;
}

interface PortalDocument {
  id: string;
  title: string;
  document_type: string;
  filing_period?: string;
  status: string;
  visible_to_client: boolean;
}

export default function ClientPortal() {
  const [companyId, setCompanyId] = useState("");
  const [clientEmail, setClientEmail] = useState("");
  const [clientName, setClientName] = useState("");
  const [documentTitle, setDocumentTitle] = useState("");
  const [documentType, setDocumentType] = useState("compliance_report");
  const [filingPeriod, setFilingPeriod] = useState("2026-06");
  const [invites, setInvites] = useState<Invite[]>([]);
  const [documents, setDocuments] = useState<PortalDocument[]>([]);
  const [latestToken, setLatestToken] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  const load = async () => {
    setError("");
    try {
      const params = companyId ? { company_id: companyId } : undefined;
      const [inviteRes, docRes] = await Promise.all([
        api.get("/client-portal/invites", { params }).catch(() => ({ data: [] })),
        api.get("/client-portal/documents", { params }).catch(() => ({ data: [] })),
      ]);
      setInvites(Array.isArray(inviteRes.data) ? inviteRes.data : []);
      setDocuments(Array.isArray(docRes.data) ? docRes.data : []);
    } catch (err) {
      setError(extractApiError(err, "Failed to load client portal data."));
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const createInvite = async () => {
    setBusy("invite");
    setError("");
    setLatestToken("");
    try {
      const { data } = await api.post("/client-portal/invites", {
        company_id: companyId,
        client_email: clientEmail,
        client_name: clientName || undefined,
        expires_days: 14,
      });
      setLatestToken(data.invite_token || "");
      await load();
    } catch (err) {
      setError(extractApiError(err, "Failed to create client portal invite."));
    } finally {
      setBusy("");
    }
  };

  const publishDocument = async () => {
    setBusy("document");
    setError("");
    try {
      await api.post("/client-portal/documents", {
        company_id: companyId,
        title: documentTitle,
        document_type: documentType,
        filing_period: filingPeriod,
        visible_to_client: true,
      });
      setDocumentTitle("");
      await load();
    } catch (err) {
      setError(extractApiError(err, "Failed to publish portal document."));
    } finally {
      setBusy("");
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h2 className="text-2xl font-bold">Client Portal</h2>
          <p className="text-sm text-muted-foreground">Invite clients and publish compliance documents safely.</p>
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
            <CardTitle className="text-lg">Invite Client</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <input className="h-9 w-full rounded-md border px-3 text-sm" placeholder="Company ID" value={companyId} onChange={(e) => setCompanyId(e.target.value)} />
            <div className="grid gap-3 md:grid-cols-2">
              <input className="h-9 rounded-md border px-3 text-sm" placeholder="Client email" value={clientEmail} onChange={(e) => setClientEmail(e.target.value)} />
              <input className="h-9 rounded-md border px-3 text-sm" placeholder="Client name" value={clientName} onChange={(e) => setClientName(e.target.value)} />
            </div>
            <Button disabled={!companyId || !clientEmail || busy === "invite"} onClick={createInvite}>
              <KeyRound className="mr-2 h-4 w-4" />
              {busy === "invite" ? "Creating..." : "Create Invite"}
            </Button>
            {latestToken && (
              <textarea readOnly className="min-h-24 w-full rounded-md border bg-muted px-3 py-2 font-mono text-xs" value={latestToken} data-testid="client-portal-token" />
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-lg">Publish Document</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <input className="h-9 w-full rounded-md border px-3 text-sm" placeholder="Document title" value={documentTitle} onChange={(e) => setDocumentTitle(e.target.value)} />
            <div className="grid gap-3 md:grid-cols-2">
              <select className="h-9 rounded-md border px-3 text-sm" value={documentType} onChange={(e) => setDocumentType(e.target.value)}>
                <option value="compliance_report">Compliance report</option>
                <option value="tax_challan">Tax challan</option>
                <option value="ca_client_invoice">Client invoice</option>
                <option value="working_papers">Working papers</option>
              </select>
              <input className="h-9 rounded-md border px-3 text-sm" placeholder="Filing period" value={filingPeriod} onChange={(e) => setFilingPeriod(e.target.value)} />
            </div>
            <Button variant="outline" disabled={!companyId || !documentTitle || busy === "document"} onClick={publishDocument}>
              <Upload className="mr-2 h-4 w-4" />
              {busy === "document" ? "Publishing..." : "Publish"}
            </Button>
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-lg">Invites</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {invites.length === 0 && <p className="text-sm text-muted-foreground">No invites yet.</p>}
            {invites.slice(0, 8).map((item) => (
              <div key={item.id} className="rounded-md border px-3 py-2 text-sm">
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate font-medium">{item.client_email}</span>
                  <Badge variant={item.status === "accepted" ? "success" : "secondary"}>{item.status}</Badge>
                </div>
                <p className="mt-1 truncate text-xs text-muted-foreground">{item.client_name || "Unnamed client"} &middot; expires {item.expires_at.slice(0, 10)}</p>
              </div>
            ))}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-lg">Documents</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {documents.length === 0 && <p className="text-sm text-muted-foreground">No client-visible documents yet.</p>}
            {documents.slice(0, 8).map((item) => (
              <div key={item.id} className="flex items-center justify-between gap-3 rounded-md border px-3 py-2 text-sm">
                <div className="min-w-0">
                  <p className="truncate font-medium"><FileText className="mr-1 inline h-4 w-4" />{item.title}</p>
                  <p className="truncate text-xs text-muted-foreground">{item.document_type} {item.filing_period || ""}</p>
                </div>
                <Badge variant={item.visible_to_client ? "success" : "warning"}>{item.status}</Badge>
              </div>
            ))}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
