import { Link } from "react-router-dom";
import { Helmet } from "react-helmet-async";

const FLOW_NODES = [
  {
    title: "Merchant systems",
    eyebrow: "Source of record",
    copy: "Storefront, catalog, inventory, policy, OMS, support, and payment status systems keep operational truth.",
    tone: "border-cyan-300/40 bg-cyan-300/10 text-cyan-50",
  },
  {
    title: "Seller commerce agent",
    eyebrow: "Merchant setup",
    copy: "AgenticOrg helps the merchant connect systems, preview what agents can see, and prepare review packets.",
    tone: "border-emerald-300/40 bg-emerald-300/10 text-emerald-50",
  },
  {
    title: "Grantex authority",
    eyebrow: "Trust and policy",
    copy: "Grantex validates public-safe facts and issues canonical OACP artifacts or blockers.",
    tone: "border-amber-300/40 bg-amber-300/10 text-amber-50",
  },
  {
    title: "OACP artifact cache",
    eyebrow: "Scoped runtime memory",
    copy: "AgenticOrg stores public-safe references scoped by buyer agent, seller agent, tenant, and merchant.",
    tone: "border-blue-300/40 bg-blue-300/10 text-blue-50",
  },
  {
    title: "Buyer agent",
    eyebrow: "Answer or refuse",
    copy: "The buyer agent checks TTL, freshness, revocation posture, risk, source refs, and action boundary.",
    tone: "border-violet-300/40 bg-violet-300/10 text-violet-50",
  },
  {
    title: "Buyer channels",
    eyebrow: "User surface",
    copy: "ChatGPT, Claude, web, mobile, MCP clients, and future channel bridges receive grounded answers or refusals.",
    tone: "border-rose-300/40 bg-rose-300/10 text-rose-50",
  },
];

const LIFECYCLE = [
  {
    step: "01",
    title: "Seller connects existing systems",
    copy: "The merchant does not move the source of truth into AgenticOrg. Existing commerce, ERP, inventory, policy, order, support, and provider systems remain authoritative.",
  },
  {
    step: "02",
    title: "Public-safe evidence is reviewed",
    copy: "Connector custody produces source and freshness evidence without exposing raw provider payloads, secrets, credentials, card data, bank data, or private merchant APIs.",
  },
  {
    step: "03",
    title: "Grantex issues OACP artifacts",
    copy: "Grantex applies protocol and policy checks. Signed artifacts, freshness windows, revocation posture, blocked capabilities, and unsupported actions become the agent input.",
  },
  {
    step: "04",
    title: "AgenticOrg caches scoped references",
    copy: "The cache is a local runtime aid for non-binding discovery and prepared handoff behavior. It is not transaction authority and cannot override Grantex.",
  },
  {
    step: "05",
    title: "Buyer asks through any channel",
    copy: "The buyer agent answers only from valid OACP artifacts and approved evidence. Missing, stale, revoked, ambiguous, private, raw, or executable records fail closed.",
  },
  {
    step: "06",
    title: "Commitment requests become prepared or refused",
    copy: "A commitment-bound request can be prepared for review only when policy, freshness, eligibility, and dry-run checks support it. Checkout and payment execution remain separately gated.",
  },
];

const DECISIONS = [
  {
    title: "Answer",
    condition: "Valid artifact, low-risk request, fresh source evidence",
    result: "Show grounded product, policy, or support facts with source and freshness labels.",
  },
  {
    title: "Refresh",
    condition: "Missing or stale evidence where a separate authority refresh path is approved",
    result: "Ask the authority path for new evidence before answering or preparing anything.",
  },
  {
    title: "Prepare",
    condition: "Commitment-adjacent request with valid boundaries and allowed_to_execute=false",
    result: "Create a non-executing handoff state for human or approved downstream review.",
  },
  {
    title: "Refuse",
    condition: "Revoked, ambiguous, private/raw, executable, high-risk, or unsupported artifact state",
    result: "Return a clear refusal instead of inventing facts or calling checkout/payment/provider rails.",
  },
];

const SURFACES = [
  ["Seller agent", "Merchant setup, connector custody, preview, gap review, Grantex authority request"],
  ["Buyer agent", "Read-only discovery, grounded comparison, prepared-only handoff, refusal copy"],
  ["MCP", "Tool surface for approved clients that need a protocol-backed commerce agent entry point"],
  ["Web/mobile", "First controlled buyer-agent channel for artifact-backed discovery and review"],
  ["Provider rails", "Mandates and payment execution remain provider-owned and separately approved"],
  ["Audit", "Source refs, freshness, policy blockers, risk tier, and non-execution posture stay reviewable"],
];

const NON_GOALS = [
  "No public OACP publication claim",
  "No live checkout or payment execution claim",
  "No live provider rail readiness claim",
  "No merchant private API execution claim",
  "No certification, compliance, conformance, or standardization claim",
  "No production commerce readiness claim from this page",
];

function NodeCard({
  title,
  eyebrow,
  copy,
  tone,
}: {
  title: string;
  eyebrow: string;
  copy: string;
  tone: string;
}) {
  return (
    <div className={`rounded-xl border p-4 ${tone}`}>
      <p className="text-xs font-semibold uppercase tracking-[0.18em] opacity-80">{eyebrow}</p>
      <h3 className="mt-2 text-lg font-bold text-white">{title}</h3>
      <p className="mt-3 text-sm leading-relaxed opacity-80">{copy}</p>
    </div>
  );
}

export default function OpenAgenticCommerceProtocol() {
  return (
    <div className="min-h-screen bg-white text-slate-900">
      <Helmet>
        <title>Open Agentic Commerce Protocol | AgenticOrg</title>
        <meta
          name="description"
          content="A visual end-to-end explanation of Open Agentic Commerce Protocol: seller agents, buyer agents, Grantex authority, OACP artifacts, cache freshness, prepared handoffs, and fail-closed commerce boundaries."
        />
        <link rel="canonical" href="https://agenticorg.ai/open-agentic-commerce-protocol" />
      </Helmet>

      <nav className="sticky top-0 z-50 bg-slate-950/95 backdrop-blur border-b border-white/10">
        <div className="max-w-7xl mx-auto h-16 px-4 sm:px-6 lg:px-8 flex items-center justify-between">
          <Link to="/" className="flex items-center gap-2 text-white font-semibold">
            <span className="w-8 h-8 rounded-lg bg-gradient-to-br from-cyan-500 to-emerald-500 flex items-center justify-center text-xs font-black">AO</span>
            AgenticOrg
          </Link>
          <div className="flex items-center gap-4">
            <a href="#flow" className="hidden sm:inline text-sm text-slate-300 hover:text-white">Flow</a>
            <a href="#boundaries" className="hidden sm:inline text-sm text-slate-300 hover:text-white">Boundaries</a>
            <Link to="/integration-workflow" className="text-sm text-slate-300 hover:text-white">MCP example</Link>
            <Link to="/signup" className="rounded-lg bg-cyan-400 px-4 py-2 text-sm font-semibold text-slate-950 hover:bg-cyan-300 transition-colors">Start</Link>
          </div>
        </div>
      </nav>

      <main>
        <section className="relative overflow-hidden bg-slate-950">
          <div className="absolute inset-0 opacity-20" style={{ backgroundImage: "linear-gradient(rgba(255,255,255,.12) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,.12) 1px, transparent 1px)", backgroundSize: "48px 48px" }} />
          <div className="relative max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-20 lg:py-24 grid lg:grid-cols-[0.9fr_1.1fr] gap-12 items-center">
            <div>
              <div className="inline-flex rounded-full border border-cyan-300/30 bg-cyan-300/10 px-4 py-1.5 text-sm font-medium text-cyan-100">
                Open Agentic Commerce Protocol
              </div>
              <h1 className="mt-6 text-4xl sm:text-5xl lg:text-6xl font-extrabold tracking-tight text-white leading-tight">
                Seller and buyer agents need commerce facts they can trust.
              </h1>
              <p className="mt-6 text-lg text-slate-300 leading-relaxed max-w-2xl">
                OACP is the trust model AgenticOrg uses to consume Grantex-authorized commerce artifacts, show source and freshness, prepare non-executing handoffs, and refuse unsafe requests instead of inventing merchant facts.
              </p>
              <div className="mt-8 flex flex-col sm:flex-row gap-3">
                <a href="#flow" className="inline-flex justify-center rounded-lg bg-cyan-400 px-6 py-3 text-sm font-semibold text-slate-950 hover:bg-cyan-300 transition-colors">
                  Follow the end-to-end flow
                </a>
                <a href="#boundaries" className="inline-flex justify-center rounded-lg border border-white/20 px-6 py-3 text-sm font-semibold text-white hover:bg-white/10 transition-colors">
                  See what stays blocked
                </a>
              </div>
            </div>

            <div className="rounded-2xl border border-white/10 bg-white/5 p-5 sm:p-6 shadow-2xl">
              <div className="grid sm:grid-cols-2 gap-3">
                {FLOW_NODES.map((node) => (
                  <NodeCard key={node.title} {...node} />
                ))}
              </div>
              <div className="mt-4 rounded-xl border border-amber-300/30 bg-amber-300/10 p-4">
                <p className="text-sm font-semibold text-amber-100">Provider-owned execution boundary</p>
                <p className="mt-2 text-sm text-amber-50/85">
                  Mandates, payment capture, checkout execution, holds, refunds, and provider rails remain outside this page unless a separate approved implementation and verification path exists.
                </p>
              </div>
            </div>
          </div>
        </section>

        <section id="flow" className="py-20 bg-slate-50 scroll-mt-20">
          <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
            <div className="max-w-3xl">
              <p className="text-sm font-semibold uppercase tracking-[0.18em] text-cyan-700">End-to-end commerce custody</p>
              <h2 className="mt-3 text-3xl sm:text-4xl font-bold text-slate-950">From merchant data to a buyer-agent answer.</h2>
              <p className="mt-4 text-lg text-slate-600 leading-relaxed">
                The protocol keeps responsibility explicit: merchant systems own facts, Grantex owns artifact authority, AgenticOrg owns agent runtime behavior, and provider rails own execution.
              </p>
            </div>

            <div className="mt-12 grid lg:grid-cols-3 gap-5">
              {LIFECYCLE.map((item) => (
                <div key={item.step} className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
                  <div className="flex items-center gap-3">
                    <span className="inline-flex h-10 w-10 items-center justify-center rounded-lg bg-slate-950 text-sm font-bold text-white">{item.step}</span>
                    <h3 className="text-lg font-bold text-slate-950">{item.title}</h3>
                  </div>
                  <p className="mt-4 text-sm leading-relaxed text-slate-600">{item.copy}</p>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section className="py-20 bg-white">
          <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
            <div className="grid lg:grid-cols-[0.85fr_1.15fr] gap-10 items-start">
              <div>
                <p className="text-sm font-semibold uppercase tracking-[0.18em] text-emerald-700">Runtime decision model</p>
                <h2 className="mt-3 text-3xl sm:text-4xl font-bold text-slate-950">Every buyer request resolves to one safe posture.</h2>
                <p className="mt-4 text-lg text-slate-600 leading-relaxed">
                  A buyer agent should not guess. It should either answer from valid artifacts, refresh through approved authority, prepare a non-executing handoff, or refuse.
                </p>
              </div>

              <div className="grid sm:grid-cols-2 gap-4">
                {DECISIONS.map((decision) => (
                  <div key={decision.title} className="rounded-xl border border-slate-200 bg-slate-50 p-5">
                    <h3 className="text-xl font-bold text-slate-950">{decision.title}</h3>
                    <p className="mt-3 text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">When</p>
                    <p className="mt-1 text-sm text-slate-700 leading-relaxed">{decision.condition}</p>
                    <p className="mt-4 text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">Result</p>
                    <p className="mt-1 text-sm text-slate-700 leading-relaxed">{decision.result}</p>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </section>

        <section id="boundaries" className="py-20 bg-slate-950 text-white scroll-mt-20">
          <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
            <div className="grid lg:grid-cols-2 gap-10">
              <div>
                <p className="text-sm font-semibold uppercase tracking-[0.18em] text-amber-300">Commitment boundary</p>
                <h2 className="mt-3 text-3xl sm:text-4xl font-bold">OACP explains where the agent stops.</h2>
                <p className="mt-4 text-lg text-slate-300 leading-relaxed">
                  AgenticOrg can use OACP artifacts for discovery, comparison, source/freshness explanation, and prepared-only handoff behavior. It must not turn those artifacts into live commerce execution without a separately approved rollout.
                </p>
              </div>

              <div className="rounded-2xl border border-white/10 bg-white/5 p-6">
                <h3 className="text-lg font-bold">Non-goals this page does not claim</h3>
                <div className="mt-5 grid gap-3">
                  {NON_GOALS.map((item) => (
                    <div key={item} className="flex items-start gap-3 rounded-lg border border-white/10 bg-white/5 px-4 py-3">
                      <span className="mt-1 h-2 w-2 rounded-full bg-rose-300" />
                      <span className="text-sm text-slate-200">{item}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </section>

        <section className="py-20 bg-slate-50">
          <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
            <div className="max-w-3xl">
              <p className="text-sm font-semibold uppercase tracking-[0.18em] text-blue-700">Surfaces and ownership</p>
              <h2 className="mt-3 text-3xl sm:text-4xl font-bold text-slate-950">Where OACP appears in the product.</h2>
            </div>
            <div className="mt-10 grid md:grid-cols-2 lg:grid-cols-3 gap-5">
              {SURFACES.map(([title, copy]) => (
                <div key={title} className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
                  <h3 className="text-lg font-bold text-slate-950">{title}</h3>
                  <p className="mt-3 text-sm leading-relaxed text-slate-600">{copy}</p>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section className="py-20 bg-white">
          <div className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8 text-center">
            <p className="text-sm font-semibold uppercase tracking-[0.18em] text-cyan-700">Next step</p>
            <h2 className="mt-3 text-3xl sm:text-4xl font-bold text-slate-950">See a concrete MCP-backed OACP preview.</h2>
            <p className="mt-4 text-lg text-slate-600 leading-relaxed">
              The workflow example shows how a third-party client asks AgenticOrg to run a commerce agent, how OACP artifacts are evaluated, and how the result stays prepared-only or refused.
            </p>
            <div className="mt-8 flex flex-col sm:flex-row justify-center gap-3">
              <Link to="/integration-workflow" className="inline-flex justify-center rounded-lg bg-slate-950 px-6 py-3 text-sm font-semibold text-white hover:bg-slate-800 transition-colors">
                Open the MCP workflow
              </Link>
              <Link to="/" className="inline-flex justify-center rounded-lg border border-slate-300 px-6 py-3 text-sm font-semibold text-slate-900 hover:bg-slate-50 transition-colors">
                Back to landing page
              </Link>
            </div>
          </div>
        </section>
      </main>
    </div>
  );
}
