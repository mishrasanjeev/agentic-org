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

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-bold">External Integrations</h2>
        <p className="text-sm text-muted-foreground mt-1">
          Connect external AI systems to your agents via SDK, CLI, A2A, or MCP
        </p>
      </div>

      {loading ? (
        <p className="text-muted-foreground">Loading...</p>
      ) : (
        <>
          {/* SDK Quickstart */}
          <Card>
            <CardHeader>
              <div className="flex justify-between items-center">
                <CardTitle>Python SDK</CardTitle>
                <Badge>pip install agenticorg</Badge>
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              <p className="text-sm text-muted-foreground">
                The fastest way to run agents, create from SOP, and manage connectors programmatically.
              </p>

              <div>
                <label className="text-xs text-muted-foreground uppercase tracking-wide">Install</label>
                <pre className="bg-muted rounded p-3 text-sm font-mono mt-1">pip install agenticorg</pre>
              </div>

              <div>
                <label className="text-xs text-muted-foreground uppercase tracking-wide">Run an Agent (3 lines)</label>
                <pre className="bg-muted rounded p-3 text-sm font-mono mt-1 whitespace-pre-wrap" data-testid="sdk-snippet-python">{`from agenticorg import AgenticOrg, AgentRunResult

client = AgenticOrg(api_key="your-key")
result: AgentRunResult = client.agents.run(
    "ap_processor",
    inputs={"invoice_id": "INV-001"},
)
print(result.status, result.confidence, result.output)`}</pre>
              </div>

              <div>
                <label className="text-xs text-muted-foreground uppercase tracking-wide">Create Agent from SOP (5 lines)</label>
                <pre className="bg-muted rounded p-3 text-sm font-mono mt-1 whitespace-pre-wrap">{`draft = client.sop.parse_text("""
Step 1: Receive invoice from vendor
Step 2: Validate GSTIN
Step 3: 3-way match with PO
Step 4: If amount > 5L, escalate to CFO
""", domain_hint="finance")

agent = client.sop.deploy(draft["config"])
print(agent["agent_id"])  # deployed as shadow agent`}</pre>
              </div>
            </CardContent>
          </Card>

          {/* CLI */}
          <Card>
            <CardHeader>
              <div className="flex justify-between items-center">
                <CardTitle>CLI Tool</CardTitle>
                <Badge variant="secondary">Terminal</Badge>
              </div>
            </CardHeader>
            <CardContent className="space-y-3">
              <pre className="bg-muted rounded p-3 text-sm font-mono whitespace-pre-wrap">{`# Set API key
export AGENTICORG_API_KEY=your-key

# List agents
agenticorg agents list --domain finance

# Run an agent
agenticorg agents run ap_processor --input '{"invoice_id": "INV-001"}'

# Parse SOP document
agenticorg sop parse --file invoice_sop.pdf --domain finance

# View A2A agent card
agenticorg a2a card

# List MCP tools (for ChatGPT/Claude)
agenticorg mcp tools`}</pre>
            </CardContent>
          </Card>

          {/* A2A Protocol */}
          <Card>
            <CardHeader>
              <div className="flex justify-between items-center">
                <CardTitle>Agent-to-Agent (A2A) Protocol</CardTitle>
                <Badge>Grantex Auth</Badge>
              </div>
            </CardHeader>
            <CardContent className="space-y-3">
              <p className="text-sm text-muted-foreground">
                Other AI agents discover and call your agents via A2A. Auth uses Grantex grant tokens.
              </p>

              <pre className="bg-muted rounded p-3 text-sm font-mono whitespace-pre-wrap">{`# Discover agents (SDK)
agents = client.a2a.agents()

# Or via A2A protocol directly
card = client.a2a.agent_card()  # ${agentCard?.skills?.length || 25} skills available`}</pre>

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
            </CardContent>
          </Card>

          {/* MCP */}
          <Card>
            <CardHeader>
              <div className="flex justify-between items-center">
                <CardTitle>Model Context Protocol (MCP)</CardTitle>
                <Badge variant="secondary">ChatGPT / Claude</Badge>
              </div>
            </CardHeader>
            <CardContent className="space-y-3">
              <p className="text-sm text-muted-foreground">
                ChatGPT, Claude, and MCP-compatible interfaces use your agents as tools.
              </p>

              <pre className="bg-muted rounded p-3 text-sm font-mono whitespace-pre-wrap">{`# List tools (SDK)
tools = client.mcp.tools()  # ${mcpTools.length} tools

# Call a tool
result = client.mcp.call("agenticorg_ap_processor", {
    "inputs": {"invoice_id": "INV-001"}
})`}</pre>

              <div>
                <label className="text-xs text-muted-foreground uppercase tracking-wide">Tools ({mcpTools.length})</label>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 mt-2">
                  {mcpTools.slice(0, 6).map((t: any) => (
                    <div key={t.name} className="border rounded p-2 text-xs">
                      <span className="font-mono font-medium">{t.name}</span>
                    </div>
                  ))}
                  {mcpTools.length > 6 && (
                    <div className="border rounded p-2 text-xs text-muted-foreground flex items-center justify-center">
                      +{mcpTools.length - 6} more tools
                    </div>
                  )}
                </div>
              </div>
            </CardContent>
          </Card>

          {/* Auth */}
          <Card>
            <CardHeader><CardTitle>Authentication</CardTitle></CardHeader>
            <CardContent className="space-y-3 text-sm">
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                <div className="border rounded p-3">
                  <p className="font-medium">API Key</p>
                  <p className="text-xs text-muted-foreground mt-1">For dashboard users. Get from Settings page.</p>
                  <code className="text-xs bg-muted px-1 rounded mt-1 block">AgenticOrg(api_key="...")</code>
                </div>
                <div className="border rounded p-3">
                  <p className="font-medium">Grantex Token</p>
                  <p className="text-xs text-muted-foreground mt-1">For external agents. RS256 JWT with scoped grants.</p>
                  <code className="text-xs bg-muted px-1 rounded mt-1 block">AgenticOrg(grantex_token="...")</code>
                </div>
                <div className="border rounded p-3">
                  <p className="font-medium">Environment</p>
                  <p className="text-xs text-muted-foreground mt-1">Auto-detected from env vars.</p>
                  <code className="text-xs bg-muted px-1 rounded mt-1 block">AGENTICORG_API_KEY=...</code>
                </div>
              </div>
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}
