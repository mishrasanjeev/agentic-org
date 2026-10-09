// SPDX-License-Identifier: Apache-2.0
import { useEffect, useState } from "react";
import { Helmet } from "react-helmet-async";
import { Link } from "react-router";
import api, { extractApiError } from "@/lib/api";

interface Entry {
  agent_id: string;
  name: string;
  agent_type: string;
  domain: string;
  status: string;
  purpose: string | null;
  risk_tier: string | null;
  use_case: string | null;
  channels: string[];
  state: string;
  environment: string | null;
}

interface CatalogueOut {
  entries: Entry[];
  states: string[];
  risk_tiers: string[];
  channels: string[];
}

interface Template {
  pack: string;
  pack_display_name: string;
  installable: boolean;
  install_disabled_reason: string;
  agent_type: string;
  name: string;
  domain: string | null;
  description: string;
  model: string | null;
  tools: string[];
  hitl_condition: string | null;
  confidence_floor: number | null;
  compliance: string[];
}

const DOMAINS = ["finance", "ops", "hr", "marketing", "sales", "legal"];

function stateClass(state: string): string {
  if (state === "published") return "bg-emerald-100 text-emerald-800";
  if (state === "approved") return "bg-sky-100 text-sky-800";
  if (state === "review") return "bg-amber-100 text-amber-800";
  if (state === "deprecated" || state === "retired") return "bg-slate-200 text-slate-600";
  return "bg-slate-100 text-slate-700";
}

/**
 * The agent catalogue: every agent the caller may see with its registry card
 * fields and lifecycle state, filtered by domain, use case, channel, risk tier
 * and approval state, searched by name, type, purpose or use case; and the
 * templates the industry packs offer. Absent where the registry is off.
 */
export default function AgentCatalogue() {
  const [data, setData] = useState<CatalogueOut | null>(null);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [off, setOff] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [q, setQ] = useState("");
  const [domain, setDomain] = useState("");
  const [state, setState] = useState("");
  const [riskTier, setRiskTier] = useState("");
  const [channel, setChannel] = useState("");
  const [useCase, setUseCase] = useState("");
  const [showTemplates, setShowTemplates] = useState(false);

  useEffect(() => {
    let current = true;
    setError(null);
    setLoading(true);
    const params: Record<string, string> = {};
    if (q.trim()) params.q = q.trim();
    if (domain) params.domain = domain;
    if (state) params.state = state;
    if (riskTier) params.risk_tier = riskTier;
    if (channel) params.channel = channel;
    if (useCase.trim()) params.use_case = useCase.trim();
    const load = async () => {
      try {
        const response = await api.get("/agent-registry", { params });
        if (!current) return;
        setData(response.data as CatalogueOut);
        setOff(false);
      } catch (err) {
        if (!current) return;
        setData(null);
        const status = (err as { response?: { status?: number } })?.response?.status;
        if (status === 409) {
          setOff(true);
          return;
        }
        setError(extractApiError(err, "The catalogue could not be loaded."));
      } finally {
        if (current) setLoading(false);
      }
    };
    void load();
    return () => { current = false; };
  }, [q, domain, state, riskTier, channel, useCase]);

  useEffect(() => {
    if (off || !showTemplates || templates.length > 0) return;
    let current = true;
    api
      .get("/agent-registry/templates")
      .then((response) => { if (current) setTemplates(response.data.templates as Template[]); })
      .catch((err) => { if (current) setError(extractApiError(err, "The templates could not be loaded.")); });
    return () => { current = false; };
  }, [off, showTemplates, templates.length]);

  return (
    <div className="min-w-0 space-y-4">
      <Helmet>
        <title>Agent catalogue</title>
      </Helmet>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-2xl font-bold">Agent catalogue</h2>
        <button
          type="button"
          disabled={off}
          aria-expanded={showTemplates && !off}
          className="rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-700"
          onClick={() => setShowTemplates((current) => !current)}
          data-testid="catalogue-templates-toggle"
        >
          {showTemplates ? "Hide templates" : "Templates"}
        </button>
      </div>
      {off && (
        <p className="text-sm text-slate-500" data-testid="catalogue-off">
          The agent registry is off in this deployment.
        </p>
      )}
      {error && (
        <div role="alert" className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
          {error}
        </div>
      )}
      {!off && (
        <div className="flex flex-wrap gap-2" data-testid="catalogue-filters">
          <input
            aria-label="Search agents"
            className="w-56 rounded-md border border-slate-300 px-2 py-1 text-sm"
            placeholder="Search name, type, purpose"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            data-testid="catalogue-q"
          />
          <select aria-label="Domain" className="rounded-md border border-slate-300 px-2 py-1 text-sm" value={domain} onChange={(e) => setDomain(e.target.value)} data-testid="catalogue-domain">
            <option value="">All domains</option>
            {DOMAINS.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
          <select aria-label="Approval state" className="rounded-md border border-slate-300 px-2 py-1 text-sm" value={state} onChange={(e) => setState(e.target.value)} data-testid="catalogue-state">
            <option value="">All states</option>
            {(data?.states ?? []).map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
          <select aria-label="Risk tier" className="rounded-md border border-slate-300 px-2 py-1 text-sm" value={riskTier} onChange={(e) => setRiskTier(e.target.value)} data-testid="catalogue-risk">
            <option value="">All risk tiers</option>
            {(data?.risk_tiers ?? []).map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
          <select aria-label="Channel" className="rounded-md border border-slate-300 px-2 py-1 text-sm" value={channel} onChange={(e) => setChannel(e.target.value)} data-testid="catalogue-channel">
            <option value="">All channels</option>
            {(data?.channels ?? []).map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
          <input
            aria-label="Use case"
            className="w-40 rounded-md border border-slate-300 px-2 py-1 text-sm"
            placeholder="Use case"
            value={useCase}
            onChange={(e) => setUseCase(e.target.value)}
            data-testid="catalogue-use-case"
          />
        </div>
      )}
      {loading && <p role="status" className="text-sm text-slate-600">Loading catalogue...</p>}
      {data && !loading && (
        <div className="overflow-x-auto">
        <table aria-label="Agent catalogue" className="w-full min-w-[640px] text-left text-sm" data-testid="catalogue-table">
          <thead className="text-xs uppercase text-slate-500">
            <tr>
              <th className="py-1">Agent</th>
              <th>Domain</th>
              <th>Use case</th>
              <th>Channels</th>
              <th>Risk</th>
              <th>State</th>
              <th>Runtime</th>
            </tr>
          </thead>
          <tbody>
            {data.entries.length === 0 && (
              <tr>
                <td className="py-2 text-slate-500" colSpan={7}>
                  No agents match.
                </td>
              </tr>
            )}
            {data.entries.map((entry) => (
              <tr
                key={entry.agent_id}
                className="border-t border-slate-100 hover:bg-slate-50"
                data-testid={`catalogue-row-${entry.agent_id}`}
              >
                <td className="py-1.5">
                  <Link className="font-medium text-slate-800 underline focus-visible:outline-2" to={`/dashboard/agents/${entry.agent_id}`}>{entry.name}</Link>
                  <span className="ml-2 text-xs text-slate-500">{entry.agent_type}</span>
                  {entry.purpose && <p className="text-xs text-slate-500">{entry.purpose}</p>}
                </td>
                <td>{entry.domain}</td>
                <td>{entry.use_case ?? ""}</td>
                <td>{entry.channels.join(", ")}</td>
                <td>{entry.risk_tier ?? ""}</td>
                <td>
                  <span className={`rounded px-2 py-0.5 text-xs ${stateClass(entry.state)}`}>{entry.state}</span>
                  {entry.environment && <span className="ml-1 text-xs text-slate-500">{entry.environment}</span>}
                </td>
                <td>{entry.status}</td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      )}
      {!off && showTemplates && templates.length > 0 && (
        <div data-testid="catalogue-templates">
          <h3 className="text-sm font-semibold text-slate-800">Templates from the industry packs</h3>
          <p className="text-xs text-slate-500">Install a pack from Industry Packs to create its agents in shadow mode.</p>
          <ul className="mt-2 divide-y divide-slate-100 text-sm">
            {templates.map((template) => (
              <li key={`${template.pack}:${template.agent_type}`} className="py-2" data-testid={`template-${template.pack}-${template.agent_type}`}>
                <span className="font-medium text-slate-800">{template.name}</span>
                <span className="ml-2 text-xs text-slate-500">
                  {template.pack_display_name} · {template.domain ?? ""} · {template.model ?? ""}
                  {template.confidence_floor !== null ? ` · floor ${Math.round(template.confidence_floor * 100)}%` : ""}
                  {template.installable ? "" : ` · not installable: ${template.install_disabled_reason}`}
                </span>
                {template.description && <p className="text-xs text-slate-600">{template.description}</p>}
                <p className="text-xs text-slate-500">Tools: {template.tools.join(", ") || "none"}</p>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
