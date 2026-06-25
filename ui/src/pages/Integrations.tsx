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
          Connect SDKs, external agents, MCP clients, connectors, workflows, and knowledge.
        </p>
      </div>

      {loading ? (
        <p className="text-muted-foreground">Loading...</p>
      ) : (
        <>
          <Card>
            <CardHeader>
              <div className="flex justify-between items-center gap-3">
                <CardTitle>Python SDK</CardTitle>
                <Badge>pip install agenticorg</Badge>
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              <div>
                <label className="text-xs text-muted-foreground uppercase tracking-wide">Launch agents, commerce, KB, and workflows</label>
                <pre className="bg-muted rounded p-3 text-sm font-mono mt-1 whitespace-pre-wrap" data-testid="sdk-snippet-python">{`from agenticorg import AgenticOrg, AgentRunResult

client = AgenticOrg(api_key="your-key")

result: AgentRunResult = client.agents.run(
    "ap_processor",
    inputs={"invoice_id": "INV-001"},
)
print(result.status, result.confidence, result.output)

commerce: AgentRunResult = client.agents.run(
    "commerce_sales_agent",
    action="buyer_discovery_preview",
    inputs={
        "merchant_id": "merchant_demo",
        "query": "Show available laptop stands under Rs 3000",
    },
)

draft_agent = client.agents.generate(
    "Create a contract intelligence agent that uses Confluence knowledge, "
    "Jira issues, and vendor policy documents to review renewal risk.",
)

matches = client.knowledge.search("vendor renewal policy", top_k=3)
workflow_draft = client.workflows.generate(
    "Review vendor renewal risk using KB and Jira, then notify vendor_manager."
)
workflow = client.workflows.create(
    name="Renewal Risk Review",
    definition=workflow_draft["workflow"],
    domain="ops",
)
run = client.workflows.run(workflow["id"], payload={"vendor_id": "V-100"})`}</pre>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <div className="flex justify-between items-center gap-3">
                <CardTitle>TypeScript SDK</CardTitle>
                <Badge variant="secondary">npm i agenticorg-sdk</Badge>
              </div>
            </CardHeader>
            <CardContent>
              <pre className="bg-muted rounded p-3 text-sm font-mono whitespace-pre-wrap" data-testid="sdk-snippet-typescript">{`import { AgenticOrg } from "agenticorg-sdk";

const client = new AgenticOrg({ apiKey: "your-key" });

const result = await client.agents.run("ap_processor", {
  inputs: { invoice_id: "INV-001" },
});

const commerce = await client.agents.run("commerce_sales_agent", {
  action: "buyer_discovery_preview",
  inputs: {
    merchant_id: "merchant_demo",
    query: "Show available laptop stands under Rs 3000",
  },
});

const draftAgent = await client.agents.generate(
  "Create a contract intelligence agent using Confluence, Jira, and vendor policy KB.",
);

const kb = await client.knowledge.search("vendor renewal policy", { topK: 3 });
const workflowDraft = await client.workflows.generate(
  "Review vendor renewal risk using KB and Jira, then notify vendor_manager.",
);
const workflow = await client.workflows.create({
  name: "Renewal Risk Review",
  definition: workflowDraft.workflow,
  domain: "ops",
});
const run = await client.workflows.run(workflow.id, {
  payload: { vendor_id: "V-100" },
});`}</pre>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <div className="flex justify-between items-center gap-3">
                <CardTitle>CLI Tool</CardTitle>
                <Badge variant="secondary">Terminal</Badge>
              </div>
            </CardHeader>
            <CardContent className="space-y-3">
              <pre className="bg-muted rounded p-3 text-sm font-mono whitespace-pre-wrap">{`pip install agenticorg
export AGENTICORG_API_KEY=your-key

agenticorg agents list --domain finance
agenticorg agents run ap_processor --input '{"invoice_id": "INV-001"}'
agenticorg workflows run wf-123 --input '{"vendor_id":"V-100"}'
agenticorg sop parse --file invoice_sop.pdf --domain finance
agenticorg a2a card
agenticorg mcp tools`}</pre>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <div className="flex justify-between items-center gap-3">
                <CardTitle>Agent-to-Agent (A2A) Protocol</CardTitle>
                <Badge>Grantex Auth</Badge>
              </div>
            </CardHeader>
            <CardContent className="space-y-3">
              <p className="text-sm text-muted-foreground">
                External buyer agents discover launchable AgenticOrg seller agents through A2A.
              </p>

              <pre className="bg-muted rounded p-3 text-sm font-mono whitespace-pre-wrap">{`agents = client.a2a.agents()
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

          <Card>
            <CardHeader>
              <div className="flex justify-between items-center gap-3">
                <CardTitle>Model Context Protocol (MCP)</CardTitle>
                <Badge variant="secondary">ChatGPT / Claude</Badge>
              </div>
            </CardHeader>
            <CardContent className="space-y-3">
              <p className="text-sm text-muted-foreground">
                MCP-compatible clients can discover AgenticOrg agents and call them as tools.
              </p>

              <pre className="bg-muted rounded p-3 text-sm font-mono whitespace-pre-wrap">{`tools = client.mcp.tools()  # ${mcpTools.length} tools

result = client.mcp.call("agenticorg_commerce_sales_agent", {
    "inputs": {
        "merchant_id": "merchant_demo",
        "query": "Show available laptop stands under Rs 3000",
    }
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

          <Card>
            <CardHeader><CardTitle>Authentication</CardTitle></CardHeader>
            <CardContent className="space-y-3 text-sm">
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                <div className="border rounded p-3">
                  <p className="font-medium">API Key</p>
                  <p className="text-xs text-muted-foreground mt-1">For dashboard users.</p>
                  <code className="text-xs bg-muted px-1 rounded mt-1 block">AgenticOrg(api_key="...")</code>
                </div>
                <div className="border rounded p-3">
                  <p className="font-medium">Grantex Token</p>
                  <p className="text-xs text-muted-foreground mt-1">For scoped external agents.</p>
                  <code className="text-xs bg-muted px-1 rounded mt-1 block">AgenticOrg(grantex_token="...")</code>
                </div>
                <div className="border rounded p-3">
                  <p className="font-medium">Environment</p>
                  <p className="text-xs text-muted-foreground mt-1">Detected by SDKs and MCP server.</p>
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
