import { useState } from "react";
import { Link } from "react-router-dom";
import { Helmet } from "react-helmet-async";

/* ── Step data for the workflow ── */
const WORKFLOW_STEPS = [
  {
    id: 1,
    actor: "User",
    action: "Asks ChatGPT to buy something",
    detail: '"Hey ChatGPT, find me the best wireless earbuds under $50 on Amazon and order the top-rated one."',
    color: "bg-blue-500",
    icon: (
      <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
      </svg>
    ),
  },
  {
    id: 2,
    actor: "ChatGPT",
    action: "Discovers AgenticOrg via MCP",
    detail: "ChatGPT detects the agenticorg MCP server in its config. Calls list_mcp_tools to discover available capabilities. Finds shopping_agent with product search and order tools.",
    color: "bg-emerald-500",
    icon: (
      <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
      </svg>
    ),
  },
  {
    id: 3,
    actor: "ChatGPT",
    action: "Calls run_agent via MCP",
    detail: 'MCP call: run_agent({ agent_type: "shopping_agent", inputs: { query: "wireless earbuds under $50", marketplace: "amazon", action: "search_and_order" } })',
    color: "bg-purple-500",
    icon: (
      <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" />
      </svg>
    ),
  },
  {
    id: 4,
    actor: "AgenticOrg",
    action: "Authenticates via API Key / Grantex",
    detail: "The MCP server sends the API key (ao_sk_...) as a Bearer token. Auth middleware validates the key against the database, extracts tenant_id and scopes, and authorizes the request.",
    color: "bg-orange-500",
    icon: (
      <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
      </svg>
    ),
  },
  {
    id: 5,
    actor: "Shopping Agent",
    action: "Executes LangGraph workflow",
    detail: "The Shopping Agent (LangGraph) runs a multi-step graph:\n1. Search products via Amazon connector tool\n2. Rank by ratings, price, reviews\n3. Select top match\n4. Prepare order payload\n5. Check confidence score (0.92 > 0.88 floor)\n6. Triggers HITL approval (order > $20 threshold)",
    color: "bg-red-500",
    icon: (
      <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
      </svg>
    ),
  },
  {
    id: 6,
    actor: "AgenticOrg",
    action: "HITL approval request",
    detail: "Order exceeds auto-approve threshold. AgenticOrg sends an approval request:\n- Slack notification to the user\n- Dashboard approval card\n- Email with one-click Approve/Reject\n\nThe user sees: 'Shopping Agent wants to order Sony WF-C500 ($39.99) — Approve?'",
    color: "bg-yellow-500",
    icon: (
      <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
      </svg>
    ),
  },
  {
    id: 7,
    actor: "User",
    action: "Approves the order",
    detail: "User clicks 'Approve' on Slack or in the dashboard. The HITL queue entry is marked as approved. The Shopping Agent resumes execution.",
    color: "bg-blue-500",
    icon: (
      <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M14 10h4.764a2 2 0 011.789 2.894l-3.5 7A2 2 0 0115.263 21h-4.017c-.163 0-.326-.02-.485-.06L7 20m7-10V5a2 2 0 00-2-2h-.095c-.5 0-.905.405-.905.905 0 .714-.211 1.412-.608 2.006L7 11v9m7-10h-2M7 20H5a2 2 0 01-2-2v-6a2 2 0 012-2h2.5" />
      </svg>
    ),
  },
  {
    id: 8,
    actor: "Shopping Agent",
    action: "Places the order & returns result",
    detail: "Agent calls the Amazon connector's place_order tool. Order confirmed: #AMZ-2026-78543.\n\nResult sent back through MCP → ChatGPT → User:\n'Ordered Sony WF-C500 Wireless Earbuds ($39.99) — 4.4 stars, 12K+ reviews. Order #AMZ-2026-78543. Arrives in 2 days.'",
    color: "bg-emerald-500",
    icon: (
      <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M5 13l4 4L19 7" />
      </svg>
    ),
  },
];

const ARCHITECTURE_LAYERS = [
  { label: "User", desc: "ChatGPT, Claude, Cursor, Custom App", color: "bg-blue-100 border-blue-300 text-blue-800" },
  { label: "MCP Client", desc: "Model Context Protocol transport (stdio/SSE)", color: "bg-emerald-100 border-emerald-300 text-emerald-800" },
  { label: "AgenticOrg MCP Server", desc: "agenticorg-mcp-server (npx)", color: "bg-purple-100 border-purple-300 text-purple-800" },
  { label: "Auth Layer", desc: "API Key (ao_sk_) / Grantex Token / JWT", color: "bg-orange-100 border-orange-300 text-orange-800" },
  { label: "Agent Runtime", desc: "LangGraph executor + Gemini LLM", color: "bg-red-100 border-red-300 text-red-800" },
  { label: "HITL Gateway", desc: "Human approval via Slack / Email / Dashboard", color: "bg-yellow-100 border-yellow-300 text-yellow-800" },
  { label: "Connectors", desc: "Native connectors + Composio (Amazon, Jira, SAP...)", color: "bg-indigo-100 border-indigo-300 text-indigo-800" },
];

export default function IntegrationWorkflow() {
  const [activeStep, setActiveStep] = useState(0);

  return (
    <div className="min-h-screen bg-white">
      <Helmet>
        <title>Integration Workflow — How ChatGPT Launches an AgenticOrg Shopping Agent</title>
        <meta name="description" content="Step-by-step workflow: how a third-party app like ChatGPT launches a shopping agent on AgenticOrg via MCP, with HITL approval and real order placement." />
      </Helmet>

      {/* Nav */}
      <nav className="bg-slate-900 border-b border-slate-700/50">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
          <Link to="/" className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-blue-500 to-teal-500 flex items-center justify-center text-white font-bold text-sm">AO</div>
            <span className="text-white font-semibold text-lg">AgenticOrg</span>
          </Link>
          <div className="flex items-center gap-4">
            <a href="/#developers" className="text-slate-300 hover:text-white text-sm">Developers</a>
            <Link to="/signup" className="bg-gradient-to-r from-blue-500 to-cyan-500 text-white px-5 py-2 rounded-lg text-sm font-medium hover:from-blue-600 hover:to-cyan-600 transition-all">Get API Key</Link>
          </div>
        </div>
      </nav>

      {/* Hero */}
      <section className="bg-gradient-to-br from-slate-900 via-slate-800 to-teal-900 py-20">
        <div className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 text-center">
          <div className="inline-flex items-center gap-2 bg-slate-800/80 border border-slate-700 rounded-full px-4 py-1.5 mb-6">
            <span className="w-2 h-2 bg-emerald-400 rounded-full animate-pulse" />
            <span className="text-slate-300 text-sm">End-to-End Integration Example</span>
          </div>
          <h1 className="text-3xl sm:text-5xl font-extrabold text-white leading-tight">
            How ChatGPT Launches a{" "}
            <span className="bg-gradient-to-r from-blue-400 via-purple-400 to-emerald-400 bg-clip-text text-transparent">
              Shopping Agent
            </span>{" "}
            on AgenticOrg
          </h1>
          <p className="mt-6 text-lg text-slate-400 max-w-2xl mx-auto">
            A user asks ChatGPT to buy earbuds. ChatGPT discovers AgenticOrg via MCP, launches a Shopping Agent, gets HITL approval, and places the order — all in one conversation.
          </p>
        </div>
      </section>

      {/* Architecture Diagram */}
      <section className="py-16 bg-slate-50">
        <div className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8">
          <h2 className="text-2xl font-bold text-slate-900 text-center mb-8">Architecture Stack</h2>
          <div className="space-y-3">
            {ARCHITECTURE_LAYERS.map((layer, i) => (
              <div key={layer.label} className="flex items-center gap-4">
                <div className="w-8 text-center text-sm font-bold text-slate-400">{i + 1}</div>
                <div className={`flex-1 border rounded-lg px-5 py-3 flex items-center justify-between ${layer.color}`}>
                  <span className="font-semibold">{layer.label}</span>
                  <span className="text-sm opacity-80">{layer.desc}</span>
                </div>
                {i < ARCHITECTURE_LAYERS.length - 1 && (
                  <div className="w-8" />
                )}
              </div>
            ))}
          </div>
          <div className="flex justify-center mt-4">
            <div className="flex items-center gap-2 text-sm text-slate-500">
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 14l-7 7m0 0l-7-7m7 7V3" /></svg>
              Data flows top-to-bottom, results return bottom-to-top
            </div>
          </div>
        </div>
      </section>

      {/* Step-by-Step Workflow */}
      <section className="py-20">
        <div className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8">
          <h2 className="text-2xl font-bold text-slate-900 text-center mb-4">Step-by-Step Workflow</h2>
          <p className="text-slate-500 text-center mb-12 max-w-xl mx-auto">Click any step to see details. This is the exact flow that happens when a third-party app runs an agent on your behalf.</p>

          <div className="relative">
            {/* Timeline line */}
            <div className="absolute left-8 top-0 bottom-0 w-0.5 bg-slate-200 hidden md:block" />

            <div className="space-y-6">
              {WORKFLOW_STEPS.map((step, i) => (
                <div
                  key={step.id}
                  className={`relative flex gap-6 cursor-pointer transition-all duration-300 ${activeStep === i ? "scale-[1.01]" : "hover:scale-[1.005]"}`}
                  onClick={() => setActiveStep(i)}
                >
                  {/* Timeline dot */}
                  <div className={`relative z-10 w-16 h-16 rounded-full ${step.color} flex items-center justify-center text-white flex-shrink-0 shadow-lg transition-all duration-300 ${activeStep === i ? "ring-4 ring-offset-2 ring-blue-300" : ""}`}>
                    {step.icon}
                  </div>

                  {/* Content */}
                  <div className={`flex-1 rounded-xl border p-5 transition-all duration-300 ${activeStep === i ? "bg-white border-blue-200 shadow-lg" : "bg-slate-50 border-slate-200"}`}>
                    <div className="flex items-center gap-3 mb-2">
                      <span className={`text-xs font-bold px-2.5 py-0.5 rounded-full ${step.color} text-white`}>Step {step.id}</span>
                      <span className="text-xs font-medium text-slate-500">{step.actor}</span>
                    </div>
                    <h3 className="font-bold text-slate-900 mb-2">{step.action}</h3>
                    {activeStep === i && (
                      <div className="mt-3 bg-slate-900 rounded-lg px-4 py-3 text-sm text-slate-300 font-mono whitespace-pre-wrap leading-relaxed animate-in fade-in">
                        {step.detail}
                      </div>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* Sequence Diagram */}
      <section className="py-16 bg-slate-900">
        <div className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8">
          <h2 className="text-2xl font-bold text-white text-center mb-8">Sequence Diagram</h2>
          <div className="bg-slate-800 rounded-2xl border border-slate-700 p-8 overflow-x-auto">
            <pre className="text-sm text-slate-300 font-mono leading-relaxed whitespace-pre">{`
  User          ChatGPT         MCP Server       AgenticOrg API      Shopping Agent      HITL
   |               |               |                  |                    |               |
   |  "Buy earbuds |               |                  |                    |               |
   |   under $50"  |               |                  |                    |               |
   |──────────────>|               |                  |                    |               |
   |               |  list_tools() |                  |                    |               |
   |               |──────────────>|                  |                    |               |
   |               |  [10 tools]   |                  |                    |               |
   |               |<──────────────|                  |                    |               |
   |               |               |                  |                    |               |
   |               |  run_agent(   |                  |                    |               |
   |               |   shopping,   |                  |                    |               |
   |               |   earbuds)    |                  |                    |               |
   |               |──────────────>|                  |                    |               |
   |               |               | POST /a2a/tasks  |                    |               |
   |               |               | Bearer ao_sk_... |                    |               |
   |               |               |─────────────────>|                    |               |
   |               |               |                  | validate API key   |               |
   |               |               |                  | extract tenant     |               |
   |               |               |                  |                    |               |
   |               |               |                  | invoke LangGraph   |               |
   |               |               |                  |───────────────────>|               |
   |               |               |                  |                    |  search()     |
   |               |               |                  |                    |  rank()       |
   |               |               |                  |                    |  select()     |
   |               |               |                  |                    |               |
   |               |               |                  |                    | conf=0.92     |
   |               |               |                  |                    | order > $20   |
   |               |               |                  |                    |──────────────>|
   |               |               |                  |                    |               |
   |  Slack: "Approve Sony WF-C500 ($39.99)?"         |                    |               |
   |<──────────────────────────────────────────────────────────────────────────────────────|
   |               |               |                  |                    |               |
   |  [Approve]    |               |                  |                    |               |
   |──────────────────────────────────────────────────────────────────────────────────────>|
   |               |               |                  |                    |               |
   |               |               |                  |                    | place_order() |
   |               |               |                  |                    |<──────────────|
   |               |               |                  |                    |               |
   |               |               |                  |  result: ordered   |               |
   |               |               |                  |<───────────────────|               |
   |               |               | 200 OK           |                    |               |
   |               |               |<─────────────────|                    |               |
   |               |  result JSON  |                  |                    |               |
   |               |<──────────────|                  |                    |               |
   |  "Ordered Sony WF-C500       |                  |                    |               |
   |   $39.99 — arrives in 2d"    |                  |                    |               |
   |<──────────────|               |                  |                    |               |
`}</pre>
          </div>
        </div>
      </section>

      {/* Key Takeaways */}
      <section className="py-16 bg-white">
        <div className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8">
          <h2 className="text-2xl font-bold text-slate-900 text-center mb-8">Key Takeaways</h2>
          <div className="grid md:grid-cols-2 gap-6">
            {[
              { title: "Zero Custom Code Needed", desc: "ChatGPT discovers and uses AgenticOrg agents via MCP — no custom integration code required. Just configure the MCP server.", icon: "code" },
              { title: "HITL on Every Critical Action", desc: "The user always approves high-stakes actions (orders, payments, data changes). Configurable thresholds per agent.", icon: "shield" },
              { title: "Works with Any MCP Client", desc: "ChatGPT, Claude Desktop, Cursor, Windsurf, or any custom MCP client. The same server, same tools, same agents.", icon: "plug" },
              { title: "Delegated Auth via API Keys", desc: "Third-party apps use scoped API keys. No password sharing, no OAuth dance. Generate keys from the dashboard, revoke anytime.", icon: "key" },
            ].map((item) => (
              <div key={item.title} className="border border-slate-200 rounded-xl p-6 hover:shadow-md transition-shadow">
                <h3 className="font-bold text-slate-900 mb-2">{item.title}</h3>
                <p className="text-sm text-slate-600 leading-relaxed">{item.desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* CTA */}
      <section className="py-16 bg-slate-900">
        <div className="max-w-3xl mx-auto px-4 text-center">
          <h2 className="text-2xl font-bold text-white mb-4">Ready to Build Your Integration?</h2>
          <p className="text-slate-400 mb-8">Get an API key and connect AgenticOrg to ChatGPT, Claude, or your own app in under 5 minutes.</p>
          <div className="flex flex-col sm:flex-row items-center justify-center gap-4">
            <Link
              to="/signup"
              className="inline-flex items-center justify-center bg-gradient-to-r from-blue-500 to-cyan-500 text-white px-8 py-3.5 rounded-xl text-base font-semibold hover:from-blue-600 hover:to-cyan-600 transition-all shadow-lg shadow-blue-500/25"
            >
              Get API Key — Free
            </Link>
            <a
              href="/#developers"
              className="inline-flex items-center justify-center gap-2 border border-slate-600 text-slate-300 px-8 py-3.5 rounded-xl text-base font-semibold hover:bg-slate-800 hover:text-white transition-all"
            >
              View All SDKs
            </a>
          </div>
        </div>
      </section>

      {/* Footer */}
      <footer className="bg-slate-950 border-t border-slate-800 py-8">
        <div className="max-w-7xl mx-auto px-4 text-center">
          <p className="text-sm text-slate-500">&copy; 2026 AgenticOrg &middot; Edumatica Pvt Ltd. All rights reserved.</p>
        </div>
      </footer>
    </div>
  );
}
