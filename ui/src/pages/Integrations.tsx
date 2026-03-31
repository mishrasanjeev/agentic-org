import { useState, useEffect } from "react";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

export default function Integrations() {
  const [agentCard, setAgentCard] = useState<any>(null);
  const [mcpTools, setMcpTools] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.allSettled([
      fetch("/api/v1/a2a/agent-card").then((r) => r.json()),
      fetch("/api/v1/mcp/tools").then((r) => r.json()),
    ]).then(([cardResult, toolsResult]) => {
      if (cardResult.status === "fulfilled") setAgentCard(cardResult.value);
      if (toolsResult.status === "fulfilled") setMcpTools(toolsResult.value.tools || []);
      setLoading(false);
    });
  }, []);

  const baseUrl = window.location.origin;

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-bold">External Integrations</h2>
        <p className="text-sm text-muted-foreground mt-1">
          Connect external AI systems to your agents via A2A or MCP protocols
        </p>
      </div>

      {loading ? (
        <p className="text-muted-foreground">Loading...</p>
      ) : (
        <>
          {/* A2A Section */}
          <Card>
            <CardHeader>
              <div className="flex justify-between items-center">
                <CardTitle>Agent-to-Agent (A2A) Protocol</CardTitle>
                <Badge>Grantex Auth</Badge>
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              <p className="text-sm text-muted-foreground">
                Other AI agents and systems can discover and call your agents using the A2A protocol.
                Authentication uses Grantex grant tokens (RS256 JWT).
              </p>

              <div className="space-y-3">
                <div>
                  <label className="text-xs text-muted-foreground uppercase tracking-wide">Agent Discovery URL</label>
                  <div className="flex items-center gap-2 mt-1">
                    <code className="bg-muted px-3 py-2 rounded text-sm font-mono flex-1 break-all">
                      {baseUrl}/api/v1/a2a/.well-known/agent.json
                    </code>
                    <button onClick={() => navigator.clipboard.writeText(`${baseUrl}/api/v1/a2a/.well-known/agent.json`)} className="text-xs text-primary hover:underline flex-shrink-0">
                      Copy
                    </button>
                  </div>
                </div>

                <div>
                  <label className="text-xs text-muted-foreground uppercase tracking-wide">Task Execution URL</label>
                  <code className="bg-muted px-3 py-2 rounded text-sm font-mono block mt-1">
                    POST {baseUrl}/api/v1/a2a/tasks
                  </code>
                </div>

                {agentCard && (
                  <div>
                    <label className="text-xs text-muted-foreground uppercase tracking-wide">Available Skills ({agentCard.skills?.length || 0})</label>
                    <div className="flex flex-wrap gap-1.5 mt-2">
                      {(agentCard.skills || []).map((s: any) => (
                        <Badge key={s.id} variant="outline" className="text-xs">{s.name}</Badge>
                      ))}
                    </div>
                  </div>
                )}

                <div className="bg-muted/50 rounded-lg p-4 text-sm">
                  <p className="font-medium mb-2">Example: Call AP Processor via A2A</p>
                  <pre className="text-xs font-mono whitespace-pre-wrap">{`curl -X POST ${baseUrl}/api/v1/a2a/tasks \\
  -H "Authorization: Bearer <grantex_grant_token>" \\
  -H "Content-Type: application/json" \\
  -d '{
    "agent_type": "ap_processor",
    "action": "process",
    "inputs": {"invoice_id": "INV-001", "vendor_id": "V-100"}
  }'`}</pre>
                </div>
              </div>
            </CardContent>
          </Card>

          {/* MCP Section */}
          <Card>
            <CardHeader>
              <div className="flex justify-between items-center">
                <CardTitle>Model Context Protocol (MCP)</CardTitle>
                <Badge variant="secondary">ChatGPT / Claude</Badge>
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              <p className="text-sm text-muted-foreground">
                ChatGPT, Claude, and other MCP-compatible AI interfaces can use your agents as tools.
              </p>

              <div className="space-y-3">
                <div>
                  <label className="text-xs text-muted-foreground uppercase tracking-wide">Tool Discovery URL</label>
                  <div className="flex items-center gap-2 mt-1">
                    <code className="bg-muted px-3 py-2 rounded text-sm font-mono flex-1 break-all">
                      {baseUrl}/api/v1/mcp/tools
                    </code>
                    <button onClick={() => navigator.clipboard.writeText(`${baseUrl}/api/v1/mcp/tools`)} className="text-xs text-primary hover:underline flex-shrink-0">
                      Copy
                    </button>
                  </div>
                </div>

                <div>
                  <label className="text-xs text-muted-foreground uppercase tracking-wide">Tool Call URL</label>
                  <code className="bg-muted px-3 py-2 rounded text-sm font-mono block mt-1">
                    POST {baseUrl}/api/v1/mcp/call
                  </code>
                </div>

                <div>
                  <label className="text-xs text-muted-foreground uppercase tracking-wide">Available Tools ({mcpTools.length})</label>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 mt-2">
                    {mcpTools.slice(0, 10).map((t: any) => (
                      <div key={t.name} className="border rounded p-2 text-xs">
                        <span className="font-mono font-medium">{t.name}</span>
                        <p className="text-muted-foreground mt-0.5 line-clamp-2">{t.description}</p>
                      </div>
                    ))}
                    {mcpTools.length > 10 && (
                      <div className="border rounded p-2 text-xs text-muted-foreground flex items-center justify-center">
                        +{mcpTools.length - 10} more tools
                      </div>
                    )}
                  </div>
                </div>
              </div>
            </CardContent>
          </Card>

          {/* Grantex Auth Section */}
          <Card>
            <CardHeader><CardTitle>Authentication — Grantex</CardTitle></CardHeader>
            <CardContent className="space-y-3 text-sm">
              <p className="text-muted-foreground">
                All external API calls require a Grantex grant token (RS256 JWT) with the appropriate scopes.
                Each agent auto-registers on Grantex at creation time with a unique DID.
              </p>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="text-xs text-muted-foreground">JWKS URL</label>
                  <code className="bg-muted px-2 py-1 rounded text-xs font-mono block mt-1 break-all">
                    {agentCard?.authentication?.jwksUri || "https://api.grantex.dev/.well-known/jwks.json"}
                  </code>
                </div>
                <div>
                  <label className="text-xs text-muted-foreground">Auth Scheme</label>
                  <p className="font-medium mt-1">Grantex RS256 Grant Token</p>
                </div>
                <div>
                  <label className="text-xs text-muted-foreground">Required Scope</label>
                  <code className="bg-muted px-2 py-1 rounded text-xs font-mono block mt-1">agenticorg:agents:execute</code>
                </div>
                <div>
                  <label className="text-xs text-muted-foreground">Delegation</label>
                  <p className="font-medium mt-1">Supported (parent → child scope subset)</p>
                </div>
              </div>
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}
