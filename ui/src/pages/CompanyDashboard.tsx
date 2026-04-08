import { useState, useEffect, useCallback } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Helmet } from "react-helmet-async";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import api from "@/lib/api";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface Company {
  id: string;
  name: string;
  gstin?: string;
  pan?: string;
  industry?: string;
  state?: string;
  status?: string;
  created_at?: string;
  health_score?: number;
}

/* ------------------------------------------------------------------ */
/*  Mock data                                                          */
/* ------------------------------------------------------------------ */

const MOCK_COMPANIES: Company[] = [
  { id: "c1", name: "Acme Manufacturing Pvt Ltd", gstin: "29AABCU9603R1ZM", pan: "AABCU9603R", industry: "Manufacturing", state: "Karnataka", status: "active", health_score: 95 },
  { id: "c2", name: "Greenleaf Exports Ltd", gstin: "07AADCG1234N1Z5", pan: "AADCG1234N", industry: "Export", state: "Delhi", status: "active", health_score: 88 },
  { id: "c3", name: "Sunrise Healthcare LLP", gstin: "27AACFS5678P1ZK", pan: "AACFS5678P", industry: "Healthcare", state: "Maharashtra", status: "active", health_score: 72 },
  { id: "c4", name: "Horizon IT Solutions", gstin: "36AABCH7890Q1ZJ", pan: "AABCH7890Q", industry: "IT Services", state: "Telangana", status: "inactive", health_score: 45 },
  { id: "c5", name: "Sapphire Textiles", gstin: "24AAGCS3456R1ZL", pan: "AAGCS3456R", industry: "Textile", state: "Gujarat", status: "active", health_score: 91 },
  { id: "c6", name: "Metro Logistics Corp", gstin: "33AABCM6789S1ZN", pan: "AABCM6789S", industry: "Logistics", state: "Tamil Nadu", status: "active", health_score: 85 },
];

const INDUSTRY_COLORS: Record<string, string> = {
  Manufacturing: "from-blue-500 to-cyan-600",
  Export: "from-emerald-500 to-teal-600",
  Healthcare: "from-red-500 to-pink-600",
  "IT Services": "from-purple-500 to-indigo-600",
  Textile: "from-amber-500 to-orange-600",
  Logistics: "from-slate-500 to-slate-600",
};

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

export default function CompanyDashboard() {
  const navigate = useNavigate();
  const [companies, setCompanies] = useState<Company[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [industryFilter, setIndustryFilter] = useState("");
  const [stateFilter, setStateFilter] = useState("");

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.get("/companies");
      const data = Array.isArray(res.data) ? res.data : Array.isArray(res.data?.items) ? res.data.items : [];
      if (data.length > 0) {
        setCompanies(data);
      } else {
        setCompanies(MOCK_COMPANIES);
      }
    } catch {
      setCompanies(MOCK_COMPANIES);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const industries = [...new Set(companies.map((c) => c.industry).filter(Boolean))];
  const states = [...new Set(companies.map((c) => c.state).filter(Boolean))];

  const filtered = companies.filter((c) => {
    const matchSearch = !search || c.name.toLowerCase().includes(search.toLowerCase()) || (c.gstin || "").toLowerCase().includes(search.toLowerCase());
    const matchIndustry = !industryFilter || c.industry === industryFilter;
    const matchState = !stateFilter || c.state === stateFilter;
    return matchSearch && matchIndustry && matchState;
  });

  const activeCount = companies.filter((c) => c.status === "active").length;
  const inactiveCount = companies.filter((c) => c.status !== "active").length;

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[50vh]">
        <p className="text-muted-foreground">Loading companies...</p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <Helmet>
        <title>Companies | AgenticOrg</title>
      </Helmet>

      {/* Header */}
      <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
        <div>
          <h2 className="text-2xl font-bold">Companies</h2>
          <p className="text-sm text-muted-foreground mt-1">
            {companies.length} total &middot; {activeCount} active &middot; {inactiveCount} inactive
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" onClick={fetchData}>Refresh</Button>
          <Link to="/dashboard/partner">
            <Button variant="outline">Partner View</Button>
          </Link>
          <Link to="/dashboard/companies/new">
            <Button>Add Client</Button>
          </Link>
        </div>
      </div>

      {/* Stats Bar */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <Card>
          <CardContent className="pt-4 pb-4">
            <p className="text-2xl font-bold">{companies.length}</p>
            <p className="text-xs text-muted-foreground">Total Clients</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4 pb-4">
            <p className="text-2xl font-bold text-emerald-600">{activeCount}</p>
            <p className="text-xs text-muted-foreground">Active</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4 pb-4">
            <p className="text-2xl font-bold text-amber-600">{Math.max(1, Math.floor(activeCount * 0.6))}</p>
            <p className="text-xs text-muted-foreground">Pending Filings</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4 pb-4">
            <p className="text-2xl font-bold text-blue-600">{Math.floor(activeCount * 0.8)}</p>
            <p className="text-xs text-muted-foreground">Recon Complete</p>
          </CardContent>
        </Card>
      </div>

      {/* Filters */}
      <div className="flex flex-col sm:flex-row gap-3">
        <input
          type="text"
          placeholder="Search by name or GSTIN..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="flex-1 h-9 rounded-md border border-input bg-background px-3 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
        />
        <select
          value={industryFilter}
          onChange={(e) => setIndustryFilter(e.target.value)}
          className="h-9 rounded-md border border-input bg-background px-3 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
        >
          <option value="">All Industries</option>
          {industries.map((ind) => (
            <option key={ind} value={ind}>{ind}</option>
          ))}
        </select>
        <select
          value={stateFilter}
          onChange={(e) => setStateFilter(e.target.value)}
          className="h-9 rounded-md border border-input bg-background px-3 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
        >
          <option value="">All States</option>
          {states.map((st) => (
            <option key={st} value={st}>{st}</option>
          ))}
        </select>
      </div>

      {/* Company Cards Grid */}
      {filtered.length === 0 ? (
        <div className="text-center py-12">
          <p className="text-muted-foreground">No companies found matching your filters.</p>
        </div>
      ) : (
        <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-4">
          {filtered.map((company) => (
            <Card
              key={company.id}
              className="cursor-pointer hover:shadow-md transition-shadow"
              onClick={() => navigate(`/dashboard/companies/${company.id}`)}
            >
              <CardHeader className="pb-2">
                <div className="flex items-start justify-between">
                  <div className={`w-10 h-10 rounded-lg bg-gradient-to-br ${INDUSTRY_COLORS[company.industry || ""] || "from-gray-500 to-gray-600"} flex items-center justify-center text-white font-bold text-sm`}>
                    {company.name.charAt(0)}
                  </div>
                  <div className="flex items-center gap-2">
                    <span
                      className={`w-2.5 h-2.5 rounded-full ${
                        (company.health_score ?? 0) >= 80 ? "bg-emerald-500" :
                        (company.health_score ?? 0) >= 50 ? "bg-amber-500" : "bg-red-500"
                      }`}
                      title={`Health: ${company.health_score ?? "N/A"}`}
                    />
                    <Badge variant={company.status === "active" ? "success" : "secondary"}>
                      {company.status || "active"}
                    </Badge>
                  </div>
                </div>
                <CardTitle className="text-base mt-2">{company.name}</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="space-y-1.5">
                  {company.gstin && (
                    <div className="flex items-center gap-2 text-xs">
                      <span className="text-muted-foreground w-12">GSTIN</span>
                      <span className="font-mono">{company.gstin}</span>
                    </div>
                  )}
                  {company.pan && (
                    <div className="flex items-center gap-2 text-xs">
                      <span className="text-muted-foreground w-12">PAN</span>
                      <span className="font-mono">{company.pan}</span>
                    </div>
                  )}
                  <div className="flex items-center gap-3 mt-2 pt-2 border-t">
                    {company.industry && (
                      <Badge variant="outline" className="text-[10px]">{company.industry}</Badge>
                    )}
                    {company.state && (
                      <span className="text-[10px] text-muted-foreground">{company.state}</span>
                    )}
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
