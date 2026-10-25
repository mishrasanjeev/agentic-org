// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
// Diagrams for the OACP user guides. A guide names one with a ```figure fence
// (ui/scripts/generate-user-docs.mjs); Documentation.tsx renders it in place.
// Every diagram is a narrow vertical layout so it stays legible on a phone.
import type { ReactNode } from "react";
import { Box, Cross, StepStack, Svg, Text, Tick, type Tone } from "@/components/figures/FigureKit";
import { HubPanel, RevocationFigure, WiredPanel } from "@/components/figures/OacpDiagrams";

const W = 420;

function StandardsMap() {
  const rows = [
    { name: "UCP", line: "Find the shop and check out" },
    { name: "ACP", line: "Product feed and agent checkout" },
    { name: "AP2", line: "What the buyer allowed" },
    { name: "TAP · Web Bot Auth", line: "Which agent is calling" },
    { name: "MCP · A2A", line: "How agents and tools talk" },
  ];
  const rowH = 58;
  const gap = 10;
  const top = 12;
  const oacpY = top + rows.length * (rowH + gap) + 26;
  const shopY = oacpY + 104;
  return (
    <Svg id="docfig-standards" width={W} height={shopY + 56} title="Each standard answers one question"
      desc="Five rows of standards: UCP for finding the shop and checking out, ACP for the product feed and agent checkout, AP2 for what the buyer allowed, Visa TAP and Web Bot Auth for which agent is calling, and MCP and A2A for how agents and tools talk. Underneath them sits one OACP layer of signed facts, one rule set and one record, fed by your shop.">
      {rows.map((row, i) => {
        const y = top + i * (rowH + gap);
        return (
          <g key={row.name}>
            <Box x={20} y={y} w={380} h={rowH} tone="protocol" />
            <Text x={36} y={y + 24} kind="strong">{row.name}</Text>
            <Text x={36} y={y + 45} kind="small">{row.line}</Text>
          </g>
        );
      })}
      <line x1={210} y1={oacpY - 2} x2={210} y2={oacpY - 24} className="fig-wire" markerEnd="url(#docfig-standards-arrow)" />
      <Box x={20} y={oacpY} w={380} h={80} tone="oacp" rx={14} />
      <Text x={210} y={oacpY + 32} anchor="middle" kind="title" tone="oacp">OACP underneath</Text>
      <Text x={210} y={oacpY + 58} anchor="middle">Signed facts · one rule set · one record</Text>
      <line x1={210} y1={shopY - 2} x2={210} y2={oacpY + 82} className="fig-wire" markerEnd="url(#docfig-standards-arrow)" />
      <Box x={130} y={shopY} w={160} h={44} tone="neutral" />
      <Text x={210} y={shopY + 28} anchor="middle" kind="strong">Your shop</Text>
    </Svg>
  );
}

function DocsFlow() {
  return (
    <StepStack id="docfig-flow" title="From your shop to every buyer agent"
      desc="Seven steps. The merchant states its facts once. Grantex signs each fact with an expiry and a revocation slot. AgenticOrg verifies each fact before keeping a copy. The arbiter decides allow or refuse with fixed rules and nobody waits for a person. The ledger records every decision in signed rows you can export and check. Adapters speak UCP, ACP, AP2, MCP and A2A. Buyer agents get the same answer."
      steps={[
        { owner: "Merchant", tone: "neutral", title: "States its facts once", detail: "Catalog, price, stock, policy, limits" },
        { owner: "Grantex", tone: "grantex", title: "Signs each fact", detail: "Signature, expiry, revocation slot" },
        { owner: "AgenticOrg", tone: "agenticorg", title: "Verifies, then keeps a copy", detail: "Refuses unsigned, changed or revoked" },
        { owner: "AgenticOrg arbiter", tone: "agenticorg", title: "Decides allow or refuse", detail: "Fixed rules; nobody waits for a person" },
        { owner: "AgenticOrg ledger", tone: "agenticorg", title: "Records every decision", detail: "Signed rows you can export and check" },
        { owner: "Adapters", tone: "oacp", title: "Speak each standard", chips: ["UCP", "ACP", "AP2", "MCP", "A2A"] },
        { owner: "Buyer agents", tone: "neutral", title: "Get the same answer", detail: "Same price, same rules, same refusals" },
      ]} />
  );
}

function VerifyGate() {
  const checks = [
    "Signature matches the content",
    "Key is a published Grantex key",
    "In scope and not expired",
    "Valid on the revocation list",
  ];
  const top = 92;
  const rowH = 50;
  const outY = top + checks.length * (rowH + 8) + 34;
  return (
    <Svg id="docfig-verify" width={W} height={outY + 112} title="Four checks before a fact is kept"
      desc="A signed fact arrives from Grantex. AgenticOrg checks four things: the signature matches the content, the key is a published Grantex key, the fact is in scope and not expired, and it is valid on the revocation list. If all pass, it is kept with source and freshness labels. If any fails, it is refused, and in strict mode nothing from that batch is kept.">
      <Box x={20} y={12} w={380} h={56} tone="grantex" />
      <Text x={36} y={36} kind="owner" tone="grantex">Grantex</Text>
      <Text x={36} y={57} kind="strong">A signed fact arrives</Text>
      <line x1={210} y1={68} x2={210} y2={top - 4} className="fig-wire" markerEnd="url(#docfig-verify-arrow)" />
      {checks.map((check, i) => {
        const y = top + i * (rowH + 8);
        return (
          <g key={check}>
            <Box x={20} y={y} w={380} h={rowH} tone="agenticorg" />
            <Tick cx={46} cy={y + rowH / 2} />
            <Text x={68} y={y + rowH / 2 + 5.5} kind="body">{`${i + 1}. ${check}`}</Text>
          </g>
        );
      })}
      <line x1={110} y1={outY - 30} x2={110} y2={outY - 4} className="fig-wire" markerEnd="url(#docfig-verify-arrow)" />
      <line x1={310} y1={outY - 30} x2={310} y2={outY - 4} className="fig-wire" markerEnd="url(#docfig-verify-arrow)" />
      <Box x={20} y={outY} w={184} h={100} tone="allow" />
      <Text x={36} y={outY + 28} kind="owner" tone="allow">ALL PASS</Text>
      <Text x={36} y={outY + 52} kind="strong">Kept</Text>
      <Text x={36} y={outY + 74} kind="small">with source and</Text>
      <Text x={36} y={outY + 92} kind="small">freshness labels</Text>
      <Box x={216} y={outY} w={184} h={100} tone="refuse" />
      <Text x={232} y={outY + 28} kind="owner" tone="refuse">ANY FAILS</Text>
      <Text x={232} y={outY + 52} kind="strong">Refused</Text>
      <Text x={232} y={outY + 74} kind="small">strict mode keeps</Text>
      <Text x={232} y={outY + 92} kind="small">nothing from the batch</Text>
    </Svg>
  );
}

function DocsJourney() {
  return (
    <StepStack id="docfig-journey" title="A purchase with mandates"
      desc="Eight steps. The buyer agent opens a checkout priced from verified catalog facts. Grantex issues an Intent mandate for what the buyer allowed, for example up to 2,000 rupees at one shop; a Cart mandate for this exact cart by hash, signed by the buyer's passkey when the buyer is present; and a Payment mandate for the amount, rail and a token fingerprint, never the token itself. AgenticOrg verifies all three. The arbiter applies eight rules and answers allow or refuse; UPI AutoPay may first need RBI AFA. The payment provider charges once, only on allow, behind a switch that is off by default. The ledger records the mandate hashes, the decision and the payment."
      steps={[
        { owner: "Buyer agent", tone: "neutral", title: "Opens a checkout", detail: "Priced from verified catalog facts" },
        { owner: "Grantex · Intent mandate", tone: "grantex", title: "What the buyer allowed", detail: "Example: up to ₹2,000 at one shop" },
        { owner: "Grantex · Cart mandate", tone: "grantex", title: "This exact cart, by hash", detail: "Buyer present? Their passkey signs it", badge: "passkey" },
        { owner: "Grantex · Payment mandate", tone: "grantex", title: "Amount, rail, token fingerprint", detail: "Never the card or UPI token itself" },
        { owner: "AgenticOrg", tone: "agenticorg", title: "Verifies all three mandates", detail: "Signatures, revocation, this checkout" },
        { owner: "AgenticOrg arbiter", tone: "agenticorg", title: "Eight rules: allow or refuse", detail: "UPI AutoPay may first need RBI AFA", badge: "India: AFA", badgeTone: "hold" },
        { owner: "Payment provider", tone: "neutral", title: "Charges once, only on allow", detail: "Behind a switch that is off by default" },
        { owner: "AgenticOrg ledger", tone: "agenticorg", title: "Records it all", detail: "Mandate hashes, decision, payment" },
      ]} />
  );
}

function DecisionFork() {
  const branches: { label: string; tone: Tone; title: string; detail: string }[] = [
    { label: "ALLOW", tone: "allow", title: "The payment runs once", detail: "Order confirmed and recorded" },
    { label: "REFUSE", tone: "refuse", title: "First failing rule named", detail: "Nothing is charged" },
    { label: "AFA FIRST", tone: "hold", title: "Buyer approves in UPI app", detail: "Next attempt: a fresh decision" },
  ];
  const top = 96;
  const cardH = 92;
  const gap = 14;
  const height = top + branches.length * (cardH + gap) + 2;
  return (
    <Svg id="docfig-fork" width={W} height={height} title="Three possible answers"
      desc="The arbiter decision has three outcomes. Allow: the payment runs once and the order is confirmed and recorded. Refuse: the first failing rule is named and nothing is charged. AFA first, for UPI AutoPay: the buyer approves in their UPI app, and the next attempt gets a fresh decision.">
      <Box x={20} y={12} w={380} h={56} tone="agenticorg" />
      <Text x={36} y={36} kind="owner" tone="agenticorg">AgenticOrg arbiter</Text>
      <Text x={36} y={57} kind="strong">Decides at once</Text>
      <line x1={40} y1={68} x2={40} y2={top + (branches.length - 1) * (cardH + gap) + cardH / 2} className="fig-wire" />
      {branches.map((branch, i) => {
        const y = top + i * (cardH + gap);
        const pill = branch.label.length * 9 + 22;
        return (
          <g key={branch.label}>
            <line x1={40} y1={y + cardH / 2} x2={66} y2={y + cardH / 2} className="fig-wire" markerEnd="url(#docfig-fork-arrow)" />
            <Box x={70} y={y} w={330} h={cardH} tone={branch.tone} />
            <Box x={86} y={y + 12} w={pill} h={24} tone={branch.tone} rx={12} />
            <Text x={86 + pill / 2} y={y + 29} anchor="middle" kind="owner" tone={branch.tone}>{branch.label}</Text>
            <Text x={86} y={y + 60} kind="strong">{branch.title}</Text>
            <Text x={86} y={y + 81} kind="small">{branch.detail}</Text>
          </g>
        );
      })}
    </Svg>
  );
}

function SetupPath() {
  return (
    <StepStack id="docfig-setup" title="Five phases from shop to sales"
      desc="Phase 1, connect your shop: settings, Shopify, sync and limits. Phase 2, connect to Grantex: the allowlist, the authority request and verified facts. Phase 3, publish your UCP profile: the DNS TXT record, the signing key and publishing. Phase 4, open sales channels one by one: UCP checkout, the ACP feed and checkout, and MCP tools. Phase 5, turn on payments, only after a sandbox run passes; it is off for now."
      steps={[
        { owner: "Phase 1", tone: "neutral", title: "Connect your shop", detail: "Settings, Shopify, sync, limits" },
        { owner: "Phase 2", tone: "grantex", title: "Connect to Grantex", detail: "Allowlist, authority, verified facts" },
        { owner: "Phase 3", tone: "oacp", title: "Publish your UCP profile", detail: "DNS TXT, signing key, publish" },
        { owner: "Phase 4", tone: "agenticorg", title: "Open sales channels", detail: "UCP, ACP feed and checkout, MCP", badge: "one by one" },
        { owner: "Phase 5", tone: "hold", title: "Turn on payments", detail: "Only after a sandbox run passes", badge: "off for now", badgeTone: "hold" },
      ]} />
  );
}

function CustomDomainPath() {
  return (
    <StepStack id="docfig-custom-domain" title="Your own domain, step by step"
      desc="Five steps. You add a subdomain such as agent.merchant.example. You publish two DNS records: a CNAME to the AgenticOrg address ending in web.app, and a TXT record that ties the subdomain to your account. AgenticOrg verifies both records. AgenticOrg connects the domain by itself. Google issues the certificate, and the domain is live."
      steps={[
        { owner: "You", tone: "neutral", title: "Add a subdomain", detail: "For example agent.merchant.example" },
        { owner: "You", tone: "neutral", title: "Publish two DNS records", detail: "CNAME to <site>.web.app, TXT for your account" },
        { owner: "AgenticOrg", tone: "agenticorg", title: "Verifies both records", detail: "Every failed check says how to fix it" },
        { owner: "AgenticOrg", tone: "agenticorg", title: "Connects the domain", detail: "Automatically, nobody waits for a person" },
        { owner: "Google", tone: "allow", title: "Issues the certificate", detail: "Usually within the hour, at most 24 hours", badge: "then live" },
      ]} />
  );
}

function DnsTxt() {
  return (
    <Svg id="docfig-dns" width={W} height={462} title="The DNS TXT step"
      desc="Step 1: AgenticOrg gives you a TXT record. Its name is _agenticorg-ucp.shop.merchant.example and its value is agenticorg-ucp-verify followed by a random code. Step 2: you add that record at your DNS provider. Step 3: AgenticOrg looks it up, and on a match it records that you control the host.">
      <circle cx={34} cy={30} r={14} className="fig-box fig-box-agenticorg" />
      <Text x={34} y={35} anchor="middle" kind="strong">1</Text>
      <Text x={58} y={35} kind="strong">AgenticOrg gives you a record</Text>
      <Box x={20} y={56} w={380} h={168} tone="neutral" />
      <Text x={36} y={84} kind="owner" tone="oacp">TYPE</Text>
      <Text x={36} y={106} kind="mono">TXT</Text>
      <Text x={36} y={136} kind="owner" tone="oacp">NAME</Text>
      <Text x={36} y={158} kind="mono">_agenticorg-ucp.shop.merchant.example</Text>
      <Text x={36} y={188} kind="owner" tone="oacp">VALUE</Text>
      <Text x={36} y={210} kind="mono">agenticorg-ucp-verify=&lt;random&gt;</Text>
      <circle cx={34} cy={262} r={14} className="fig-box fig-box-neutral" />
      <Text x={34} y={267} anchor="middle" kind="strong">2</Text>
      <Text x={58} y={267} kind="strong">You add it at your DNS provider</Text>
      <line x1={34} y1={238} x2={34} y2={246} className="fig-wire" />
      <line x1={34} y1={278} x2={34} y2={300} className="fig-wire" markerEnd="url(#docfig-dns-arrow)" />
      <circle cx={34} cy={318} r={14} className="fig-box fig-box-agenticorg" />
      <Text x={34} y={323} anchor="middle" kind="strong">3</Text>
      <Text x={58} y={323} kind="strong">AgenticOrg looks it up</Text>
      <Box x={20} y={348} w={380} h={100} tone="allow" />
      <Tick cx={46} cy={378} />
      <Text x={68} y={384} kind="strong" tone="allow">Host verified</Text>
      <Text x={36} y={412} kind="small">The proof is stored and the host is linked</Text>
      <Text x={36} y={432} kind="small">to your merchant. No other tenant can claim it.</Text>
    </Svg>
  );
}

function ArbiterRules() {
  const rules = [
    { title: "Mandate present", detail: "A buyer mandate, not expired" },
    { title: "Consent active", detail: "The buyer's consent still stands" },
    { title: "Spending cap", detail: "Within the Commerce Passport cap" },
    { title: "Verified buyer authority", detail: "Checked by AgenticOrg itself" },
    { title: "Facts still valid", detail: "Signed, fresh, not revoked" },
    { title: "Merchant limits", detail: "Your amount, currency, quantity" },
    { title: "Not-present limits", detail: "Stricter caps when the buyer is away" },
    { title: "RBI AFA", detail: "UPI AutoPay evidence when needed" },
  ];
  const top = 82;
  const rowH = 58;
  const gap = 8;
  const allowY = top + rules.length * (rowH + gap) + 14;
  return (
    <Svg id="docfig-rules" width={W} height={allowY + 64} title="Eight rules, one answer"
      desc="The first failure refuses, with that rule as the reason. The eight rules, in order: mandate present, consent active, spending cap, verified buyer authority, facts still valid, merchant limits, not-present limits, and RBI AFA. If all pass, the answer is allow.">
      <Box x={20} y={12} w={380} h={52} tone="refuse" />
      <Cross cx={46} cy={38} />
      <Text x={68} y={44} kind="body" tone="refuse">First failure: refuse, with that rule</Text>
      <line x1={34} y1={top + 20} x2={34} y2={allowY - 6} className="fig-wire fig-spine" />
      {rules.map((rule, i) => {
        const y = top + i * (rowH + gap);
        return (
          <g key={rule.title}>
            <circle cx={34} cy={y + rowH / 2} r={14} className="fig-box fig-box-agenticorg" />
            <Text x={34} y={y + rowH / 2 + 5.5} anchor="middle" kind="strong">{String(i + 1)}</Text>
            <Box x={58} y={y} w={342} h={rowH} tone="neutral" />
            <Text x={72} y={y + 24} kind="strong">{rule.title}</Text>
            <Text x={72} y={y + 45} kind="small">{rule.detail}</Text>
          </g>
        );
      })}
      <Box x={20} y={allowY} w={380} h={52} tone="allow" />
      <Tick cx={46} cy={allowY + 26} />
      <Text x={68} y={allowY + 32} kind="body" tone="allow">All pass: allow, single use, 10 minutes</Text>
    </Svg>
  );
}

function LedgerVerify() {
  return (
    <Svg id="docfig-ledger" width={W} height={378} title="Check the record yourself"
      desc="Export the ledger as a file. Run scripts/verify_ledger.py on your own computer, with no database and no network. Exit code 0 means verified. Exit code 1 names the first break, such as sequence_gap, link_hash or truncated.">
      <Box x={20} y={12} w={380} h={68} tone="agenticorg" />
      <path d="M 40 28 L 62 28 L 70 36 L 70 66 L 40 66 Z" className="fig-box fig-box-neutral" />
      <Text x={86} y={40} kind="owner" tone="agenticorg">Tenant administrator</Text>
      <Text x={86} y={64} kind="strong">Exports the ledger file</Text>
      <line x1={210} y1={80} x2={210} y2={102} className="fig-wire" markerEnd="url(#docfig-ledger-arrow)" />
      <Box x={20} y={106} w={380} h={92} tone="neutral" />
      <Text x={36} y={132} kind="owner" tone="oacp">ON YOUR OWN COMPUTER</Text>
      <Text x={36} y={158} kind="mono">verify_ledger.py export.json</Text>
      <Text x={36} y={182} kind="small">No database. No network.</Text>
      <line x1={110} y1={198} x2={110} y2={222} className="fig-wire" markerEnd="url(#docfig-ledger-arrow)" />
      <line x1={310} y1={198} x2={310} y2={222} className="fig-wire" markerEnd="url(#docfig-ledger-arrow)" />
      <Box x={20} y={226} w={184} h={140} tone="allow" />
      <Text x={36} y={254} kind="owner" tone="allow">EXIT 0</Text>
      <Text x={36} y={280} kind="strong">Verified</Text>
      <Text x={36} y={304} kind="small">Links, anchors and</Text>
      <Text x={36} y={322} kind="small">witnessed heads</Text>
      <Text x={36} y={340} kind="small">all match</Text>
      <Box x={216} y={226} w={184} h={140} tone="refuse" />
      <Text x={232} y={254} kind="owner" tone="refuse">EXIT 1</Text>
      <Text x={232} y={280} kind="strong">First break</Text>
      <Text x={232} y={304} kind="mono">sequence_gap</Text>
      <Text x={232} y={324} kind="mono">link_hash</Text>
      <Text x={232} y={344} kind="mono">truncated</Text>
    </Svg>
  );
}

function SignatureModes() {
  const panels: { mode: string; note: string; tone: Tone; rows: { label: string; outcome: string; tone: Tone }[] }[] = [
    { mode: "log", note: "the default", tone: "oacp", rows: [
      { label: "Signed and valid", outcome: "answered", tone: "allow" },
      { label: "Unsigned or invalid", outcome: "answered, logged", tone: "hold" },
      { label: "Public pages", outcome: "answered", tone: "allow" },
    ] },
    { mode: "enforce", note: "only by choice", tone: "agenticorg", rows: [
      { label: "Signed and valid", outcome: "answered", tone: "allow" },
      { label: "Unsigned or invalid", outcome: "401 refused", tone: "refuse" },
      { label: "Public pages", outcome: "answered", tone: "allow" },
    ] },
  ];
  const panelH = 52 + 3 * 44 + 8;
  return (
    <Svg id="docfig-httpsig" width={W} height={12 + panels.length * (panelH + 16) - 4} title="Log mode and enforce mode"
      desc="Two modes for agent request signatures. In log mode, the default, signed requests, unsigned or invalid requests and public pages are all answered, and unsigned ones are logged. In enforce mode, chosen deliberately, signed requests and public pages are answered and unsigned or invalid requests to the commerce API are refused with 401.">
      {panels.map((panel, p) => {
        const y = 12 + p * (panelH + 16);
        return (
          <g key={panel.mode}>
            <Box x={20} y={y} w={380} h={panelH} tone={panel.tone} />
            <Text x={36} y={y + 32} kind="title" tone={panel.tone}>{panel.mode}</Text>
            <Text x={384} y={y + 31} anchor="end" kind="small">{panel.note}</Text>
            {panel.rows.map((row, r) => {
              const ry = y + 48 + r * 44;
              const pill = row.outcome.length * 8 + 22;
              return (
                <g key={row.label}>
                  <Box x={32} y={ry} w={356} h={36} tone="neutral" rx={8} />
                  <Text x={46} y={ry + 23} kind="body">{row.label}</Text>
                  <Box x={380 - pill} y={ry + 6} w={pill} h={24} tone={row.tone} rx={12} />
                  <Text x={380 - pill / 2} y={ry + 22.5} anchor="middle" kind="small" tone={row.tone}>{row.outcome}</Text>
                </g>
              );
            })}
          </g>
        );
      })}
    </Svg>
  );
}

function Toggle({ x, y, on }: { x: number; y: number; on: boolean }) {
  return (
    <g>
      <rect x={x} y={y} width={44} height={24} rx={12} className={`fig-box fig-box-${on ? "allow" : "neutral"}`} />
      <circle cx={on ? x + 32 : x + 12} cy={y + 12} r={8} className="fig-knob" />
    </g>
  );
}

function SwitchBoard() {
  const panels: { title: string; tone: Tone; on: boolean; items: string[] }[] = [
    { title: "On in production", tone: "allow", on: true, items: [
      "Signature checks before keeping facts", "Verified buyer authority", "Grantex countersignature on allows", "Ledger row for every commerce write",
    ] },
    { title: "Off until you turn it on", tone: "hold", on: false, items: [
      "UCP profile and UCP checkout", "ACP feed and ACP checkout", "Grantex mandates", "Payment execution and refunds", "Chain sealing, witness, retention",
    ] },
    { title: "Log first, enforce by choice", tone: "oacp", on: false, items: [
      "Agent request signatures", "ACP request signatures: keep log",
    ] },
  ];
  const lineH = 26;
  let y = 12;
  const layout = panels.map((panel) => {
    const h = 56 + panel.items.length * lineH + 6;
    const entry = { ...panel, y, h };
    y += h + 14;
    return entry;
  });
  return (
    <Svg id="docfig-switches" width={W} height={y - 2} title="Safe by default"
      desc="Three groups of switches. On in production: signature checks before keeping facts, verified buyer authority, the Grantex countersignature on allows, and a ledger row for every commerce write. Off until you turn it on: the UCP profile and checkout, the ACP feed and checkout, Grantex mandates, payment execution and refunds, and chain sealing, witness and retention. Log first and enforce only by choice: agent request signatures; ACP request signatures stay in log.">
      {layout.map((panel) => (
        <g key={panel.title}>
          <Box x={20} y={panel.y} w={380} h={panel.h} tone={panel.tone} />
          <Toggle x={36} y={panel.y + 16} on={panel.on} />
          <Text x={92} y={panel.y + 34} kind="strong" tone={panel.tone}>{panel.title}</Text>
          {panel.items.map((item, i) => (
            <g key={item}>
              <circle cx={44} cy={panel.y + 66 + i * lineH} r={3.5} className={`fig-dot fig-dot-${panel.tone}`} />
              <Text x={58} y={panel.y + 71 + i * lineH} kind="small">{item}</Text>
            </g>
          ))}
        </g>
      ))}
    </Svg>
  );
}

function Roles() {
  const roles: { owner: string; tone: Tone; does: string; limit: string }[] = [
    { owner: "Your shop", tone: "neutral", does: "Owns products, prices, stock and orders", limit: "Stays the source of truth" },
    { owner: "Grantex", tone: "grantex", does: "Signs facts, revokes, issues mandates", limit: "Never moves money" },
    { owner: "AgenticOrg", tone: "agenticorg", does: "Verifies, decides, records, renders", limit: "Never charges without an allow" },
    { owner: "Payment provider", tone: "hold", does: "Charges and refunds", limit: "Only after a consumed allow" },
  ];
  const cardH = 92;
  const gap = 12;
  return (
    <Svg id="docfig-roles" width={W} height={12 + roles.length * (cardH + gap) - 0} title="Four roles, clear limits"
      desc="Your shop owns products, prices, stock and orders and stays the source of truth. Grantex signs facts, revokes them and issues mandates, and never moves money. AgenticOrg verifies, decides, records and renders, and never charges without an allow decision. The payment provider charges and refunds, only after a consumed allow decision.">
      {roles.map((role, i) => {
        const y = 12 + i * (cardH + gap);
        return (
          <g key={role.owner}>
            <Box x={20} y={y} w={380} h={cardH} tone={role.tone} />
            <Text x={36} y={y + 26} kind="owner" tone={role.tone}>{role.owner.toUpperCase()}</Text>
            <Text x={36} y={y + 52} kind="body">{role.does}</Text>
            <Text x={36} y={y + 76} kind="small">{role.limit}</Text>
          </g>
        );
      })}
    </Svg>
  );
}

// The live example merchant, Nilgiri Tea House (synthetic data; docs/runbooks/oacp-demo-merchant.md).
function NilgiriStory() {
  return (
    <StepStack id="docfig-nilgiri-story" title="Nilgiri Tea House, from shop to sale"
      desc="Six steps. The shop: Nilgiri Tea House in Ooty, ten teas and accessories priced in rupees. AgenticOrg syncs its catalog and its order limits. Grantex signs those facts: eleven signed artifacts, every one verified. The shop proves its own address, nilgiritea.agenticorg.ai, with a DNS TXT record. Buyer agents find it at /.well-known/ucp and search the catalog. Every checkout is decided by the arbiter's fixed rules and recorded in the ledger; payments are off in this demo."
      steps={[
        { owner: "The shop", tone: "neutral", title: "Nilgiri Tea House, Ooty", detail: "10 teas and accessories, priced in ₹" },
        { owner: "AgenticOrg", tone: "agenticorg", title: "Catalog synced, limits set", detail: "Products, prices, stock, order limits" },
        { owner: "Grantex", tone: "grantex", title: "Facts signed", detail: "11 signed artifacts, all verified", badge: "verified" },
        { owner: "DNS", tone: "oacp", title: "Its own address", detail: "nilgiritea.agenticorg.ai, proven by TXT" },
        { owner: "Buyer agents", tone: "neutral", title: "Find it and search it", detail: "Profile at /.well-known/ucp" },
        { owner: "AgenticOrg arbiter", tone: "agenticorg", title: "Every checkout decided", detail: "Fixed rules, recorded in the ledger", badge: "payments off", badgeTone: "hold" },
      ]} />
  );
}

function NilgiriShelf() {
  const items: { name: string; price: string; inStock: boolean }[] = [
    { name: "Frost Black Tea", price: "from ₹349", inStock: true },
    { name: "Doddabetta Green Tea", price: "₹399", inStock: true },
    { name: "Masala Chai Blend", price: "₹449", inStock: true },
    { name: "Homemade Chocolate Tea", price: "₹499", inStock: true },
    { name: "Blue Mountain Oolong", price: "₹899", inStock: false },
    { name: "Silver Tips White Tea", price: "₹1,299", inStock: true },
    { name: "Estate Sampler, 6 teas", price: "₹999", inStock: true },
    { name: "Cast Iron Kettle 800 ml", price: "₹2,499", inStock: true },
    { name: "Tea Infuser", price: "₹199", inStock: true },
    { name: "Tin Tea Caddy", price: "₹299", inStock: true },
  ];
  const top = 100;
  const rowH = 36;
  const height = top + items.length * rowH + 40;
  return (
    <Svg id="docfig-nilgiri-shelf" width={W} height={height} title="What a buyer agent sees"
      desc="The catalog that nilgiritea.agenticorg.ai serves to buyer agents over UCP 2026-04-08: Frost Black Tea from 349 rupees, Doddabetta Green Tea 399, Masala Chai Blend 449, Homemade Chocolate Tea 499, Blue Mountain Oolong 899 and sold out, Silver Tips White Tea 1,299, an Estate Sampler of six teas 999, a Cast Iron Kettle of 800 millilitres 2,499, a Tea Infuser 199 and a Tin Tea Caddy 299. Every other item is in stock. All of it is synthetic demo data.">
      <Box x={20} y={12} w={380} h={72} tone="oacp" />
      <Text x={36} y={38} kind="owner" tone="oacp">NILGIRITEA.AGENTICORG.AI</Text>
      <Text x={36} y={64} kind="strong">UCP 2026-04-08 · 10 products</Text>
      {items.map((item, i) => {
        const y = top + i * rowH;
        const tone: Tone = item.inStock ? "allow" : "refuse";
        return (
          <g key={item.name}>
            <line x1={20} y1={y + rowH} x2={400} y2={y + rowH} className="fig-wire" />
            <Text x={24} y={y + 23} kind="body">{item.name}</Text>
            <Text x={300} y={y + 23} anchor="end" kind="strong">{item.price}</Text>
            <Box x={314} y={y + 7} w={86} h={22} tone={tone} rx={11} />
            <Text x={357} y={y + 22.5} anchor="middle" kind="small" tone={tone}>{item.inStock ? "in stock" : "sold out"}</Text>
          </g>
        );
      })}
      <Text x={24} y={height - 12} kind="small">Synthetic demo data, prices in Indian rupees.</Text>
    </Svg>
  );
}

function NilgiriRecords() {
  const records = [
    { label: "RECORD 1 · ROUTES THE HOST", type: "CNAME", name: "nilgiritea", value: "ghs.googlehosted.com" },
    { label: "RECORD 2 · PROVES OWNERSHIP", type: "TXT", name: "_agenticorg-ucp.nilgiritea", value: "agenticorg-ucp-verify=rYSvH2jU…" },
  ];
  const cardH = 150;
  const gap = 16;
  const resultY = 12 + records.length * (cardH + gap);
  return (
    <Svg id="docfig-nilgiri-records" width={W} height={resultY + 100} title="Nilgiri Tea House's two DNS records"
      desc="Both records live in the agenticorg.ai DNS zone. Record 1 routes the host: a CNAME named nilgiritea pointing to ghs.googlehosted.com. Record 2 proves ownership: a TXT record named _agenticorg-ucp.nilgiritea whose value is agenticorg-ucp-verify followed by the code AgenticOrg issued. With both in place the host was attested and the profile published under the namespace ai.agenticorg.nilgiritea.">
      {records.map((record, i) => {
        const y = 12 + i * (cardH + gap);
        return (
          <g key={record.type}>
            <Box x={20} y={y} w={380} h={cardH} tone="neutral" />
            <Text x={36} y={y + 26} kind="owner" tone="oacp">{record.label}</Text>
            <Text x={36} y={y + 54} kind="small">Type</Text>
            <Text x={110} y={y + 54} kind="mono">{record.type}</Text>
            <Text x={36} y={y + 82} kind="small">Name, in the agenticorg.ai zone</Text>
            <Text x={36} y={y + 104} kind="mono">{record.name}</Text>
            <Text x={36} y={y + 128} kind="small">Value</Text>
            <Text x={110} y={y + 128} kind="mono">{record.value}</Text>
          </g>
        );
      })}
      <Box x={20} y={resultY} w={380} h={84} tone="allow" />
      <Tick cx={46} cy={resultY + 30} />
      <Text x={68} y={resultY + 36} kind="strong" tone="allow">Host attested, profile live</Text>
      <Text x={36} y={resultY + 66} kind="small">Namespace ai.agenticorg.nilgiritea</Text>
    </Svg>
  );
}

function NilgiriBuyer() {
  return (
    <StepStack id="docfig-nilgiri-buyer" title="A buyer agent shops at Nilgiri Tea House"
      desc="Seven steps. The buyer agent reads the shop's profile at /.well-known/ucp. It searches the catalog: chai finds the Masala Chai Blend at 449 rupees. AgenticOrg answers only from facts Grantex signed. The agent opens a checkout with its platform API key. Grantex issues Intent, Cart and Payment mandates for that exact cart. The arbiter allows or refuses by fixed rules and records the decision in the ledger. A payment provider would charge once, only on allow; payments are off in this demo."
      steps={[
        { owner: "Buyer agent", tone: "neutral", title: "Reads the shop's profile", detail: "GET /.well-known/ucp" },
        { owner: "Buyer agent", tone: "neutral", title: "Searches the catalog", detail: "\"chai\" finds Masala Chai Blend, ₹449" },
        { owner: "AgenticOrg", tone: "agenticorg", title: "Answers from signed facts", detail: "Price and stock verified, never guessed" },
        { owner: "Buyer agent", tone: "neutral", title: "Opens a checkout", detail: "With its own platform API key" },
        { owner: "Grantex", tone: "grantex", title: "Mandates for this cart", detail: "Intent, Cart and Payment" },
        { owner: "AgenticOrg arbiter", tone: "agenticorg", title: "Allow or refuse", detail: "Fixed rules, sealed in the ledger", badge: "recorded" },
        { owner: "Payment provider", tone: "hold", title: "Charges once, on allow", detail: "Switched off in this demo", badge: "off in demo", badgeTone: "hold" },
      ]} />
  );
}

// The AgenticOrg shopping network (docs/oacp/shopping-network.md).
function SellEverywhereNetwork() {
  return (
    <StepStack id="docfig-sell-everywhere" title="One switch, every surface"
      desc="Six steps. The merchant connects its catalog, checks its policies and turns on Sell everywhere once. AgenticOrg lists the shop in the network directory and gives it a web address if it has none. ChatGPT, Claude and other AI apps reach every network shop through one AgenticOrg server. The AgenticOrg shopping bot on Telegram answers anyone, with a link for each shop. Google, Microsoft Copilot and Perplexity are next, once AgenticOrg's registrations are approved. Buyers always finish and pay with the shop."
      steps={[
        { owner: "Merchant", tone: "neutral", title: "Turns on Sell everywhere", detail: "Catalog, policies, one consent" },
        { owner: "AgenticOrg", tone: "agenticorg", title: "Lists the shop", detail: "Network directory and a web address" },
        { owner: "AI apps", tone: "oacp", title: "ChatGPT, Claude and others", detail: "One server for every network shop" },
        { owner: "Telegram", tone: "oacp", title: "The AgenticOrg shopping bot", detail: "A link for each shop" },
        { owner: "Next", tone: "hold", title: "Google, Copilot, Perplexity", detail: "After AgenticOrg's registrations", badge: "coming", badgeTone: "hold" },
        { owner: "The shop", tone: "neutral", title: "Takes the payment", detail: "Buyers finish and pay with you" },
      ]} />
  );
}

const FIGURES: Record<string, { wide?: boolean; render: () => ReactNode }> = {
  "sell-everywhere-network": { render: () => <SellEverywhereNetwork /> },
  "nilgiri-story": { render: () => <NilgiriStory /> },
  "nilgiri-shelf": { render: () => <NilgiriShelf /> },
  "nilgiri-records": { render: () => <NilgiriRecords /> },
  "nilgiri-buyer": { render: () => <NilgiriBuyer /> },
  "oacp-five-copies": { wide: true, render: () => <div className="fig-pair"><WiredPanel /><HubPanel /></div> },
  "oacp-standards-map": { render: () => <StandardsMap /> },
  "oacp-docs-flow": { render: () => <DocsFlow /> },
  "oacp-verify-gate": { render: () => <VerifyGate /> },
  "oacp-docs-journey": { render: () => <DocsJourney /> },
  "oacp-decision-fork": { render: () => <DecisionFork /> },
  "oacp-setup-path": { render: () => <SetupPath /> },
  "oacp-dns-txt": { render: () => <DnsTxt /> },
  "oacp-custom-domain": { render: () => <CustomDomainPath /> },
  "oacp-revocation": { render: () => <RevocationFigure /> },
  "oacp-arbiter-rules": { render: () => <ArbiterRules /> },
  "oacp-ledger-verify": { render: () => <LedgerVerify /> },
  "oacp-httpsig-modes": { render: () => <SignatureModes /> },
  "oacp-switch-board": { render: () => <SwitchBoard /> },
  "oacp-roles": { render: () => <Roles /> },
};

/** One guide figure: its title and caption from the guide, and the diagram by id. */
export default function DocsFigure({ id, title, caption }: { id: string; title: string; caption: string }) {
  const figure = FIGURES[id];
  const titleId = `docs-figure-${id}-title`;
  return (
    <figure className={`docs-figure${figure?.wide ? " docs-figure-wide" : ""}`} data-docs-figure={id} aria-labelledby={titleId}>
      <figcaption>
        <p className="docs-figure-title" id={titleId}>{title}</p>
        <p className="docs-figure-caption">{caption}</p>
      </figcaption>
      {figure && <div className="docs-figure-canvas">{figure.render()}</div>}
    </figure>
  );
}
