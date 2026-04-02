import { useState, useEffect } from "react";
import api from "../lib/api";

interface Company {
  id: string;
  name: string;
  gstin?: string;
}

export default function CompanySwitcher() {
  const [companies, setCompanies] = useState<Company[]>([]);
  const [current, setCurrent] = useState<string>(
    localStorage.getItem("company_id") || "",
  );
  const [loading, setLoading] = useState(true);
  const [dropdownOpen, setDropdownOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await api.get<Company[]>("/companies");
        if (cancelled) return;
        setCompanies(res.data);

        // Auto-select first company if none stored
        if (!current && res.data.length > 0) {
          const first = res.data[0].id;
          setCurrent(first);
          localStorage.setItem("company_id", first);
        }
      } catch {
        // Silently fail — header should still render
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [current]);

  const handleSelect = (id: string) => {
    setCurrent(id);
    localStorage.setItem("company_id", id);
    setDropdownOpen(false);
    // Reload the page so all data refreshes for the new company context
    window.location.reload();
  };

  const currentCompany = companies.find((c) => c.id === current);

  if (loading) {
    return (
      <div className="h-8 w-32 animate-pulse rounded bg-slate-800/50" />
    );
  }

  // Single company — no switcher needed
  if (companies.length <= 1) {
    return (
      <span className="text-sm text-slate-300 font-medium truncate max-w-[160px]">
        {currentCompany?.name || "No Company"}
      </span>
    );
  }

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setDropdownOpen((o) => !o)}
        className="flex items-center gap-1.5 h-8 rounded-lg border border-slate-700 bg-slate-800/50 px-3 text-sm text-slate-200 hover:bg-slate-700/50 transition-colors"
      >
        <svg
          xmlns="http://www.w3.org/2000/svg"
          width="14"
          height="14"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          className="text-slate-400"
        >
          <path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
          <polyline points="9 22 9 12 15 12 15 22" />
        </svg>
        <span className="truncate max-w-[120px]">
          {currentCompany?.name || "Select Company"}
        </span>
        <svg
          xmlns="http://www.w3.org/2000/svg"
          width="12"
          height="12"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          className="text-slate-400"
        >
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </button>

      {dropdownOpen && (
        <>
          {/* Backdrop to close dropdown */}
          <div
            className="fixed inset-0 z-40"
            onClick={() => setDropdownOpen(false)}
          />
          <div className="absolute top-full left-0 mt-1 w-56 rounded-lg border border-slate-700 bg-slate-800 shadow-xl z-50 py-1">
            {companies.map((c) => (
              <button
                key={c.id}
                type="button"
                onClick={() => handleSelect(c.id)}
                className={`w-full text-left px-3 py-2 text-sm transition-colors ${
                  c.id === current
                    ? "bg-primary/10 text-primary"
                    : "text-slate-300 hover:bg-slate-700/50"
                }`}
              >
                <span className="block truncate">{c.name}</span>
                {c.gstin && (
                  <span className="block text-[10px] text-slate-500 mt-0.5">
                    GSTIN: {c.gstin}
                  </span>
                )}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
