// SPDX-License-Identifier: Apache-2.0
import { useState } from "react";
import api, { extractApiError } from "@/lib/api";

/**
 * Live agent assist: a call opened by reference and type, each transcribed turn posted as it
 * happens, and what the agent should see now: the disclosure checklist, the customer's mood, the
 * intent, the next question, the knowledge that answers the customer and any flag this turn raised.
 */

interface ChecklistItem {
  key: string;
  title: string;
  script: string;
  state: "pending" | "said" | "late";
  at: number | null;
}

interface Flag {
  kind: string;
  key: string | null;
  title: string;
  at: number;
}

interface TurnView {
  id: string;
  status: string;
  turn_count: number;
  flags: Flag[];
  checklist: ChecklistItem[];
  compliant_so_far: boolean;
  mood: { score: number; label: string };
  sentiment: { overall: number; negative_streak: number };
  intent: { name: string; title: string; confidence: number } | null;
  next_step: { slot: string | null; question: string } | null;
  new_flags: Flag[];
  suggestions: Array<{ document: string; text: string; score: number }>;
}

interface Session {
  id: string;
  status: string;
  required: string[];
  checklist?: ChecklistItem[];
  report?: { compliant: boolean; missing: Array<{ key: string; title: string }>; late: Array<{ key: string }> };
}

export const CALL_TYPES = ["service", "loan", "card", "insurance", "sales", "complaint", "collections"];

export function flagLabel(flag: Flag): string {
  if (flag.kind === "disclosure_overdue") return `${flag.title} not said in time`;
  return flag.title;
}

export default function LiveAssist() {
  const [callRef, setCallRef] = useState("");
  const [callType, setCallType] = useState("service");
  const [session, setSession] = useState<Session | null>(null);
  const [view, setView] = useState<TurnView | null>(null);
  const [speaker, setSpeaker] = useState("customer");
  const [text, setText] = useState("");
  const [seconds, setSeconds] = useState("");
  const [flags, setFlags] = useState<Flag[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const start = async () => {
    setBusy(true);
    setError(null);
    try {
      const { data } = await api.post("/speech/live/sessions", { call_ref: callRef, call_type: callType });
      setSession(data as Session);
      setView(null);
      setFlags([]);
    } catch (err) {
      setError(extractApiError(err, "The live session could not be opened."));
    } finally {
      setBusy(false);
    }
  };

  const send = async () => {
    if (!session || !text.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const at = seconds.trim() ? Number(seconds) : undefined;
      const { data } = await api.post(`/speech/live/sessions/${session.id}/turns`, { speaker, text: text.trim(), ...(at !== undefined ? { at } : {}) });
      const next = data as TurnView;
      setView(next);
      setFlags((prev) => [...prev, ...next.new_flags]);
      setText("");
    } catch (err) {
      setError(extractApiError(err, "The turn was not taken."));
    } finally {
      setBusy(false);
    }
  };

  const close = async () => {
    if (!session) return;
    setBusy(true);
    setError(null);
    try {
      const { data } = await api.post(`/speech/live/sessions/${session.id}/close`);
      setSession(data as Session);
    } catch (err) {
      setError(extractApiError(err, "The session could not be closed."));
    } finally {
      setBusy(false);
    }
  };

  const checklist = view?.checklist || session?.checklist || [];

  return (
    <section className="space-y-3 rounded-md border border-slate-200 bg-white p-3" aria-label="Live agent assist" data-testid="live-assist">
      <h2 className="text-base font-semibold text-slate-900">Live agent assist</h2>
      {error && (
        <div role="alert" className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
          {error}
        </div>
      )}
      {!session && (
        <div className="flex flex-wrap items-end gap-2">
          <label className="text-sm text-slate-700">
            Call reference
            <input className="ml-2 rounded-md border border-slate-300 px-2 py-1 text-sm" value={callRef} onChange={(e) => setCallRef(e.target.value)} data-testid="live-ref" />
          </label>
          <label className="text-sm text-slate-700">
            Call type
            <select className="ml-2 rounded-md border border-slate-300 px-2 py-1 text-sm" value={callType} onChange={(e) => setCallType(e.target.value)} data-testid="live-type">
              {CALL_TYPES.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </label>
          <button type="button" className="rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50" disabled={busy} onClick={() => void start()} data-testid="live-start">
            Open call
          </button>
        </div>
      )}
      {session && (
        <div className="grid gap-3 lg:grid-cols-12">
          <div className="space-y-2 lg:col-span-5">
            <ul className="space-y-1" aria-label="Disclosure checklist" data-testid="live-checklist">
              {checklist.map((item) => (
                <li key={item.key} className="flex items-start gap-2 text-sm">
                  <span className={`mt-0.5 inline-block h-4 w-4 shrink-0 rounded-full ${item.state === "said" ? "bg-emerald-500" : item.state === "late" ? "bg-amber-500" : "bg-slate-300"}`} aria-hidden="true" />
                  <span>
                    <span className="font-medium text-slate-900">{item.title}</span> <span className="text-xs text-slate-500">{item.state}</span>
                    <span className="block text-xs text-slate-600">{item.script}</span>
                  </span>
                </li>
              ))}
            </ul>
            {flags.length > 0 && (
              <ul className="space-y-1" aria-label="Flags" data-testid="live-flags">
                {flags.map((flag, index) => (
                  <li key={index} className="rounded-md border border-amber-200 bg-amber-50 px-2 py-1 text-sm text-amber-900">
                    {flagLabel(flag)}
                  </li>
                ))}
              </ul>
            )}
            {session.status === "closed" && session.report && (
              <p className="text-sm text-slate-700" data-testid="live-report">
                Closed: {session.report.compliant ? "compliant" : `${session.report.missing.length} missing, ${session.report.late.length} late`}.
              </p>
            )}
            {session.status === "closed" && (
              <button
                type="button"
                className="rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-700"
                onClick={() => {
                  setSession(null);
                  setView(null);
                  setFlags([]);
                }}
                data-testid="live-new"
              >
                New call
              </button>
            )}
          </div>
          <div className="space-y-2 lg:col-span-7">
            {session.status === "open" && (
              <div className="flex flex-wrap items-end gap-2">
                <label className="text-sm text-slate-700">
                  Speaker
                  <select className="ml-2 rounded-md border border-slate-300 px-2 py-1 text-sm" value={speaker} onChange={(e) => setSpeaker(e.target.value)} data-testid="live-speaker">
                    <option value="customer">customer</option>
                    <option value="agent">agent</option>
                  </select>
                </label>
                <label className="text-sm text-slate-700">
                  Seconds
                  <input className="ml-2 w-20 rounded-md border border-slate-300 px-2 py-1 text-sm" value={seconds} onChange={(e) => setSeconds(e.target.value)} inputMode="decimal" data-testid="live-seconds" />
                </label>
                <label className="grow text-sm text-slate-700">
                  Turn
                  <input className="ml-2 w-full rounded-md border border-slate-300 px-2 py-1 text-sm" value={text} onChange={(e) => setText(e.target.value)} data-testid="live-text" />
                </label>
                <button type="button" className="rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50" disabled={busy || !text.trim()} onClick={() => void send()} data-testid="live-send">
                  Add turn
                </button>
                <button type="button" className="rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-700 disabled:opacity-50" disabled={busy} onClick={() => void close()} data-testid="live-close">
                  Close call
                </button>
              </div>
            )}
            {view && (
              <div className="space-y-2 text-sm" data-testid="live-view" aria-live="polite">
                <p className="text-slate-700">
                  Mood <span className="font-medium">{view.mood.label}</span>
                  {view.intent ? (
                    <>
                      {" "}
                      · intent <span className="font-medium">{view.intent.title}</span>
                    </>
                  ) : null}
                  {view.next_step ? <> · next: {view.next_step.question}</> : null}
                </p>
                {view.suggestions.length > 0 && (
                  <ul className="space-y-1" aria-label="Knowledge" data-testid="live-suggestions">
                    {view.suggestions.map((s, i) => (
                      <li key={i} className="rounded-md border border-slate-200 bg-slate-50 px-2 py-1">
                        <span className="font-medium text-slate-900">{s.document}</span> <span className="text-slate-700">{s.text}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
