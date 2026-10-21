// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
import { useRef, useState, type KeyboardEvent } from "react";
import {
  ArrowLeft,
  ArrowRight,
  BadgeCheck,
  ChartColumn,
  Database,
  Gauge,
  IndianRupee,
  Network,
  Receipt,
  Scale,
  Zap,
  type LucideIcon,
} from "lucide-react";
import "./spend-pipeline.css";

const phases = [
  { id: "meter", title: "Meter", note: "Count the usage with no database work on the call path" },
  { id: "price", title: "Price and record", note: "Attribute, price in rupees and sum" },
  { id: "reconcile", title: "Reconcile", note: "Check against invoices and the gate" },
] as const;

type PhaseId = (typeof phases)[number]["id"];

const stages: readonly {
  title: string;
  label: string;
  phase: PhaseId;
  icon: LucideIcon;
  happens: string;
  kept: string;
  boundary: string;
  request: string;
  owner: string;
}[] = [
  {
    title: "Billable event",
    label: "A model call or other usage",
    phase: "meter",
    icon: Zap,
    happens:
      "A model call finishes, or another billable event happens: an embedding, OCR pages, a transcribed recording, a priced tool call, the daily storage sample or an hour of in-house GPU serving.",
    kept: "Counts, lengths and ids read from objects the call site already holds. Never a prompt, a response, a document or customer data.",
    boundary:
      "Nothing is metered while AGENTICORG_SPEND_INTELLIGENCE_ENABLED is off; each call site is then a single setting check.",
    request: "No request: metering starts at the call site.",
    owner: "The runtime",
  },
  {
    title: "Meter",
    label: "Non-blocking writer",
    phase: "meter",
    icon: Gauge,
    happens:
      "The hook builds the usage events in memory and puts them on a bounded in-process queue. A writer thread in each process writes them in batches, tenant by tenant, on its own database connections.",
    kept: "Each event carries an idempotency key, so a retry, a hand-off to the maintenance queue or a backfill never writes a record twice.",
    boundary:
      "A metering failure is logged and counted, and the call carries on unchanged. Usage that cannot be written is counted as a meter gap with its reason.",
    request: "GET /api/v1/spend/status\nGET /api/v1/spend/gaps",
    owner: "The runtime; an administrator or auditor reads the gaps",
  },
  {
    title: "Attribution",
    label: "Mappings and the organisation tree",
    phase: "meter",
    icon: Network,
    happens:
      "The server works out who the usage belongs to: the agent's mapping, a legacy cost-centre label, the workflow's or application's mapping, or the initiating user's department. The first match wins.",
    kept: "The node, its business unit (the nearest at or above it), the product line, the use case and the attribution path, or the reason it is unattributed.",
    boundary:
      "Labels a caller sends are ignored. A legacy label that names no node stays unattributed as unknown_label; it is never moved to a broader mapping.",
    request: "POST /api/v1/spend/org-nodes\nPUT /api/v1/spend/mappings",
    owner: "An administrator keeps the tree and the mappings",
  },
  {
    title: "Priced in rupees",
    label: "Contract or list rate cards, FX",
    phase: "price",
    icon: IndianRupee,
    happens:
      "The card in force on the provider's billing date prices the usage: model-specific before provider default, a cached price before the plain input price, contract before list. The amount converts to INR at the reporting date's rate.",
    kept: "The card, unit price, amount and currency, the FX rate and its date, and the INR amount, all as exact decimals.",
    boundary:
      "No card and no fallback price means unpriced: no amount, never zero. With no rate that day the latest earlier rate is used (fx_estimated); with no rate at all the record is unconverted.",
    request:
      "POST /api/v1/spend/rate-cards\nPUT /api/v1/spend/fx-rates\nGET /api/v1/spend/price",
    owner: "An administrator enters cards, rates and commitments",
  },
  {
    title: "Usage record",
    label: "Append-only, attributed, priced",
    phase: "price",
    icon: Database,
    happens:
      "Each billable quantity in one unit becomes one record: a model call gives up to three (uncached input, cached input and output tokens).",
    kept: "Event time, reporting and billing dates, provider, model, quantity, price, attribution and who paid: the tenant's key, the platform's key or in-house.",
    boundary:
      "Event fields never change. Only audited maintenance jobs revise prices, FX, the attribution of unattributed records or commitment drawdown.",
    request: "GET /api/v1/spend/usage?start=…&end=…",
    owner: "Readers under the agent visibility rule",
  },
  {
    title: "Rollups and coverage",
    label: "Daily sums, attributed share",
    phase: "price",
    icon: ChartColumn,
    happens:
      "Each record adds to its day's rollup in the same transaction. Coverage reports the attributed share, the unattributed share and its reasons, unpriced and unconverted volume and meter gaps.",
    kept: "Sums per day and dimension, per currency and in INR. Shares to six decimals, rounded so they never look better than they are.",
    boundary:
      "Attribution to a group node does not count. Coverage is a tenant-wide figure: an administrator or auditor reads it, anyone else is refused.",
    request: "GET /api/v1/spend/rollups?group_by=…\nGET /api/v1/spend/coverage",
    owner: "An administrator or auditor; the CFO and other domain roles are refused",
  },
  {
    title: "Invoice import",
    label: "One provider, one billing month",
    phase: "reconcile",
    icon: Receipt,
    happens:
      "An administrator imports the provider's invoice or billing export, CSV or JSON, for one billing month in the provider's own zone. A dry run reports the outcome without writing.",
    kept: "The invoice whole, with its usage, credit, tax, fee and commitment lines. A replacement supersedes the earlier invoice; both are kept.",
    boundary:
      "One refused line refuses the whole file (422 invoice_rejected), because a partial invoice would reconcile against a wrong total.",
    request:
      "POST /api/v1/spend/invoices/import\n?provider=…&period=YYYY-MM&invoice_ref=…&currency=…",
    owner: "Administrator A, the importer",
  },
  {
    title: "Reconciliation",
    label: "Both figures within 1%",
    phase: "reconcile",
    icon: Scale,
    happens:
      "The month's tenant-billed usage is compared with the current invoices in two figures: as stored, and re-priced at the cards in force as known now, with volume tiers and commitment overage added.",
    kept: "Items with both variances, the cards and FX rows used, data entered after the month, and digests that mark the run stale when what it read changes.",
    boundary:
      "An item passes only when both figures are within 1%; otherwise it needs review. Another administrator, never the importer, may accept it with a written reason.",
    request:
      "POST /api/v1/spend/reconciliations\nPOST …/reconciliations/{id}/items/{item_id}/accept",
    owner: "Administrator B accepts differences",
  },
  {
    title: "Gate 1",
    label: "Attributed and reconciled",
    phase: "reconcile",
    icon: BadgeCheck,
    happens:
      "The month's status: an attributed share of at least 98% with no unpriced, unconverted or FX-pending record, and every provider reconciled within 1% by a current, fresh run.",
    kept: "The shares and reasons, missing and stale reconciliations, accepted exceptions and whether lost model-call writes can still be recreated.",
    boundary:
      "An accepted difference does not meet the gate. Verdicts use exact values, so a share shown as 0.979996 is not met.",
    request: "GET /api/v1/spend/gate?period=YYYY-MM",
    owner: "An administrator or auditor; the CFO and other domain roles are refused",
  },
];

const pad = (value: number) => String(value).padStart(2, "0");

/* Below this width the stages stack above the detail panel, so a chosen stage scrolls it into view. */
const STACKED = "(max-width: 640px)";

export default function SpendPipelineMap() {
  const [active, setActive] = useState(0);
  const buttons = useRef<(HTMLButtonElement | null)[]>([]);
  const detail = useRef<HTMLDivElement>(null);
  const stage = stages[active];
  const first = active === 0;
  const last = active === stages.length - 1;
  const phase = phases.find((item) => item.id === stage.phase) ?? phases[0];
  const StageIcon = stage.icon;

  function select(index: number, focus = false) {
    const next = Math.max(0, Math.min(stages.length - 1, index));
    setActive(next);
    if (focus) buttons.current[next]?.focus();
  }

  function choose(index: number) {
    select(index);
    const panel = detail.current;
    if (!panel || typeof window.matchMedia !== "function" || !window.matchMedia(STACKED).matches) return;
    // The sticky docs header wraps to a different height at different widths, so clear its real height.
    const header = document.querySelector<HTMLElement>(".docs-header");
    if (header) panel.style.scrollMarginTop = `${header.getBoundingClientRect().height + 14}px`;
    panel.scrollIntoView({ block: "start" });
  }

  function onStageKey(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    const moves: Record<string, number> = {
      ArrowRight: index + 1,
      ArrowDown: index + 1,
      ArrowLeft: index - 1,
      ArrowUp: index - 1,
      Home: 0,
      End: stages.length - 1,
    };
    if (!(event.key in moves)) return;
    event.preventDefault();
    select(moves[event.key], true);
  }

  return (
    <section className="docs-spend" aria-label="AI spend pipeline map">
      <div className="docs-spend-head">
        <p className="docs-eyebrow">Interactive pipeline</p>
        <h2>From a billable event to the Gate 1 status</h2>
        <p>
          Select a stage, or move with the arrow keys, to see what happens,
          what is kept and where it stops.
        </p>
      </div>
      <div className="docs-spend-phases">
        {phases.map((item) => (
          <div
            key={item.id}
            className={`docs-spend-phase docs-spend-phase-${item.id}`}
            role="group"
            aria-label={`${item.title} stages`}
          >
            <div className="docs-spend-phase-label">
              <strong>{item.title}</strong>
              <small>{item.note}</small>
            </div>
            <ol>
              {stages.map((entry, index) =>
                entry.phase !== item.id ? null : (
                  <li key={entry.title}>
                    <button
                      ref={(element) => {
                        buttons.current[index] = element;
                      }}
                      type="button"
                      className="docs-spend-stage"
                      aria-current={index === active ? "step" : undefined}
                      aria-controls="docs-spend-detail"
                      aria-label={`Stage ${index + 1} of ${stages.length}: ${entry.title}`}
                      tabIndex={index === active ? 0 : -1}
                      onClick={() => choose(index)}
                      onKeyDown={(event) => onStageKey(event, index)}
                    >
                      <span className="docs-spend-stage-number">
                        {pad(index + 1)}
                      </span>
                      <span className="docs-spend-stage-text">
                        <strong>{entry.title}</strong>
                        <small>{entry.label}</small>
                      </span>
                    </button>
                  </li>
                ),
              )}
            </ol>
          </div>
        ))}
      </div>
      <p className="sr-only" role="status">
        Stage {active + 1} of {stages.length}: {stage.title}
      </p>
      <div
        id="docs-spend-detail"
        ref={detail}
        className={`docs-spend-detail docs-spend-detail-${stage.phase}`}
      >
        <div className="docs-spend-detail-top">
          <span className="docs-spend-detail-phase">{phase.title}</span>
          <small>
            Stage {active + 1} of {stages.length}
          </small>
        </div>
        <div className="docs-spend-detail-title">
          <span className="docs-spend-detail-icon">
            <StageIcon size={22} aria-hidden="true" />
          </span>
          <div>
            <h3>{stage.title}</h3>
            <p>{stage.label}</p>
          </div>
        </div>
        <dl>
          <div>
            <dt>What happens</dt>
            <dd>{stage.happens}</dd>
          </div>
          <div>
            <dt>What is kept</dt>
            <dd>{stage.kept}</dd>
          </div>
          <div>
            <dt>Where it stops</dt>
            <dd>{stage.boundary}</dd>
          </div>
          <div>
            <dt>Who</dt>
            <dd>{stage.owner}</dd>
          </div>
        </dl>
        <pre aria-label="Requests for this stage">
          <code>{stage.request}</code>
        </pre>
        <div className="docs-spend-controls">
          <button
            type="button"
            title="Previous stage"
            aria-label="Previous stage"
            aria-disabled={first}
            onClick={() => {
              if (!first) select(active - 1);
            }}
          >
            <ArrowLeft size={17} aria-hidden="true" />
          </button>
          <span>
            {pad(active + 1)} / {pad(stages.length)}
          </span>
          <button
            type="button"
            title="Next stage"
            aria-label="Next stage"
            aria-disabled={last}
            onClick={() => {
              if (!last) select(active + 1);
            }}
          >
            <ArrowRight size={17} aria-hidden="true" />
          </button>
        </div>
      </div>
      <p className="docs-spend-note">
        An explanatory map of phase 1, not a live view of your spend. Spend
        intelligence is off by default and is read and written through the API;
        there is no spend screen in the app.
      </p>
    </section>
  );
}
