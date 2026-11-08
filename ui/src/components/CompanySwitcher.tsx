import { useState, useEffect } from "react";
import { Link } from "react-router";
import api from "../lib/api";
import { setSelectedCompanyId, useSelectedCompanyId } from "../lib/companyScope";

interface Company {
  id: string;
  name: string;
  gstin?: string;
  industry?: string;
}

export default function CompanySwitcher() {
  const [companies, setCompanies] = useState<Company[]>([]);
  const current = useSelectedCompanyId();
  const [loading, setLoading] = useState(true);
  const [dropdownOpen, setDropdownOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await api.get("/companies");
        if (cancelled) return;
        // API returns { items: [...], total, page, per_page } or a plain array
        const list: Company[] = Array.isArray(res.data)
          ? res.data
          : Array.isArray(res.data?.items)
            ? res.data.items
            : [];
        setCompanies(list);

        // Validate the stored selection against the tenant's real list. A
        // stale id (deleted company, or another user's tenant on a shared
        // browser) would otherwise scope every dashboard to an empty set.
        const storedIsValid = current !== "" && list.some((c) => c.id === current);
        if (!storedIsValid) {
          if (list.length > 0) {
            const first = list[0].id;
            setSelectedCompanyId(first);
          } else if (current) {
            setSelectedCompanyId("");
          }
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
    setSelectedCompanyId(id);
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
      <span className="text-sm text-foreground font-medium truncate max-w-[160px]">
        {currentCompany?.name || "No Company"}
      </span>
    );
  }

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setDropdownOpen((o) => !o)}
        className="flex items-center gap-1.5 h-8 rounded-lg border bg-background px-3 text-sm text-foreground hover:bg-muted transition-colors"
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
          className="text-muted-foreground"
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
          className="text-muted-foreground"
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
          <div className="absolute top-full left-0 mt-1 w-56 rounded-lg border bg-background shadow-xl z-50 py-1">
            {companies.map((c) => (
              <button
                key={c.id}
                type="button"
                onClick={() => handleSelect(c.id)}
                className={`w-full text-left px-3 py-2 text-sm transition-colors ${
                  c.id === current
                    ? "bg-primary/10 text-primary"
                    : "text-foreground hover:bg-muted"
                }`}
              >
                <span className="block truncate">{c.name}</span>
                <span className="block text-[10px] text-muted-foreground mt-0.5">
                  {c.gstin ? `GSTIN: ${c.gstin}` : ""}
                  {c.gstin && c.industry ? " · " : ""}
                  {c.industry || ""}
                </span>
              </button>
            ))}
            <Link
              to="/dashboard/companies"
              onClick={() => setDropdownOpen(false)}
              className="block w-full text-left px-3 py-2 text-xs text-primary hover:underline border-t mt-1 pt-2 transition-colors"
            >
              Manage Companies
            </Link>
          </div>
        </>
      )}
    </div>
  );
}
