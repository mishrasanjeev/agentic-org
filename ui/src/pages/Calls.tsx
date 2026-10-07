// SPDX-License-Identifier: Apache-2.0
import { useCallback, useEffect, useRef, useState } from "react";
import { Helmet } from "react-helmet-async";
import api, { extractApiError } from "@/lib/api";
import LiveAssist from "@/components/LiveAssist";

/**
 * Calls: the kept recordings with who spoke when, the transcript as turns, the summary (intent, key
 * points, next actions, outcome) and the analytics (sentiment, empathy, talk ratio, signals), and
 * the overview across calls. Summaries are requested from here; transcripts come from the engines.
 */

interface RecordingRow {
  id: string;
  filename: string;
  status: string;
  engine: string | null;
  duration_seconds: number;
  channels: number;
  speakers: Record<string, { talk_seconds: number; turns: number; share: number }>;
  segments: number;
  summarised: boolean;
  scores: Record<string, number | string | null>;
  last_error: string | null;
  created_at: string | null;
}

interface Turn {
  speaker: string | null;
  start: number;
  end: number;
  text: string;
  confidence: number;
}

interface Detail extends RecordingRow {
  transcript: { turns: Turn[]; word_count: number; confidence: number | null } | null;
  summary: { method: string; intent: string; key_points: string[]; next_actions: string[]; outcome: string; customer_mood?: string; fallback_from?: string } | null;
  analytics: {
    scores?: Record<string, number | string | null>;
    sentiment?: { overall: number; opening: number | null; closing: number | null; negative_share: number };
    empathy?: { score: number; markers: Record<string, number>; answered_with_empathy: number; negative_customer_turns: number };
    interaction?: { talk_ratio: Record<string, number>; interruptions: unknown[]; silences: unknown[] };
    signals?: Array<{ kind: string; phrase?: string }>;
  };
}

interface Overview {
  recordings: number;
  analysed: number;
  averages: Record<string, number | null>;
  escalation_risk: Record<string, number>;
  signals: Record<string, number>;
}

export function clock(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "";
  const whole = Math.max(0, Math.round(seconds));
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}

export function percent(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : `${Math.round(value * 100)}%`;
}

export default function Calls() {
  const [rows, setRows] = useState<RecordingRow[]>([]);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const seq = useRef(0);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [list, stats] = await Promise.all([api.get("/speech/recordings", { params: { limit: "100" } }), api.get("/speech/analytics")]);
      setRows((list.data as { recordings: RecordingRow[] }).recordings);
      setOverview(stats.data as Overview);
    } catch (err) {
      setError(extractApiError(err, "Failed to load the calls."));
    }
  }, []);

  const open = useCallback(async (id: string) => {
    const mine = ++seq.current;
    setSelected(id);
    setDetail(null);
    setNotice(null);
    try {
      const { data } = await api.get(`/speech/recordings/${id}`);
      if (mine !== seq.current) return;
      setDetail(data as Detail);
    } catch (err) {
      if (mine !== seq.current) return;
      setError(extractApiError(err, "Failed to load the call."));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const summarise = async (method: "auto" | "extractive") => {
    if (!selected) return;
    setBusy(true);
    setError(null);
    try {
      await api.post(`/speech/recordings/${selected}/summary?method=${method}`);
      await Promise.all([open(selected), load()]);
      setNotice("Summary and analytics updated.");
    } catch (err) {
      setError(extractApiError(err, "The call could not be summarised."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-4 p-4">
      <Helmet>
        <title>Calls</title>
      </Helmet>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-xl font-semibold text-slate-900">Calls</h1>
        {overview && (
          <p className="text-sm text-slate-600" data-testid="calls-overview">
            {overview.analysed} of {overview.recordings} analysed · sentiment {overview.averages.customer_sentiment ?? "—"} · empathy {overview.averages.empathy ?? "—"} · customer talk share {percent(overview.averages.customer_talk_share)}
          </p>
        )}
      </div>
      {error && (
        <div role="alert" className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
          {error}
        </div>
      )}
      {notice && (
        <div className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800" data-testid="calls-notice">
          {notice}
        </div>
      )}
      <LiveAssist />
      <div className="grid gap-4 lg:grid-cols-12">
        <ul className="divide-y divide-slate-200 rounded-md border border-slate-200 lg:col-span-4" data-testid="calls-list" aria-label="Recordings">
          {rows.length === 0 && <li className="px-3 py-2 text-sm text-slate-500">No recordings yet.</li>}
          {rows.map((row) => (
            <li key={row.id}>
              <button type="button" className={`flex w-full items-start justify-between gap-2 px-3 py-2 text-left text-sm ${selected === row.id ? "bg-indigo-50" : "hover:bg-slate-50"}`} onClick={() => void open(row.id)} data-testid="calls-row">
                <span>
                  <span className="font-medium text-slate-900">{row.filename}</span>
                  <span className="block text-xs text-slate-500">
                    {row.status} · {clock(row.duration_seconds)} · {Object.keys(row.speakers).join(", ") || "no speech"}
                  </span>
                </span>
                <span className="text-right text-xs text-slate-500">
                  {row.summarised ? <span className="block">{String(row.scores.escalation_risk || "")} risk</span> : null}
                  {row.created_at ? new Date(row.created_at).toLocaleDateString() : ""}
                </span>
              </button>
            </li>
          ))}
        </ul>
        <div className="lg:col-span-8">
          {!selected && <p className="text-sm text-slate-500">Select a recording.</p>}
          {selected && !detail && !error && <p className="text-sm text-slate-500">Loading…</p>}
          {detail && (
            <div className="space-y-4" data-testid="calls-detail">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h2 className="text-base font-semibold text-slate-900">{detail.filename}</h2>
                <div className="flex gap-2">
                  <button type="button" className="rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50" disabled={busy || detail.status !== "transcribed"} onClick={() => void summarise("auto")} data-testid="calls-summarise">
                    Summarise
                  </button>
                  <button type="button" className="rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-700 disabled:opacity-50" disabled={busy || detail.status !== "transcribed"} onClick={() => void summarise("extractive")} data-testid="calls-summarise-words">
                    From the words only
                  </button>
                </div>
              </div>
              {detail.last_error && <p className="text-sm text-red-700">{detail.last_error}</p>}
              <section aria-label="Speakers" className="text-sm text-slate-700">
                {Object.entries(detail.speakers).map(([name, s]) => (
                  <span key={name} className="mr-4">
                    <span className="font-medium">{name}</span> {clock(s.talk_seconds)} · {s.turns} turns · {percent(s.share)}
                  </span>
                ))}
              </section>
              {detail.summary && (
                <section aria-label="Summary" className="rounded-md border border-slate-200 bg-white p-3 text-sm" data-testid="calls-summary">
                  <h3 className="font-semibold text-slate-900">
                    {detail.summary.intent} <span className="font-normal text-slate-500">· {detail.summary.outcome}{detail.summary.customer_mood ? ` · ${detail.summary.customer_mood}` : ""} · {detail.summary.method}{detail.summary.fallback_from ? " (the model did not answer)" : ""}</span>
                  </h3>
                  {detail.summary.key_points.length > 0 && (
                    <>
                      <h4 className="mt-2 text-xs font-medium uppercase text-slate-500">Key points</h4>
                      <ul className="list-disc pl-5">{detail.summary.key_points.map((p, i) => <li key={i}>{p}</li>)}</ul>
                    </>
                  )}
                  {detail.summary.next_actions.length > 0 && (
                    <>
                      <h4 className="mt-2 text-xs font-medium uppercase text-slate-500">Next actions</h4>
                      <ul className="list-disc pl-5">{detail.summary.next_actions.map((p, i) => <li key={i}>{p}</li>)}</ul>
                    </>
                  )}
                </section>
              )}
              {detail.analytics?.scores && (
                <section aria-label="Analytics" className="grid gap-2 text-sm sm:grid-cols-4" data-testid="calls-analytics">
                  <div className="rounded-md border border-slate-200 bg-white p-2">
                    <div className="text-xs text-slate-500">Customer sentiment</div>
                    <div className="font-semibold">{detail.analytics.sentiment?.overall ?? "—"}</div>
                    <div className="text-xs text-slate-500">
                      opening {detail.analytics.sentiment?.opening ?? "—"} · closing {detail.analytics.sentiment?.closing ?? "—"}
                    </div>
                  </div>
                  <div className="rounded-md border border-slate-200 bg-white p-2">
                    <div className="text-xs text-slate-500">Empathy</div>
                    <div className="font-semibold">{detail.analytics.empathy?.score ?? "—"} / 100</div>
                    <div className="text-xs text-slate-500">
                      {detail.analytics.empathy?.answered_with_empathy ?? 0} of {detail.analytics.empathy?.negative_customer_turns ?? 0} negative turns answered
                    </div>
                  </div>
                  <div className="rounded-md border border-slate-200 bg-white p-2">
                    <div className="text-xs text-slate-500">Talk ratio</div>
                    <div className="font-semibold">
                      {Object.entries(detail.analytics.interaction?.talk_ratio || {})
                        .map(([k, v]) => `${k} ${percent(v)}`)
                        .join(" · ") || "—"}
                    </div>
                    <div className="text-xs text-slate-500">
                      {detail.analytics.interaction?.interruptions.length ?? 0} interruptions · {detail.analytics.interaction?.silences.length ?? 0} silences
                    </div>
                  </div>
                  <div className="rounded-md border border-slate-200 bg-white p-2">
                    <div className="text-xs text-slate-500">Escalation risk</div>
                    <div className="font-semibold">{String(detail.analytics.scores.escalation_risk ?? "—")}</div>
                    <div className="text-xs text-slate-500">{(detail.analytics.signals || []).map((s) => s.phrase || s.kind).join(", ") || "no signals"}</div>
                  </div>
                </section>
              )}
              <section aria-label="Transcript" className="rounded-md border border-slate-200 bg-white p-3 text-sm" data-testid="calls-transcript">
                {!detail.transcript && <p className="text-slate-500">No transcript yet{detail.engine ? ` (engine ${detail.engine})` : ""}.</p>}
                {detail.transcript && (
                  <ol className="space-y-1">
                    {detail.transcript.turns.map((turn, index) => (
                      <li key={index} className="flex gap-2">
                        <span className="w-12 shrink-0 text-xs text-slate-400">{clock(turn.start)}</span>
                        <span className="w-24 shrink-0 font-medium text-slate-700">{turn.speaker || "?"}</span>
                        <span className="text-slate-800">{turn.text}</span>
                      </li>
                    ))}
                  </ol>
                )}
              </section>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
