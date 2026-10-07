// SPDX-License-Identifier: Apache-2.0
import { useCallback, useEffect, useState } from "react";
import { Helmet } from "react-helmet-async";
import api, { extractApiError } from "@/lib/api";
import { AgenticOrgWS, type FeedMessage } from "@/lib/websocket";
import { useAuth } from "../contexts/AuthContext";

/**
 * The supervisor console: conversations in progress or escalated, a transcript,
 * takeover, replies that reach the user's chat, and release. Live through the feed.
 */

interface SessionRow {
  id: string;
  session_key: string;
  user_id: string;
  agent_id: string | null;
  channel: string;
  status: string;
  intent: string | null;
  stage: string | null;
  turns: number;
  taken_over_by: string | null;
  taken_over_at: string | null;
  escalation: { reason?: string; intent?: string; at?: string; hitl_id?: string | null; ticket?: { reference?: string | null } | null } | null;
  updated_at: string | null;
}

interface Transcript extends SessionRow {
  history: Array<{ role: string; text: string }>;
  slots: Record<string, unknown>;
  escalation_summary: string | null;
}

const LIVE_EVENTS = new Set(["conversation.turn", "conversation.message", "conversation.escalated", "conversation.takeover", "conversation.release"]);

export function statusLabel(row: SessionRow): string {
  if (row.taken_over_by) return `held by ${row.taken_over_by}`;
  if (row.status === "escalated") return "escalated";
  return row.stage ? `${row.status} · ${row.stage}` : row.status;
}

export function roleLabel(role: string): string {
  if (role === "user") return "User";
  if (role === "supervisor") return "Supervisor";
  if (role === "system") return "System";
  return "Assistant";
}

export default function Conversations() {
  const auth = useAuth();
  const tenantId = auth.user?.tenant_id ?? "";
  const [sessions, setSessions] = useState<SessionRow[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [transcript, setTranscript] = useState<Transcript | null>(null);
  const [reply, setReply] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [includeIdle, setIncludeIdle] = useState(false);

  const loadSessions = useCallback(async () => {
    setError(null);
    try {
      const { data } = await api.get("/conversation/supervisor/sessions", { params: { limit: "50", include_idle: includeIdle ? "true" : "false" } });
      setSessions((data as { sessions: SessionRow[] }).sessions);
    } catch (err) {
      setError(extractApiError(err, "Failed to load conversations."));
    }
  }, [includeIdle]);

  const loadTranscript = useCallback(async (id: string) => {
    setError(null);
    try {
      const { data } = await api.get(`/conversation/supervisor/sessions/${id}`);
      setTranscript(data as Transcript);
    } catch (err) {
      setTranscript(null);
      setError(extractApiError(err, "Failed to load the conversation."));
    }
  }, []);

  useEffect(() => {
    void loadSessions();
  }, [loadSessions]);

  useEffect(() => {
    if (selected) void loadTranscript(selected);
  }, [selected, loadTranscript]);

  useEffect(() => {
    if (!tenantId) return;
    const ws = new AgenticOrgWS();
    const unsubscribe = ws.subscribe((event: FeedMessage) => {
      if (!LIVE_EVENTS.has(String(event.type))) return;
      void loadSessions();
      if (selected && transcript && event.session_key === transcript.session_key) void loadTranscript(selected);
    });
    ws.connect(tenantId);
    return () => {
      unsubscribe();
      ws.disconnect();
    };
  }, [tenantId, selected, transcript, loadSessions, loadTranscript]);

  const act = async (action: "takeover" | "release" | "reply") => {
    if (!selected) return;
    setBusy(true);
    setError(null);
    try {
      if (action === "reply") {
        await api.post(`/conversation/supervisor/sessions/${selected}/reply`, { text: reply });
        setReply("");
      } else {
        await api.post(`/conversation/supervisor/sessions/${selected}/${action}`);
      }
      await loadTranscript(selected);
      await loadSessions();
    } catch (err) {
      setError(extractApiError(err, `Failed to ${action}.`));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-4 p-4">
      <Helmet>
        <title>Conversations</title>
      </Helmet>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-xl font-semibold text-slate-900">Conversations</h1>
        <label className="text-sm text-slate-700">
          <input type="checkbox" className="mr-2" checked={includeIdle} onChange={(e) => setIncludeIdle(e.target.checked)} data-testid="conversations-idle" />
          Show idle sessions
        </label>
      </div>
      {error && (
        <div role="alert" className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
          {error}
        </div>
      )}
      <div className="grid gap-4 md:grid-cols-12">
        <div className="md:col-span-5">
          <table className="w-full text-sm" data-testid="conversations-table">
            <thead>
              <tr className="text-left text-xs uppercase text-slate-500">
                <th className="py-1">User</th>
                <th>Intent</th>
                <th>State</th>
                <th className="text-right">Turns</th>
              </tr>
            </thead>
            <tbody>
              {sessions.length === 0 && (
                <tr>
                  <td colSpan={4} className="py-3 text-slate-500" data-testid="conversations-empty">
                    No conversations in progress.
                  </td>
                </tr>
              )}
              {sessions.map((row) => (
                <tr
                  key={row.id}
                  className={`cursor-pointer border-t border-slate-100 ${row.id === selected ? "bg-indigo-50" : "hover:bg-slate-50"}`}
                  onClick={() => setSelected(row.id)}
                  data-testid={`conversation-row-${row.id}`}
                >
                  <td className="py-1 font-mono text-xs">{row.user_id}</td>
                  <td>{row.intent ?? "–"}</td>
                  <td>
                    <span className={`rounded px-1.5 py-0.5 text-xs ${row.status === "escalated" ? "bg-amber-100 text-amber-800" : row.taken_over_by ? "bg-indigo-100 text-indigo-800" : "bg-slate-100 text-slate-700"}`}>
                      {statusLabel(row)}
                    </span>
                  </td>
                  <td className="text-right">{row.turns}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="md:col-span-7">
          {transcript && (
            <div className="rounded-lg border border-slate-200 bg-white p-3" data-testid="conversation-transcript">
              <div className="mb-2 flex flex-wrap items-center justify-between gap-2 text-sm">
                <span className="font-mono text-xs text-slate-600">{transcript.session_key}</span>
                <div className="flex gap-2">
                  {!transcript.taken_over_by && (
                    <button type="button" className="rounded-md border border-slate-300 px-2 py-0.5 text-xs hover:bg-slate-50 disabled:opacity-50" disabled={busy} onClick={() => void act("takeover")} data-testid="conversation-takeover">
                      Take over
                    </button>
                  )}
                  {transcript.taken_over_by && (
                    <button type="button" className="rounded-md border border-slate-300 px-2 py-0.5 text-xs hover:bg-slate-50 disabled:opacity-50" disabled={busy} onClick={() => void act("release")} data-testid="conversation-release">
                      Release
                    </button>
                  )}
                </div>
              </div>
              {transcript.escalation_summary && (
                <p className="mb-2 rounded-md bg-amber-50 px-2 py-1 text-xs text-amber-800" data-testid="conversation-summary">
                  {transcript.escalation_summary}
                  {transcript.escalation?.ticket?.reference ? ` Ticket ${transcript.escalation.ticket.reference}.` : ""}
                </p>
              )}
              {Object.keys(transcript.slots).length > 0 && (
                <p className="mb-2 text-xs text-slate-600" data-testid="conversation-slots">
                  {Object.entries(transcript.slots).map(([key, value]) => `${key.replace(/_/g, " ")}: ${String(value)}`).join(" · ")}
                </p>
              )}
              <ol className="max-h-96 space-y-1 overflow-auto text-sm" data-testid="conversation-history">
                {transcript.history.map((line, index) => (
                  <li key={`${index}-${line.role}`} className={line.role === "user" ? "text-slate-900" : line.role === "supervisor" ? "text-indigo-800" : "text-slate-600"}>
                    <span className="mr-1 text-xs uppercase text-slate-400">{roleLabel(line.role)}</span>
                    {line.text}
                  </li>
                ))}
              </ol>
              {transcript.taken_over_by && (
                <div className="mt-2 flex gap-2">
                  <input
                    className="flex-1 rounded-md border border-slate-300 px-2 py-1 text-sm"
                    value={reply}
                    onChange={(e) => setReply(e.target.value)}
                    placeholder="Reply to the user"
                    data-testid="conversation-reply"
                  />
                  <button type="button" className="rounded-md bg-indigo-600 px-3 py-1 text-sm text-white disabled:opacity-50" disabled={busy || !reply.trim()} onClick={() => void act("reply")} data-testid="conversation-send">
                    Send
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
