// SPDX-License-Identifier: Apache-2.0
import { useRef, useState, type KeyboardEvent } from "react";
import {
  Ban,
  Check,
  Eye,
  Filter,
  KeyRound,
  UserCheck,
  UserRound,
  type LucideIcon,
} from "lucide-react";
import "./spend-pipeline.css";

type Level = "full" | "filtered" | "refused";

const readers: readonly {
  id: string;
  label: string;
  note: string;
  icon: LucideIcon;
}[] = [
  {
    id: "administrator",
    label: "Administrator",
    note: "A person signed in as a tenant administrator",
    icon: UserCheck,
  },
  {
    id: "auditor",
    label: "Auditor",
    note: "A person whose domains are unrestricted",
    icon: Eye,
  },
  {
    id: "domain",
    label: "Domain role",
    note: "A person whose domains are restricted, such as the CFO role",
    icon: UserRound,
  },
  {
    id: "machine",
    label: "API key or agent token",
    note: "A machine credential",
    icon: KeyRound,
  },
];

const reads: readonly {
  label: string;
  request: string;
  access: Record<string, { level: Level; text: string }>;
}[] = [
  {
    label: "Usage records and rollups",
    request: "GET /spend/usage, /spend/rollups",
    access: {
      administrator: { level: "full", text: "Every record and row, with user ids and contract terms." },
      auditor: { level: "full", text: "Every record and row, with user ids and contract terms." },
      domain: {
        level: "filtered",
        text: "Records with no agent, shared agents in their domains and their own personal agents. Contract terms and user ids hidden; no rate-card or commitment grouping.",
      },
      machine: {
        level: "filtered",
        text: "Records with no agent and of every shared agent, or every record with the administrator scope. Contract terms and user ids hidden.",
      },
    },
  },
  {
    label: "Coverage, meter gaps, jobs and GPU allocations",
    request: "GET /spend/coverage, /spend/gaps, /spend/jobs, /spend/gpu-allocations",
    access: {
      administrator: { level: "full", text: "The whole tenant." },
      auditor: { level: "full", text: "The whole tenant." },
      domain: { level: "refused", text: "Refused: 403 tenant_wide_read_refused." },
      machine: {
        level: "refused",
        text: "Refused without the administrator scope: 403 tenant_wide_read_refused.",
      },
    },
  },
  {
    label: "Rate cards, commitments and price quotes",
    request: "GET /spend/rate-cards, /spend/commitments, /spend/price",
    access: {
      administrator: { level: "full", text: "Yes." },
      auditor: { level: "full", text: "Yes." },
      domain: { level: "refused", text: "Refused: 403 commercial_read_refused." },
      machine: {
        level: "refused",
        text: "Refused whatever its scopes: 403 commercial_read_refused.",
      },
    },
  },
  {
    label: "Invoices, reconciliations and Gate 1",
    request: "GET /spend/invoices, /spend/reconciliations, /spend/gate",
    access: {
      administrator: { level: "full", text: "Yes." },
      auditor: { level: "full", text: "Yes." },
      domain: { level: "refused", text: "Refused: 403 commercial_read_refused." },
      machine: {
        level: "refused",
        text: "Refused whatever its scopes: 403 commercial_read_refused.",
      },
    },
  },
  {
    label: "Changes, imports, jobs and acceptance",
    request: "POST, PUT and PATCH under /spend",
    access: {
      administrator: {
        level: "full",
        text: "Yes, signed in as a person. Never the acceptance of a difference on an invoice they imported.",
      },
      auditor: { level: "refused", text: "No: changes need a tenant administrator." },
      domain: { level: "refused", text: "No: approvals:write alone is not enough." },
      machine: { level: "refused", text: "No: changes need a person, not a credential." },
    },
  },
];

const levels: Record<Level, { label: string; icon: LucideIcon }> = {
  full: { label: "Reads", icon: Check },
  filtered: { label: "Filtered", icon: Filter },
  refused: { label: "Refused", icon: Ban },
};

export default function SpendReadersMap() {
  const [active, setActive] = useState(0);
  const buttons = useRef<(HTMLButtonElement | null)[]>([]);
  const reader = readers[active];
  const tally = (level: Level) => reads.filter((read) => read.access[reader.id].level === level).length;

  function onReaderKey(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    const moves: Record<string, number> = {
      ArrowRight: (index + 1) % readers.length,
      ArrowDown: (index + 1) % readers.length,
      ArrowLeft: (index - 1 + readers.length) % readers.length,
      ArrowUp: (index - 1 + readers.length) % readers.length,
      Home: 0,
      End: readers.length - 1,
    };
    if (!(event.key in moves)) return;
    event.preventDefault();
    setActive(moves[event.key]);
    buttons.current[moves[event.key]]?.focus();
  }

  return (
    <section className="docs-spend-readers" aria-label="Who reads spend figures">
      <div className="docs-spend-head">
        <h2>Who reads what</h2>
        <p>
          Choose a reader to see which spend figures they get. Commercial
          figures are for a human administrator or auditor only.
        </p>
      </div>
      <div className="docs-spend-readers-choice" role="group" aria-label="Choose a reader">
        {readers.map((item, index) => {
          const Icon = item.icon;
          return (
            <button
              key={item.id}
              ref={(element) => {
                buttons.current[index] = element;
              }}
              type="button"
              aria-pressed={index === active}
              tabIndex={index === active ? 0 : -1}
              onClick={() => setActive(index)}
              onKeyDown={(event) => onReaderKey(event, index)}
            >
              <Icon size={17} aria-hidden="true" />
              <span>
                <strong>{item.label}</strong>
                <small>{item.note}</small>
              </span>
            </button>
          );
        })}
      </div>
      <p className="sr-only" role="status">
        {reader.label}: {tally("full")} read, {tally("filtered")} filtered, {tally("refused")} refused
      </p>
      <ul
        className="docs-spend-readers-list"
        aria-label={`What ${reader.label.toLowerCase()} reads`}
      >
        {reads.map((read) => {
          const access = read.access[reader.id];
          const level = levels[access.level];
          const LevelIcon = level.icon;
          return (
            <li key={read.label} className={`is-${access.level}`}>
              <span className="docs-spend-readers-level">
                <LevelIcon size={15} aria-hidden="true" />
                {level.label}
              </span>
              <div>
                <strong>{read.label}</strong>
                <p>{access.text}</p>
                <code>{read.request}</code>
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
