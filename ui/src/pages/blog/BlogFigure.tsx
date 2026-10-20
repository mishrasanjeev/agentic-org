// SPDX-License-Identifier: Apache-2.0
import type { ReactNode } from "react";
import type { BlogDiagramId, BlogFigure as Figure } from "./blogData";

type Tone = "neutral" | "grantex" | "agenticorg" | "oacp" | "protocol" | "refuse" | "allow" | "hold";

function Box({ x, y, w, h, tone = "neutral", rx = 10 }: { x: number; y: number; w: number; h: number; tone?: Tone; rx?: number }) {
  return <rect x={x} y={y} width={w} height={h} rx={rx} className={`fig-box fig-box-${tone}`} />;
}

function Text({ x, y, children, kind = "body", anchor = "start", tone }: {
  x: number; y: number; children: ReactNode; kind?: "title" | "strong" | "body" | "small" | "owner"; anchor?: "start" | "middle"; tone?: Tone;
}) {
  return <text x={x} y={y} textAnchor={anchor} className={`fig-text fig-text-${kind}${tone ? ` fig-ink-${tone}` : ""}`}>{children}</text>;
}

function Chip({ x, y, w, h, label, size = "body" }: { x: number; y: number; w: number; h: number; label: string; size?: "body" | "small" }) {
  return (
    <g>
      <Box x={x} y={y} w={w} h={h} tone="protocol" rx={h / 2} />
      <Text x={x + w / 2} y={y + h / 2 + (size === "small" ? 4.5 : 5.5)} anchor="middle" kind={size === "small" ? "small" : "strong"}>{label}</Text>
    </g>
  );
}

function Svg({ id, width, height, title, desc, children }: { id: string; width: number; height: number; title: string; desc: string; children: ReactNode }) {
  return (
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-labelledby={`${id}-svg-title ${id}-svg-desc`} className="fig-svg" focusable="false">
      <title id={`${id}-svg-title`}>{title}</title>
      <desc id={`${id}-svg-desc`}>{desc}</desc>
      <defs>
        <marker id={`${id}-arrow`} viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" className="fig-arrowhead" />
        </marker>
      </defs>
      {children}
    </svg>
  );
}

const PROTOCOLS = ["UCP", "ACP", "AP2", "TAP", "MCP"];

function WiredPanel() {
  const boxes = [
    { name: "UCP", x: 16, y: 196 }, { name: "ACP", x: 148, y: 196 }, { name: "AP2", x: 280, y: 196 },
    { name: "TAP", x: 82, y: 318 }, { name: "MCP", x: 214, y: 318 },
  ];
  return (
    <Svg id="fig-wired" width={400} height={468} title="Without OACP"
      desc="A shop wired to UCP, ACP, AP2, TAP and MCP separately. Tangled lines lead to five boxes, each holding its own facts, keys and log.">
      <Box x={0} y={0} w={400} h={468} tone="neutral" rx={14} />
      <Text x={24} y={40} kind="title">Without OACP</Text>
      <Text x={24} y={64} kind="small">Each protocol wired on its own</Text>
      <g className="fig-wire fig-wire-tangled">
        <path d="M 240 130 C 240 170, 68 160, 68 196" />
        <path d="M 160 130 C 160 170, 332 160, 332 196" />
        <path d="M 200 130 C 120 160, 280 170, 200 196" />
        <path d="M 250 130 C 262 230, 134 232, 134 318" />
        <path d="M 150 130 C 138 232, 266 232, 266 318" />
      </g>
      <Box x={140} y={86} w={120} h={44} tone="neutral" />
      <Text x={200} y={113} anchor="middle" kind="strong">Your shop</Text>
      {boxes.map((box) => (
        <g key={box.name}>
          <Box x={box.x} y={box.y} w={104} h={100} tone="protocol" />
          <Text x={box.x + 14} y={box.y + 28} kind="strong">{box.name}</Text>
          {["own facts", "own keys", "own log"].map((item, i) => (
            <g key={item}>
              <rect x={box.x + 14} y={box.y + 44 + i * 18} width={8} height={8} rx={2} className="fig-dot fig-dot-refuse" />
              <Text x={box.x + 28} y={box.y + 52 + i * 18} kind="small">{item}</Text>
            </g>
          ))}
        </g>
      ))}
      <Text x={200} y={450} anchor="middle" kind="body" tone="refuse">Five copies to keep in sync</Text>
    </Svg>
  );
}

function HubPanel() {
  const centers = [44, 122, 200, 278, 356];
  return (
    <Svg id="fig-hub" width={400} height={468} title="With OACP underneath"
      desc="The same shop feeds one OACP layer of signed facts, one rule set, one ledger and one revoke switch. Adapters render it as UCP, ACP, AP2, TAP and MCP for every buyer agent.">
      <Box x={0} y={0} w={400} h={468} tone="neutral" rx={14} />
      <Text x={24} y={40} kind="title">With OACP underneath</Text>
      <Text x={24} y={64} kind="small">One set of facts, many outputs</Text>
      <Box x={140} y={86} w={120} h={44} tone="neutral" />
      <Text x={200} y={113} anchor="middle" kind="strong">Your shop</Text>
      <line x1={200} y1={130} x2={200} y2={154} className="fig-wire" markerEnd="url(#fig-hub-arrow)" />
      <Box x={40} y={158} w={320} h={112} tone="oacp" rx={14} />
      <Text x={200} y={192} anchor="middle" kind="title" tone="oacp">OACP layer</Text>
      <Text x={200} y={218} anchor="middle">Signed facts · one rule set</Text>
      <Text x={200} y={242} anchor="middle">One ledger · one revoke switch</Text>
      <g className="fig-wire">
        {centers.map((cx) => <line key={cx} x1={200} y1={270} x2={cx} y2={318} />)}
        {centers.map((cx) => <line key={`b${cx}`} x1={cx} y1={358} x2={200} y2={384} />)}
      </g>
      {PROTOCOLS.map((name, i) => <Chip key={name} x={centers[i] - 32} y={318} w={64} h={40} label={name} />)}
      <Box x={100} y={384} w={200} h={40} tone="neutral" />
      <Text x={200} y={409} anchor="middle" kind="strong">Every buyer agent</Text>
      <Text x={200} y={452} anchor="middle" kind="body" tone="allow">One copy, the same everywhere</Text>
    </Svg>
  );
}

interface Step { owner: string; tone: Tone; title: string; detail?: string; chips?: string[]; badge?: string }

function StepStack({ id, title, desc, steps }: { id: string; title: string; desc: string; steps: Step[] }) {
  const row = 96;
  const height = 12 + steps.length * row - 6;
  return (
    <Svg id={id} width={420} height={height} title={title} desc={desc}>
      <line x1={26} y1={30} x2={26} y2={height - 40} className="fig-wire fig-spine" />
      {steps.map((step, i) => {
        const y = 12 + i * row;
        return (
          <g key={step.title}>
            <circle cx={26} cy={y + 39} r={16} className={`fig-box fig-box-${step.tone}`} />
            <Text x={26} y={y + 45} anchor="middle" kind="strong">{String(i + 1)}</Text>
            <Box x={56} y={y} w={352} h={78} tone={step.tone} />
            <Text x={70} y={y + 22} kind="owner" tone={step.tone}>{step.owner}</Text>
            <Text x={70} y={y + 45} kind="strong">{step.title}</Text>
            {step.detail && <Text x={70} y={y + 66} kind="small">{step.detail}</Text>}
            {step.chips?.map((chip, c) => <Chip key={chip} x={70 + c * 64} y={y + 52} w={56} h={20} label={chip} size="small" />)}
            {step.badge && (
              <g>
                <Box x={396 - 84} y={y + 9} w={84} h={22} tone="allow" rx={11} />
                <Text x={396 - 42} y={y + 24.5} anchor="middle" kind="small" tone="allow">{step.badge}</Text>
              </g>
            )}
          </g>
        );
      })}
    </Svg>
  );
}

function FlowFigure() {
  return (
    <StepStack id="fig-flow" title="From merchant facts to buyer agents"
      desc="Seven steps: the merchant states facts once, Grantex signs them, AgenticOrg verifies and caches them, the arbiter decides allow or refuse, the ledger records it, adapters render UCP, ACP, AP2, MCP and A2A, and buyer agents get the same answer."
      steps={[
        { owner: "Merchant", tone: "neutral", title: "States its facts once", detail: "Catalog, price, stock, policy, limits" },
        { owner: "Grantex", tone: "grantex", title: "Signs each fact", detail: "ES256 signature, expiry, revoke slot" },
        { owner: "AgenticOrg", tone: "agenticorg", title: "Verifies and caches", detail: "Refuses unsigned, changed or revoked" },
        { owner: "AgenticOrg arbiter", tone: "agenticorg", title: "Decides allow or refuse", detail: "Fixed rules; nobody waits for a person" },
        { owner: "AgenticOrg ledger", tone: "agenticorg", title: "Records every decision", detail: "Hash-chained and checkable offline" },
        { owner: "Adapters", tone: "oacp", title: "Speak each protocol", chips: ["UCP", "ACP", "AP2", "MCP", "A2A"] },
        { owner: "Buyer agents", tone: "neutral", title: "Get the same answer", detail: "Same price, same rules, same refusals" },
      ]} />
  );
}

function JourneyFigure() {
  return (
    <StepStack id="fig-journey" title="A purchase with mandates"
      desc="Seven steps: Grantex issues an Intent mandate, a Cart mandate signed with a passkey when the buyer is present, and a Payment mandate. The buyer's bank adds AFA when RBI rules require it. The arbiter applies eight rules, the payment provider runs the payment once on allow, and the ledger records everything."
      steps={[
        { owner: "Grantex · Intent mandate", tone: "grantex", title: "What the buyer allowed", detail: "Example: up to ₹2,000 at one shop" },
        { owner: "Grantex · Cart mandate", tone: "grantex", title: "The exact cart, by hash", detail: "Buyer present? A passkey signs it", badge: "passkey" },
        { owner: "Grantex · Payment mandate", tone: "grantex", title: "Amount, rail, token reference", detail: "Plural, UPI AutoPay or card token" },
        { owner: "Buyer's bank · AFA", tone: "hold", title: "Extra factor, when RBI needs it", detail: "AutoPay set-up, or above a limit", badge: "India" },
        { owner: "AgenticOrg arbiter", tone: "agenticorg", title: "Eight rules: allow or refuse", detail: "First failing rule is the reason" },
        { owner: "Payment provider", tone: "neutral", title: "Runs once, only on allow", detail: "Behind a switch that is off by default" },
        { owner: "AgenticOrg ledger", tone: "agenticorg", title: "One record of it all", detail: "Mandate hashes, decision, payment" },
      ]} />
  );
}

function RevocationFigure() {
  const tiles = [
    { title: "AgenticOrg", lines: ["Refuses with", "artifact_revoked"], x: 20, y: 222 },
    { title: "UCP profile", lines: ["Stops offering", "what relied on it"], x: 216, y: 222 },
    { title: "Arbiter", lines: ["Refuses new", "purchases"], x: 20, y: 326 },
    { title: "Mandates", lines: ["Revoke one and", "its children go too"], x: 216, y: 326 },
  ];
  return (
    <Svg id="fig-revoke" width={420} height={486} title="What happens when something is revoked"
      desc="The merchant or operator revokes one fact. Grantex sets its status list slot to invalid and signs a revocation record. Verifiers check the list before trusting a fact. AgenticOrg refuses the cached fact, the UCP profile stops offering it, the arbiter refuses new purchases, and revoking a mandate revokes the mandates built on it. If the list cannot be reached, everything refuses.">
      <Box x={20} y={12} w={380} h={64} tone="refuse" />
      <Text x={36} y={36} kind="owner" tone="refuse">Merchant or operator</Text>
      <Text x={36} y={60} kind="strong">Revokes one fact, once</Text>
      <line x1={210} y1={76} x2={210} y2={98} className="fig-wire" markerEnd="url(#fig-revoke-arrow)" />
      <Box x={20} y={100} w={380} h={72} tone="grantex" />
      <Text x={36} y={124} kind="owner" tone="grantex">Grantex</Text>
      <Text x={36} y={146} kind="strong">Status list slot set to INVALID</Text>
      <Text x={36} y={164} kind="small">plus a signed revocation record</Text>
      <line x1={210} y1={172} x2={210} y2={190} className="fig-wire" />
      <Text x={210} y={208} anchor="middle" kind="small">Verifiers check the list before trusting a fact</Text>
      {tiles.map((tile) => (
        <g key={tile.title}>
          <Box x={tile.x} y={tile.y} w={184} h={92} tone="neutral" />
          <circle cx={tile.x + 24} cy={tile.y + 28} r={11} className="fig-box fig-box-refuse" />
          <path d={`M ${tile.x + 19} ${tile.y + 23} L ${tile.x + 29} ${tile.y + 33} M ${tile.x + 29} ${tile.y + 23} L ${tile.x + 19} ${tile.y + 33}`} className="fig-cross" />
          <Text x={tile.x + 44} y={tile.y + 34} kind="strong">{tile.title}</Text>
          <Text x={tile.x + 16} y={tile.y + 62} kind="small">{tile.lines[0]}</Text>
          <Text x={tile.x + 16} y={tile.y + 80} kind="small">{tile.lines[1]}</Text>
        </g>
      ))}
      <Box x={20} y={430} w={380} h={44} tone="hold" />
      <Text x={210} y={457} anchor="middle" kind="body" tone="hold">List unreachable? Everything refuses.</Text>
    </Svg>
  );
}

const DIAGRAMS: Record<BlogDiagramId, () => ReactNode> = {
  "oacp-hub": () => (
    <div className="fig-pair">
      <WiredPanel />
      <HubPanel />
    </div>
  ),
  "oacp-flow": () => <FlowFigure />,
  "oacp-journey": () => <JourneyFigure />,
  "oacp-revocation": () => <RevocationFigure />,
};

export default function BlogFigure({ figure }: { figure: Figure }) {
  const titleId = `${figure.id}-title`;
  return (
    <figure className={`blog-figure${figure.table ? " blog-figure-table" : ""}`} aria-labelledby={titleId} data-figure={figure.id}>
      <figcaption>
        <h3 id={titleId}>{figure.title}</h3>
        <p className="figure-caption">{figure.caption}</p>
      </figcaption>
      {figure.diagram && <div className="figure-canvas">{DIAGRAMS[figure.diagram]()}</div>}
      {figure.table && (
        <table className="figure-table">
          <thead>
            <tr>{figure.table.columns.map((column) => <th key={column} scope="col">{column}</th>)}</tr>
          </thead>
          <tbody>
            {figure.table.rows.map((row) => (
              <tr key={row[0]}>
                <th scope="row">{row[0]}</th>
                {row.slice(1).map((cell, i) => <td key={cell} data-label={figure.table!.columns[i + 1]}>{cell}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </figure>
  );
}
