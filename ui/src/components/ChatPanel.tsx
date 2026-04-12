import { useState, useRef, useEffect } from "react";
import api from "../lib/api";

interface Message {
  id: string;
  role: "user" | "agent";
  text: string;
  agent?: string;
  confidence?: number;
  domain?: string;
  timestamp: Date;
}

interface ChatQueryResponse {
  answer: string;
  agent: string;
  confidence: number;
  domain: string;
}

export default function ChatPanel({
  open,
  onClose,
  agentId,
  agentName,
}: {
  open: boolean;
  onClose: () => void;
  agentId?: string;
  agentName?: string;
}) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const listRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const companyId = localStorage.getItem("company_id") || "";

  // Auto-scroll to bottom on new messages
  useEffect(() => {
    if (listRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight;
    }
  }, [messages]);

  // Load chat history on open
  useEffect(() => {
    if (!open) return;
    const params = agentId ? `?agent_id=${agentId}` : "";
    api.get(`/chat/history${params}`).then(({ data }) => {
      const items: any[] = Array.isArray(data) ? data : data?.messages || [];
      const loaded: Message[] = items.map((m: any) => ({
        id: m.id || crypto.randomUUID(),
        role: m.role === "user" ? "user" : "agent",
        text: m.text || m.content || "",
        agent: m.agent,
        confidence: m.confidence,
        domain: m.domain,
        timestamp: new Date(m.timestamp || m.created_at || Date.now()),
      }));
      if (loaded.length > 0) setMessages(loaded);
    }).catch(() => {
      // history endpoint unavailable — start fresh
    });
  }, [open, agentId]);

  // Focus input when panel opens
  useEffect(() => {
    if (open) {
      setTimeout(() => inputRef.current?.focus(), 200);
    }
  }, [open]);

  const sendMessage = async () => {
    const text = input.trim();
    if (!text || sending) return;

    const userMsg: Message = {
      id: crypto.randomUUID(),
      role: "user",
      text,
      timestamp: new Date(),
    };

    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setSending(true);

    try {
      const res = await api.post<ChatQueryResponse>("/chat/query", {
        query: text,
        company_id: companyId,
        agent_id: agentId,
      });
      const agentMsg: Message = {
        id: crypto.randomUUID(),
        role: "agent",
        text: res.data.answer,
        agent: res.data.agent,
        confidence: res.data.confidence,
        domain: res.data.domain,
        timestamp: new Date(),
      };
      setMessages((prev) => [...prev, agentMsg]);
    } catch {
      const errMsg: Message = {
        id: crypto.randomUUID(),
        role: "agent",
        text: "Sorry, something went wrong. Please try again.",
        agent: "System",
        timestamp: new Date(),
      };
      setMessages((prev) => [...prev, errMsg]);
    } finally {
      setSending(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  return (
    <>
      {/* Backdrop */}
      {open && (
        <div
          className="fixed inset-0 bg-black/30 z-40"
          onClick={onClose}
        />
      )}

      {/* Panel */}
      <div
        className={`fixed top-0 right-0 h-full w-full sm:w-[420px] bg-slate-900 border-l border-slate-700 z-50 flex flex-col transform transition-transform duration-300 ease-in-out ${
          open ? "translate-x-0" : "translate-x-full"
        }`}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-slate-700">
          <h2 className="text-sm font-semibold text-slate-200">
            {agentName ? `Chat with ${agentName}` : agentId ? "Agent Chat" : "Ask Anything"}
          </h2>
          <button
            onClick={onClose}
            className="p-1 rounded hover:bg-slate-800 text-slate-400 hover:text-slate-200 transition-colors"
            aria-label="Close chat"
          >
            <svg
              xmlns="http://www.w3.org/2000/svg"
              width="18"
              height="18"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>

        {/* Messages */}
        <div ref={listRef} className="flex-1 overflow-y-auto p-4 space-y-3">
          {messages.length === 0 && (
            <div className="text-center text-slate-500 text-sm mt-8">
              <p className="mb-1">Ask any question about your business.</p>
              <p className="text-xs">
                The right agent will be routed automatically.
              </p>
            </div>
          )}

          {messages.map((msg) =>
            msg.role === "user" ? (
              <div key={msg.id} className="flex justify-end">
                <div className="max-w-[80%] rounded-lg bg-blue-600 px-3 py-2 text-sm text-white">
                  {msg.text}
                </div>
              </div>
            ) : (
              <div key={msg.id} className="flex justify-start">
                <div className="max-w-[80%] rounded-lg bg-slate-800 border border-slate-700 px-3 py-2">
                  {msg.agent && (
                    <div className="flex items-center gap-2 mb-1">
                      <span className="inline-flex items-center rounded-full bg-primary/20 px-2 py-0.5 text-[10px] font-medium text-primary">
                        {msg.agent}
                      </span>
                      {msg.confidence != null && (
                        <span className="text-[10px] text-slate-500">
                          {Math.round(msg.confidence * 100)}%
                        </span>
                      )}
                    </div>
                  )}
                  <p className="text-sm text-slate-300 leading-relaxed">
                    {msg.text}
                  </p>
                </div>
              </div>
            ),
          )}

          {sending && (
            <div className="flex justify-start">
              <div className="rounded-lg bg-slate-800 border border-slate-700 px-3 py-2">
                <div className="flex items-center gap-1">
                  <div className="h-2 w-2 rounded-full bg-slate-500 animate-bounce" />
                  <div className="h-2 w-2 rounded-full bg-slate-500 animate-bounce [animation-delay:0.15s]" />
                  <div className="h-2 w-2 rounded-full bg-slate-500 animate-bounce [animation-delay:0.3s]" />
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Input */}
        <div className="border-t border-slate-700 p-3">
          <div className="flex gap-2">
            <input
              ref={inputRef}
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Type a message..."
              disabled={sending}
              className="flex-1 h-9 rounded-lg border border-slate-700 bg-slate-800/50 px-3 text-sm text-slate-200 placeholder:text-slate-500 focus:outline-none focus:ring-1 focus:ring-primary disabled:opacity-50"
            />
            <button
              onClick={sendMessage}
              disabled={sending || !input.trim()}
              className="h-9 px-3 rounded-lg bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              Send
            </button>
          </div>
        </div>
      </div>
    </>
  );
}
