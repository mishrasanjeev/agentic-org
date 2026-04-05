"""Generate QA Test Plan PDFs for AgenticOrg v3.3.0.

Produces two files:
  - QA_TestPlan_Short_v3.3.0.pdf  (~10 pages, executive summary)
  - QA_TestPlan_Full_v3.3.0.pdf   (~40+ pages, comprehensive)
"""

from __future__ import annotations

import datetime
from fpdf import FPDF


# ── Shared constants ──────────────────────────────────────────────────────

VERSION = "4.0.0"
DATE = datetime.date.today().strftime("%Y-%m-%d")
TITLE_SHORT = f"AgenticOrg v{VERSION} -QA Test Plan (Summary)"
TITLE_FULL = f"AgenticOrg v{VERSION} -QA Test Plan (Comprehensive)"


class QAPdf(FPDF):
    """Custom PDF with headers/footers."""

    def __init__(self, title: str):
        super().__init__()
        self._title = title
        self.set_auto_page_break(auto=True, margin=20)

    def header(self):
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(100, 100, 100)
        self.cell(0, 6, self._title, align="L")
        self.cell(0, 6, f"v{VERSION} | {DATE}", align="R", new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(200, 200, 200)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(4)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(128, 128, 128)
        self.cell(0, 10, f"Page {self.page_no()}/{{nb}}", align="C")

    # ── helpers ──

    def section_title(self, num: str, text: str):
        self.set_font("Helvetica", "B", 14)
        self.set_text_color(25, 60, 120)
        self.cell(0, 10, f"{num}  {text}", new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(25, 60, 120)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(3)

    def sub_title(self, text: str):
        self.set_font("Helvetica", "B", 11)
        self.set_text_color(60, 60, 60)
        self.cell(0, 7, text, new_x="LMARGIN", new_y="NEXT")
        self.ln(1)

    def body(self, text: str):
        self.set_font("Helvetica", "", 10)
        self.set_text_color(30, 30, 30)
        self.multi_cell(0, 5.5, text)
        self.ln(2)

    def bullet(self, text: str):
        self.set_font("Helvetica", "", 10)
        self.set_text_color(30, 30, 30)
        x = self.get_x()
        self.cell(6, 5.5, "- ")
        self.multi_cell(0, 5.5, text)
        self.set_x(x)

    def table_header(self, cols: list[tuple[str, int]]):
        self.set_font("Helvetica", "B", 9)
        self.set_fill_color(25, 60, 120)
        self.set_text_color(255, 255, 255)
        for label, w in cols:
            self.cell(w, 7, label, border=1, fill=True, align="C")
        self.ln()

    def table_row(self, cols: list[tuple[str, int]], shade: bool = False):
        self.set_font("Helvetica", "", 9)
        self.set_text_color(30, 30, 30)
        if shade:
            self.set_fill_color(240, 245, 255)
        else:
            self.set_fill_color(255, 255, 255)
        for val, w in cols:
            self.cell(w, 6, val, border=1, fill=True)
        self.ln()

    def cover_page(self, title: str, subtitle: str):
        self.add_page()
        self.ln(50)
        self.set_font("Helvetica", "B", 28)
        self.set_text_color(25, 60, 120)
        self.cell(0, 15, "AgenticOrg", align="C", new_x="LMARGIN", new_y="NEXT")
        self.set_font("Helvetica", "", 16)
        self.set_text_color(80, 80, 80)
        self.cell(0, 10, title, align="C", new_x="LMARGIN", new_y="NEXT")
        self.ln(5)
        self.set_font("Helvetica", "I", 12)
        self.cell(0, 8, subtitle, align="C", new_x="LMARGIN", new_y="NEXT")
        self.ln(15)
        self.set_font("Helvetica", "", 11)
        self.set_text_color(60, 60, 60)
        info = [
            f"Version: {VERSION}",
            f"Date: {DATE}",
            "Platform: AI Virtual Employee Platform",
            "35 Agents | 54 Connectors | 340+ Tools",
            "Test Coverage: 1,662 backend + 93 frontend + 342 Playwright E2E",
        ]
        for line in info:
            self.cell(0, 7, line, align="C", new_x="LMARGIN", new_y="NEXT")
        self.ln(20)
        self.set_draw_color(25, 60, 120)
        self.set_line_width(0.5)
        self.line(60, self.get_y(), 150, self.get_y())
        self.ln(5)
        self.set_font("Helvetica", "I", 10)
        self.cell(0, 7, "Confidential - For QA Team Use Only", align="C")


# ═══════════════════════════════════════════════════════════════════════════
# SHORT PDF
# ═══════════════════════════════════════════════════════════════════════════

def build_short_pdf() -> QAPdf:
    pdf = QAPdf(TITLE_SHORT)
    pdf.alias_nb_pages()
    pdf.cover_page("QA Test Plan (Summary)", f"Release v{VERSION} - Scope Enforcement Fix")

    # ── 1. Scope ──
    pdf.add_page()
    pdf.section_title("1", "Release Scope")
    pdf.body(
        "v3.3.0 is a critical security release that replaces keyword-based permission guessing "
        "with Grantex SDK manifest-based scope enforcement. Every agent tool call is now verified "
        "via grantex.enforce() with offline JWT validation (<1ms). This release also adds 2 new UI pages, "
        "updates 3 existing pages, and includes 29 new tests."
    )
    pdf.sub_title("Key Changes")
    changes = [
        "validate_scopes graph node in LangGraph (reason -> validate_scopes -> execute_tools)",
        "53 pre-built Grantex manifests loaded at startup",
        "ToolGateway uses grantex.enforce() instead of keyword guessing",
        "check_scope() deprecated with DeprecationWarning",
        "grantex dependency bumped to >=0.3.3",
        "JWKS cache pre-warmed at FastAPI startup",
        "New pages: Scope Dashboard (/dashboard/scopes), Enforce Audit Log (/dashboard/enforce-audit)",
        "Updated: AgentCreate (permission badges), AgentDetail (Scopes tab), OrgChart (scope narrowing)",
        "New page: How Grantex Works (/how-grantex-works) - public explainer",
        "Landing page: v3.3.0 release banner",
    ]
    for c in changes:
        pdf.bullet(c)
    pdf.ln(3)

    # ── 2. Quick Smoke Tests ──
    pdf.section_title("2", "Smoke Tests (15 min)")
    smoke = [
        ("ST-01", "Health check", "GET /health returns 200 with status=ok"),
        ("ST-02", "Login", "POST /auth/login with valid creds returns JWT"),
        ("ST-03", "Agent list", "GET /agents returns paginated list (auth required)"),
        ("ST-04", "Create agent", "POST /agents creates agent in shadow mode"),
        ("ST-05", "MCP tools", "GET /mcp/tools returns 340+ tools (no auth)"),
        ("ST-06", "A2A card", "GET /a2a/agent-card returns public agent card"),
        ("ST-07", "Landing page", "GET / returns 200, has v3.3.0 banner"),
        ("ST-08", "Dashboard loads", "GET /dashboard loads with sidebar nav"),
        ("ST-09", "Scope Dashboard", "GET /dashboard/scopes loads with stats cards"),
        ("ST-10", "Enforce Audit", "GET /dashboard/enforce-audit loads with table"),
    ]
    cols = [("ID", 18), ("Test", 50), ("Expected Result", 122)]
    pdf.table_header(cols)
    for i, (tid, test, expected) in enumerate(smoke):
        pdf.table_row([(tid, 18), (test, 50), (expected, 122)], shade=i % 2 == 1)

    # ── 3. Scope Enforcement Tests ──
    pdf.add_page()
    pdf.section_title("3", "Scope Enforcement Tests (Critical)")
    scope_tests = [
        ("SE-01", "Read scope blocks delete", "Agent with tool:salesforce:read:* cannot call delete_contact"),
        ("SE-02", "Write covers read", "Agent with write scope can call read tools (hierarchy)"),
        ("SE-03", "Admin covers all", "Agent with admin scope can call any tool"),
        ("SE-04", "Expired token denied", "Expired JWT blocks all tool calls"),
        ("SE-05", "Empty token = no-op", "Empty grant_token allows tools (legacy mode)"),
        ("SE-06", "Manifest-based check", "process_refund correctly requires WRITE (not keyword guess)"),
        ("SE-07", "Gateway enforcement", "ToolGateway uses grantex.enforce() when grant_token present"),
        ("SE-08", "Budget enforcement", "Amount exceeding budget is blocked"),
        ("SE-09", "53 manifests loaded", "All 53 connector manifests load at startup"),
        ("SE-10", "Deprecation warning", "check_scope() emits DeprecationWarning"),
    ]
    cols = [("ID", 18), ("Test", 55), ("Expected Result", 117)]
    pdf.table_header(cols)
    for i, (tid, test, expected) in enumerate(scope_tests):
        pdf.table_row([(tid, 18), (test, 55), (expected, 117)], shade=i % 2 == 1)

    # ── 4. UI Tests ──
    pdf.ln(5)
    pdf.section_title("4", "UI/UX Tests")
    ui_tests = [
        ("UI-01", "AgentCreate badges", "Permission badges (READ/WRITE/DELETE/ADMIN) shown next to tools"),
        ("UI-02", "AgentCreate warning", "Yellow banner when DELETE/ADMIN tools selected"),
        ("UI-03", "AgentCreate scopes", "Resolved scope strings shown (tool:domain:perm:tool_name)"),
        ("UI-04", "AgentCreate minimal set", "Minimal scope set computed and displayed"),
        ("UI-05", "AgentDetail Scopes tab", "New tab with scope table, connector, status, grant token indicator"),
        ("UI-06", "AgentDetail enforce log", "Enforcement log section with timestamp/tool/result/reason"),
        ("UI-07", "ScopeDashboard stats", "4 aggregate stats: agents, calls, denials, rate"),
        ("UI-08", "ScopeDashboard table", "Table with agent/connector/tools/permission/status"),
        ("UI-09", "ScopeDashboard filters", "Filter by connector, permission, agent"),
        ("UI-10", "EnforceAuditLog table", "7 columns: timestamp, agent, connector, tool, perm, result, reason"),
        ("UI-11", "EnforceAuditLog denied", "Filter by denied only works"),
        ("UI-12", "EnforceAuditLog CSV", "Export to CSV downloads file"),
        ("UI-13", "EnforceAuditLog pagination", "50 rows per page, newest first"),
        ("UI-14", "OrgChart narrowing", "Scope narrowing indicator between parent/child nodes"),
        ("UI-15", "HowGrantexWorks page", "/how-grantex-works loads with 7 sections"),
        ("UI-16", "Landing banner", "v3.3.0 release banner visible with shimmer animation"),
        ("UI-17", "Sidebar nav", "Scope Dashboard and Enforce Audit in sidebar"),
    ]
    cols = [("ID", 16), ("Test", 52), ("Expected Result", 122)]
    pdf.table_header(cols)
    for i, (tid, test, expected) in enumerate(ui_tests):
        pdf.table_row([(tid, 16), (test, 52), (expected, 122)], shade=i % 2 == 1)

    # ── 5. Regression ──
    pdf.add_page()
    pdf.section_title("5", "Regression Checklist")
    regression = [
        ("RG-01", "Existing agents work", "Agents without Grantex tokens execute tools normally"),
        ("RG-02", "Workflows run", "All 15 workflow templates trigger and complete"),
        ("RG-03", "HITL approvals", "Approval queue shows pending, approve/reject works"),
        ("RG-04", "Shadow mode", "New agents start in shadow, quality gates enforced"),
        ("RG-05", "Kill switch", "Pause agent stops execution within 30s"),
        ("RG-06", "NL Query", "Cmd+K search returns agent-attributed answers"),
        ("RG-07", "CFO Dashboard", "Cash Runway, DSO, DPO, AR/AP Aging all render"),
        ("RG-08", "CMO Dashboard", "CAC, ROAS, MQLs, SQLs pipeline all render"),
        ("RG-09", "Multi-company", "Company switcher isolates data per entity"),
        ("RG-10", "Scheduled reports", "Celery beat runs, PDF/Excel generated"),
        ("RG-11", "A/B testing", "Campaign variants created, auto-winner selected"),
        ("RG-12", "Email drip", "Behavior-triggered sequences fire on open/click"),
        ("RG-13", "Web push", "Browser push notification for HITL approval"),
        ("RG-14", "SDK/MCP/A2A", "Python SDK, TS SDK, MCP tools, A2A tasks all work"),
    ]
    cols = [("ID", 16), ("Test", 48), ("Expected Result", 126)]
    pdf.table_header(cols)
    for i, (tid, test, expected) in enumerate(regression):
        pdf.table_row([(tid, 16), (test, 48), (expected, 126)], shade=i % 2 == 1)

    # ── 6. Sign-off ──
    pdf.ln(10)
    pdf.section_title("6", "Sign-off")
    pdf.body("All tests above must PASS before v3.3.0 can be marked as QA-approved.")
    pdf.ln(5)
    signoff = [("QA Lead", "", ""), ("Dev Lead", "", ""), ("Product Owner", "", "")]
    cols = [("Role", 45), ("Name", 70), ("Signature / Date", 75)]
    pdf.table_header(cols)
    for role, name, sig in signoff:
        pdf.table_row([(role, 45), (name, 70), (sig, 75)])

    return pdf


# ═══════════════════════════════════════════════════════════════════════════
# FULL PDF
# ═══════════════════════════════════════════════════════════════════════════

def build_full_pdf() -> QAPdf:
    pdf = QAPdf(TITLE_FULL)
    pdf.alias_nb_pages()
    pdf.cover_page("QA Test Plan (Comprehensive)", f"Release v{VERSION} - Full Product E2E Coverage")

    # ── TOC ──
    pdf.add_page()
    pdf.section_title("", "Table of Contents")
    toc = [
        "1. Release Overview",
        "2. Scope Enforcement (v3.3.0 - 29 tests)",
        "3. Authentication & Authorization (18 tests)",
        "4. Agent Management (25 tests)",
        "5. Connector Framework (54 connectors, 340+ tools)",
        "6. Workflow Engine (15 templates)",
        "7. HITL & Approvals (10 tests)",
        "8. Dashboards & KPIs (12 tests)",
        "9. Marketing Automation (15 tests)",
        "10. UI Pages & Routing (41 pages)",
        "11. API Endpoints (92 endpoints)",
        "12. External Integrations (SDK/MCP/A2A/CLI)",
        "13. Security & Compliance (47 tests)",
        "14. Performance & Scalability",
        "15. Landing Page & SEO",
        "16. Regression Matrix",
        "17. Environment Configuration",
        "18. Sign-off",
    ]
    for item in toc:
        pdf.bullet(item)
    pdf.ln(3)

    # ────────── 1. Release Overview ──────────
    pdf.add_page()
    pdf.section_title("1", "Release Overview")
    pdf.body(
        "AgenticOrg v3.3.0 is a critical security release. The primary change is replacing "
        "keyword-based permission guessing (check_scope()) with Grantex SDK manifest-based "
        "scope enforcement (grantex.enforce()). This affects the LangGraph agent execution path, "
        "the ToolGateway API-direct path, and adds new UI pages for scope visibility.\n\n"
        "Platform summary: 35 pre-built AI agents across 6 domains (finance, HR, marketing, ops, "
        "backoffice, comms), 54 connectors with 340+ tools, 15 workflow templates, CFO/CMO/ABM "
        "dashboards, NL Query, HITL governance, A/B testing, email drip, web push, multi-company, "
        "scheduled reports, and full SDK/MCP/A2A/CLI support."
    )
    pdf.sub_title("Files Changed in v3.3.0")
    files = [
        ("core/langgraph/grantex_auth.py", "Load 53 manifests at init"),
        ("core/langgraph/agent_graph.py", "validate_scopes node + graph routing"),
        ("core/tool_gateway/gateway.py", "grant_token param + grantex.enforce()"),
        ("auth/scopes.py", "Deprecation warning on check_scope()"),
        ("pyproject.toml", "grantex>=0.3.3, version 3.3.0"),
        ("api/main.py", "JWKS warm-up at startup, version 3.3.0"),
        ("ui/src/pages/ScopeDashboard.tsx", "NEW: Scope coverage dashboard"),
        ("ui/src/pages/EnforceAuditLog.tsx", "NEW: Real-time enforce audit log"),
        ("ui/src/pages/HowGrantexWorks.tsx", "NEW: Public Grantex explainer"),
        ("ui/src/pages/AgentCreate.tsx", "Permission badges, scope strings, warnings"),
        ("ui/src/pages/AgentDetail.tsx", "Scopes tab with enforce log"),
        ("ui/src/pages/OrgChart.tsx", "Scope narrowing indicators"),
        ("ui/src/pages/Landing.tsx", "v3.3.0 release banner"),
        ("ui/src/App.tsx", "New routes for 3 pages"),
        ("ui/src/components/Layout.tsx", "Sidebar nav entries"),
    ]
    cols = [("File", 85), ("Change", 105)]
    pdf.table_header(cols)
    for i, (f, c) in enumerate(files):
        pdf.table_row([(f, 85), (c, 105)], shade=i % 2 == 1)

    # ────────── 2. Scope Enforcement ──────────
    pdf.add_page()
    pdf.section_title("2", "Scope Enforcement Tests (v3.3.0)")
    pdf.body(
        "These 29 tests verify the new Grantex SDK integration. They are the highest priority "
        "for this release. Permission hierarchy: admin > delete > write > read."
    )

    pdf.sub_title("2.1 Permission Hierarchy (6 tests)")
    tests = [
        ("SE-01", "Denies delete with read scope", "Agent with tool:salesforce:read:* calls delete_contact -> DENIED", "P1"),
        ("SE-02", "Allows read with write scope", "Agent with write scope calls query -> ALLOWED (write > read)", "P1"),
        ("SE-03", "Allows read with read scope", "Agent with read scope calls get_contact -> ALLOWED", "P1"),
        ("SE-04", "Denies write with read scope", "Agent with read scope calls create_lead -> DENIED", "P1"),
        ("SE-05", "Denies admin with write scope", "Agent with write scope calls bulk_export_all -> DENIED", "P1"),
        ("SE-06", "Admin covers all", "Agent with admin scope calls get/create/delete/bulk -> ALL ALLOWED", "P1"),
    ]
    cols = [("ID", 16), ("Test", 52), ("Expected Result", 100), ("Pri", 12)]
    pdf.table_header(cols)
    for i, row in enumerate(tests):
        pdf.table_row([(v, w) for v, w in zip(row, [16, 52, 100, 12])], shade=i % 2 == 1)

    pdf.ln(3)
    pdf.sub_title("2.2 Token Validation (4 tests)")
    tests = [
        ("SE-07", "Denies revoked token", "Revoked grant token blocks all tool calls", "P1"),
        ("SE-08", "Denies expired token", "Expired JWT (exp in past) blocks all tools", "P1"),
        ("SE-09", "Empty token = no-op", "Legacy auth mode (empty grant_token) allows tools", "P1"),
        ("SE-10", "Invalid JWT blocked", "Bad signature JWT (eyJ..invalid) blocks all tools", "P1"),
    ]
    cols = [("ID", 16), ("Test", 52), ("Expected Result", 100), ("Pri", 12)]
    pdf.table_header(cols)
    for i, row in enumerate(tests):
        pdf.table_row([(v, w) for v, w in zip(row, [16, 52, 100, 12])], shade=i % 2 == 1)

    pdf.ln(3)
    pdf.sub_title("2.3 Offline Verification (1 test)")
    pdf.bullet("SE-11: enforce() uses cached JWKS, never calls POST /v1/tokens/verify (P1)")

    pdf.sub_title("2.4 Budget / Capped Scope (2 tests)")
    pdf.bullet("SE-12: Amount 500 exceeding budget 100 -> DENIED (P1)")
    pdf.bullet("SE-13: Amount 50 within budget 1000 -> ALLOWED (P1)")

    pdf.sub_title("2.5 Manifest Loading (3 tests)")
    pdf.bullet("SE-14: All 53 pre-built manifests load without error (P1)")
    pdf.bullet("SE-15: Custom JSON manifest from GRANTEX_MANIFESTS_DIR loads (P2)")
    pdf.bullet("SE-16: Extend pre-built manifest with add_tool() works (P2)")

    pdf.sub_title("2.6 Gateway Enforcement (2 tests)")
    pdf.bullet("SE-17: process_refund uses manifest WRITE permission, not keyword 'read' guess (P1)")
    pdf.bullet("SE-18: ToolGateway.execute() calls grantex.enforce() when grant_token provided (P1)")

    pdf.sub_title("2.7 Integration Tests (4 tests)")
    pdf.bullet("SE-19: Full flow - create agent, read scope, try write tool -> denied (P1)")
    pdf.bullet("SE-20: Delegation narrowing - parent write, child read, child can't write (P1)")
    pdf.bullet("SE-21: Token revocation stops running tools (P1)")
    pdf.bullet("SE-22: Budget debit - under budget allowed, over budget blocked (P2)")

    pdf.sub_title("2.8 E2E Tests (3 tests)")
    pdf.bullet("SE-23: RS256 token auth -> tool execution succeeds (P1)")
    pdf.bullet("SE-24: Insufficient scope -> structured error response (P1)")
    pdf.bullet("SE-25: Agent creation returns grantex_did (P2)")

    pdf.sub_title("2.9 UI Tests (4 tests)")
    pdf.bullet("SE-26: AgentCreate shows permission badges next to tools (P1)")
    pdf.bullet("SE-27: AgentDetail shows enforcement log tab (P1)")
    pdf.bullet("SE-28: Scope Dashboard renders with stats (P1)")
    pdf.bullet("SE-29: Enforce Audit Log filters by denied (P1)")

    # ────────── 3. Auth ──────────
    pdf.add_page()
    pdf.section_title("3", "Authentication & Authorization")
    auth_tests = [
        ("AU-01", "Email/password login", "Valid creds return JWT, invalid return 401", "P1"),
        ("AU-02", "Google OAuth", "Google id_token verified, user created/logged in", "P1"),
        ("AU-03", "Signup flow", "New org created, admin user, welcome email", "P1"),
        ("AU-04", "Password reset", "Forgot -> email -> reset token -> new password", "P2"),
        ("AU-05", "Token expiry", "JWT expires after 60 min, refresh required", "P1"),
        ("AU-06", "Token blacklist", "POST /auth/logout blacklists token in Redis", "P1"),
        ("AU-07", "API key auth", "ao_sk_ key authenticates API calls", "P1"),
        ("AU-08", "API key generation", "Admin creates key, bcrypt hashed at rest", "P2"),
        ("AU-09", "RBAC - CFO", "CFO sees only finance domain agents/dashboards", "P1"),
        ("AU-10", "RBAC - CMO", "CMO sees only marketing domain agents/dashboards", "P1"),
        ("AU-11", "RBAC - Auditor", "Auditor has read-only audit log access", "P2"),
        ("AU-12", "Brute force protection", "10 failed attempts -> 15 min IP lockout", "P1"),
        ("AU-13", "Grantex middleware", "RS256 token sets request.state.grant_token", "P1"),
        ("AU-14", "Scope ceiling", "Clone agent cannot elevate parent scopes", "P1"),
        ("AU-15", "Cross-tenant isolation", "Tenant A cannot access Tenant B data", "P1"),
        ("AU-16", "CORS enforcement", "Production restricts origins, dev allows all", "P2"),
        ("AU-17", "Team invite", "Invite -> accept with token -> member added", "P2"),
        ("AU-18", "Member removal", "DELETE /members/{id} revokes access", "P2"),
    ]
    cols = [("ID", 16), ("Test", 48), ("Expected Result", 104), ("Pri", 12)]
    pdf.table_header(cols)
    for i, row in enumerate(auth_tests):
        pdf.table_row([(v, w) for v, w in zip(row, [16, 48, 104, 12])], shade=i % 2 == 1)

    # ────────── 4. Agent Management ──────────
    pdf.add_page()
    pdf.section_title("4", "Agent Management")
    agent_tests = [
        ("AG-01", "Create agent (wizard)", "5-step wizard creates agent in shadow mode", "P1"),
        ("AG-02", "Create from SOP", "Upload SOP PDF -> parse -> deploy agent", "P2"),
        ("AG-03", "List agents", "GET /agents returns paginated, RBAC-filtered list", "P1"),
        ("AG-04", "Agent detail", "GET /agents/{id} returns full config + tools", "P1"),
        ("AG-05", "Update agent", "PUT /agents/{id} updates name, prompt, config", "P1"),
        ("AG-06", "Run agent", "POST /agents/{id}/run executes with LLM + tools", "P1"),
        ("AG-07", "Promote shadow", "Shadow -> Active after quality gates pass", "P1"),
        ("AG-08", "Rollback prompt", "Revert to previous prompt version", "P2"),
        ("AG-09", "Clone agent", "Clone with new persona, scope ceiling enforced", "P1"),
        ("AG-10", "Kill switch", "POST /agents/{id}/pause stops within 30s", "P1"),
        ("AG-11", "Resume agent", "POST /agents/{id}/resume restarts paused agent", "P2"),
        ("AG-12", "Prompt history", "GET prompt-history returns edit audit trail", "P2"),
        ("AG-13", "Cost controls", "Budget cap enforced, agent paused at limit", "P1"),
        ("AG-14", "Shadow quality gates", "6 gates: accuracy, confidence, HITL, hallucination, errors, latency", "P1"),
        ("AG-15", "Org chart hierarchy", "Parent-child reporting, smart escalation", "P1"),
        ("AG-16", "CSV import", "POST /agents/import-csv bulk creates agents", "P3"),
        ("AG-17", "Tool auto-population", "Agent type -> default tools assigned", "P2"),
        ("AG-18", "Delegation", "POST /delegate sets up Grantex trust chain", "P1"),
        ("AG-19", "LLM selection", "Agent uses configured model (Gemini/Claude/GPT)", "P2"),
        ("AG-20", "Confidence scoring", "LLM output confidence extracted, capped on errors", "P1"),
        ("AG-21", "Anti-hallucination", "Agent cannot invent data not from tools", "P1"),
        ("AG-22", "35 agent types", "All 35 pre-built agents register and run", "P1"),
        ("AG-23", "Custom agent type", "Wizard custom type creates with BaseAgent", "P2"),
        ("AG-24", "Routing filters", "Multiple agents of same type, filter-based routing", "P3"),
        ("AG-25", "Agent personas", "Employee name, designation, avatar, specialization", "P3"),
    ]
    cols = [("ID", 16), ("Test", 50), ("Expected Result", 102), ("Pri", 12)]
    pdf.table_header(cols)
    for i, row in enumerate(agent_tests):
        pdf.table_row([(v, w) for v, w in zip(row, [16, 50, 102, 12])], shade=i % 2 == 1)

    # ────────── 5. Connectors ──────────
    pdf.add_page()
    pdf.section_title("5", "Connector Framework (54 Connectors)")
    pdf.body(
        "Each connector must: authenticate, list tools, execute tools, handle errors gracefully, "
        "and report health status. All 54 connectors are live with real API endpoints."
    )
    domains = [
        ("Finance", "Oracle Fusion, SAP, Tally, GSTN, QuickBooks, Zoho Books, Banking AA, Income Tax India, Stripe, PineLabs, NetSuite", "11"),
        ("HR", "Darwinbox, Okta, Greenhouse, LinkedIn Talent, DocuSign, Keka, Zoom, EPFO", "8"),
        ("Marketing", "HubSpot, Salesforce, Google Ads, Meta Ads, LinkedIn Ads, Ahrefs, GA4, Mixpanel, Mailchimp, MoEngage, Buffer, Brandwatch, WordPress, Twitter, YouTube, Bombora, G2, TrustRadius, SendGrid", "19"),
        ("Ops", "Jira, ServiceNow, Zendesk, PagerDuty, Confluence, Sanctions API, MCA Portal", "7"),
        ("Comms", "Slack, GitHub, Gmail, Google Calendar, Twilio, WhatsApp, LangSmith, S3, SendGrid", "9"),
    ]
    cols = [("Domain", 28), ("Connectors", 142), ("Count", 20)]
    pdf.table_header(cols)
    for i, (d, c, n) in enumerate(domains):
        pdf.table_row([(d, 28), (c, 142), (n, 20)], shade=i % 2 == 1)

    pdf.ln(3)
    pdf.sub_title("Connector Test Checklist (per connector)")
    for t in [
        "CN-01: Authentication succeeds (OAuth/API key/JWT)",
        "CN-02: GET /connectors/{id}/health returns ok",
        "CN-03: Tool list matches expected count",
        "CN-04: Read tool executes and returns data",
        "CN-05: Write tool executes (if applicable)",
        "CN-06: Error handling (invalid creds, rate limit, timeout)",
        "CN-07: PII masking in logs (email, phone, Aadhaar, PAN)",
    ]:
        pdf.bullet(t)

    # ────────── 6. Workflows ──────────
    pdf.add_page()
    pdf.section_title("6", "Workflow Engine (15 Templates)")
    wf_tests = [
        ("WF-01", "invoice_to_pay_v3", "OCR -> GSTIN -> 3-way match -> payment", "P1"),
        ("WF-02", "month_end_close", "Trial balance -> adjustments -> close", "P1"),
        ("WF-03", "daily_treasury", "Cash position -> sweep -> forecast -> report", "P2"),
        ("WF-04", "tax_calendar", "Deadline tracking -> filing -> DSC signing", "P2"),
        ("WF-05", "campaign_launch", "Brief -> content -> review -> publish", "P1"),
        ("WF-06", "content_pipeline", "Ideation -> draft -> SEO -> publish", "P2"),
        ("WF-07", "lead_nurture", "Score -> segment -> drip -> wait_for_event -> handoff", "P1"),
        ("WF-08", "email_drip_sequence", "Trigger on open/click/delay, re-engage", "P1"),
        ("WF-09", "ab_test_campaign", "Variants -> run -> auto-winner -> CMO override", "P1"),
        ("WF-10", "abm_campaign", "CSV -> intent score -> personalized outreach", "P2"),
        ("WF-11", "incident_response", "Triage -> Jira -> assign -> resolve", "P1"),
        ("WF-12", "weekly_devops_health", "GitHub + Jira -> health score -> report", "P2"),
        ("WF-13", "employee_onboarding", "Approve -> provision -> train -> report", "P2"),
        ("WF-14", "weekly_marketing_report", "Collect -> build report -> deliver", "P2"),
        ("WF-15", "support_triage", "Email -> classify -> assign -> resolve", "P2"),
    ]
    cols = [("ID", 16), ("Template", 50), ("Flow", 102), ("Pri", 12)]
    pdf.table_header(cols)
    for i, row in enumerate(wf_tests):
        pdf.table_row([(v, w) for v, w in zip(row, [16, 50, 102, 12])], shade=i % 2 == 1)

    pdf.ln(3)
    pdf.sub_title("Workflow Engine Features")
    for f in [
        "Step types: agent, condition, wait, wait_for_event, approval, parallel, sub-workflow",
        "Triggers: manual, cron schedule, webhook, api_event, email_received",
        "Error handling: retry (exponential backoff), timeout, skip",
        "Parallel execution with dependency resolution",
        "State checkpointing and context passing between steps",
    ]:
        pdf.bullet(f)

    # ────────── 7. HITL ──────────
    pdf.add_page()
    pdf.section_title("7", "HITL & Approvals")
    hitl_tests = [
        ("HI-01", "HITL trigger on low confidence", "Confidence < floor triggers interrupt", "P1"),
        ("HI-02", "HITL trigger on condition", "amount > 50000 triggers interrupt", "P1"),
        ("HI-03", "Approve resumes agent", "POST /approvals/{id}/decide action=approve", "P1"),
        ("HI-04", "Reject stops agent", "action=reject sets status=failed", "P1"),
        ("HI-05", "Web push notification", "Browser push fires on HITL trigger", "P1"),
        ("HI-06", "Approval queue", "GET /approvals lists pending decisions", "P1"),
        ("HI-07", "Escalation chain", "Low confidence escalates up org chart", "P2"),
        ("HI-08", "Timeout auto-escalate", "No decision in N min escalates", "P3"),
        ("HI-09", "HITL in workflow", "approval step pauses workflow execution", "P1"),
        ("HI-10", "HITL audit trail", "All decisions logged with user + timestamp", "P2"),
    ]
    cols = [("ID", 16), ("Test", 52), ("Expected Result", 100), ("Pri", 12)]
    pdf.table_header(cols)
    for i, row in enumerate(hitl_tests):
        pdf.table_row([(v, w) for v, w in zip(row, [16, 52, 100, 12])], shade=i % 2 == 1)

    # ────────── 8. Dashboards ──────────
    pdf.ln(5)
    pdf.section_title("8", "Dashboards & KPIs")
    dash_tests = [
        ("DA-01", "Main dashboard", "Overview cards render for all roles", "P1"),
        ("DA-02", "CFO: Cash Runway", "GET /kpis/cfo returns cash runway days", "P1"),
        ("DA-03", "CFO: DSO/DPO", "Days Sales/Payable Outstanding calculated", "P1"),
        ("DA-04", "CFO: AR/AP Aging", "30/60/90/120+ day aging buckets", "P1"),
        ("DA-05", "CMO: CAC by channel", "Cost per acquisition per channel", "P1"),
        ("DA-06", "CMO: ROAS", "Return on ad spend by Google/Meta/LinkedIn", "P1"),
        ("DA-07", "CMO: Pipeline", "MQL -> SQL -> Opportunity funnel", "P1"),
        ("DA-08", "ABM: Intent heatmap", "Bombora + G2 + TrustRadius scoring", "P1"),
        ("DA-09", "ABM: Account list", "Target accounts with tier/status", "P1"),
        ("DA-10", "Report scheduler", "Create/toggle/run-now scheduled reports", "P2"),
        ("DA-11", "Company switcher", "Switch between entities, data isolates", "P1"),
        ("DA-12", "Observatory", "Performance metrics across all agents", "P2"),
    ]
    cols = [("ID", 16), ("Test", 48), ("Expected Result", 104), ("Pri", 12)]
    pdf.table_header(cols)
    for i, row in enumerate(dash_tests):
        pdf.table_row([(v, w) for v, w in zip(row, [16, 48, 104, 12])], shade=i % 2 == 1)

    # ────────── 9. Marketing Automation ──────────
    pdf.add_page()
    pdf.section_title("9", "Marketing Automation (v3.2.0)")
    mkt_tests = [
        ("MK-01", "A/B test create", "Create 2+ campaign variants", "P1"),
        ("MK-02", "A/B auto-winner", "Auto-select by open rate or CTR", "P1"),
        ("MK-03", "A/B CMO override", "CMO overrides auto-winner before send", "P1"),
        ("MK-04", "Drip: time delay", "Trigger next email after N hours", "P1"),
        ("MK-05", "Drip: open trigger", "Next step fires when email opened", "P1"),
        ("MK-06", "Drip: click trigger", "Next step fires when link clicked", "P1"),
        ("MK-07", "Drip: re-engage", "Non-openers get re-engagement email", "P2"),
        ("MK-08", "ABM: CSV upload", "Upload target accounts via CSV", "P1"),
        ("MK-09", "ABM: intent score", "Bombora 40% + G2 30% + TrustRadius 30%", "P1"),
        ("MK-10", "ABM: campaign launch", "One-click campaign for target account", "P1"),
        ("MK-11", "Webhook: SendGrid", "POST /webhooks/email/sendgrid processes events", "P2"),
        ("MK-12", "Webhook: Mailchimp", "Open/click events received and stored", "P2"),
        ("MK-13", "Push subscribe", "POST /push/subscribe registers device", "P2"),
        ("MK-14", "Push notification", "Browser push fires with approve/reject", "P1"),
        ("MK-15", "Push unsubscribe", "POST /push/unsubscribe removes device", "P3"),
    ]
    cols = [("ID", 16), ("Test", 48), ("Expected Result", 104), ("Pri", 12)]
    pdf.table_header(cols)
    for i, row in enumerate(mkt_tests):
        pdf.table_row([(v, w) for v, w in zip(row, [16, 48, 104, 12])], shade=i % 2 == 1)

    # ────────── 10. UI Pages ──────────
    pdf.add_page()
    pdf.section_title("10", "UI Pages & Routing (41 Pages)")
    pdf.sub_title("10.1 Public Pages (19)")
    public_pages = [
        ("/", "Landing page with v3.3.0 banner, animations, demo"),
        ("/login", "Email/password + Google OAuth login"),
        ("/signup", "Organization registration"),
        ("/forgot-password", "Password reset request"),
        ("/reset-password", "Reset with token"),
        ("/pricing", "Free/Pro/Enterprise tiers"),
        ("/playground", "Demo agents without login"),
        ("/evals", "Agent evaluation scores"),
        ("/blog", "Blog index + /blog/:slug articles"),
        ("/resources", "SEO content hub + /resources/:slug"),
        ("/integration-workflow", "SDK/CLI/MCP integration guide"),
        ("/how-grantex-works", "Grantex scope enforcement explainer (NEW v3.3.0)"),
        ("/solutions/*", "3 Google Ads landing pages"),
    ]
    for path, desc in public_pages:
        pdf.bullet(f"{path} - {desc}")

    pdf.sub_title("10.2 Protected Pages (22)")
    protected_pages = [
        ("/dashboard", "Main dashboard (role-filtered)"),
        ("/dashboard/cfo", "CFO KPI dashboard"),
        ("/dashboard/cmo", "CMO KPI dashboard"),
        ("/dashboard/abm", "ABM target accounts + intent"),
        ("/dashboard/scopes", "Scope enforcement dashboard (NEW v3.3.0)"),
        ("/dashboard/enforce-audit", "Enforce audit log (NEW v3.3.0)"),
        ("/dashboard/agents", "Agent fleet list"),
        ("/dashboard/agents/new", "5-step agent creator wizard"),
        ("/dashboard/agents/from-sop", "Create agent from SOP"),
        ("/dashboard/agents/:id", "Agent detail (6 tabs)"),
        ("/dashboard/org-chart", "Organization hierarchy tree"),
        ("/dashboard/workflows", "Workflow templates + instances"),
        ("/dashboard/workflows/new", "Create workflow"),
        ("/dashboard/workflows/:id", "Workflow detail"),
        ("/dashboard/approvals", "HITL approval queue"),
        ("/dashboard/connectors", "Connector management"),
        ("/dashboard/prompt-templates", "Prompt template library"),
        ("/dashboard/audit", "Audit log"),
        ("/dashboard/sales", "Sales pipeline"),
        ("/dashboard/report-schedules", "Scheduled reports"),
        ("/dashboard/sla", "SLA monitoring"),
        ("/dashboard/settings", "Admin settings"),
    ]
    for path, desc in protected_pages:
        pdf.bullet(f"{path} - {desc}")

    # ────────── 11. API Endpoints ──────────
    pdf.add_page()
    pdf.section_title("11", "API Endpoints (92 Total)")
    pdf.body("Base URL: https://app.agenticorg.ai/api/v1")
    api_groups = [
        ("Auth", "signup, login, google, forgot-password, reset-password, logout, config", "7"),
        ("Health", "health, health/liveness", "2"),
        ("Agents", "CRUD, run, pause, resume, promote, retire, clone, rollback, delegate, prompt-history, budget", "16"),
        ("Workflows", "CRUD, run, runs/{id}", "6"),
        ("Approvals", "list, decide", "2"),
        ("Connectors", "registry, CRUD, health", "6"),
        ("Prompts", "CRUD templates", "5"),
        ("Sales", "leads, pipeline, metrics, import, follow-ups, inbox", "10"),
        ("Chat/NL", "query, history", "2"),
        ("KPIs", "cfo, cmo", "2"),
        ("ABM", "accounts CRUD, upload, intent, campaign, dashboard", "7"),
        ("Reports", "schedules CRUD, run-now", "5"),
        ("Audit", "list (paginated, WORM)", "1"),
        ("Compliance", "DSAR access/erase/export, evidence-package", "4"),
        ("Org", "profile, members, invite, accept-invite, onboarding", "6"),
        ("Companies", "CRUD", "4"),
        ("A2A", "agent-card, agents, tasks", "4"),
        ("MCP", "tools, call", "2"),
        ("Push", "vapid-key, subscribe, unsubscribe, test", "4"),
        ("Webhooks", "sendgrid, mailchimp, moengage", "3"),
        ("Bridge", "register, status, list, route", "4"),
    ]
    cols = [("Group", 28), ("Endpoints", 132), ("Count", 20)]
    pdf.table_header(cols)
    for i, (g, e, c) in enumerate(api_groups):
        pdf.table_row([(g, 28), (e, 132), (c, 20)], shade=i % 2 == 1)

    # ────────── 12. External Integrations ──────────
    pdf.add_page()
    pdf.section_title("12", "External Integrations")
    pdf.sub_title("12.1 Python SDK (pip install agenticorg)")
    pdf.bullet("EX-01: client.agents.list() returns agent list")
    pdf.bullet("EX-02: client.agents.run() executes agent")
    pdf.bullet("EX-03: client.sop.parse_text() parses SOP")
    pdf.bullet("EX-04: client.a2a.agent_card() returns discovery card")

    pdf.sub_title("12.2 TypeScript SDK (npm i agenticorg-sdk)")
    pdf.bullet("EX-05: client.agents.list() returns agent list")
    pdf.bullet("EX-06: client.agents.run() executes agent")

    pdf.sub_title("12.3 MCP Server (npx agenticorg-mcp-server)")
    pdf.bullet("EX-07: GET /mcp/tools returns 340+ tools")
    pdf.bullet("EX-08: POST /mcp/call executes tool with auth")
    pdf.bullet("EX-09: Claude Desktop / Cursor integration works")

    pdf.sub_title("12.4 A2A Protocol")
    pdf.bullet("EX-10: GET /a2a/agent-card returns public card (no auth)")
    pdf.bullet("EX-11: GET /a2a/agents lists discoverable agents")
    pdf.bullet("EX-12: POST /a2a/tasks executes cross-platform task")

    pdf.sub_title("12.5 CLI")
    pdf.bullet("EX-13: agenticorg agents list")
    pdf.bullet("EX-14: agenticorg agents run ap_processor --input '{...}'")
    pdf.bullet("EX-15: agenticorg mcp tools")

    # ────────── 13. Security ──────────
    pdf.section_title("13", "Security & Compliance (47 tests)")
    pdf.body("Existing security test suite covers:")
    sec_categories = [
        ("SEC-AUTH", "Auth bypass, brute force, token replay, scope elevation, cross-tenant", "8"),
        ("SEC-LLM", "Prompt injection (direct/indirect/context), SQL injection, hallucination", "6"),
        ("SEC-DATA", "PII masking, encryption, TLS, data residency, tenant isolation, DPDP erasure", "7"),
        ("SEC-INFRA", "Container CVE scanning, WORM audit immutability", "2"),
        ("SEC-SCOPE (v3.3)", "Permission hierarchy, manifest enforcement, JWT validation, budget", "29"),
    ]
    cols = [("Category", 35), ("Tests", 125), ("Count", 20)]
    pdf.table_header(cols)
    for i, (cat, tests, count) in enumerate(sec_categories):
        pdf.table_row([(cat, 35), (tests, 125), (count, 20)], shade=i % 2 == 1)

    # ────────── 14. Performance ──────────
    pdf.add_page()
    pdf.section_title("14", "Performance & Scalability")
    perf = [
        ("PF-01", "enforce() latency", "< 1ms after JWKS warm-up (offline)", "P1"),
        ("PF-02", "JWKS warm-up", "~300ms at startup (one-time)", "P2"),
        ("PF-03", "Agent response time", "< 10s for simple tasks (Gemini Flash)", "P2"),
        ("PF-04", "API response time", "< 200ms for CRUD endpoints", "P1"),
        ("PF-05", "Concurrent agents", "500+ concurrent workflow runs", "P3"),
        ("PF-06", "Rate limiting", "Token bucket per tenant per connector", "P2"),
        ("PF-07", "DB connection pool", "AsyncPG pool handles 100+ connections", "P3"),
        ("PF-08", "Redis latency", "< 5ms for cache/blacklist operations", "P3"),
    ]
    cols = [("ID", 16), ("Test", 48), ("Expected Result", 104), ("Pri", 12)]
    pdf.table_header(cols)
    for i, row in enumerate(perf):
        pdf.table_row([(v, w) for v, w in zip(row, [16, 48, 104, 12])], shade=i % 2 == 1)

    # ────────── 15. Landing & SEO ──────────
    pdf.ln(5)
    pdf.section_title("15", "Landing Page & SEO")
    seo = [
        ("LP-01", "Landing page loads", "/ returns 200 with all sections", "P1"),
        ("LP-02", "v3.3.0 banner", "Release banner with shimmer animation visible", "P1"),
        ("LP-03", "Grantex link", "'Learn how it works' links to /how-grantex-works", "P2"),
        ("LP-04", "sitemap.xml", "40+ URLs, includes new pages", "P2"),
        ("LP-05", "robots.txt", "Allows major crawlers (GPTBot, ClaudeBot)", "P3"),
        ("LP-06", "llms.txt", "Product summary for AI crawlers", "P3"),
        ("LP-07", "Blog renders", "/blog and /blog/:slug load correctly", "P2"),
        ("LP-08", "Pricing page", "3 tiers with feature comparison", "P2"),
        ("LP-09", "Responsive", "Mobile/tablet/desktop layouts work", "P2"),
        ("LP-10", "JSON-LD schemas", "Organization, Product, FAQ structured data", "P3"),
    ]
    cols = [("ID", 16), ("Test", 48), ("Expected Result", 104), ("Pri", 12)]
    pdf.table_header(cols)
    for i, row in enumerate(seo):
        pdf.table_row([(v, w) for v, w in zip(row, [16, 48, 104, 12])], shade=i % 2 == 1)

    # ────────── 16. Regression Matrix ──────────
    pdf.add_page()
    pdf.section_title("16", "Regression Matrix")
    pdf.body("All features from v2.0 through v3.2 must continue working after v3.3.0 deployment.")
    reg = [
        ("v2.0", "24 agents, 43 connectors, workflow engine, OAuth2, audit", "P1"),
        ("v2.1", "Token pool, rate limiter, shadow comparator, OTel, LangSmith", "P1"),
        ("v2.2", "Agent-to-connector bridge, tool calling, GitHub/Jira/HubSpot live", "P1"),
        ("v2.3", "Password reset, API keys, SDKs, MCP, CLI, A2A, comms agents", "P1"),
        ("v3.1", "CFO/CMO dashboards, NL Query, multi-company, 8 new agents, 7 connectors", "P1"),
        ("v3.2", "A/B testing, email drip, ABM, web push, Bombora/G2/TrustRadius", "P1"),
        ("v3.3", "Scope enforcement, 53 manifests, ScopeDashboard, EnforceAuditLog", "P1"),
    ]
    cols = [("Version", 20), ("Features to Verify", 138), ("Pri", 12)]
    pdf.table_header(cols)
    for i, row in enumerate(reg):
        pdf.table_row([(v, w) for v, w in zip(row, [20, 138, 12])], shade=i % 2 == 1)

    # ────────── 17. Environment ──────────
    pdf.ln(5)
    pdf.section_title("17", "Environment Configuration")
    pdf.sub_title("Required Environment Variables")
    envs = [
        ("GOOGLE_GEMINI_API_KEY", "LLM primary (free tier)", "Required"),
        ("GRANTEX_API_KEY", "Grantex SDK for enforce()", "Required (v3.3)"),
        ("GRANTEX_BASE_URL", "Grantex API base URL", "Optional"),
        ("GRANTEX_MANIFESTS_DIR", "Custom manifest directory", "Optional"),
        ("AGENTICORG_DB_URL", "PostgreSQL connection", "Required"),
        ("AGENTICORG_REDIS_URL", "Redis connection", "Required"),
        ("AGENTICORG_SECRET_KEY", "JWT signing key", "Required"),
    ]
    cols = [("Variable", 60), ("Purpose", 85), ("Status", 25)]
    pdf.table_header(cols)
    for i, (var, purpose, status) in enumerate(envs):
        pdf.table_row([(var, 60), (purpose, 85), (status, 25)], shade=i % 2 == 1)

    pdf.ln(3)
    pdf.sub_title("Test Environments")
    pdf.bullet("Production: https://app.agenticorg.ai (GKE asia-south1)")
    pdf.bullet("Staging: N/A (skipped in current pipeline)")
    pdf.bullet("Local: docker-compose up (PostgreSQL + Redis + API + UI)")

    # ────────── 18. Sign-off ──────────
    pdf.add_page()
    pdf.section_title("18", "Sign-off")
    pdf.body(
        "All P1 tests must PASS. P2 tests should PASS (waivers require Product Owner approval). "
        "P3 tests are informational. Total test count: ~200 manual + 1,662 automated backend + "
        "93 frontend vitest + 342 Playwright E2E."
    )
    pdf.ln(5)
    signoff = [
        ("QA Lead", "", "", ""),
        ("Dev Lead", "", "", ""),
        ("Security Lead", "", "", ""),
        ("Product Owner", "", "", ""),
    ]
    cols = [("Role", 38), ("Name", 52), ("Result (PASS/FAIL)", 48), ("Date / Signature", 52)]
    pdf.table_header(cols)
    for role, name, result, sig in signoff:
        pdf.table_row([(role, 38), (name, 52), (result, 48), (sig, 52)])

    return pdf


# ═══════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import os

    out_dir = os.path.join(os.path.dirname(__file__), "..", "docs")
    os.makedirs(out_dir, exist_ok=True)

    short_path = os.path.join(out_dir, f"QA_TestPlan_Short_v{VERSION}.pdf")
    full_path = os.path.join(out_dir, f"QA_TestPlan_Full_v{VERSION}.pdf")

    print("Generating short PDF...")
    short = build_short_pdf()
    short.output(short_path)
    print(f"  -> {short_path} ({short.pages_count} pages)")

    print("Generating full PDF...")
    full = build_full_pdf()
    full.output(full_path)
    print(f"  -> {full_path} ({full.pages_count} pages)")

    print("\nDone!")
