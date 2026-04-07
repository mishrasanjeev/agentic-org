import { useState, useRef, useEffect, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import api from "../lib/api";

interface QueryResponse {
  answer: string;
  agent: string;
  confidence: number;
  domain: string;
}

// Client-side intent detection for navigation queries
interface NavIntent {
  path: string;
  params?: Record<string, string>;
}

function detectNavigationIntent(text: string): NavIntent | null {
  const q = text.trim().toLowerCase();

  // "show me <domain> agents" / "list <domain> agents"
  const agentDomainMatch = q.match(/^(?:show\s+(?:me\s+)?|list\s+|view\s+)(.+?)\s+agents?$/);
  if (agentDomainMatch) {
    const domain = agentDomainMatch[1].trim();
    return { path: "/dashboard/agents", params: { domain } };
  }

  // "show agents" / "list agents" / "go to agents"
  if (/^(?:show\s+(?:me\s+)?|list\s+|view\s+|go\s+to\s+)agents?$/.test(q)) {
    return { path: "/dashboard/agents" };
  }

  // "show workflows" / "list workflows" / "go to workflows"
  if (/^(?:show\s+(?:me\s+)?|list\s+|view\s+|go\s+to\s+)workflows?$/.test(q)) {
    return { path: "/dashboard/workflows" };
  }

  // "show connectors" / "go to connectors"
  if (/^(?:show\s+(?:me\s+)?|list\s+|view\s+|go\s+to\s+)connectors?$/.test(q)) {
    return { path: "/dashboard/connectors" };
  }

  // "go to dashboard" / "show dashboard"
  if (/^(?:show\s+(?:me\s+)?|go\s+to\s+|open\s+)(?:the\s+)?dashboard$/.test(q)) {
    return { path: "/dashboard" };
  }

  // "go to approvals" / "show approvals"
  if (/^(?:show\s+(?:me\s+)?|list\s+|view\s+|go\s+to\s+)approvals?$/.test(q)) {
    return { path: "/dashboard/approvals" };
  }

  // "go to settings"
  if (/^(?:show\s+(?:me\s+)?|go\s+to\s+|open\s+)settings?$/.test(q)) {
    return { path: "/dashboard/settings" };
  }

  return null;
}

export default function NLQueryBar({ onOpenChat }: { onOpenChat?: () => void }) {
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<QueryResponse | null>(null);
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const wrapperRef = useRef<HTMLDivElement>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const navigate = useNavigate();

  // Cmd+K / Ctrl+K keyboard shortcut to focus
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        inputRef.current?.focus();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  // Close dropdown on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target as Node)) {
        setDropdownOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const companyId = localStorage.getItem("company_id") || "";

  const submitQuery = useCallback(
    async (text: string) => {
      if (!text.trim()) return;

      // Check for navigation intent before making API call
      const navIntent = detectNavigationIntent(text);
      if (navIntent) {
        const searchParams = navIntent.params
          ? "?" + new URLSearchParams(navIntent.params).toString()
          : "";
        navigate(navIntent.path + searchParams);
        setQuery("");
        setDropdownOpen(false);
        setResult(null);
        return;
      }

      setLoading(true);
      try {
        const res = await api.post<QueryResponse>("/chat/query", {
          query: text,
          company_id: companyId,
        });
        setResult(res.data);
        setDropdownOpen(true);
      } catch {
        setResult(null);
      } finally {
        setLoading(false);
      }
    },
    [companyId, navigate],
  );

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const value = e.target.value;
    setQuery(value);

    // Debounce: clear previous timer
    if (debounceRef.current) clearTimeout(debounceRef.current);

    if (value.trim().length > 2) {
      debounceRef.current = setTimeout(() => {
        submitQuery(value);
      }, 300);
    } else {
      setDropdownOpen(false);
      setResult(null);
    }
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (debounceRef.current) clearTimeout(debounceRef.current);
    submitQuery(query);
  };

  const handleOpenChat = () => {
    setDropdownOpen(false);
    onOpenChat?.();
  };

  return (
    <div ref={wrapperRef} className="relative">
      <form onSubmit={handleSubmit} className="relative">
        {/* Search icon */}
        <svg
          className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400 pointer-events-none"
          xmlns="http://www.w3.org/2000/svg"
          width="14"
          height="14"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <circle cx="11" cy="11" r="8" />
          <line x1="21" y1="21" x2="16.65" y2="16.65" />
        </svg>

        <input
          ref={inputRef}
          type="text"
          value={query}
          onChange={handleChange}
          placeholder="Ask anything... (\u2318K)"
          className="h-9 w-64 lg:w-96 rounded-lg border border-slate-700 bg-slate-800/50 pl-8 pr-3 text-sm text-slate-200 placeholder:text-slate-500 focus:outline-none focus:ring-1 focus:ring-primary"
        />

        {loading && (
          <div className="absolute right-2.5 top-1/2 -translate-y-1/2">
            <div className="h-4 w-4 animate-spin rounded-full border-2 border-slate-500 border-t-primary" />
          </div>
        )}
      </form>

      {/* Dropdown result */}
      {dropdownOpen && result && (
        <div className="absolute top-full left-0 mt-1 w-80 lg:w-[28rem] rounded-lg border border-slate-700 bg-slate-800 shadow-xl z-50 p-4">
          <div className="flex items-center gap-2 mb-2">
            <span className="inline-flex items-center rounded-full bg-primary/20 px-2 py-0.5 text-xs font-medium text-primary">
              {result.agent}
            </span>
            <span className="text-[10px] text-slate-500 uppercase tracking-wide">
              {result.domain}
            </span>
            <span className="ml-auto text-[10px] text-slate-500">
              {Math.round(result.confidence * 100)}% confidence
            </span>
          </div>

          <p className="text-sm text-slate-300 leading-relaxed mb-3">
            {result.answer}
          </p>

          <div className="flex items-center justify-between border-t border-slate-700 pt-2">
            <button
              type="button"
              onClick={handleOpenChat}
              className="text-xs text-primary hover:text-primary/80 font-medium transition-colors"
            >
              Open Chat &rarr;
            </button>
            <button
              type="button"
              onClick={() => setDropdownOpen(false)}
              className="text-xs text-slate-500 hover:text-slate-400 transition-colors"
            >
              Dismiss
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
