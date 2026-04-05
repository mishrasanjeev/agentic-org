"""Generate Functional Specification Document PDF for AgenticOrg v3.3.0.

Produces:
  docs/AgenticOrg_FunctionalSpec_v3.3.0.pdf  (~50-60 pages)
"""

from __future__ import annotations

import datetime
import os

from fpdf import FPDF

# -- Constants ----------------------------------------------------------------

VERSION = "4.0.0"
DATE = datetime.datetime.now(tz=datetime.UTC).strftime("%Y-%m-%d")
DOC_TITLE = f"AgenticOrg Functional Specification v{VERSION}"


class FuncSpecPdf(FPDF):
    """Custom PDF with branded headers, footers, and helper methods."""

    def __init__(self) -> None:
        super().__init__()
        self._title = DOC_TITLE
        self.set_auto_page_break(auto=True, margin=22)
        self._skip_header = False

    # -- Header / Footer ------------------------------------------------------

    def header(self) -> None:
        if self._skip_header:
            return
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(100, 100, 100)
        self.cell(95, 6, self._title, align="L")
        self.cell(95, 6, f"v{VERSION} | {DATE}", align="R",
                  new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(200, 200, 200)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(4)

    def footer(self) -> None:
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(128, 128, 128)
        self.cell(0, 10, f"Page {self.page_no()}/{{nb}}", align="C")

    # -- Cover page -----------------------------------------------------------

    def cover_page(self) -> None:
        self._skip_header = True
        self.add_page()
        self._skip_header = False

        self.ln(40)
        self.set_font("Helvetica", "B", 32)
        self.set_text_color(25, 60, 120)
        self.cell(0, 15, "AgenticOrg", align="C",
                  new_x="LMARGIN", new_y="NEXT")

        self.ln(3)
        self.set_font("Helvetica", "", 18)
        self.set_text_color(60, 60, 60)
        self.cell(0, 10, "Functional Specification Document",
                  align="C", new_x="LMARGIN", new_y="NEXT")

        self.ln(2)
        self.set_font("Helvetica", "B", 14)
        self.set_text_color(25, 60, 120)
        self.cell(0, 10, f"Version {VERSION}",
                  align="C", new_x="LMARGIN", new_y="NEXT")

        self.ln(10)
        self.set_draw_color(25, 60, 120)
        self.set_line_width(0.6)
        self.line(60, self.get_y(), 150, self.get_y())
        self.ln(10)

        self.set_font("Helvetica", "", 11)
        self.set_text_color(60, 60, 60)
        info_lines = [
            f"Date: {DATE}",
            "Classification: CONFIDENTIAL",
            "",
            "AI Virtual Employee Platform",
            "35 Agents | 54 Connectors | 340+ Tools | 15 Workflows",
            "",
            "Prepared by: AgenticOrg Engineering Team",
            "Approved by: Sanjeev Kumar, CEO & Founder",
        ]
        for ln in info_lines:
            self.cell(0, 7, ln, align="C", new_x="LMARGIN", new_y="NEXT")

        self.ln(20)
        self.set_font("Helvetica", "I", 10)
        self.set_text_color(150, 50, 50)
        self.cell(0, 7,
                  "CONFIDENTIAL -- For internal and authorized partner use only",
                  align="C")

    # -- Table of Contents ----------------------------------------------------

    def toc_page(self) -> None:
        self.add_page()
        self.set_font("Helvetica", "B", 18)
        self.set_text_color(25, 60, 120)
        self.cell(0, 12, "Table of Contents",
                  new_x="LMARGIN", new_y="NEXT")
        self.ln(4)

        toc = [
            ("1", "Product Overview", "3"),
            ("2", "Authentication & Authorization", "4"),
            ("3", "Grantex Scope Enforcement (v3.3.0)", "7"),
            ("4", "Agent Management", "10"),
            ("5", "LangGraph Agent Runtime", "14"),
            ("6", "Connector Framework", "16"),
            ("7", "Tool Gateway", "19"),
            ("8", "Workflow Engine", "21"),
            ("9", "HITL System", "24"),
            ("10", "Dashboards & KPIs", "26"),
            ("11", "Marketing Automation", "28"),
            ("12", "Sales Pipeline", "30"),
            ("13", "Multi-Company Support", "31"),
            ("14", "Scheduled Reports", "32"),
            ("15", "Compliance & Audit", "33"),
            ("16", "External Integrations", "34"),
            ("17", "Error Taxonomy", "36"),
            ("18", "Environment Configuration", "38"),
            ("A", "Version History", ""),
            ("B", "Database Schema Overview", ""),
            ("C", "Glossary of Terms", ""),
            ("D", "Sign-off", ""),
        ]
        self.set_font("Helvetica", "", 11)
        self.set_text_color(30, 30, 30)
        for num, title, _pg in toc:
            if num.isdigit():
                prefix = f"  Section {num}:"
            else:
                prefix = f"  Appendix {num}:"
            self.cell(100, 7, f"{prefix}  {title}")
            self.cell(80, 7, "", new_x="LMARGIN", new_y="NEXT")
        self.ln(5)

    # -- Helpers --------------------------------------------------------------

    def section_title(self, num: str, text: str) -> None:
        self.set_font("Helvetica", "B", 15)
        self.set_text_color(25, 60, 120)
        self.cell(0, 10, f"{num}  {text}",
                  new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(25, 60, 120)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(4)

    def sub_title(self, text: str) -> None:
        self.set_font("Helvetica", "B", 11)
        self.set_text_color(50, 50, 50)
        self.cell(0, 8, text, new_x="LMARGIN", new_y="NEXT")
        self.ln(1)

    def sub_sub_title(self, text: str) -> None:
        self.set_font("Helvetica", "BI", 10)
        self.set_text_color(70, 70, 70)
        self.cell(0, 7, text, new_x="LMARGIN", new_y="NEXT")
        self.ln(1)

    def body(self, text: str) -> None:
        self.set_font("Helvetica", "", 10)
        self.set_text_color(30, 30, 30)
        self.set_x(10)
        self.multi_cell(190, 5.5, text)
        self.ln(2)

    def body_small(self, text: str) -> None:
        self.set_font("Helvetica", "", 9)
        self.set_text_color(30, 30, 30)
        self.set_x(10)
        self.multi_cell(190, 5, text)
        self.ln(1.5)

    def bullet(self, text: str) -> None:
        self.set_font("Helvetica", "", 10)
        self.set_text_color(30, 30, 30)
        self.set_x(14)
        self.cell(5, 5.5, "-")
        self.multi_cell(175, 5.5, text)

    def bullet_small(self, text: str) -> None:
        self.set_font("Helvetica", "", 9)
        self.set_text_color(30, 30, 30)
        self.set_x(18)
        self.cell(5, 5, "-")
        self.multi_cell(170, 5, text)

    def code_block(self, text: str) -> None:
        self.set_font("Courier", "", 9)
        self.set_text_color(30, 30, 30)
        self.set_fill_color(245, 245, 245)
        self.set_x(14)
        self.multi_cell(180, 5, text, fill=True)
        self.ln(2)

    def table_header(self, cols: list[tuple[str, int]]) -> None:
        self.set_font("Helvetica", "B", 9)
        self.set_fill_color(25, 60, 120)
        self.set_text_color(255, 255, 255)
        for label, w in cols:
            self.cell(w, 7, label, border=1, fill=True, align="C")
        self.ln()

    def table_row(self, cols: list[tuple[str, int]],
                  shade: bool = False) -> None:
        self.set_font("Helvetica", "", 8)
        self.set_text_color(30, 30, 30)
        if shade:
            self.set_fill_color(240, 245, 255)
        else:
            self.set_fill_color(255, 255, 255)
        for val, w in cols:
            self.cell(w, 6, val, border=1, fill=True)
        self.ln()

    def table_row_wrap(self, cols: list[tuple[str, int]],
                       shade: bool = False) -> None:
        """Row with wrapping text in the last column."""
        self.set_font("Helvetica", "", 8)
        self.set_text_color(30, 30, 30)
        if shade:
            self.set_fill_color(240, 245, 255)
        else:
            self.set_fill_color(255, 255, 255)
        # Fixed cols first
        x_start = self.get_x()
        y_start = self.get_y()
        total_fixed_w = 0
        for val, w in cols[:-1]:
            self.cell(w, 6, val, border=1, fill=True)
            total_fixed_w += w
        # Last col wraps
        last_val, last_w = cols[-1]
        self.multi_cell(last_w, 6, last_val, border=1, fill=True)
        y_end = self.get_y()
        row_h = y_end - y_start
        # If multi_cell made the row taller, re-draw fixed cells
        if row_h > 6:
            self.set_xy(x_start, y_start)
            for val, w in cols[:-1]:
                self.cell(w, row_h, val, border=1, fill=True)
            self.set_xy(x_start, y_end)

    def note_box(self, text: str) -> None:
        self.set_font("Helvetica", "I", 9)
        self.set_text_color(80, 80, 80)
        self.set_fill_color(255, 255, 230)
        self.set_x(14)
        self.multi_cell(180, 5, f"NOTE: {text}", fill=True)
        self.ln(2)

    def check_space(self, needed: float = 40) -> None:
        """Add page if less than `needed` mm remain."""
        if self.get_y() > (297 - 22 - needed):
            self.add_page()


# =============================================================================
# SECTION BUILDERS
# =============================================================================

def sec01_product_overview(pdf: FuncSpecPdf) -> None:
    pdf.add_page()
    pdf.section_title("1", "Product Overview")

    pdf.body(
        "AgenticOrg is an enterprise-grade AI Virtual Employee Platform that "
        "deploys autonomous AI agents across every business function. "
        "Each agent acts as a specialized virtual employee -- reasoning over "
        "live enterprise data, executing multi-step workflows, and escalating "
        "decisions to human stakeholders when confidence is low."
    )
    pdf.body(
        "Unlike simple chatbot or copilot solutions, AgenticOrg agents operate "
        "autonomously within defined guardrails. They read and write to enterprise "
        "systems (ERP, CRM, HRMS, marketing platforms), follow Standard Operating "
        "Procedures (SOPs) encoded as workflows, and maintain a full audit trail "
        "of every action taken. The platform is designed for the India market "
        "with first-class support for GST, TDS, EPFO, and RBI Account Aggregator."
    )

    pdf.sub_title("1.1  Platform at a Glance")
    stats = [
        "50+ AI Agents spanning Finance, HR, Marketing, Operations, and Sales",
        "54 Production Connectors (Oracle, SAP, Tally, GSTN, HubSpot, Jira, etc.)",
        "340+ Tools exposed via MCP server, A2A protocol, and REST API",
        "15 Pre-built Workflow Templates (invoice-to-pay, campaign launch, incident response, etc.)",
        "Grantex-powered scope enforcement with 53 permission manifests (v3.3.0)",
        "Human-in-the-Loop (HITL) approvals with web push notifications",
        "Multi-company / multi-tenant architecture with RBAC",
        "Full compliance suite: WORM audit logs, DSAR, PII masking, 7-year retention",
        "Python SDK, TypeScript SDK, CLI, MCP server, and A2A protocol for integrations",
        "LangGraph-based agent runtime with confidence scoring and cost controls",
    ]
    for s in stats:
        pdf.bullet(s)
    pdf.ln(3)

    pdf.sub_title("1.2  Target Users")
    pdf.body(
        "AgenticOrg is designed for enterprise CXOs and their teams:"
    )
    users = [
        "CFO and Finance Teams -- Cash flow forecasting, AP/AR aging, month-end close, "
        "tax compliance, treasury management, revenue recognition, and financial planning",
        "CMO and Marketing Teams -- Campaign automation, ABM, lead nurture, A/B testing, "
        "SEO optimization, social media management, and content pipeline",
        "CHRO and HR Teams -- Employee onboarding, payroll processing, compliance tracking, "
        "recruitment pipeline, benefits administration, and L&D management",
        "COO and Operations Teams -- Incident response, DevOps health, support triage, "
        "vendor management, SLA monitoring, and capacity planning",
        "CA Firms -- Multi-client GST filing, TDS reconciliation, audit preparation, "
        "income tax returns, statutory compliance, and MCA filings",
        "IT Operations -- Infrastructure monitoring, alert routing, runbook execution, "
        "change management, and security incident response",
    ]
    for u in users:
        pdf.bullet(u)
    pdf.ln(3)

    pdf.sub_title("1.3  Deployment Model")
    pdf.body(
        "Production deployment runs on Google Cloud Platform (GCP) with "
        "Cloud Run for compute, Cloud SQL (PostgreSQL 15) for persistence, "
        "Memorystore (Redis 7) for caching and rate limiting, and "
        "GCP Secret Manager for credentials. CI/CD is handled via GitHub Actions "
        "with automated ruff/mypy/pytest gating."
    )
    pdf.body(
        "The platform supports three deployment environments: development (local Docker), "
        "staging (GCP with synthetic data), and production (GCP with real data and "
        "full monitoring). All environments use identical Docker images built from the "
        "same CI pipeline, ensuring parity across stages."
    )

    pdf.sub_title("1.4  Architecture Overview")
    pdf.body(
        "AgenticOrg follows a modular, layered architecture:"
    )
    arch_layers = [
        "Presentation Layer: React 18 SPA with role-based dashboards, company switcher, "
        "and real-time notifications via ServiceWorker",
        "API Layer: FastAPI (Python 3.12) with async handlers, JWT auth middleware, "
        "rate limiting, and OpenAPI documentation",
        "Agent Layer: LangGraph StateGraph with reason/validate/execute/evaluate nodes, "
        "Grantex scope enforcement, and HITL interrupts",
        "Connector Layer: 54 connectors with BaseConnector interface, pluggable auth "
        "adapters, secret resolution chain, and health checks",
        "Data Layer: PostgreSQL 15 with JSONB, Redis 7 for caching/rate-limits, "
        "GCS for blob storage, and WORM audit logs",
        "Infrastructure Layer: GCP Cloud Run (auto-scaling), Cloud SQL, Memorystore, "
        "Secret Manager, Cloud Monitoring, and Cloud Logging",
    ]
    for a in arch_layers:
        pdf.bullet(a)
    pdf.ln(2)

    pdf.sub_title("1.5  Design Principles")
    principles = [
        "Open Source Only: All components use open-source libraries. No proprietary "
        "SaaS dependencies, no AGPL-licensed code, no vendor lock-in.",
        "Security by Default: PII masking always on, WORM audit logs, scope enforcement "
        "on every tool call, short-lived tokens, and zero-trust architecture.",
        "India-First: Native support for GST, TDS, EPFO, RBI AA, Tally, Darwinbox, "
        "Keka, PineLabs, and other India-specific platforms.",
        "Observable: Every agent action is logged, traced, and auditable. Token usage, "
        "latency, cost, and confidence are tracked per-run.",
        "Scalable: Stateless compute on Cloud Run (0 to 1000 instances), connection "
        "pooling, async everywhere, and circuit breakers on external calls.",
    ]
    for p in principles:
        pdf.bullet(p)
    pdf.ln(2)


def sec02_auth(pdf: FuncSpecPdf) -> None:
    pdf.add_page()
    pdf.section_title("2", "Authentication & Authorization")

    pdf.body(
        "AgenticOrg supports multiple authentication methods to accommodate "
        "enterprise SSO, developer API access, and standard email/password login."
    )

    # 2.1
    pdf.sub_title("2.1  Login Flows")
    pdf.sub_sub_title("Email / Password")
    pdf.body(
        "Users register with email and password. Passwords are hashed with bcrypt "
        "(12 rounds). Password policy requires minimum 8 characters with at least "
        "one uppercase letter, one lowercase letter, and one digit. On login, "
        "the server verifies the bcrypt hash and issues a JWT."
    )
    pdf.sub_sub_title("Google OAuth")
    pdf.body(
        "Users click 'Sign in with Google' which initiates the OAuth 2.0 flow. "
        "The backend verifies the Google id_token, extracts email and name, "
        "auto-creates the user if first login, and issues a local JWT."
    )
    pdf.sub_sub_title("API Keys")
    pdf.body(
        "Developers generate API keys via the dashboard. Keys use the prefix "
        "'ao_sk_' followed by 48 random hex characters. The raw key is shown "
        "once; the backend stores only the bcrypt hash. API keys are passed "
        "via the X-API-Key header."
    )

    # 2.2
    pdf.sub_title("2.2  JWT Tokens")
    pdf.body(
        "Two JWT signing strategies are supported:"
    )
    pdf.bullet(
        "HS256 (local): Used for user-facing auth. Issuer: agenticorg.ai, "
        "Audience: agenticorg-tool-gateway, TTL: 60 minutes. Claims include "
        "sub (user ID), tenant_id, scopes (list), name, role, domain."
    )
    pdf.bullet(
        "RS256 (Grantex): Used for scope enforcement tokens. Issued by Grantex "
        "service, validated via JWKS endpoint. Contains tool-level scopes."
    )
    pdf.ln(2)

    # 2.3
    pdf.sub_title("2.3  Token Blacklist")
    pdf.body(
        "When a user logs out or a token is revoked, the token is blacklisted. "
        "Primary storage is Redis with an HMAC-SHA256 hash of the token as the key "
        "and TTL of 3700 seconds (slightly longer than token TTL). An in-memory "
        "LRU cache holds up to 10,000 entries as a fallback if Redis is unreachable."
    )

    # 2.4
    pdf.sub_title("2.4  Rate Limiting")
    pdf.body("The following rate limits are enforced at the API gateway level:")
    rate_limits = [
        "Signup: 5 requests per IP per hour",
        "Login: 5 requests per IP per minute",
        "Failed logins: After 10 consecutive failures from an IP, the IP is blocked for 15 minutes",
        "API endpoints: 60 requests per minute per user (configurable per plan)",
    ]
    for r in rate_limits:
        pdf.bullet(r)
    pdf.ln(2)

    # 2.5
    pdf.check_space(60)
    pdf.sub_title("2.5  RBAC Roles")
    pdf.body(
        "Role-Based Access Control restricts what each user can see and do. "
        "Roles are assigned at the tenant level and enforced on every API call."
    )
    cols = [("Role", 25), ("Scope", 70), ("Description", 95)]
    pdf.table_header(cols)
    roles = [
        ("admin", "All resources", "Full platform access, user management, billing"),
        ("cfo", "Finance only", "Finance dashboards, AP/AR, treasury, tax, reports"),
        ("chro", "HR only", "HR dashboards, onboarding, payroll, compliance"),
        ("cmo", "Marketing only", "Marketing dashboards, campaigns, leads, analytics"),
        ("coo", "Ops only", "Operations dashboards, incidents, DevOps, support"),
        ("auditor", "Read-only audit", "Read-only access to audit logs and compliance reports"),
    ]
    for i, (role, scope, desc) in enumerate(roles):
        pdf.table_row([(role, 25), (scope, 70), (desc, 95)], shade=i % 2 == 1)
    pdf.ln(3)

    # 2.6
    pdf.sub_title("2.6  Password Reset")
    pdf.body(
        "Flow: User submits email to POST /auth/forgot-password. System sends "
        "a password reset email with a unique token (60-minute TTL). User clicks "
        "the link and submits a new password to POST /auth/reset-password with "
        "the token. Token is single-use and invalidated after successful reset."
    )

    # 2.7
    pdf.sub_title("2.7  Team Management")
    pdf.body(
        "Admins invite team members via POST /team/invite with email and role. "
        "The invitee receives an email with an accept link containing a signed token. "
        "On acceptance, the user account is created (or linked) and assigned the "
        "specified role within the tenant. Admins can remove members via "
        "DELETE /team/{user_id}, which revokes their access and blacklists active tokens."
    )

    pdf.sub_title("2.8  Auth API Endpoints Summary")
    auth_eps = [
        ("POST", "/auth/signup", "201", "Register new user with email/password"),
        ("POST", "/auth/login", "200", "Login and receive JWT"),
        ("POST", "/auth/logout", "200", "Blacklist current token"),
        ("POST", "/auth/google", "200", "Google OAuth login/register"),
        ("POST", "/auth/forgot-password", "200", "Send password reset email"),
        ("POST", "/auth/reset-password", "200", "Reset password with token"),
        ("POST", "/auth/refresh", "200", "Refresh JWT before expiry"),
        ("GET", "/auth/me", "200", "Get current user profile"),
        ("PATCH", "/auth/me", "200", "Update current user profile"),
        ("POST", "/auth/api-keys", "201", "Generate new API key"),
        ("GET", "/auth/api-keys", "200", "List user's API keys"),
        ("DELETE", "/auth/api-keys/{id}", "200", "Revoke an API key"),
        ("POST", "/team/invite", "201", "Invite team member"),
        ("POST", "/team/accept", "200", "Accept team invitation"),
        ("DELETE", "/team/{user_id}", "200", "Remove team member"),
        ("GET", "/team", "200", "List team members"),
    ]
    cols_auth = [("Method", 18), ("Path", 52), ("Status", 15), ("Description", 105)]
    pdf.table_header(cols_auth)
    for i, (method, path, status, desc) in enumerate(auth_eps):
        pdf.table_row(
            [(method, 18), (path, 52), (status, 15), (desc, 105)],
            shade=i % 2 == 1,
        )
    pdf.ln(3)

    pdf.sub_title("2.9  Security Headers")
    pdf.body(
        "All API responses include standard security headers:"
    )
    sec_headers = [
        "X-Content-Type-Options: nosniff -- prevents MIME type sniffing",
        "X-Frame-Options: DENY -- prevents clickjacking via iframes",
        "Strict-Transport-Security: max-age=31536000 -- enforces HTTPS",
        "X-XSS-Protection: 1; mode=block -- enables browser XSS filter",
        "Content-Security-Policy: default-src 'self' -- restricts resource loading",
        "CORS: Configured per environment with explicit allowed origins",
    ]
    for h in sec_headers:
        pdf.bullet(h)
    pdf.ln(2)


def sec03_grantex(pdf: FuncSpecPdf) -> None:
    pdf.add_page()
    pdf.section_title("3", "Grantex Scope Enforcement (v3.3.0)")

    pdf.body(
        "v3.3.0 introduces Grantex SDK-based scope enforcement, replacing the "
        "legacy keyword-guessing approach that was both insecure and unreliable."
    )

    # 3.1
    pdf.sub_title("3.1  Problem Statement")
    pdf.body(
        "Prior to v3.3.0, permission checks used keyword matching on tool names. "
        "For example, 'process_refund' was incorrectly classified as a 'read' "
        "operation because the word 'process' did not match any write/delete keywords. "
        "Additionally, the LangGraph execution path had NO scope enforcement at all -- "
        "agents could call any tool regardless of their assigned permissions."
    )

    # 3.2
    pdf.sub_title("3.2  Solution: grantex.enforce()")
    pdf.body(
        "Every tool call now goes through grantex.enforce() which performs offline "
        "JWT validation against pre-built permission manifests. Each of the 53 "
        "connectors has a manifest that explicitly maps every tool to its required "
        "permission level (read, write, delete, or admin). Validation is sub-millisecond "
        "as it uses cached JWKS keys and local JWT verification."
    )

    # 3.3
    pdf.sub_title("3.3  Permission Hierarchy")
    pdf.body(
        "Permissions follow a strict hierarchy where higher levels include lower ones:"
    )
    pdf.code_block("  admin > delete > write > read")
    pdf.body(
        "An agent with 'write' permission on a connector can also perform 'read' "
        "operations. An agent with 'admin' can do everything. This reduces scope "
        "string proliferation while maintaining least-privilege principles."
    )

    # 3.4
    pdf.sub_title("3.4  Scope String Format")
    pdf.body("Scope strings follow a structured format:")
    pdf.code_block(
        "  tool:{connector}:{permission}:{resource}[:capped:{N}]\n"
        "\n"
        "  Examples:\n"
        "    tool:salesforce:read:contacts\n"
        "    tool:oracle_fusion:write:invoices\n"
        "    tool:stripe:delete:subscriptions\n"
        "    tool:banking_aa:write:transfers:capped:500000"
    )
    pdf.body(
        "The optional :capped:{N} suffix enforces monetary limits on write operations. "
        "For example, a transfer agent capped at 500000 cannot initiate transfers "
        "exceeding that amount in a single call."
    )

    # 3.5
    pdf.check_space(50)
    pdf.sub_title("3.5  validate_scopes Graph Node")
    pdf.body(
        "In the LangGraph agent runtime, a new validate_scopes node is inserted "
        "between the reason and execute_tools nodes. The graph flow is:"
    )
    pdf.code_block(
        "  reason -> validate_scopes -> execute_tools -> reason (loop)\n"
        "                 |\n"
        "           (denied) -> END with error"
    )
    pdf.body(
        "When the LLM decides to call tools, the validate_scopes node extracts "
        "all requested tool names, looks up their required scopes from manifests, "
        "and calls grantex.enforce() with the agent's grant_token. If any tool "
        "is denied, the entire batch is rejected (atomic denial)."
    )

    # 3.6
    pdf.sub_title("3.6  ToolGateway Enforcement")
    pdf.body(
        "The ToolGateway.execute() method also enforces scopes as a second layer "
        "of defense. It accepts an optional grant_token parameter. Token resolution "
        "order: (1) explicit grant_token param, (2) agent config's grant_token field, "
        "(3) legacy fallback (no enforcement, logged as warning). When a grant_token "
        "is present, grantex.enforce() is called before connector execution."
    )

    # 3.7
    pdf.sub_title("3.7  Manifest Loading")
    pdf.body(
        "At startup, the system loads 53 pre-built manifests from "
        "grantex.manifests.* Python modules. Each manifest maps tool names to "
        "required permission levels. Custom manifests can be loaded from the "
        "directory specified by GRANTEX_MANIFESTS_DIR environment variable. "
        "Manifests are cached in memory for the lifetime of the process."
    )

    # 3.8
    pdf.sub_title("3.8  JWKS Warm-up")
    pdf.body(
        "During FastAPI startup (lifespan handler), a dummy grantex.enforce() "
        "call is made to pre-warm the JWKS cache. This incurs a one-time cost "
        "of approximately 300ms but ensures that the first real enforce() call "
        "does not suffer cold-start latency."
    )

    # 3.9
    pdf.sub_title("3.9  Edge Cases")
    edge_cases = [
        "Empty token: If grant_token is empty or None, enforcement is skipped (no-op). "
        "This maintains backward compatibility with pre-v3.3.0 agents.",
        "Unknown tool: If a tool name is not found in any manifest, the call is denied "
        "by default. This prevents unregistered tools from bypassing enforcement.",
        "Batch denial: If a single tool call in a batch is denied, ALL tool calls "
        "in that batch are rejected. This prevents partial execution.",
        "Offline revocation: Tokens are short-lived (5 minutes). Revocation is "
        "achieved by simply not renewing the token. No real-time revocation needed.",
    ]
    for e in edge_cases:
        pdf.bullet(e)
    pdf.ln(2)

    pdf.sub_title("3.10  Grantex API Endpoints")
    grantex_eps = [
        ("GET", "/grantex/manifests", "200", "List all loaded manifests"),
        ("GET", "/grantex/manifests/{connector}", "200", "Get manifest for a connector"),
        ("POST", "/grantex/check", "200", "Test enforce a scope (dry-run)"),
        ("GET", "/grantex/stats", "200", "Enforcement statistics (today)"),
        ("GET", "/grantex/audit", "200", "Enforce audit log (paginated)"),
        ("GET", "/grantex/audit/export", "200", "Export audit log as CSV"),
    ]
    cols_gx = [("Method", 18), ("Path", 62), ("Status", 15), ("Description", 95)]
    pdf.table_header(cols_gx)
    for i, (method, path, status, desc) in enumerate(grantex_eps):
        pdf.table_row(
            [(method, 18), (path, 62), (status, 15), (desc, 95)],
            shade=i % 2 == 1,
        )
    pdf.ln(3)

    pdf.sub_title("3.11  Scope Enforcement Flow Diagram")
    pdf.body(
        "The complete enforcement flow for a single tool call:"
    )
    flow_steps = [
        "1. Agent LLM emits tool_call (e.g., salesforce.create_contact)",
        "2. validate_scopes node extracts tool_name from each ToolCallMessage",
        "3. Manifest lookup: find salesforce manifest, look up create_contact -> 'write'",
        "4. Token extraction: get grant_token from AgentState",
        "5. grantex.enforce(token=grant_token, scope='tool:salesforce:write:contacts')",
        "6. JWKS validation: verify token signature against cached JWKS public keys",
        "7. Claim check: verify token scopes include 'tool:salesforce:write:contacts' "
        "(or higher: delete, admin)",
        "8. Result: ALLOW -> proceed to execute_tools | DENY -> return error to agent",
        "9. Audit: write enforcement decision to enforce_audit_log table",
    ]
    for s in flow_steps:
        pdf.bullet(s)
    pdf.ln(2)

    pdf.sub_title("3.12  Performance Characteristics")
    pdf.body(
        "Grantex enforcement has been optimized for minimal latency:"
    )
    perf_chars = [
        "Manifest lookup: O(1) hash map lookup, <0.01ms per tool",
        "JWT validation: Offline verification against cached JWKS, <0.5ms",
        "JWKS refresh: Background refresh every 5 minutes (non-blocking)",
        "Total enforce() latency: <1ms per call (p99)",
        "JWKS warm-up at startup: ~300ms one-time cost",
        "Memory footprint: ~2MB for 53 manifests + JWKS cache",
    ]
    for p in perf_chars:
        pdf.bullet(p)
    pdf.ln(2)


def sec04_agents(pdf: FuncSpecPdf) -> None:
    pdf.add_page()
    pdf.section_title("4", "Agent Management")

    pdf.body(
        "Agents are the core entities in AgenticOrg. Each agent is a specialized "
        "AI virtual employee with a defined domain, tool permissions, cost controls, "
        "and quality gates."
    )

    # 4.1
    pdf.sub_title("4.1  Agent Model Fields")
    pdf.body("The complete agent data model with all fields, types, and defaults:")

    fields = [
        ("id", "UUID", "Primary key, auto-generated"),
        ("tenant_id", "UUID (FK)", "Owning tenant, NOT NULL"),
        ("name", "VARCHAR(200)", "Display name, NOT NULL"),
        ("agent_type", "VARCHAR(100)", "e.g., cfo_agent, hr_agent"),
        ("domain", "VARCHAR(50)", "finance, hr, marketing, ops, sales"),
        ("status", "VARCHAR(20)", "shadow/beta/active/paused/retired"),
        ("system_prompt_text", "TEXT", "LLM system prompt"),
        ("llm_model", "VARCHAR(100)", "e.g., gemini-2.0-flash"),
        ("confidence_floor", "NUMERIC(4,3)", "Default 0.880"),
        ("max_retries", "INTEGER", "Default 3"),
        ("authorized_tools", "JSONB", "Array of tool scope strings"),
        ("parent_agent_id", "UUID (FK)", "For org hierarchy"),
        ("employee_name", "VARCHAR(200)", "Virtual employee name"),
        ("avatar_url", "VARCHAR(500)", "Profile image URL"),
        ("designation", "VARCHAR(200)", "e.g., CFO Assistant"),
        ("specialization", "VARCHAR(200)", "e.g., Tax Compliance"),
        ("org_level", "INTEGER", "Default 0 (top)"),
        ("shadow_min_samples", "INTEGER", "Default 10"),
        ("shadow_accuracy_floor", "NUMERIC(4,3)", "Default 0.950"),
        ("shadow_accuracy_current", "NUMERIC(4,3)", "Current accuracy"),
        ("shadow_sample_count", "INTEGER", "Default 0"),
        ("cost_controls", "JSONB", "{monthly_cap_usd, ...}"),
        ("routing_filter", "JSONB", "Agent selection criteria"),
        ("is_builtin", "BOOLEAN", "Default false"),
        ("tags", "VARCHAR[]", "String array for filtering"),
        ("created_at", "TIMESTAMPTZ", "Auto-set on create"),
        ("updated_at", "TIMESTAMPTZ", "Auto-set on update"),
    ]

    cols = [("Field", 42), ("Type", 38), ("Description", 110)]
    pdf.table_header(cols)
    for i, (field, ftype, desc) in enumerate(fields):
        pdf.table_row([(field, 42), (ftype, 38), (desc, 110)],
                      shade=i % 2 == 1)
    pdf.ln(3)

    # 4.2
    pdf.check_space(50)
    pdf.sub_title("4.2  API Endpoints")
    pdf.body("Complete list of agent management endpoints:")

    endpoints = [
        ("POST", "/agents", "201", "Create agent (starts in shadow mode)"),
        ("GET", "/agents", "200", "List agents (paginated, RBAC filtered)"),
        ("GET", "/agents/{id}", "200", "Get single agent details"),
        ("PUT", "/agents/{id}", "200", "Full update of agent config"),
        ("PATCH", "/agents/{id}", "200", "Partial update of agent fields"),
        ("POST", "/agents/{id}/run", "200", "Execute agent with task input"),
        ("POST", "/agents/{id}/pause", "200", "Pause active agent"),
        ("POST", "/agents/{id}/resume", "200", "Resume paused agent"),
        ("POST", "/agents/{id}/promote", "200", "Promote shadow -> active"),
        ("POST", "/agents/{id}/retire", "200", "Retire agent permanently"),
        ("POST", "/agents/{id}/retest", "200", "Reset shadow counters"),
        ("POST", "/agents/{id}/rollback", "200", "Rollback to prior config"),
        ("POST", "/agents/{id}/clone", "201", "Clone agent with overrides"),
        ("GET", "/agents/{id}/prompt-history", "200", "Prompt version history"),
        ("GET", "/agents/{id}/budget", "200", "Budget usage and remaining"),
        ("POST", "/agents/{id}/delegate", "200", "Delegate task to child"),
        ("POST", "/agents/import-csv", "201", "Bulk import from CSV"),
        ("GET", "/agents/org-tree", "200", "Hierarchical org chart data"),
    ]

    cols_ep = [("Method", 18), ("Path", 55), ("Status", 15), ("Description", 102)]
    pdf.table_header(cols_ep)
    for i, (method, path, status, desc) in enumerate(endpoints):
        pdf.table_row(
            [(method, 18), (path, 55), (status, 15), (desc, 102)],
            shade=i % 2 == 1,
        )
    pdf.ln(3)

    # 4.3
    pdf.check_space(50)
    pdf.sub_title("4.3  Agent Execution")
    pdf.body(
        "The run_agent() function is the main entry point for agent execution. "
        "It takes an agent configuration object and a task_input string, then:"
    )
    steps = [
        "Builds a LangGraph StateGraph from the agent config",
        "Injects the system prompt, authorized tools, and grant_token",
        "Invokes the graph with the task input as initial message",
        "Returns a structured result with: status, output, confidence, "
        "reasoning_trace, tool_calls_log, hitl_trigger, error, performance",
    ]
    for s in steps:
        pdf.bullet(s)
    pdf.ln(2)

    pdf.body(
        "The performance object includes token_usage (prompt_tokens, "
        "completion_tokens, total_tokens), cost_usd, and latency_ms. "
        "This is tracked per-run and aggregated in the agent_cost_ledger table."
    )

    # 4.4
    pdf.sub_title("4.4  Shadow Mode & Quality Gates")
    pdf.body(
        "All new agents start in 'shadow' status. In shadow mode, the agent "
        "processes requests but its outputs are compared against human decisions "
        "rather than being acted upon. Two quality gates must pass before promotion:"
    )
    pdf.bullet(
        "Minimum samples: shadow_sample_count >= shadow_min_samples (default 10)"
    )
    pdf.bullet(
        "Accuracy floor: shadow_accuracy_current >= shadow_accuracy_floor (default 0.950)"
    )
    pdf.body(
        "The POST /agents/{id}/promote endpoint checks both gates. If either "
        "fails, the promotion is rejected with a 400 error and a message "
        "indicating which gate failed. The POST /agents/{id}/retest endpoint "
        "resets shadow_sample_count and shadow_accuracy_current to zero, "
        "allowing the agent to be re-evaluated."
    )

    # 4.5
    pdf.check_space(40)
    pdf.sub_title("4.5  Clone with Scope Ceiling")
    pdf.body(
        "POST /agents/{id}/clone creates a copy of an agent with optional "
        "overrides (name, system_prompt, tools, etc.). A critical security "
        "constraint is the scope ceiling: a cloned (child) agent cannot have "
        "broader permissions than its parent. If the clone request includes "
        "tools not in the parent's authorized_tools, the request is rejected."
    )

    # 4.6
    pdf.sub_title("4.6  Cost Controls")
    pdf.body(
        "Each agent can have cost_controls defined as a JSONB object containing "
        "monthly_cap_usd. Every tool call and LLM invocation is tracked in the "
        "agent_cost_ledger table with columns: agent_id, month, total_cost_usd, "
        "llm_cost_usd, tool_cost_usd. When an agent's monthly spend reaches "
        "its cap, the agent is automatically paused and an alert is sent."
    )

    # 4.7
    pdf.check_space(50)
    pdf.sub_title("4.7  Pre-built Agents (50+)")
    pdf.body("AgenticOrg ships with 50+ pre-built agents across 7 domains:")

    pdf.sub_sub_title("Finance (10 agents)")
    fin_agents = [
        "CFO Agent -- Cash flow, runway forecasting, daily treasury briefing",
        "AP Agent -- Accounts payable processing, vendor payments, aging",
        "AR Agent -- Accounts receivable, collections, customer aging",
        "Tax Agent -- GST filing, TDS reconciliation, tax calendar",
        "Audit Agent -- Compliance checks, evidence packaging, SOX",
        "Treasury Agent -- Bank balance monitoring, fund transfers",
        "Payroll Agent -- Salary processing, deductions, payslips",
        "Expense Agent -- Expense report processing, policy enforcement",
        "Revenue Agent -- Revenue recognition, MRR/ARR tracking",
        "FP&A Agent -- Financial planning, budget vs actuals",
    ]
    for a in fin_agents:
        pdf.bullet_small(a)

    pdf.sub_sub_title("HR (6 agents)")
    hr_agents = [
        "CHRO Agent -- Workforce analytics, attrition prediction, org health",
        "Recruiter Agent -- Resume screening, interview scheduling, offers",
        "Onboarding Agent -- Day-1 setup, document collection, IT provisioning",
        "Benefits Agent -- Benefits enrollment, queries, compliance",
        "L&D Agent -- Training recommendations, skill gap analysis",
        "Compliance Agent -- Policy adherence, POSH, labor law checks",
    ]
    for a in hr_agents:
        pdf.bullet_small(a)

    pdf.sub_sub_title("Marketing (9 agents)")
    mkt_agents = [
        "CMO Agent -- Dashboard briefings, campaign ROI, brand health",
        "Content Agent -- Blog generation, SEO optimization, publishing",
        "Social Agent -- Social media scheduling, engagement tracking",
        "Email Agent -- Email campaigns, drip sequences, A/B testing",
        "SEO Agent -- Keyword research, backlink analysis, rank tracking",
        "PPC Agent -- Google/Meta/LinkedIn ad management, bid optimization",
        "ABM Agent -- Account-based marketing, intent scoring, targeting",
        "Analytics Agent -- GA4, Mixpanel, attribution modeling",
        "Lead Nurture Agent -- Lead scoring, nurture sequences, MQL->SQL",
    ]
    for a in mkt_agents:
        pdf.bullet_small(a)

    pdf.check_space(40)
    pdf.sub_sub_title("Operations (5 agents)")
    ops_agents = [
        "Incident Agent -- PagerDuty alerts, runbook execution, RCA",
        "DevOps Agent -- CI/CD monitoring, deployment health, infra alerts",
        "Support Agent -- Ticket triage, auto-response, escalation",
        "Vendor Agent -- Vendor evaluation, contract management, SLA tracking",
        "Facilities Agent -- Office management, asset tracking, maintenance",
    ]
    for a in ops_agents:
        pdf.bullet_small(a)

    pdf.sub_sub_title("BackOffice (3 agents)")
    bo_agents = [
        "Legal Agent -- Contract review, clause extraction, compliance",
        "Procurement Agent -- Purchase orders, RFQ management, approvals",
        "Admin Agent -- Meeting scheduling, travel booking, expense filing",
    ]
    for a in bo_agents:
        pdf.bullet_small(a)

    pdf.sub_sub_title("Communications (2 agents)")
    comms_agents = [
        "Internal Comms Agent -- Slack updates, team newsletters, announcements",
        "External Comms Agent -- Press releases, investor updates, PR tracking",
    ]
    for a in comms_agents:
        pdf.bullet_small(a)

    pdf.sub_sub_title("Sales (1 agent)")
    pdf.bullet_small(
        "Sales Agent -- Pipeline management, lead scoring, follow-up scheduling, CRM sync"
    )
    pdf.ln(2)

    pdf.check_space(50)
    pdf.sub_title("4.8  Agent Lifecycle State Machine")
    pdf.body(
        "Agents transition through a well-defined lifecycle:"
    )
    pdf.code_block(
        "  [CREATE] -> shadow -> [PROMOTE] -> active -> [PAUSE] -> paused\n"
        "                |                        |                  |\n"
        "                |                        |          [RESUME] -> active\n"
        "                |                        |\n"
        "           [RETEST] -> shadow      [RETIRE] -> retired\n"
        "                                         |\n"
        "                                    [ROLLBACK] -> active (prev config)"
    )
    pdf.body(
        "Key constraints: (1) Only shadow agents can be promoted. (2) Only active "
        "agents can be paused or retired. (3) Only paused agents can be resumed. "
        "(4) Rollback restores the previous configuration snapshot. (5) Retired "
        "agents cannot be reactivated -- clone instead."
    )

    pdf.sub_title("4.9  Delegation Model")
    pdf.body(
        "Agents can delegate tasks to child agents in the org hierarchy via "
        "POST /agents/{id}/delegate. The parent agent specifies the task input "
        "and optionally a subset of its own tools that the child may use. "
        "Delegation creates a new agent run linked to both the parent and child. "
        "Results flow back up the hierarchy, and if the child triggers HITL, "
        "the escalation follows the org chart."
    )

    pdf.sub_title("4.10  CSV Import Format")
    pdf.body(
        "POST /agents/import-csv accepts a CSV file for bulk agent creation. "
        "Required columns: name, agent_type, domain, system_prompt_text. "
        "Optional columns: llm_model, confidence_floor, max_retries, "
        "employee_name, designation, specialization, tags (pipe-separated). "
        "All imported agents start in shadow status regardless of any "
        "status column in the CSV."
    )


def sec05_langgraph(pdf: FuncSpecPdf) -> None:
    pdf.add_page()
    pdf.section_title("5", "LangGraph Agent Runtime")

    pdf.body(
        "Every agent in AgenticOrg executes within a LangGraph StateGraph. "
        "This provides a structured, observable, and interruptible execution model "
        "with built-in support for tool calling, confidence evaluation, and HITL."
    )

    # 5.1
    pdf.sub_title("5.1  AgentState Fields")
    pdf.body("The shared state object passed between graph nodes:")

    state_fields = [
        ("messages", "list[BaseMessage]", "Conversation history (system + user + AI + tool)"),
        ("agent_id", "str", "UUID of the executing agent"),
        ("agent_type", "str", "Agent type (e.g., cfo_agent)"),
        ("domain", "str", "Business domain (finance, hr, etc.)"),
        ("tenant_id", "str", "Tenant UUID for data isolation"),
        ("grant_token", "str", "Grantex JWT for scope enforcement"),
        ("confidence", "float", "Current confidence score (0.0 - 1.0)"),
        ("status", "str", "Execution status (running, completed, etc.)"),
        ("output", "str", "Final agent output text"),
        ("reasoning_trace", "list[str]", "Step-by-step reasoning log"),
        ("tool_calls_log", "list[dict]", "Record of all tool invocations"),
        ("hitl_trigger", "dict|None", "HITL trigger details if activated"),
        ("error", "str|None", "Error message if execution failed"),
    ]

    cols = [("Field", 35), ("Type", 45), ("Description", 110)]
    pdf.table_header(cols)
    for i, (field, ftype, desc) in enumerate(state_fields):
        pdf.table_row([(field, 35), (ftype, 45), (desc, 110)],
                      shade=i % 2 == 1)
    pdf.ln(3)

    # 5.2
    pdf.sub_title("5.2  Graph Nodes")
    nodes = [
        "reason: Calls the LLM with current messages. May produce text output or tool_calls.",
        "validate_scopes: Checks all requested tool calls against Grantex manifests. "
        "Denies the batch if any tool is unauthorized.",
        "execute_tools: LangGraph ToolNode that actually invokes the tools via ToolGateway.",
        "evaluate: Extracts confidence score from the LLM output. Caps to 0.5 on errors.",
        "hitl_gate: Raises GraphInterrupt if confidence < floor or custom condition matches.",
    ]
    for n in nodes:
        pdf.bullet(n)
    pdf.ln(2)

    # 5.3
    pdf.sub_title("5.3  Graph Flow")
    pdf.body("The complete execution flow through the graph:")
    pdf.code_block(
        "  START\n"
        "    |\n"
        "    v\n"
        "  reason (LLM call)\n"
        "    |\n"
        "    +-- [has tool_calls?]\n"
        "    |     YES -> validate_scopes\n"
        "    |              |\n"
        "    |              +-- [scopes OK?]\n"
        "    |              |     YES -> execute_tools -> reason (loop)\n"
        "    |              |     NO  -> END (scope_denied error)\n"
        "    |     NO  -> evaluate\n"
        "    |              |\n"
        "    |              +-- [HITL triggered?]\n"
        "    |              |     YES -> hitl_gate -> END (hitl_triggered)\n"
        "    |              |     NO  -> END (completed)\n"
        "    v\n"
        "  END"
    )

    # 5.4
    pdf.sub_title("5.4  Confidence Scoring")
    pdf.body(
        "After the LLM produces its final response, the evaluate node extracts "
        "a confidence score. The score is determined by:"
    )
    pdf.bullet(
        "JSON output: If the LLM returns a JSON object with a 'confidence' field, "
        "that numeric value (0.0-1.0) is used directly."
    )
    pdf.bullet(
        "String mappings: If the confidence field is a string, it is mapped: "
        "'high' = 0.95, 'medium' = 0.75, 'low' = 0.5."
    )
    pdf.bullet(
        "Error capping: If any tool call returned an error or the output is "
        "incomplete, confidence is capped at 0.5 regardless of LLM claim."
    )
    pdf.ln(2)

    # 5.5
    pdf.sub_title("5.5  HITL Triggers")
    pdf.body("Human-in-the-Loop is triggered under two conditions:")
    pdf.bullet(
        "Confidence below floor: If the extracted confidence < agent's "
        "confidence_floor (default 0.880), HITL is triggered."
    )
    pdf.bullet(
        "Custom condition: Agents can define a condition expression in their config, "
        "e.g., 'amount > 500000'. This is evaluated against the agent's output context. "
        "If true, HITL is triggered regardless of confidence."
    )
    pdf.ln(2)

    # 5.6
    pdf.sub_title("5.6  Performance Tracking")
    pdf.body(
        "Token usage is extracted from LLM responses for cost calculation. "
        "Supported providers and their cost rates:"
    )
    perf = [
        "Google Gemini Flash: $0.000375 per 1K tokens (prompt + completion)",
        "Anthropic Claude: $0.003 per 1K input tokens, $0.015 per 1K output tokens",
        "OpenAI GPT-4o: $0.005 per 1K input tokens, $0.015 per 1K output tokens",
    ]
    for p in perf:
        pdf.bullet(p)
    pdf.body(
        "Performance metrics (token counts, cost_usd, latency_ms) are returned "
        "in the agent run response and persisted to the agent_cost_ledger table."
    )

    pdf.sub_title("5.7  Memory & Context Management")
    pdf.body(
        "Agent memory is managed through the LangGraph state and conversation "
        "history. Key behaviors:"
    )
    memory_features = [
        "Short-term: Current conversation messages are maintained in the AgentState.messages "
        "list. This includes system prompt, user input, AI responses, and tool results.",
        "Tool context: Tool call results are appended as ToolMessages and are visible to "
        "the LLM in subsequent reasoning steps within the same run.",
        "Cross-run: Each run is independent by default. Long-term memory can be achieved "
        "by persisting key outputs to the workflow context or a dedicated memory store.",
        "Token window: Messages are trimmed to fit within the LLM's context window. "
        "Oldest messages are dropped first, preserving system prompt and recent context.",
        "Reasoning trace: Every intermediate reasoning step is appended to the "
        "reasoning_trace list, providing full observability of the agent's thought process.",
    ]
    for m in memory_features:
        pdf.bullet(m)
    pdf.ln(2)

    pdf.sub_title("5.8  Error Recovery in Graph")
    pdf.body(
        "The LangGraph runtime handles errors gracefully at each node:"
    )
    err_recovery = [
        "LLM timeout: If the LLM does not respond within 30 seconds, the reason node "
        "retries up to max_retries times. On final failure, status is set to 'error'.",
        "Tool failure: If a tool call fails, the error is added as a ToolMessage and "
        "the graph loops back to the reason node, letting the LLM decide next steps.",
        "Scope denial: If validate_scopes denies a tool call, the batch is rejected and "
        "the error is returned immediately (no retry, as it is a permission issue).",
        "HITL interrupt: GraphInterrupt is caught by the runner and the state is persisted. "
        "This is not an error -- it is a controlled pause awaiting human input.",
        "Unhandled exception: Any unexpected error is caught at the top level, logged with "
        "full stack trace, and returned as status='error' with the exception message.",
    ]
    for e in err_recovery:
        pdf.bullet(e)
    pdf.ln(2)


def sec06_connectors(pdf: FuncSpecPdf) -> None:
    pdf.add_page()
    pdf.section_title("6", "Connector Framework")

    pdf.body(
        "Connectors are the bridge between AI agents and external systems. "
        "AgenticOrg ships with 54 production connectors covering finance, HR, "
        "marketing, operations, and communications platforms."
    )

    # 6.1
    pdf.sub_title("6.1  BaseConnector Interface")
    pdf.body(
        "All connectors inherit from BaseConnector, which defines the standard "
        "interface and configuration options:"
    )
    base_fields = [
        ("name", "str", "Unique connector identifier (e.g., 'salesforce')"),
        ("category", "str", "Domain category (finance, hr, marketing, ops, comms)"),
        ("auth_type", "str", "One of: oauth, apikey, basic, jwt, custom"),
        ("base_url", "str", "API base URL for the external service"),
        ("rate_limit_rpm", "int", "Requests per minute limit (default 60)"),
        ("timeout_ms", "int", "Request timeout in milliseconds (default 10000)"),
    ]
    cols = [("Field", 35), ("Type", 20), ("Description", 135)]
    pdf.table_header(cols)
    for i, (field, ftype, desc) in enumerate(base_fields):
        pdf.table_row([(field, 35), (ftype, 20), (desc, 135)],
                      shade=i % 2 == 1)
    pdf.ln(3)

    # 6.2
    pdf.sub_title("6.2  Required Methods")
    methods = [
        "_register_tools(): Define all tools this connector exposes (name, description, schema, handler)",
        "connect(): Establish connection to the external service (OAuth flow, API key validation, etc.)",
        "disconnect(): Clean up connections, revoke temporary tokens",
        "health_check(): Verify the connector can reach its external service, return status",
        "execute_tool(tool_name, params, context): Execute a specific tool with given parameters",
        "_get_secret(key): Retrieve a secret via the resolution chain (config -> env -> GCP Secret Manager)",
    ]
    for m in methods:
        pdf.bullet(m)
    pdf.ln(2)

    # 6.3
    pdf.sub_title("6.3  Secret Resolution")
    pdf.body(
        "Connector secrets (API keys, OAuth tokens, etc.) are resolved through "
        "a three-tier chain:"
    )
    pdf.bullet("1. Direct config: Secret passed directly in connector configuration")
    pdf.bullet("2. Environment variable: Looked up from os.environ")
    pdf.bullet(
        "3. GCP Secret Manager: For production, secrets stored in GCP. "
        "Referenced as gcp://projects/{project}/secrets/{name}/versions/{version}"
    )
    pdf.ln(2)

    # 6.4
    pdf.sub_title("6.4  Auth Adapters")
    pdf.body("Connectors use pluggable auth adapters for different auth mechanisms:")
    adapters = [
        "OAuth2Adapter: Handles authorization code flow, token refresh, PKCE",
        "APIKeyAdapter: Injects API key via header (X-API-Key) or query parameter",
        "JWTAdapter: Signs requests with a JWT bearer token",
        "BasicAdapter: HTTP Basic auth with base64 encoded credentials",
        "CustomHeaderAdapter: Arbitrary headers for non-standard auth (e.g., Tally XML/TDL)",
    ]
    for a in adapters:
        pdf.bullet(a)
    pdf.ln(2)

    # 6.5
    pdf.check_space(50)
    pdf.sub_title("6.5  All 54 Connectors by Domain")

    pdf.sub_sub_title("Finance (11 connectors)")
    fin_connectors = [
        ("Oracle Fusion", 12, "ERP: GL, AP, AR, FA, journals, balances"),
        ("SAP", 15, "S/4HANA: FI, CO, MM, SD, procurement, payments"),
        ("Tally", 8, "XML/TDL + Bridge: vouchers, ledgers, stock, GST"),
        ("GSTN", 10, "Adaequare auth + DSC: GSTR-1, GSTR-3B, e-invoice, e-waybill"),
        ("QuickBooks", 9, "Invoices, payments, expenses, reports, customers"),
        ("Zoho Books", 8, "Bills, contacts, chart of accounts, bank reconciliation"),
        ("Banking AA", 6, "RBI Account Aggregator: consent, FI fetch, statements"),
        ("Income Tax India", 5, "ITR filing, Form 26AS, TDS certificates, refund status"),
        ("Stripe", 10, "Charges, subscriptions, refunds, payouts, disputes"),
        ("PineLabs", 5, "POS transactions, settlements, reconciliation"),
        ("NetSuite", 14, "SuiteQL: customers, invoices, GL, inventory, projects"),
    ]
    cols_c = [("Connector", 32), ("Tools", 12), ("Capabilities", 146)]
    pdf.table_header(cols_c)
    for i, (name, tools, caps) in enumerate(fin_connectors):
        pdf.table_row([(name, 32), (str(tools), 12), (caps, 146)],
                      shade=i % 2 == 1)
    pdf.ln(2)

    pdf.check_space(40)
    pdf.sub_sub_title("HR (8 connectors)")
    hr_connectors = [
        ("Darwinbox", 10, "Employee records, attendance, leave, payroll, org chart"),
        ("Okta", 8, "SSO, user provisioning, group management, MFA policies"),
        ("Greenhouse", 7, "Job postings, candidates, interviews, scorecards, offers"),
        ("LinkedIn Talent", 6, "Talent search, InMail, pipeline, analytics"),
        ("DocuSign", 5, "Envelope creation, signing, templates, status tracking"),
        ("Keka", 8, "Attendance, payroll, leave, employee self-service"),
        ("Zoom", 6, "Meetings, recordings, participants, webinars"),
        ("EPFO", 4, "PF balance, passbook, claims, UAN management"),
    ]
    cols_c2 = [("Connector", 32), ("Tools", 12), ("Capabilities", 146)]
    pdf.table_header(cols_c2)
    for i, (name, tools, caps) in enumerate(hr_connectors):
        pdf.table_row([(name, 32), (str(tools), 12), (caps, 146)],
                      shade=i % 2 == 1)
    pdf.ln(2)

    pdf.check_space(50)
    pdf.sub_sub_title("Marketing (19 connectors)")
    mkt_connectors = [
        ("HubSpot", 14, "CRM, deals, contacts, email, workflows, forms"),
        ("Salesforce", 16, "Leads, opportunities, accounts, campaigns, reports"),
        ("Google Ads", 8, "Campaigns, ad groups, keywords, conversions, bidding"),
        ("Meta Ads", 8, "Campaigns, ad sets, creatives, audiences, pixels"),
        ("LinkedIn Ads", 6, "Campaigns, creatives, audiences, conversions"),
        ("Ahrefs", 5, "Backlinks, keywords, site audit, rank tracking"),
        ("GA4", 7, "Sessions, users, events, conversions, real-time"),
        ("Mixpanel", 6, "Events, funnels, cohorts, retention, user profiles"),
        ("Mailchimp", 8, "Lists, campaigns, automations, analytics, segments"),
        ("MoEngage", 7, "Push, email, SMS, in-app, analytics, segments"),
        ("Buffer", 5, "Post scheduling, analytics, team collaboration"),
        ("Brandwatch", 4, "Social listening, sentiment, trends, alerts"),
        ("WordPress", 6, "Posts, pages, media, categories, SEO meta"),
        ("Twitter/X", 5, "Tweets, analytics, followers, DMs, lists"),
        ("YouTube", 5, "Videos, analytics, playlists, comments, channels"),
        ("Bombora", 3, "Intent data, topic surge, company scores"),
        ("G2", 3, "Reviews, ratings, buyer intent, competitor data"),
        ("TrustRadius", 3, "Reviews, ratings, buyer intent signals"),
        ("SendGrid", 6, "Email send, templates, analytics, webhooks, lists"),
    ]
    cols_c3 = [("Connector", 32), ("Tools", 12), ("Capabilities", 146)]
    pdf.table_header(cols_c3)
    for i, (name, tools, caps) in enumerate(mkt_connectors):
        pdf.table_row([(name, 32), (str(tools), 12), (caps, 146)],
                      shade=i % 2 == 1)
    pdf.ln(2)

    pdf.check_space(40)
    pdf.sub_sub_title("Operations (7 connectors)")
    ops_connectors = [
        ("Jira", 10, "Issues, projects, sprints, boards, JQL queries"),
        ("ServiceNow", 9, "Incidents, changes, problems, CMDB, knowledge"),
        ("Zendesk", 8, "Tickets, users, organizations, macros, SLAs"),
        ("PagerDuty", 6, "Incidents, services, escalation, on-call, analytics"),
        ("Confluence", 5, "Pages, spaces, search, comments, attachments"),
        ("Sanctions API", 3, "OFAC, UN, EU sanctions screening, watchlists"),
        ("MCA Portal", 4, "Company filings, director info, charge status"),
    ]
    cols_c4 = [("Connector", 32), ("Tools", 12), ("Capabilities", 146)]
    pdf.table_header(cols_c4)
    for i, (name, tools, caps) in enumerate(ops_connectors):
        pdf.table_row([(name, 32), (str(tools), 12), (caps, 146)],
                      shade=i % 2 == 1)
    pdf.ln(2)

    pdf.check_space(40)
    pdf.sub_sub_title("Communications (9 connectors)")
    comms_connectors = [
        ("Slack", 8, "Messages, channels, reactions, files, threads"),
        ("GitHub", 10, "Repos, issues, PRs, actions, commits, webhooks"),
        ("Gmail", 6, "Send, read, labels, threads, attachments"),
        ("Google Calendar", 5, "Events, calendars, availability, reminders"),
        ("Twilio", 5, "SMS, voice calls, verify, messaging services"),
        ("WhatsApp", 4, "Messages, templates, media, business profile"),
        ("LangSmith", 3, "Tracing, datasets, evaluations (note: open-source alt)"),
        ("S3", 5, "Upload, download, list, presigned URLs, metadata"),
        ("SendGrid", 6, "Email delivery, webhooks, analytics, templates"),
    ]
    cols_c5 = [("Connector", 32), ("Tools", 12), ("Capabilities", 146)]
    pdf.table_header(cols_c5)
    for i, (name, tools, caps) in enumerate(comms_connectors):
        pdf.table_row([(name, 32), (str(tools), 12), (caps, 146)],
                      shade=i % 2 == 1)
    pdf.ln(2)

    pdf.check_space(40)
    pdf.sub_title("6.6  Connector Health Check")
    pdf.body(
        "Every connector implements a health_check() method that verifies "
        "connectivity to the external service. Health checks are called:"
    )
    hc_details = [
        "At startup: All connectors are health-checked during FastAPI startup",
        "Periodically: Every 60 seconds via a background task",
        "On demand: GET /health/connectors returns current status of all connectors",
        "After failure: Connector is re-checked every 15 seconds until recovery",
    ]
    for h in hc_details:
        pdf.bullet(h)
    pdf.body(
        "Health status values: 'healthy' (all good), 'degraded' (slow but working), "
        "'unhealthy' (cannot connect). Unhealthy connectors trigger PagerDuty alerts "
        "and circuit breakers prevent tool calls from queueing behind failed services."
    )

    pdf.sub_title("6.7  Tool Registration Pattern")
    pdf.body(
        "Each connector registers its tools in the _register_tools() method. "
        "A tool definition includes:"
    )
    tool_def_fields = [
        "name: Fully qualified name (e.g., 'salesforce.create_contact')",
        "description: Human-readable description for LLM tool selection",
        "parameters: JSON Schema defining input parameters with types and validation",
        "required: List of required parameter names",
        "handler: Async function that executes the tool logic",
        "permission: Required Grantex permission level (read/write/delete/admin)",
        "category: Functional category for UI grouping (e.g., 'contacts', 'invoices')",
    ]
    for t in tool_def_fields:
        pdf.bullet(t)
    pdf.ln(2)


def sec07_tool_gateway(pdf: FuncSpecPdf) -> None:
    pdf.add_page()
    pdf.section_title("7", "Tool Gateway")

    pdf.body(
        "The Tool Gateway is the central execution layer that mediates all "
        "tool calls between agents and connectors. Every tool invocation passes "
        "through a standardized pipeline ensuring security, observability, "
        "and reliability."
    )

    # 7.1
    pdf.sub_title("7.1  Execution Pipeline")
    pdf.body("Each tool call passes through the following stages in order:")
    pipeline = [
        "1. Scope Check: Validate the agent's grant_token has permission for this tool (Grantex)",
        "2. Rate Limit: Check connector-level and tenant-level rate limits (Redis counters)",
        "3. Idempotency Check: Look up request hash in idempotency store, return cached if found",
        "4. Connector Resolve: Map tool name to connector instance and handler function",
        "5. PII Mask: Scan input parameters for PII and mask before logging",
        "6. Execute: Call the connector's execute_tool() method",
        "7. Idempotency Store: Cache the result with TTL for future idempotent calls",
        "8. Audit Log: Write execution record (input_hash, output_hash, latency_ms, status)",
    ]
    for p in pipeline:
        pdf.bullet(p)
    pdf.ln(2)

    # 7.2
    pdf.sub_title("7.2  execute() Signature")
    pdf.code_block(
        "  async def execute(\n"
        "      self,\n"
        "      tool_name: str,           # e.g., 'salesforce.create_contact'\n"
        "      params: dict,             # Tool-specific parameters\n"
        "      context: ExecutionContext, # tenant_id, agent_id, trace_id\n"
        "      grant_token: str = '',    # Grantex JWT (optional)\n"
        "      idempotency_key: str = '',# Client-provided idempotency key\n"
        "      timeout_ms: int = 10000,  # Per-call timeout override\n"
        "  ) -> ToolResult:"
    )

    # 7.3
    pdf.sub_title("7.3  Error Codes")
    pdf.body("Standardized error codes returned by the Tool Gateway:")

    errors = [
        ("E1001", "TOOL_NOT_FOUND", "ERROR", "No", "-", "Log + alert"),
        ("E1002", "SCOPE_DENIED", "ERROR", "No", "-", "Alert admin"),
        ("E1003", "RATE_LIMITED", "WARN", "Yes", "3", "Backoff + retry"),
        ("E1004", "TIMEOUT", "ERROR", "Yes", "2", "Alert if repeated"),
        ("E1005", "CONNECTOR_ERROR", "ERROR", "Yes", "3", "Log + circuit break"),
        ("E1006", "AUTH_EXPIRED", "ERROR", "Yes", "1", "Refresh + retry"),
        ("E1007", "PII_VIOLATION", "CRITICAL", "No", "-", "Alert compliance"),
        ("E1008", "IDEMPOTENCY_CONFLICT", "WARN", "No", "-", "Return cached"),
        ("E1009", "BUDGET_EXCEEDED", "ERROR", "No", "-", "Pause agent"),
        ("E1010", "CIRCUIT_OPEN", "ERROR", "Yes", "1", "Wait + retry"),
    ]
    cols = [("Code", 15), ("Name", 38), ("Severity", 18),
            ("Retry", 12), ("Max", 10), ("Escalation", 40)]
    pdf.table_header(cols)
    for i, row in enumerate(errors):
        pdf.table_row(
            [(row[0], 15), (row[1], 38), (row[2], 18),
             (row[3], 12), (row[4], 10), (row[5], 40)],
            shade=i % 2 == 1,
        )
    pdf.ln(3)

    # 7.4
    pdf.sub_title("7.4  PII Masking")
    pdf.body(
        "Before logging any tool call inputs or outputs, the PII masking engine "
        "scans for and redacts the following patterns:"
    )
    pii = [
        "Email addresses: user@example.com -> u***@example.com",
        "Phone numbers: +91-9876543210 -> +91-****3210",
        "Aadhaar numbers: 1234 5678 9012 -> XXXX XXXX 9012",
        "PAN numbers: ABCDE1234F -> XXXXX1234X",
        "Bank account numbers: 12345678901234 -> XXXXXXXXXX1234",
    ]
    for p in pii:
        pdf.bullet(p)
    pdf.ln(2)

    # 7.5
    pdf.sub_title("7.5  Audit Logging")
    pdf.body(
        "Every tool execution is logged with: tool_name, agent_id, tenant_id, "
        "input_hash (SHA256 of input, truncated to first 16 chars), output_hash "
        "(SHA256 of output, truncated to first 16 chars), latency_ms, status, "
        "error_code (if any), timestamp. Logs are append-only with HMAC-SHA256 "
        "tamper detection and cannot be modified or deleted (WORM policy)."
    )

    pdf.sub_title("7.6  Idempotency")
    pdf.body(
        "The idempotency system prevents duplicate tool executions when requests "
        "are retried (e.g., due to network timeouts). Implementation details:"
    )
    idemp_details = [
        "Key generation: SHA256 hash of (tool_name + sorted params + tenant_id + agent_id)",
        "Storage: Redis with configurable TTL (default 1 hour)",
        "Behavior: If a matching key exists, the cached result is returned immediately "
        "without re-executing the tool. The response includes an X-Idempotent-Replay: true header.",
        "Client key: Clients can provide their own idempotency_key to override auto-generation. "
        "This is useful for ensuring exactly-once semantics in payment or transfer operations.",
        "Cleanup: Expired keys are automatically purged by Redis TTL. No manual cleanup needed.",
    ]
    for d in idemp_details:
        pdf.bullet(d)
    pdf.ln(2)

    pdf.sub_title("7.7  ToolResult Schema")
    pdf.body("Every tool execution returns a standardized ToolResult object:")
    pdf.code_block(
        '  {\n'
        '    "tool_name": "salesforce.create_contact",\n'
        '    "status": "success",\n'
        '    "data": { ... },           // Tool-specific response\n'
        '    "error": null,             // Error object if failed\n'
        '    "latency_ms": 245,\n'
        '    "idempotent_replay": false,\n'
        '    "trace_id": "abc-123",\n'
        '    "timestamp": "2026-04-04T10:30:00Z"\n'
        '  }'
    )

    pdf.sub_title("7.8  Connector Health Monitoring")
    pdf.body(
        "The Tool Gateway continuously monitors connector health. Each connector's "
        "health_check() is called periodically (every 60 seconds) and the results "
        "are exposed via the /health/connectors endpoint. Connectors with consecutive "
        "health check failures are marked as 'degraded' and tool calls to them "
        "return E1005 (CONNECTOR_ERROR) immediately without attempting execution."
    )


def sec08_workflows(pdf: FuncSpecPdf) -> None:
    pdf.add_page()
    pdf.section_title("8", "Workflow Engine")

    pdf.body(
        "The Workflow Engine orchestrates multi-step, multi-agent processes. "
        "Workflows are defined as JSON blueprints containing ordered steps, "
        "each of which may invoke an agent, wait for approval, branch "
        "conditionally, or fan out in parallel."
    )

    # 8.1
    pdf.sub_title("8.1  Data Models")

    pdf.sub_sub_title("WorkflowDefinition")
    wf_fields = [
        ("id", "UUID", "Primary key"),
        ("tenant_id", "UUID (FK)", "Owning tenant"),
        ("name", "VARCHAR(200)", "Workflow display name"),
        ("version", "INTEGER", "Schema version number"),
        ("definition", "JSONB", "Contains steps[], parameters, outputs"),
        ("trigger_type", "VARCHAR(50)", "manual/scheduled/webhook/event_based"),
        ("trigger_config", "JSONB", "Cron expr, webhook URL, event filter"),
        ("is_active", "BOOLEAN", "Enable/disable toggle"),
        ("created_at", "TIMESTAMPTZ", "Creation timestamp"),
        ("updated_at", "TIMESTAMPTZ", "Last modification timestamp"),
    ]
    cols_wf = [("Field", 30), ("Type", 35), ("Description", 125)]
    pdf.table_header(cols_wf)
    for i, (f, t, d) in enumerate(wf_fields):
        pdf.table_row([(f, 30), (t, 35), (d, 125)], shade=i % 2 == 1)
    pdf.ln(2)

    pdf.sub_sub_title("WorkflowRun")
    run_fields = [
        ("id", "UUID", "Run instance ID"),
        ("workflow_id", "UUID (FK)", "Reference to definition"),
        ("status", "VARCHAR(30)", "running/completed/failed/waiting_hitl/timed_out/cancelled"),
        ("trigger_payload", "JSONB", "Input data from trigger"),
        ("context", "JSONB", "Shared state across steps"),
        ("result", "JSONB", "Final workflow output"),
        ("steps_total", "INTEGER", "Total steps in this run"),
        ("steps_completed", "INTEGER", "Steps finished so far"),
        ("started_at", "TIMESTAMPTZ", "Run start time"),
        ("completed_at", "TIMESTAMPTZ", "Run end time (if finished)"),
    ]
    cols_run = [("Field", 30), ("Type", 35), ("Description", 125)]
    pdf.table_header(cols_run)
    for i, (f, t, d) in enumerate(run_fields):
        pdf.table_row([(f, 30), (t, 35), (d, 125)], shade=i % 2 == 1)
    pdf.ln(2)

    pdf.check_space(50)
    pdf.sub_sub_title("StepExecution")
    step_fields = [
        ("id", "UUID", "Step execution ID"),
        ("run_id", "UUID (FK)", "Parent workflow run"),
        ("step_id", "VARCHAR(100)", "Logical step identifier"),
        ("step_type", "VARCHAR(30)", "agent/approval/parallel/conditional/wait/sub-workflow"),
        ("agent_id", "UUID (FK)", "Agent assigned to this step (if type=agent)"),
        ("status", "VARCHAR(20)", "pending/running/completed/failed/skipped/waiting"),
        ("input", "JSONB", "Step input data"),
        ("output", "JSONB", "Step output data"),
        ("confidence", "NUMERIC(4,3)", "Agent confidence for this step"),
        ("error", "TEXT", "Error message if failed"),
        ("started_at", "TIMESTAMPTZ", "Step start time"),
        ("completed_at", "TIMESTAMPTZ", "Step end time"),
    ]
    cols_step = [("Field", 30), ("Type", 35), ("Description", 125)]
    pdf.table_header(cols_step)
    for i, (f, t, d) in enumerate(step_fields):
        pdf.table_row([(f, 30), (t, 35), (d, 125)], shade=i % 2 == 1)
    pdf.ln(3)

    # 8.2
    pdf.sub_title("8.2  Step Types")
    step_types = [
        "agent: Invokes an AI agent with task input, waits for completion or HITL",
        "approval: Human-in-the-loop step. Pauses workflow until approve/reject decision",
        "parallel: Executes multiple sub-steps concurrently, waits for all to complete",
        "conditional: Evaluates a condition expression and branches to the matching step",
        "wait: Pauses for a specified duration (e.g., 'wait 1h' for cool-down periods)",
        "wait_for_event: Pauses until an external event is received (webhook, system event)",
        "sub-workflow: Invokes another workflow definition as a nested step",
    ]
    for s in step_types:
        pdf.bullet(s)
    pdf.ln(2)

    # 8.3
    pdf.sub_title("8.3  Trigger Types")
    triggers = [
        "manual: Triggered by user via API or dashboard button",
        "scheduled: Triggered by cron expression (e.g., '0 9 * * MON' for every Monday 9 AM)",
        "webhook: Triggered by external HTTP POST to a dedicated webhook URL",
        "event_based: Triggered by internal system events (e.g., 'invoice.created', 'lead.scored')",
    ]
    for t in triggers:
        pdf.bullet(t)
    pdf.ln(2)

    # 8.4
    pdf.sub_title("8.4  Run Statuses")
    statuses = [
        "running: Workflow is actively executing steps",
        "completed: All steps finished successfully",
        "failed: A step failed and no retry/fallback was available",
        "waiting_hitl: Paused at an approval or low-confidence step",
        "timed_out: Workflow exceeded its maximum execution time",
        "cancelled: Manually cancelled by user or system",
    ]
    for s in statuses:
        pdf.bullet(s)
    pdf.ln(2)

    # 8.5
    pdf.check_space(60)
    pdf.sub_title("8.5  Pre-built Workflow Templates (15)")
    pdf.body("AgenticOrg ships with 15 production-ready workflow templates:")

    templates = [
        ("invoice_to_pay_v3", "Finance", "Invoice receipt -> OCR -> matching -> approval -> payment"),
        ("month_end_close", "Finance", "Journal entries -> reconciliation -> review -> close"),
        ("daily_treasury", "Finance", "Fetch balances -> forecast -> alerts -> CFO briefing"),
        ("tax_calendar", "Finance", "Due dates -> preparation -> review -> filing -> confirmation"),
        ("campaign_launch", "Marketing", "Brief -> content -> approval -> schedule -> launch -> report"),
        ("content_pipeline", "Marketing", "Ideation -> draft -> SEO -> review -> publish -> promote"),
        ("lead_nurture", "Marketing", "Score leads -> segment -> drip setup -> monitor -> handoff"),
        ("email_drip_sequence", "Marketing", "Define triggers -> create emails -> A/B test -> optimize"),
        ("ab_test_campaign", "Marketing", "Hypothesis -> variants -> launch -> measure -> winner"),
        ("abm_campaign", "Marketing", "Target accounts -> intent score -> personalize -> engage"),
        ("weekly_marketing_rpt", "Marketing", "Collect metrics -> analyze -> visualize -> distribute"),
        ("incident_response", "Ops", "Alert -> triage -> runbook -> fix -> verify -> RCA"),
        ("weekly_devops_health", "Ops", "Collect SLIs -> compare SLOs -> report -> action items"),
        ("support_triage", "Ops", "Ticket in -> classify -> route -> auto-respond -> escalate"),
        ("employee_onboarding", "HR", "Offer accepted -> IT setup -> docs -> training -> check-in"),
    ]
    cols_tpl = [("Template", 40), ("Domain", 22), ("Flow", 128)]
    pdf.table_header(cols_tpl)
    for i, (name, domain, flow) in enumerate(templates):
        pdf.table_row([(name, 40), (domain, 22), (flow, 128)],
                      shade=i % 2 == 1)
    pdf.ln(3)

    # 8.6
    pdf.sub_title("8.6  Error Handling")
    pdf.body("Workflow steps support configurable error handling strategies:")
    err_handling = [
        "Retry: Exponential backoff (base 2s, max 60s). Configurable max_retries per step (default 3).",
        "Timeout: Each step has a timeout. On timeout, the step fails and the strategy is applied.",
        "Skip: Mark the step as skipped and continue to the next step (for non-critical steps).",
        "Fail: Immediately fail the workflow and log the error (for critical steps).",
        "Fallback: Execute an alternative step if the primary step fails (e.g., manual approval).",
    ]
    for e in err_handling:
        pdf.bullet(e)
    pdf.ln(2)

    pdf.sub_title("8.7  Workflow API Endpoints")
    wf_eps = [
        ("GET", "/workflows", "200", "List workflow definitions (paginated)"),
        ("POST", "/workflows", "201", "Create a new workflow definition"),
        ("GET", "/workflows/{id}", "200", "Get workflow definition details"),
        ("PUT", "/workflows/{id}", "200", "Update workflow definition"),
        ("DELETE", "/workflows/{id}", "200", "Delete workflow definition"),
        ("POST", "/workflows/{id}/trigger", "201", "Trigger a workflow run"),
        ("GET", "/workflows/{id}/runs", "200", "List runs for a workflow"),
        ("GET", "/workflow-runs/{id}", "200", "Get run details with steps"),
        ("POST", "/workflow-runs/{id}/cancel", "200", "Cancel a running workflow"),
        ("POST", "/workflow-runs/{id}/retry", "201", "Retry a failed run"),
        ("GET", "/workflow-templates", "200", "List pre-built templates"),
        ("POST", "/workflow-templates/{name}/instantiate", "201", "Create from template"),
    ]
    cols_wfe = [("Method", 18), ("Path", 65), ("Status", 15), ("Description", 92)]
    pdf.table_header(cols_wfe)
    for i, (method, path, status, desc) in enumerate(wf_eps):
        pdf.table_row(
            [(method, 18), (path, 65), (status, 15), (desc, 92)],
            shade=i % 2 == 1,
        )
    pdf.ln(3)

    pdf.sub_title("8.8  Workflow Context & Data Passing")
    pdf.body(
        "Workflows maintain a shared context JSONB object that steps can read from "
        "and write to. Each step receives the full context as input and can add "
        "or modify keys in its output. This enables data flow between steps:"
    )
    pdf.bullet(
        "Step output mapping: Each step definition specifies which output fields "
        "to write back to the shared context (e.g., 'invoice_total' from OCR step)"
    )
    pdf.bullet(
        "Conditional references: Condition expressions can reference context fields "
        "(e.g., 'context.invoice_total > 100000' to route to manager approval)"
    )
    pdf.bullet(
        "Template variables: Step input templates use {{context.field}} syntax "
        "for dynamic parameter injection"
    )
    pdf.ln(2)


def sec09_hitl(pdf: FuncSpecPdf) -> None:
    pdf.add_page()
    pdf.section_title("9", "Human-in-the-Loop (HITL) System")

    pdf.body(
        "HITL is a core safety feature that ensures high-stakes or low-confidence "
        "decisions are reviewed by human stakeholders before execution. "
        "The system integrates deeply with both the LangGraph agent runtime "
        "and the Workflow Engine."
    )

    # 9.1
    pdf.sub_title("9.1  GraphInterrupt Mechanism")
    pdf.body(
        "When HITL is triggered, the hitl_gate node calls LangGraph's interrupt() "
        "function, which raises a GraphInterrupt exception. The agent runner "
        "catches this exception and returns a response with status='hitl_triggered' "
        "along with the pending action details (what needs approval, why it was "
        "triggered, and the agent's proposed action)."
    )
    pdf.body(
        "The graph state is persisted to the database, allowing the workflow to "
        "resume from exactly where it paused once the approval decision is made."
    )

    # 9.2
    pdf.sub_title("9.2  Trigger Conditions")
    pdf.body("HITL can be triggered by two mechanisms:")
    pdf.bullet(
        "Confidence threshold: Agent's confidence score falls below its "
        "confidence_floor (default 0.880). This catches uncertain decisions."
    )
    pdf.bullet(
        "Custom expressions: Defined in agent config, e.g., 'amount > 500000' "
        "or 'category == refund'. These catch specific high-risk scenarios "
        "regardless of confidence."
    )
    pdf.ln(2)

    # 9.3
    pdf.sub_title("9.3  Approval API")
    pdf.body("Endpoints for managing HITL approvals:")
    approval_eps = [
        ("GET", "/approvals", "List pending approvals (paginated, RBAC)"),
        ("GET", "/approvals/{id}", "Get approval details with agent reasoning"),
        ("POST", "/approvals/{id}/decide", "Submit decision: {action: approve|reject, reason: str}"),
        ("GET", "/approvals/stats", "Approval metrics (pending, approved, rejected, avg time)"),
    ]
    cols_ap = [("Method", 18), ("Path", 52), ("Description", 120)]
    pdf.table_header(cols_ap)
    for i, (method, path, desc) in enumerate(approval_eps):
        pdf.table_row([(method, 18), (path, 52), (desc, 120)],
                      shade=i % 2 == 1)
    pdf.ln(3)

    pdf.body(
        "On approval, the persisted graph state is loaded and the graph resumes "
        "execution from the hitl_gate node. On rejection, the workflow step is "
        "marked as failed with the reviewer's reason."
    )

    # 9.4
    pdf.sub_title("9.4  Web Push Notifications")
    pdf.body(
        "When HITL is triggered, web push notifications are sent to authorized "
        "approvers. The system uses VAPID (Voluntary Application Server "
        "Identification) keys for push authentication."
    )
    push_details = [
        "VAPID key pair generated at deployment, public key served to frontend",
        "ServiceWorker registered in the browser to receive push events",
        "Notification payload includes: agent name, action summary, confidence, approve/reject buttons",
        "One-tap approve/reject: Clicking the button in the notification sends the decision directly",
    ]
    for p in push_details:
        pdf.bullet(p)
    pdf.ln(2)

    # 9.5
    pdf.sub_title("9.5  Escalation")
    pdf.body(
        "Escalation follows the org chart hierarchy. If a child agent triggers HITL, "
        "the approval request is sent to the parent agent's designated human reviewer. "
        "If the parent also has low confidence, it escalates further up the chain. "
        "The top of the escalation chain is always the tenant admin."
    )

    # 9.6
    pdf.sub_title("9.6  Timeout Handling")
    pdf.body(
        "If an approval is not acted upon within a configurable timeout period "
        "(default: 4 hours), the system auto-escalates to the next level in the "
        "org chart. If no one approves within 24 hours, the workflow step is "
        "marked as timed_out and the workflow's error handling strategy is applied."
    )

    pdf.sub_title("9.7  Approval Data Model")
    approval_fields = [
        ("id", "UUID", "Approval request ID"),
        ("tenant_id", "UUID (FK)", "Tenant isolation"),
        ("agent_id", "UUID (FK)", "Agent that triggered HITL"),
        ("workflow_run_id", "UUID (FK)", "Parent workflow run (if any)"),
        ("step_id", "VARCHAR(100)", "Workflow step ID (if any)"),
        ("trigger_reason", "VARCHAR(50)", "low_confidence / custom_condition"),
        ("confidence", "NUMERIC(4,3)", "Agent's confidence at trigger time"),
        ("proposed_action", "JSONB", "What the agent wants to do"),
        ("context", "JSONB", "Supporting data for reviewer"),
        ("status", "VARCHAR(20)", "pending / approved / rejected / escalated / timed_out"),
        ("decided_by", "UUID (FK)", "User who made the decision"),
        ("decision_reason", "TEXT", "Reviewer's reason for decision"),
        ("created_at", "TIMESTAMPTZ", "When HITL was triggered"),
        ("decided_at", "TIMESTAMPTZ", "When decision was made"),
        ("escalated_at", "TIMESTAMPTZ", "When auto-escalation occurred"),
    ]
    cols_ap = [("Field", 35), ("Type", 32), ("Description", 123)]
    pdf.table_header(cols_ap)
    for i, (f, t, d) in enumerate(approval_fields):
        pdf.table_row([(f, 35), (t, 32), (d, 123)], shade=i % 2 == 1)
    pdf.ln(3)

    pdf.sub_title("9.8  HITL Metrics")
    pdf.body("The system tracks the following HITL operational metrics:")
    hitl_metrics = [
        "Total approvals pending: Count of unresolved HITL requests",
        "Avg decision time: Average time from trigger to decision (target: <30 min)",
        "Approval rate: Percentage of HITL requests that are approved",
        "Escalation rate: Percentage of HITL requests that required escalation",
        "Timeout rate: Percentage of HITL requests that timed out",
        "Top triggers: Most common trigger reasons and agents",
    ]
    for m in hitl_metrics:
        pdf.bullet(m)
    pdf.ln(2)


def sec10_dashboards(pdf: FuncSpecPdf) -> None:
    pdf.add_page()
    pdf.section_title("10", "Dashboards & KPIs")

    pdf.body(
        "AgenticOrg provides role-specific dashboards that surface real-time "
        "KPIs from connected systems. Each dashboard pulls live data via "
        "connectors and presents actionable insights."
    )

    # 10.1
    pdf.sub_title("10.1  CFO Dashboard")
    cfo_kpis = [
        "cash_runway_months: Months of cash remaining at current burn rate",
        "burn_rate: Monthly cash outflow (trailing 3-month average)",
        "dso_days: Days Sales Outstanding (AR collection efficiency)",
        "dpo_days: Days Payable Outstanding (AP payment timing)",
        "ar_aging: Bucketed AR -- 0-30 days, 31-60 days, 61-90 days, 90+ days",
        "ap_aging: Bucketed AP -- same buckets as AR",
        "monthly_pnl: Revenue, COGS, gross_margin, opex, net_income by month",
        "bank_balances: Real-time balances from all connected bank accounts",
        "tax_calendar: Upcoming filing deadlines with status (filed/pending/overdue)",
        "pending_approvals: Count and list of items awaiting CFO approval",
    ]
    for k in cfo_kpis:
        pdf.bullet(k)
    pdf.ln(2)

    # 10.2
    pdf.sub_title("10.2  CMO Dashboard")
    cmo_kpis = [
        "cac + trend: Customer Acquisition Cost with week-over-week trend",
        "mqls + trend: Marketing Qualified Leads count and velocity",
        "sqls + trend: Sales Qualified Leads count and conversion rate",
        "pipeline_value: Total value of deals in pipeline (USD)",
        "roas_by_channel: Return on Ad Spend for Google, Meta, LinkedIn, Organic",
        "email_metrics: open_rate, click_rate, unsubscribe_rate across campaigns",
        "social_engagement: Likes, shares, comments aggregated across platforms",
        "website: sessions, unique_users, bounce_rate from GA4",
        "content_performance: page, views, avg_time_on_page for top content",
        "brand_sentiment: Positive/negative/neutral breakdown from social listening",
    ]
    for k in cmo_kpis:
        pdf.bullet(k)
    pdf.ln(2)

    # 10.3
    pdf.sub_title("10.3  ABM Dashboard")
    abm_kpis = [
        "total_accounts: Number of target accounts in ABM program",
        "accounts_by_tier: Breakdown by Tier 1 (enterprise), Tier 2 (mid-market), Tier 3 (SMB)",
        "avg_intent_score: Average intent score across all target accounts",
        "top_10_by_intent: Highest-intent accounts with scores and recent signals",
        "pipeline_influenced_usd: Total pipeline value attributed to ABM activities",
        "intent_heatmap: Visual grid of accounts vs intent topics with score coloring",
    ]
    for k in abm_kpis:
        pdf.bullet(k)
    pdf.ln(2)

    # 10.4
    pdf.check_space(50)
    pdf.sub_title("10.4  Scope Dashboard (v3.3.0)")
    pdf.body(
        "New in v3.3.0, the Scope Dashboard provides visibility into "
        "Grantex enforcement across all agents and connectors."
    )
    scope_kpis = [
        "Total agents: Count of active agents with scope enforcement",
        "Tool calls today: Number of tool calls processed today",
        "Denials today: Number of tool calls denied by Grantex",
        "Denial rate %: Percentage of tool calls denied (denials/total * 100)",
        "Detailed table: Agent name, connector, tools, permission level, status (allowed/denied)",
        "Filters: By agent, by connector, by permission level, by status, by date range",
    ]
    for k in scope_kpis:
        pdf.bullet(k)
    pdf.ln(2)

    # 10.5
    pdf.sub_title("10.5  Enforce Audit Log (v3.3.0)")
    pdf.body(
        "The Enforce Audit Log provides a real-time feed of all Grantex "
        "enforcement decisions."
    )
    audit_cols = [
        "Timestamp: When the enforcement decision was made",
        "Agent: Name and ID of the requesting agent",
        "Tool: Full tool name (connector.tool_name)",
        "Permission: Required permission level (read/write/delete/admin)",
        "Granted: Whether the call was allowed (yes/no)",
        "Token Expiry: When the grant_token expires",
        "Latency: Time taken for the enforce() call in microseconds",
    ]
    for c in audit_cols:
        pdf.bullet(c)
    pdf.body(
        "Additional features: Filter by 'denied only', CSV export, pagination "
        "(50 entries per page), auto-refresh every 30 seconds."
    )

    pdf.sub_title("10.6  Dashboard API Endpoints")
    dash_eps = [
        ("GET", "/dashboard/cfo", "200", "CFO dashboard data with all KPIs"),
        ("GET", "/dashboard/cmo", "200", "CMO dashboard data with all KPIs"),
        ("GET", "/dashboard/abm", "200", "ABM dashboard data"),
        ("GET", "/dashboard/scopes", "200", "Scope enforcement dashboard (v3.3.0)"),
        ("GET", "/dashboard/enforce-audit", "200", "Enforce audit log feed (v3.3.0)"),
        ("GET", "/dashboard/agents", "200", "Agent performance overview"),
        ("GET", "/dashboard/workflows", "200", "Workflow execution metrics"),
        ("GET", "/dashboard/system-health", "200", "System health and uptime"),
    ]
    cols_de = [("Method", 18), ("Path", 60), ("Status", 15), ("Description", 97)]
    pdf.table_header(cols_de)
    for i, (method, path, status, desc) in enumerate(dash_eps):
        pdf.table_row(
            [(method, 18), (path, 60), (status, 15), (desc, 97)],
            shade=i % 2 == 1,
        )
    pdf.ln(3)

    pdf.sub_title("10.7  Real-time Updates")
    pdf.body(
        "Dashboard data is refreshed using a polling mechanism with configurable "
        "intervals. Default refresh rates: CFO dashboard every 5 minutes, "
        "CMO dashboard every 10 minutes, Scope dashboard every 30 seconds, "
        "Enforce audit log live-stream (30-second polling). The frontend uses "
        "React Query for data fetching with stale-while-revalidate caching "
        "to provide instant UI rendering while background updates proceed."
    )


def sec11_marketing(pdf: FuncSpecPdf) -> None:
    pdf.add_page()
    pdf.section_title("11", "Marketing Automation")

    pdf.body(
        "AgenticOrg includes a comprehensive marketing automation suite that "
        "enables CMOs and marketing teams to run data-driven campaigns "
        "with AI-powered optimization."
    )

    # 11.1
    pdf.sub_title("11.1  A/B Testing")
    pdf.body(
        "The A/B testing system allows marketers to create multiple variants "
        "of emails, landing pages, or ad creatives and automatically determine "
        "the winner."
    )
    ab_features = [
        "Create up to 5 variants per test with different subject lines, content, or CTAs",
        "Traffic split configurable (e.g., 20/20/20/20/20 or 50/50)",
        "Auto-winner selection based on open_rate or click-through rate (CTR)",
        "Statistical significance threshold: 95% confidence before declaring winner",
        "CMO override: Manual winner selection regardless of statistics",
        "Auto-rollout: Winning variant automatically deployed to remaining audience",
    ]
    for f in ab_features:
        pdf.bullet(f)
    pdf.ln(2)

    # 11.2
    pdf.sub_title("11.2  Email Drip Sequences")
    pdf.body(
        "Behavioral email drip sequences that adapt based on recipient actions:"
    )
    drip_features = [
        "Behavior triggers: open, click, no-open-after-N-days, form submission, page visit",
        "Time-delay steps: Wait 1 day, 3 days, 7 days between emails",
        "Re-engage non-openers: Automatic resend with different subject line after 3 days",
        "Lead rescore: Update lead score based on email engagement signals",
        "Exit conditions: Lead converts, unsubscribes, or reaches end of sequence",
        "Dynamic content: Personalized fields (name, company, last action) in templates",
    ]
    for f in drip_features:
        pdf.bullet(f)
    pdf.ln(2)

    # 11.3
    pdf.sub_title("11.3  Account-Based Marketing (ABM)")
    pdf.body(
        "Enterprise ABM capabilities for targeting high-value accounts:"
    )
    abm_features = [
        "CSV upload: Import target account list with company name, domain, tier, contacts",
        "Intent scoring: Weighted composite from Bombora (40%), G2 (30%), TrustRadius (30%)",
        "Campaign launch: Auto-create personalized campaigns for each tier",
        "Account-level tracking: Engagement aggregated per account, not just per contact",
        "Sales handoff: Auto-notify sales when account intent score exceeds threshold",
    ]
    for f in abm_features:
        pdf.bullet(f)
    pdf.ln(2)

    # 11.4
    pdf.sub_title("11.4  Email Webhooks")
    pdf.body(
        "Webhook endpoints for receiving email engagement events from ESPs:"
    )
    webhooks = [
        "POST /webhooks/email/sendgrid -- SendGrid event webhooks (open, click, bounce, spam)",
        "POST /webhooks/email/mailchimp -- Mailchimp webhook events (subscribe, unsubscribe, campaign)",
        "POST /webhooks/email/moengage -- MoEngage push/email events (delivered, opened, clicked)",
    ]
    for w in webhooks:
        pdf.bullet(w)
    pdf.body(
        "Events are processed in near-real-time, updating lead scores, "
        "drip sequence state, and dashboard metrics."
    )

    # 11.5
    pdf.sub_title("11.5  Web Push Notifications")
    pdf.body(
        "Browser push notifications for marketing engagement and HITL approvals:"
    )
    push_features = [
        "VAPID key pair for push authentication (generated at deployment)",
        "POST /push/subscribe -- Register a browser push subscription",
        "DELETE /push/unsubscribe -- Remove a push subscription",
        "POST /push/test -- Send a test notification to verify setup",
        "Payload includes: title, body, icon, action buttons, deep link URL",
    ]
    for f in push_features:
        pdf.bullet(f)
    pdf.ln(2)

    pdf.sub_title("11.6  Marketing API Endpoints")
    mkt_eps = [
        ("POST", "/marketing/ab-test", "201", "Create new A/B test"),
        ("GET", "/marketing/ab-test/{id}", "200", "Get A/B test results"),
        ("POST", "/marketing/ab-test/{id}/winner", "200", "Override winner"),
        ("POST", "/marketing/drip", "201", "Create email drip sequence"),
        ("GET", "/marketing/drip/{id}", "200", "Get drip sequence status"),
        ("POST", "/marketing/drip/{id}/pause", "200", "Pause drip sequence"),
        ("POST", "/marketing/abm/upload", "201", "Upload ABM target accounts"),
        ("GET", "/marketing/abm/accounts", "200", "List ABM accounts with scores"),
        ("POST", "/marketing/abm/campaign", "201", "Launch ABM campaign"),
        ("GET", "/marketing/abm/intent-heatmap", "200", "Intent heatmap data"),
        ("POST", "/push/subscribe", "201", "Subscribe to push notifications"),
        ("DELETE", "/push/unsubscribe", "200", "Unsubscribe from push"),
        ("POST", "/push/test", "200", "Send test push notification"),
    ]
    cols_me = [("Method", 18), ("Path", 60), ("Status", 15), ("Description", 97)]
    pdf.table_header(cols_me)
    for i, (method, path, status, desc) in enumerate(mkt_eps):
        pdf.table_row(
            [(method, 18), (path, 60), (status, 15), (desc, 97)],
            shade=i % 2 == 1,
        )
    pdf.ln(3)

    pdf.sub_title("11.7  Campaign Analytics")
    pdf.body(
        "All marketing campaigns track the following metrics in real-time:"
    )
    campaign_metrics = [
        "Impressions: Total times the content was displayed",
        "Clicks: Total clicks on links or CTAs",
        "CTR (Click-Through Rate): Clicks / Impressions * 100",
        "Conversions: Completed goal actions (signup, purchase, demo request)",
        "CPA (Cost Per Acquisition): Total spend / Conversions",
        "ROAS (Return on Ad Spend): Revenue / Ad spend",
        "Engagement rate: (Likes + Comments + Shares) / Reach * 100",
        "Unsubscribe rate: Unsubscribes / Delivered * 100",
        "Bounce rate: Bounced emails / Sent * 100",
        "Revenue attributed: Total revenue from campaign conversions",
    ]
    for m in campaign_metrics:
        pdf.bullet(m)
    pdf.ln(2)


def sec12_sales(pdf: FuncSpecPdf) -> None:
    pdf.add_page()
    pdf.section_title("12", "Sales Pipeline")

    pdf.body(
        "The Sales Pipeline module manages the full lead-to-deal lifecycle "
        "with AI-powered scoring, automated follow-ups, and CRM integration."
    )

    # 12.1
    pdf.sub_title("12.1  Lead Model")
    pdf.body("Complete lead data model:")
    lead_fields = [
        ("name", "VARCHAR(200)", "Contact full name"),
        ("email", "VARCHAR(200)", "Contact email (unique per tenant)"),
        ("company", "VARCHAR(200)", "Company name"),
        ("role", "VARCHAR(100)", "Job title / role"),
        ("source", "VARCHAR(50)", "Lead source (website, referral, ad, event)"),
        ("stage", "VARCHAR(20)", "Pipeline stage (see 12.2)"),
        ("score", "INTEGER", "Lead score (0-100)"),
        ("bant_budget", "BOOLEAN", "BANT: Has budget?"),
        ("bant_authority", "BOOLEAN", "BANT: Is decision maker?"),
        ("bant_need", "BOOLEAN", "BANT: Has identified need?"),
        ("bant_timeline", "VARCHAR(50)", "BANT: Purchase timeline"),
        ("followup_count", "INTEGER", "Number of follow-ups done"),
        ("deal_value_usd", "NUMERIC(12,2)", "Estimated deal value"),
        ("utm_source", "VARCHAR(100)", "UTM source parameter"),
        ("utm_medium", "VARCHAR(100)", "UTM medium parameter"),
        ("utm_campaign", "VARCHAR(100)", "UTM campaign parameter"),
    ]
    cols_lead = [("Field", 35), ("Type", 35), ("Description", 120)]
    pdf.table_header(cols_lead)
    for i, (f, t, d) in enumerate(lead_fields):
        pdf.table_row([(f, 35), (t, 35), (d, 120)], shade=i % 2 == 1)
    pdf.ln(3)

    # 12.2
    pdf.sub_title("12.2  Pipeline Stages")
    pdf.body("Leads progress through the following stages:")
    pdf.code_block(
        "  new -> qualified -> contacted -> demo -> trial -> won\n"
        "                                                    \\-> lost"
    )
    pdf.body(
        "Stage transitions can be manual (via API) or automatic (based on "
        "lead score thresholds and engagement signals)."
    )

    # 12.3
    pdf.sub_title("12.3  Sales API Endpoints")
    sales_eps = [
        ("POST", "/sales/pipeline/leads", "Create a new lead"),
        ("GET", "/sales/pipeline", "List all leads (paginated, filterable)"),
        ("GET", "/sales/pipeline/due-followups", "Leads due for follow-up today"),
        ("POST", "/sales/pipeline/process-lead", "AI-score and route a new lead"),
        ("PATCH", "/sales/pipeline/{id}", "Update lead fields or stage"),
        ("GET", "/sales/metrics", "Pipeline metrics (by stage, conversion rates)"),
        ("POST", "/sales/import-csv", "Bulk import leads from CSV"),
        ("POST", "/sales/run-followups", "Trigger AI follow-up for due leads"),
        ("POST", "/sales/process-inbox", "Process inbound emails for lead matching"),
    ]
    cols_se = [("Method", 18), ("Path", 60), ("Description", 112)]
    pdf.table_header(cols_se)
    for i, (method, path, desc) in enumerate(sales_eps):
        pdf.table_row([(method, 18), (path, 60), (desc, 112)],
                      shade=i % 2 == 1)
    pdf.ln(3)

    pdf.sub_title("12.4  Lead Scoring Algorithm")
    pdf.body(
        "The AI-powered lead scoring system assigns a score (0-100) based on "
        "multiple signals:"
    )
    scoring = [
        "Demographic fit (25%): Company size, industry match, job title seniority",
        "BANT qualification (25%): Budget confirmed, authority level, need identified, timeline",
        "Engagement signals (30%): Email opens, page visits, content downloads, demo requests",
        "Intent data (20%): Bombora surge topics, G2 comparison visits, TrustRadius reviews",
    ]
    for s in scoring:
        pdf.bullet(s)
    pdf.body(
        "Scores are recalculated on every engagement event. Leads scoring above 70 "
        "are automatically promoted to 'qualified' stage. Leads scoring above 85 "
        "trigger an immediate sales notification for follow-up."
    )

    pdf.sub_title("12.5  Sales Metrics")
    pdf.body("GET /sales/metrics returns the following KPIs:")
    sales_metrics = [
        "total_leads: Count of all leads in pipeline",
        "leads_by_stage: Breakdown by stage (new, qualified, contacted, demo, trial, won, lost)",
        "conversion_rate: Won / (Won + Lost) * 100",
        "avg_deal_value: Average deal_value_usd for won deals",
        "avg_sales_cycle_days: Average days from new to won",
        "pipeline_value: Sum of deal_value_usd for active deals",
        "followup_due_count: Leads requiring follow-up today",
        "win_rate_by_source: Conversion rate grouped by lead source",
    ]
    for m in sales_metrics:
        pdf.bullet(m)
    pdf.ln(2)


def sec13_multi_company(pdf: FuncSpecPdf) -> None:
    pdf.add_page()
    pdf.section_title("13", "Multi-Company Support")

    pdf.body(
        "AgenticOrg supports multi-company (multi-entity) operations within "
        "a single tenant. This is critical for holding companies, CA firms "
        "managing multiple clients, and enterprises with subsidiaries."
    )

    # 13.1
    pdf.sub_title("13.1  Company Model")
    company_fields = [
        ("id", "UUID", "Primary key"),
        ("tenant_id", "UUID (FK)", "Owning tenant"),
        ("name", "VARCHAR(200)", "Internal company identifier"),
        ("display_name", "VARCHAR(200)", "UI display name"),
        ("industry", "VARCHAR(100)", "Industry vertical"),
        ("domain", "VARCHAR(200)", "Company website domain"),
        ("is_active", "BOOLEAN", "Enable/disable company"),
        ("created_at", "TIMESTAMPTZ", "Creation timestamp"),
    ]
    cols_co = [("Field", 30), ("Type", 35), ("Description", 125)]
    pdf.table_header(cols_co)
    for i, (f, t, d) in enumerate(company_fields):
        pdf.table_row([(f, 30), (t, 35), (d, 125)], shade=i % 2 == 1)
    pdf.ln(3)

    # 13.2
    pdf.sub_title("13.2  Company Switcher")
    pdf.body(
        "The UI features a top-navigation dropdown that allows users to switch "
        "between companies. When a company is selected, all data displayed on "
        "dashboards, agent lists, and reports is filtered to that company's "
        "data only. This ensures complete data isolation between entities "
        "while allowing authorized users to view multiple companies."
    )

    # 13.3
    pdf.sub_title("13.3  RBAC per Company")
    pdf.body(
        "Users can have different roles in different companies. For example, "
        "a user might be 'admin' in the parent company but 'cfo' in a "
        "subsidiary. Role assignments are stored per (user_id, company_id) pair. "
        "The active role is determined by the currently selected company "
        "in the company switcher."
    )

    # 13.4
    pdf.sub_title("13.4  API Endpoints")
    company_eps = [
        ("GET", "/companies", "List all companies for the tenant"),
        ("POST", "/companies", "Create a new company entity"),
        ("GET", "/companies/{id}", "Get company details"),
        ("PATCH", "/companies/{id}", "Update company fields"),
    ]
    cols_ce = [("Method", 18), ("Path", 52), ("Description", 120)]
    pdf.table_header(cols_ce)
    for i, (method, path, desc) in enumerate(company_eps):
        pdf.table_row([(method, 18), (path, 52), (desc, 120)],
                      shade=i % 2 == 1)
    pdf.ln(3)

    pdf.sub_title("13.5  Data Isolation Architecture")
    pdf.body(
        "Data isolation between companies is enforced at multiple layers:"
    )
    isolation = [
        "Database: Every query includes a WHERE tenant_id = :tenant_id AND company_id = :company_id "
        "clause. This is enforced by the ORM base query class.",
        "API middleware: The active company is extracted from the JWT or X-Company-Id header "
        "and injected into every database session.",
        "Agent scope: Agents are assigned to a company and can only access data within that company.",
        "Connectors: Connector credentials are company-specific. Switching companies also "
        "switches the active credentials for connectors.",
        "Reports: Scheduled reports are scoped to a company. Cross-company rollup reports "
        "require admin role on all included companies.",
    ]
    for i_item in isolation:
        pdf.bullet(i_item)
    pdf.ln(2)

    pdf.sub_title("13.6  CA Firm Multi-Client Use Case")
    pdf.body(
        "For CA firms managing multiple client companies, AgenticOrg provides "
        "a streamlined workflow. The CA firm creates one tenant with multiple "
        "company entities (one per client). Each company has its own Tally, "
        "GSTN, and Income Tax connectors configured with client-specific "
        "credentials. The CA firm's staff can switch between clients using "
        "the company switcher and run GST filing, TDS reconciliation, or "
        "audit preparation agents for each client independently."
    )


def sec14_scheduled_reports(pdf: FuncSpecPdf) -> None:
    pdf.add_page()
    pdf.section_title("14", "Scheduled Reports")

    pdf.body(
        "Automated report generation and delivery on configurable schedules. "
        "Reports are generated by AI agents and delivered via email, Slack, "
        "or WhatsApp."
    )

    # 14.1
    pdf.sub_title("14.1  Schedule Model")
    sched_fields = [
        ("id", "UUID", "Primary key"),
        ("tenant_id", "UUID (FK)", "Owning tenant"),
        ("report_type", "VARCHAR(50)", "cfo_daily/cmo_weekly/aging_report/pnl_report/campaign_report"),
        ("cron_expression", "VARCHAR(100)", "Standard cron (e.g., '0 9 * * MON')"),
        ("delivery_channels", "JSONB", "Array of {type: email|slack|whatsapp, target: addr}"),
        ("format", "VARCHAR(20)", "pdf / excel / both"),
        ("is_active", "BOOLEAN", "Enable/disable toggle"),
        ("last_run_at", "TIMESTAMPTZ", "When the report last ran"),
        ("next_run_at", "TIMESTAMPTZ", "Computed next execution time"),
    ]
    cols_sc = [("Field", 32), ("Type", 32), ("Description", 126)]
    pdf.table_header(cols_sc)
    for i, (f, t, d) in enumerate(sched_fields):
        pdf.table_row([(f, 32), (t, 32), (d, 126)], shade=i % 2 == 1)
    pdf.ln(3)

    # 14.2
    pdf.sub_title("14.2  Delivery Channels")
    channels = [
        "email: Report attached as PDF/Excel, sent via SendGrid or Gmail connector",
        "slack: Report posted as file upload to a specified Slack channel",
        "whatsapp: Report summary sent as WhatsApp message with download link",
    ]
    for c in channels:
        pdf.bullet(c)
    pdf.ln(2)

    # 14.3
    pdf.sub_title("14.3  API Endpoints")
    report_eps = [
        ("GET", "/scheduled-reports", "List all scheduled reports"),
        ("POST", "/scheduled-reports", "Create a new schedule"),
        ("GET", "/scheduled-reports/{id}", "Get schedule details"),
        ("PATCH", "/scheduled-reports/{id}", "Update schedule config"),
        ("DELETE", "/scheduled-reports/{id}", "Delete a schedule"),
        ("POST", "/scheduled-reports/{id}/run-now", "Trigger immediate execution"),
    ]
    cols_re = [("Method", 18), ("Path", 60), ("Description", 112)]
    pdf.table_header(cols_re)
    for i, (method, path, desc) in enumerate(report_eps):
        pdf.table_row([(method, 18), (path, 60), (desc, 112)],
                      shade=i % 2 == 1)
    pdf.ln(3)

    # 14.4
    pdf.sub_title("14.4  Celery Beat Integration")
    pdf.body(
        "Scheduled reports are executed by Celery Beat, the periodic task "
        "scheduler. Each active schedule is registered as a Celery periodic "
        "task with its cron expression. When triggered, the task: "
        "(1) invokes the appropriate agent to generate the report, "
        "(2) renders the output in the requested format (PDF via fpdf2, "
        "Excel via openpyxl), (3) delivers via the configured channels, "
        "and (4) updates last_run_at and computes next_run_at."
    )


def sec15_compliance(pdf: FuncSpecPdf) -> None:
    pdf.add_page()
    pdf.section_title("15", "Compliance & Audit")

    pdf.body(
        "AgenticOrg implements enterprise-grade compliance features designed "
        "for regulated industries including financial services, healthcare, "
        "and government."
    )

    # 15.1
    pdf.sub_title("15.1  Audit Log")
    pdf.body(
        "The audit log is the foundation of the compliance system. Key properties:"
    )
    audit_features = [
        "Append-only: New entries are always INSERT operations. No UPDATE or DELETE allowed.",
        "HMAC-SHA256 tamper detection: Each log entry includes an HMAC signature computed "
        "over the entry content. Any modification invalidates the signature.",
        "WORM storage: Row-Level Security (RLS) policies block UPDATE and DELETE on the "
        "audit_log table. Even database admins cannot modify entries.",
        "7-year retention: Logs are retained for 7 years per regulatory requirements. "
        "Archival to cold storage (GCS) occurs after 90 days.",
        "Indexed: Searchable by tenant_id, agent_id, action_type, timestamp, and actor_id.",
    ]
    for f in audit_features:
        pdf.bullet(f)
    pdf.ln(2)

    # 15.2
    pdf.sub_title("15.2  DSAR (Data Subject Access Requests)")
    pdf.body("GDPR/DPDPA-compliant data subject request endpoints:")
    dsar_eps = [
        "POST /dsar/access -- Returns all data held about a given email/user within 30 days",
        "POST /dsar/erase -- Initiates data erasure for a user (30-day deadline per GDPR Art. 17)",
        "POST /dsar/export -- Generates a machine-readable export (JSON) of all user data",
    ]
    for d in dsar_eps:
        pdf.bullet(d)
    pdf.body(
        "All DSAR requests are logged in the audit trail and must be completed "
        "within 30 days. The system tracks request status and sends reminders "
        "at 7, 14, and 21 days."
    )

    # 15.3
    pdf.sub_title("15.3  Evidence Package")
    pdf.body(
        "GET /compliance/evidence-package generates a comprehensive compliance "
        "evidence package containing:"
    )
    evidence = [
        "access_controls: Current RBAC configuration, user roles, permission assignments",
        "audit_logs: Filtered audit log entries for the requested time period",
        "deployment_records: CI/CD deployment history with commit hashes and approvers",
        "incident_history: Security incidents, resolutions, and post-mortems",
    ]
    for e in evidence:
        pdf.bullet(e)
    pdf.body(
        "The evidence package is returned as a ZIP file containing JSON exports "
        "and can be provided directly to auditors."
    )

    # 15.4
    pdf.sub_title("15.4  PII Masking")
    pdf.body(
        "PII masking is enabled by default for all log writers across the platform. "
        "Before any data is written to logs, audit trails, or external monitoring "
        "systems, the PII masking engine scans for and redacts sensitive patterns "
        "(emails, phone numbers, Aadhaar, PAN, bank accounts). This is not "
        "configurable -- it is always on as a safety measure."
    )

    pdf.sub_title("15.5  Regulatory Compliance Matrix")
    pdf.body("AgenticOrg addresses the following regulatory frameworks:")
    compliance_regs = [
        ("GDPR", "Data protection", "DSAR, consent, data minimization, 72h breach notify"),
        ("DPDPA", "India data protection", "DSAR, consent, data localization, DPO appointment"),
        ("SOX", "Financial controls", "WORM audit logs, access controls, evidence package"),
        ("SOC 2", "Service organization", "Audit trail, encryption, access management"),
        ("ISO 27001", "Info security", "ISMS controls, risk assessment, incident response"),
        ("RBI AA", "Account aggregator", "Consent-based data sharing, data retention limits"),
        ("GST Act", "Tax compliance", "E-invoice, e-waybill, GSTR filing, reconciliation"),
    ]
    cols_reg = [("Regulation", 25), ("Domain", 35), ("AgenticOrg Controls", 130)]
    pdf.table_header(cols_reg)
    for i, (reg, domain, controls) in enumerate(compliance_regs):
        pdf.table_row([(reg, 25), (domain, 35), (controls, 130)],
                      shade=i % 2 == 1)
    pdf.ln(3)

    pdf.sub_title("15.6  Compliance API Endpoints")
    comp_eps = [
        ("GET", "/compliance/audit-log", "Paginated audit log entries"),
        ("GET", "/compliance/evidence-package", "Download evidence ZIP"),
        ("POST", "/dsar/access", "Data subject access request"),
        ("POST", "/dsar/erase", "Data subject erasure request"),
        ("POST", "/dsar/export", "Data subject export request"),
        ("GET", "/compliance/dsar-status", "Check DSAR request status"),
        ("GET", "/compliance/pii-report", "PII inventory report"),
    ]
    cols_ce2 = [("Method", 18), ("Path", 62), ("Description", 110)]
    pdf.table_header(cols_ce2)
    for i, (method, path, desc) in enumerate(comp_eps):
        pdf.table_row([(method, 18), (path, 62), (desc, 110)],
                      shade=i % 2 == 1)
    pdf.ln(2)


def sec16_integrations(pdf: FuncSpecPdf) -> None:
    pdf.add_page()
    pdf.section_title("16", "External Integrations")

    pdf.body(
        "AgenticOrg exposes its capabilities through multiple integration "
        "channels, enabling developers and AI assistants to interact with "
        "the platform programmatically."
    )

    # 16.1
    pdf.sub_title("16.1  Python SDK")
    pdf.code_block("  pip install agenticorg")
    pdf.body("Key SDK methods:")
    py_methods = [
        "client.agents.list() -- List all agents (with pagination and filters)",
        "client.agents.run(agent_id, task) -- Execute an agent with a task input",
        "client.sop.parse_text(text) -- Parse SOP text into structured workflow definition",
        "client.a2a.agent_card() -- Retrieve the A2A agent card",
        "client.connectors.list() -- List all available connectors",
        "client.workflows.trigger(workflow_id, payload) -- Trigger a workflow",
        "client.approvals.list() -- List pending HITL approvals",
        "client.approvals.decide(id, action, reason) -- Approve or reject",
    ]
    for m in py_methods:
        pdf.bullet(m)
    pdf.ln(2)

    # 16.2
    pdf.sub_title("16.2  TypeScript SDK")
    pdf.code_block("  npm i agenticorg-sdk")
    pdf.body(
        "The TypeScript SDK mirrors the Python SDK's API surface with "
        "TypeScript-native types and async/await patterns. It supports "
        "Node.js 18+ and modern browsers via ESM."
    )

    # 16.3
    pdf.sub_title("16.3  MCP Server")
    pdf.code_block("  npx agenticorg-mcp-server")
    pdf.body(
        "The Model Context Protocol (MCP) server exposes all 340+ tools from "
        "AgenticOrg's 54 connectors as MCP tools. Compatible clients include:"
    )
    mcp_clients = [
        "Claude Desktop -- Add as MCP server in claude_desktop_config.json",
        "Cursor IDE -- Configure in .cursor/mcp.json for AI-assisted coding",
        "ChatGPT -- Via MCP bridge plugin",
        "Any MCP-compatible AI assistant",
    ]
    for c in mcp_clients:
        pdf.bullet(c)
    pdf.body(
        "The MCP server requires no authentication for tool discovery "
        "(GET /mcp/tools) but requires a valid API key for tool execution."
    )

    # 16.4
    pdf.sub_title("16.4  A2A Protocol (Agent-to-Agent)")
    pdf.body(
        "AgenticOrg implements Google's Agent-to-Agent (A2A) protocol, "
        "enabling interoperability with other AI agent platforms."
    )
    a2a_eps = [
        "GET /a2a/agent-card -- Public agent card (no auth required). Returns capabilities, "
        "supported skills, and authentication requirements.",
        "GET /a2a/agents -- List available agents with their skills and domains",
        "POST /a2a/tasks -- Submit a task to a specific agent. Supports streaming responses.",
    ]
    for e in a2a_eps:
        pdf.bullet(e)
    pdf.ln(2)

    # 16.5
    pdf.sub_title("16.5  CLI")
    pdf.body("Command-line interface for scripting and automation:")
    cli_cmds = [
        "agenticorg agents list -- List all agents with status and domain",
        "agenticorg agents run <id> --task 'Generate monthly P&L' -- Run an agent",
        "agenticorg sop parse --file onboarding.txt -- Parse SOP into workflow",
        "agenticorg mcp tools -- List all MCP tools (340+)",
        "agenticorg workflows list -- List workflow templates",
        "agenticorg workflows trigger <id> --payload '{...}' -- Trigger a workflow",
    ]
    for c in cli_cmds:
        pdf.bullet(c)
    pdf.ln(2)

    pdf.sub_title("16.6  Webhook Endpoints")
    pdf.body(
        "AgenticOrg exposes webhook endpoints for receiving events from "
        "external systems. All webhooks verify request signatures to prevent "
        "tampering:"
    )
    webhook_eps = [
        ("POST", "/webhooks/email/sendgrid", "SendGrid event webhooks"),
        ("POST", "/webhooks/email/mailchimp", "Mailchimp event webhooks"),
        ("POST", "/webhooks/email/moengage", "MoEngage event webhooks"),
        ("POST", "/webhooks/stripe", "Stripe payment events"),
        ("POST", "/webhooks/github", "GitHub repository events"),
        ("POST", "/webhooks/slack/events", "Slack event subscriptions"),
        ("POST", "/webhooks/jira", "Jira issue events"),
        ("POST", "/webhooks/pagerduty", "PagerDuty incident events"),
        ("POST", "/webhooks/custom/{id}", "Custom workflow trigger webhooks"),
    ]
    cols_wh = [("Method", 18), ("Path", 62), ("Description", 110)]
    pdf.table_header(cols_wh)
    for i, (method, path, desc) in enumerate(webhook_eps):
        pdf.table_row([(method, 18), (path, 62), (desc, 110)],
                      shade=i % 2 == 1)
    pdf.ln(3)

    pdf.sub_title("16.7  Health & Status Endpoints")
    pdf.body("Public endpoints for monitoring and status:")
    health_eps = [
        "GET /health -- Returns {status: ok/degraded/down, version, uptime_seconds}",
        "GET /health/ready -- Kubernetes readiness probe (checks DB + Redis)",
        "GET /health/live -- Kubernetes liveness probe (process alive check)",
        "GET /mcp/tools -- List all MCP tools (340+, no auth required)",
        "GET /a2a/agent-card -- Public A2A agent card (no auth required)",
        "GET /openapi.json -- OpenAPI 3.1 specification",
    ]
    for h in health_eps:
        pdf.bullet(h)
    pdf.ln(2)


def sec17_errors(pdf: FuncSpecPdf) -> None:
    pdf.add_page()
    pdf.section_title("17", "Error Taxonomy")

    pdf.body(
        "AgenticOrg uses a structured error code system with 50 defined codes "
        "across 5 categories. Each error includes severity, retry policy, "
        "and escalation guidance."
    )

    # Tool errors E1xxx
    pdf.sub_title("17.1  Tool Errors (E1xxx)")
    tool_errors = [
        ("E1001", "TOOL_NOT_FOUND", "ERROR", "No", "0", "Log + alert ops"),
        ("E1002", "SCOPE_DENIED", "ERROR", "No", "0", "Alert admin"),
        ("E1003", "RATE_LIMITED", "WARN", "Yes", "3", "Backoff + retry"),
        ("E1004", "TIMEOUT", "ERROR", "Yes", "2", "Alert if repeated"),
        ("E1005", "CONNECTOR_ERROR", "ERROR", "Yes", "3", "Circuit breaker"),
        ("E1006", "AUTH_EXPIRED", "ERROR", "Yes", "1", "Refresh + retry"),
        ("E1007", "PII_VIOLATION", "CRITICAL", "No", "0", "Alert compliance"),
        ("E1008", "IDEMPOTENCY_HIT", "INFO", "No", "0", "Return cached"),
        ("E1009", "BUDGET_EXCEEDED", "ERROR", "No", "0", "Pause agent"),
        ("E1010", "CIRCUIT_OPEN", "ERROR", "Yes", "1", "Wait + retry"),
    ]
    cols_err = [("Code", 14), ("Name", 38), ("Sev", 16),
                ("Retry", 12), ("Max", 10), ("Escalation", 45)]
    pdf.table_header(cols_err)
    for i, row in enumerate(tool_errors):
        pdf.table_row(
            [(row[0], 14), (row[1], 38), (row[2], 16),
             (row[3], 12), (row[4], 10), (row[5], 45)],
            shade=i % 2 == 1,
        )
    pdf.ln(3)

    # Validation errors E2xxx
    pdf.sub_title("17.2  Validation Errors (E2xxx)")
    val_errors = [
        ("E2001", "INVALID_INPUT", "WARN", "No", "0", "Return 400"),
        ("E2002", "MISSING_FIELD", "WARN", "No", "0", "Return 400"),
        ("E2003", "TYPE_MISMATCH", "WARN", "No", "0", "Return 400"),
        ("E2004", "OUT_OF_RANGE", "WARN", "No", "0", "Return 400"),
        ("E2005", "DUPLICATE_ENTRY", "WARN", "No", "0", "Return 409"),
        ("E2006", "INVALID_FORMAT", "WARN", "No", "0", "Return 400"),
        ("E2007", "CONSTRAINT_FAIL", "ERROR", "No", "0", "Return 422"),
        ("E2008", "SCHEMA_MISMATCH", "ERROR", "No", "0", "Return 422"),
        ("E2009", "PAYLOAD_TOO_LARGE", "WARN", "No", "0", "Return 413"),
        ("E2010", "INVALID_ENUM", "WARN", "No", "0", "Return 400"),
    ]
    pdf.table_header(cols_err)
    for i, row in enumerate(val_errors):
        pdf.table_row(
            [(row[0], 14), (row[1], 38), (row[2], 16),
             (row[3], 12), (row[4], 10), (row[5], 45)],
            shade=i % 2 == 1,
        )
    pdf.ln(3)

    # Workflow errors E3xxx
    pdf.check_space(50)
    pdf.sub_title("17.3  Workflow Errors (E3xxx)")
    wf_errors = [
        ("E3001", "STEP_FAILED", "ERROR", "Yes", "3", "Retry or escalate"),
        ("E3002", "STEP_TIMEOUT", "ERROR", "Yes", "2", "Retry or skip"),
        ("E3003", "WORKFLOW_TIMEOUT", "CRITICAL", "No", "0", "Alert admin"),
        ("E3004", "HITL_TIMEOUT", "WARN", "No", "0", "Auto-escalate"),
        ("E3005", "HITL_REJECTED", "INFO", "No", "0", "Log + notify"),
        ("E3006", "PARALLEL_PARTIAL", "WARN", "Yes", "1", "Retry failed"),
        ("E3007", "CONDITION_ERROR", "ERROR", "No", "0", "Fail workflow"),
        ("E3008", "SUB_WF_FAILED", "ERROR", "Yes", "2", "Retry sub-wf"),
        ("E3009", "TRIGGER_FAILED", "ERROR", "Yes", "3", "Alert ops"),
        ("E3010", "STATE_CORRUPT", "CRITICAL", "No", "0", "Alert eng"),
    ]
    pdf.table_header(cols_err)
    for i, row in enumerate(wf_errors):
        pdf.table_row(
            [(row[0], 14), (row[1], 38), (row[2], 16),
             (row[3], 12), (row[4], 10), (row[5], 45)],
            shade=i % 2 == 1,
        )
    pdf.ln(3)

    # Auth errors E4xxx
    pdf.check_space(50)
    pdf.sub_title("17.4  Auth Errors (E4xxx)")
    auth_errors = [
        ("E4001", "INVALID_TOKEN", "ERROR", "No", "0", "Return 401"),
        ("E4002", "EXPIRED_TOKEN", "ERROR", "Yes", "1", "Refresh + retry"),
        ("E4003", "INSUFFICIENT_SCOPE", "ERROR", "No", "0", "Return 403"),
        ("E4004", "BLACKLISTED_TOKEN", "ERROR", "No", "0", "Return 401"),
        ("E4005", "RATE_LIMIT_AUTH", "WARN", "No", "0", "Block 15 min"),
        ("E4006", "INVALID_API_KEY", "ERROR", "No", "0", "Return 401"),
        ("E4007", "ACCOUNT_LOCKED", "ERROR", "No", "0", "Contact admin"),
        ("E4008", "SSO_FAILED", "ERROR", "Yes", "1", "Retry OAuth"),
        ("E4009", "MFA_REQUIRED", "INFO", "No", "0", "Prompt MFA"),
        ("E4010", "SESSION_EXPIRED", "WARN", "Yes", "1", "Re-login"),
    ]
    pdf.table_header(cols_err)
    for i, row in enumerate(auth_errors):
        pdf.table_row(
            [(row[0], 14), (row[1], 38), (row[2], 16),
             (row[3], 12), (row[4], 10), (row[5], 45)],
            shade=i % 2 == 1,
        )
    pdf.ln(3)

    # LLM errors E5xxx
    pdf.check_space(50)
    pdf.sub_title("17.5  LLM Errors (E5xxx)")
    llm_errors = [
        ("E5001", "LLM_TIMEOUT", "ERROR", "Yes", "2", "Retry + fallback"),
        ("E5002", "LLM_RATE_LIMIT", "WARN", "Yes", "3", "Backoff + retry"),
        ("E5003", "LLM_CONTENT_FILTER", "WARN", "No", "0", "Log + rephrase"),
        ("E5004", "LLM_INVALID_RESP", "ERROR", "Yes", "2", "Retry + parse"),
        ("E5005", "LLM_QUOTA_EXCEEDED", "CRITICAL", "No", "0", "Alert admin"),
    ]
    pdf.table_header(cols_err)
    for i, row in enumerate(llm_errors):
        pdf.table_row(
            [(row[0], 14), (row[1], 38), (row[2], 16),
             (row[3], 12), (row[4], 10), (row[5], 45)],
            shade=i % 2 == 1,
        )
    pdf.ln(3)

    pdf.body(
        "Total: 45 error codes defined above. Additional 5 codes (E1011-E1015) "
        "are reserved for future connector-specific errors, bringing the total "
        "to 50 allocated codes."
    )

    pdf.sub_title("17.6  Error Response Format")
    pdf.body("All API errors follow a standardized JSON response format:")
    pdf.code_block(
        '  {\n'
        '    "error": {\n'
        '      "code": "E1002",\n'
        '      "name": "SCOPE_DENIED",\n'
        '      "message": "Agent lacks permission for salesforce.delete_contact",\n'
        '      "severity": "ERROR",\n'
        '      "retryable": false,\n'
        '      "trace_id": "abc123-def456",\n'
        '      "timestamp": "2026-04-04T10:30:00Z"\n'
        '    }\n'
        '  }'
    )

    pdf.sub_title("17.7  Circuit Breaker Configuration")
    pdf.body(
        "External connector calls use circuit breakers to prevent cascading failures. "
        "Configuration per connector:"
    )
    cb_config = [
        "Failure threshold: 5 consecutive failures to open the circuit",
        "Recovery timeout: 30 seconds before attempting a half-open probe",
        "Half-open max: 2 successful calls to close the circuit",
        "Monitoring: Circuit state changes are logged and trigger alerts",
        "Scope: Circuit state is per-connector, per-tenant (not global)",
    ]
    for c in cb_config:
        pdf.bullet(c)
    pdf.ln(2)


def sec18_env_config(pdf: FuncSpecPdf) -> None:
    pdf.add_page()
    pdf.section_title("18", "Environment Configuration")

    pdf.body(
        "AgenticOrg is configured primarily through environment variables. "
        "All sensitive values should be stored in GCP Secret Manager for "
        "production deployments."
    )

    pdf.sub_title("18.1  Core Variables")
    core_vars = [
        ("AGENTICORG_ENV", "development", "Environment: development/staging/production"),
        ("AGENTICORG_SECRET_KEY", "(required)", "JWT signing key (HS256, 64+ chars)"),
        ("AGENTICORG_DB_URL", "(required)", "PostgreSQL connection string"),
        ("AGENTICORG_REDIS_URL", "(required)", "Redis connection string"),
        ("AGENTICORG_LOG_LEVEL", "INFO", "Logging level: DEBUG/INFO/WARNING/ERROR"),
    ]
    cols_env = [("Variable", 50), ("Default", 28), ("Description", 112)]
    pdf.table_header(cols_env)
    for i, (var, default, desc) in enumerate(core_vars):
        pdf.table_row([(var, 50), (default, 28), (desc, 112)],
                      shade=i % 2 == 1)
    pdf.ln(3)

    pdf.sub_title("18.2  LLM Provider Keys")
    llm_vars = [
        ("GOOGLE_GEMINI_API_KEY", "(required)", "Google Gemini API key"),
        ("ANTHROPIC_API_KEY", "(optional)", "Anthropic Claude API key"),
        ("OPENAI_API_KEY", "(optional)", "OpenAI GPT API key"),
    ]
    pdf.table_header(cols_env)
    for i, (var, default, desc) in enumerate(llm_vars):
        pdf.table_row([(var, 50), (default, 28), (desc, 112)],
                      shade=i % 2 == 1)
    pdf.ln(3)

    pdf.sub_title("18.3  Grantex Configuration")
    grantex_vars = [
        ("GRANTEX_API_KEY", "(required)", "Grantex service API key"),
        ("GRANTEX_BASE_URL", "https://api.grantex.dev", "Grantex API base URL"),
        ("GRANTEX_MANIFESTS_DIR", "(optional)", "Custom manifests directory path"),
    ]
    pdf.table_header(cols_env)
    for i, (var, default, desc) in enumerate(grantex_vars):
        pdf.table_row([(var, 50), (default, 28), (desc, 112)],
                      shade=i % 2 == 1)
    pdf.ln(3)

    pdf.sub_title("18.4  Connector-Specific Variables")
    pdf.body(
        "Each connector may require additional environment variables. "
        "The naming convention is: {CONNECTOR_NAME}_{CREDENTIAL_TYPE}. Examples:"
    )
    connector_vars = [
        ("HUBSPOT_API_KEY", "HubSpot private app token"),
        ("SALESFORCE_CLIENT_ID", "Salesforce OAuth client ID"),
        ("SALESFORCE_CLIENT_SECRET", "Salesforce OAuth client secret"),
        ("GOOGLE_ADS_DEVELOPER_TOKEN", "Google Ads API developer token"),
        ("META_ADS_ACCESS_TOKEN", "Meta Marketing API access token"),
        ("SLACK_BOT_TOKEN", "Slack Bot OAuth token (xoxb-)"),
        ("GITHUB_TOKEN", "GitHub personal access token"),
        ("JIRA_API_TOKEN", "Jira Cloud API token"),
        ("TALLY_BRIDGE_URL", "Tally Bridge server URL (local)"),
        ("GSTN_ADAEQUARE_KEY", "Adaequare API key for GSTN"),
        ("STRIPE_SECRET_KEY", "Stripe API secret key"),
        ("TWILIO_ACCOUNT_SID", "Twilio account SID"),
        ("TWILIO_AUTH_TOKEN", "Twilio auth token"),
        ("SENDGRID_API_KEY", "SendGrid API key"),
        ("DARWINBOX_API_KEY", "Darwinbox HRMS API key"),
        ("SERVICENOW_INSTANCE", "ServiceNow instance URL"),
        ("PAGERDUTY_API_KEY", "PagerDuty API key"),
        ("VAPID_PUBLIC_KEY", "VAPID public key for web push"),
        ("VAPID_PRIVATE_KEY", "VAPID private key for web push"),
    ]
    cols_cv = [("Variable", 60), ("Description", 130)]
    pdf.table_header(cols_cv)
    for i, (var, desc) in enumerate(connector_vars):
        pdf.table_row([(var, 60), (desc, 130)], shade=i % 2 == 1)
    pdf.ln(2)

    pdf.note_box(
        "Never commit secrets to source control. Use GCP Secret Manager "
        "references (gcp://projects/{project}/secrets/{name}/versions/latest) "
        "in production configuration files."
    )

    pdf.sub_title("18.5  Database Configuration")
    pdf.body(
        "PostgreSQL 15 is used as the primary database. Key configuration:"
    )
    db_config = [
        "Connection pooling: asyncpg with pool_size=20, max_overflow=10",
        "Statement timeout: 30 seconds (prevents long-running queries)",
        "Row-Level Security: Enabled on audit_log table (WORM policy)",
        "JSONB indexing: GIN indexes on JSONB columns for fast querying",
        "Migrations: Managed via Alembic with auto-generated migration scripts",
        "Backups: Cloud SQL automated backups every 4 hours, 7-day retention",
    ]
    for c in db_config:
        pdf.bullet(c)
    pdf.ln(2)

    pdf.sub_title("18.6  Redis Configuration")
    pdf.body(
        "Redis 7 (GCP Memorystore) is used for caching, rate limiting, "
        "and token blacklisting. Key settings:"
    )
    redis_config = [
        "Max memory: 1 GB (production), 256 MB (staging)",
        "Eviction policy: allkeys-lru (least recently used)",
        "Persistence: AOF enabled with everysec fsync",
        "Namespaces: rate_limit:*, token_blacklist:*, idempotency:*, cache:*",
        "TTLs: Rate limit counters (60s), token blacklist (3700s), idempotency (3600s)",
    ]
    for c in redis_config:
        pdf.bullet(c)
    pdf.ln(2)

    pdf.sub_title("18.7  Logging & Monitoring")
    pdf.body(
        "Structured JSON logging is used throughout the application. "
        "All logs include: timestamp, level, module, trace_id, tenant_id, "
        "and message. Logs are shipped to GCP Cloud Logging in production "
        "and can be queried via the Cloud Console or Logs Explorer."
    )
    monitoring = [
        "Application logs: FastAPI request/response logs, agent execution logs",
        "Audit logs: Append-only compliance logs (separate from application logs)",
        "Metrics: Custom Cloud Monitoring metrics for agent runs, tool calls, latency",
        "Alerts: PagerDuty integration for critical errors (E*007, E*003, E5005)",
        "Tracing: OpenTelemetry trace propagation through agent -> gateway -> connector",
        "Dashboards: Cloud Monitoring dashboards for SLI/SLO tracking",
    ]
    for m in monitoring:
        pdf.bullet(m)
    pdf.ln(2)


def appendix_version_history(pdf: FuncSpecPdf) -> None:
    pdf.add_page()
    pdf.section_title("A", "Version History")

    pdf.body(
        "This appendix documents the major releases of AgenticOrg from "
        "the initial v2.0 launch through the current v3.3.0 release."
    )

    versions = [
        ("v2.0.0", "2025-09-01", "Initial release: 15 agents, 20 connectors, basic workflows"),
        ("v2.1.0", "2025-11-15", "Added 5 agents, 10 connectors, HITL approvals, email notifications"),
        ("v2.2.0", "2026-01-10", "25 agents, 42 connectors (269 tools), enhanced dashboards"),
        ("v3.0.0", "2026-02-15", "LangGraph migration, A2A/MCP protocols, SOP-driven agents"),
        ("v3.1.0", "2026-03-01", "30 agents, 48 connectors, multi-company support, sales pipeline"),
        ("v3.2.0", "2026-04-03", "54 connectors, 35 agents, ABM/drip/A-B test, CI fully green"),
        ("v3.3.0", "2026-04-04", "Grantex scope enforcement, 53 manifests, scope dashboard, audit log"),
    ]

    cols_v = [("Version", 22), ("Date", 25), ("Changes", 143)]
    pdf.table_header(cols_v)
    for i, (ver, date, changes) in enumerate(versions):
        pdf.table_row([(ver, 22), (date, 25), (changes, 143)],
                      shade=i % 2 == 1)
    pdf.ln(5)

    pdf.body(
        "Each release follows semantic versioning (MAJOR.MINOR.PATCH). "
        "Major versions indicate breaking API changes, minor versions add "
        "features in a backward-compatible manner, and patch versions contain "
        "bug fixes only."
    )

    pdf.sub_title("A.1  v3.3.0 Release Highlights")
    pdf.body("The v3.3.0 release is a critical security and observability update:")
    v33_highlights = [
        "Replaced keyword-based permission guessing with Grantex SDK manifest-based enforcement",
        "Added validate_scopes node to LangGraph agent runtime (zero-latency inline check)",
        "53 pre-built permission manifests covering all connectors",
        "ToolGateway now calls grantex.enforce() before every tool execution",
        "Deprecated check_scope() function with DeprecationWarning",
        "New Scope Dashboard page (/dashboard/scopes) with real-time enforcement stats",
        "New Enforce Audit Log page (/dashboard/enforce-audit) with filtering and CSV export",
        "Updated AgentCreate form with permission badges from manifests",
        "Updated AgentDetail page with new Scopes tab",
        "Updated OrgChart with scope narrowing visualization",
        "JWKS cache pre-warmed at FastAPI startup for sub-ms enforcement latency",
        "New public page: How Grantex Works (/how-grantex-works)",
        "Landing page updated with v3.3.0 release banner",
        "29 new tests covering scope enforcement, manifest loading, and UI",
    ]
    for h in v33_highlights:
        pdf.bullet(h)
    pdf.ln(3)

    pdf.sub_title("A.2  Migration Guide (v3.2 -> v3.3)")
    pdf.body(
        "Upgrading from v3.2 to v3.3 requires the following steps:"
    )
    migration = [
        "1. Add GRANTEX_API_KEY to environment variables or GCP Secret Manager",
        "2. Set GRANTEX_BASE_URL if using a custom Grantex deployment",
        "3. Run database migrations (alembic upgrade head) -- no schema changes, config only",
        "4. Update agent configs to include grant_token field (optional, backward-compatible)",
        "5. Verify manifest loading at startup (check logs for '53 manifests loaded')",
        "6. Test scope enforcement with a shadow agent before enabling for production agents",
        "7. Monitor the Scope Dashboard for unexpected denials in the first 24 hours",
    ]
    for m in migration:
        pdf.bullet(m)
    pdf.ln(2)


def appendix_data_model(pdf: FuncSpecPdf) -> None:
    pdf.add_page()
    pdf.section_title("B", "Database Schema Overview")


    pdf.body(
        "This appendix provides a summary of all major database tables "
        "in the AgenticOrg PostgreSQL schema."
    )

    tables = [
        ("users", "User accounts", "id, email, password_hash, name, role, tenant_id, is_active"),
        ("tenants", "Multi-tenant orgs", "id, name, domain, plan, settings, created_at"),
        ("companies", "Multi-company", "id, tenant_id, name, display_name, industry, domain"),
        ("agents", "AI agents", "id, tenant_id, name, type, domain, status, config (27 fields)"),
        ("agent_cost_ledger", "Cost tracking", "agent_id, month, total_cost, llm_cost, tool_cost"),
        ("agent_prompt_history", "Prompt versions", "agent_id, version, prompt_text, created_at"),
        ("connectors", "Connector configs", "id, tenant_id, name, category, auth_config, status"),
        ("tool_executions", "Tool audit log", "id, tool_name, agent_id, status, latency_ms"),
        ("workflow_definitions", "Workflow blueprints", "id, name, version, definition, trigger_type"),
        ("workflow_runs", "Workflow instances", "id, workflow_id, status, context, result"),
        ("step_executions", "Workflow steps", "id, run_id, step_id, type, status, input, output"),
        ("approvals", "HITL queue", "id, agent_id, trigger_reason, status, proposed_action"),
        ("audit_log", "Compliance log", "id, actor, action, resource, details, hmac_sig"),
        ("leads", "Sales pipeline", "id, name, email, stage, score, deal_value, utm_*"),
        ("scheduled_reports", "Report schedules", "id, report_type, cron, channels, format"),
        ("api_keys", "Developer keys", "id, user_id, key_hash, prefix, last_used_at"),
        ("team_invitations", "Team invites", "id, email, role, token, accepted_at"),
        ("push_subscriptions", "Web push", "id, user_id, endpoint, keys, created_at"),
        ("dsar_requests", "DSAR tracking", "id, email, type, status, deadline"),
        ("enforce_audit_log", "Grantex audit", "id, agent_id, tool, permission, granted, latency"),
    ]

    cols_db = [("Table", 40), ("Purpose", 35), ("Key Columns", 115)]
    pdf.table_header(cols_db)
    for i, (table, purpose, columns) in enumerate(tables):
        pdf.table_row([(table, 40), (purpose, 35), (columns, 115)],
                      shade=i % 2 == 1)
    pdf.ln(3)

    pdf.body(
        "All tables include created_at and updated_at timestamp columns (except "
        "audit_log which only has created_at due to WORM policy). Foreign keys "
        "use UUID references with CASCADE on tenant deletion. JSONB columns "
        "have GIN indexes for efficient querying."
    )

    pdf.sub_title("B.1  Index Strategy")
    pdf.body("Key indexes for query performance:")
    indexes = [
        "agents: (tenant_id, status), (tenant_id, domain), (parent_agent_id)",
        "tool_executions: (agent_id, created_at), (tenant_id, created_at), (tool_name)",
        "workflow_runs: (workflow_id, status), (tenant_id, created_at)",
        "approvals: (tenant_id, status), (agent_id, created_at)",
        "audit_log: (tenant_id, created_at), (actor_id, created_at), (action_type)",
        "leads: (tenant_id, stage), (tenant_id, score), (email UNIQUE per tenant)",
        "enforce_audit_log: (tenant_id, created_at), (agent_id), (granted)",
    ]
    for idx in indexes:
        pdf.bullet(idx)
    pdf.ln(2)


def appendix_glossary(pdf: FuncSpecPdf) -> None:
    pdf.add_page()
    pdf.section_title("C", "Glossary of Terms")

    terms = [
        ("A2A", "Agent-to-Agent protocol by Google for inter-agent communication"),
        ("ABM", "Account-Based Marketing -- targeting specific high-value accounts"),
        ("BANT", "Budget, Authority, Need, Timeline -- lead qualification framework"),
        ("CAC", "Customer Acquisition Cost"),
        ("DPDPA", "Digital Personal Data Protection Act (India, 2023)"),
        ("DSO", "Days Sales Outstanding -- accounts receivable collection metric"),
        ("DPO", "Days Payable Outstanding -- accounts payable timing metric"),
        ("DSAR", "Data Subject Access Request (GDPR Art. 15)"),
        ("EPFO", "Employees' Provident Fund Organisation (India)"),
        ("GDPR", "General Data Protection Regulation (EU)"),
        ("GSTN", "Goods and Services Tax Network (India)"),
        ("HITL", "Human-in-the-Loop -- human review of AI agent decisions"),
        ("HMAC", "Hash-based Message Authentication Code"),
        ("JWKS", "JSON Web Key Set -- public key endpoint for JWT verification"),
        ("JWT", "JSON Web Token -- compact, URL-safe authentication token"),
        ("LangGraph", "LangChain's graph-based agent orchestration framework"),
        ("MCA", "Ministry of Corporate Affairs (India)"),
        ("MCP", "Model Context Protocol -- tool integration standard for AI"),
        ("MQL", "Marketing Qualified Lead"),
        ("PII", "Personally Identifiable Information"),
        ("RBAC", "Role-Based Access Control"),
        ("RBI AA", "Reserve Bank of India Account Aggregator framework"),
        ("ROAS", "Return on Ad Spend"),
        ("RLS", "Row-Level Security (PostgreSQL)"),
        ("SOP", "Standard Operating Procedure"),
        ("SQL (lead)", "Sales Qualified Lead"),
        ("VAPID", "Voluntary Application Server Identification for web push"),
        ("WORM", "Write Once Read Many -- immutable storage pattern"),
    ]

    cols_gl = [("Term", 28), ("Definition", 162)]
    pdf.table_header(cols_gl)
    for i, (term, defn) in enumerate(terms):
        pdf.table_row([(term, 28), (defn, 162)], shade=i % 2 == 1)
    pdf.ln(3)


def appendix_signoff(pdf: FuncSpecPdf) -> None:
    pdf.add_page()
    pdf.section_title("D", "Document Sign-off")

    pdf.body(
        "This Functional Specification Document has been reviewed and approved "
        "by the following stakeholders. By signing below, each reviewer confirms "
        "that the documented features, APIs, and behaviors accurately reflect "
        "the production system as of v3.3.0."
    )
    pdf.ln(5)

    signoffs = [
        ("Sanjeev Kumar", "CEO & Founder", "Product Owner"),
        ("Engineering Lead", "VP Engineering", "Technical Review"),
        ("QA Lead", "Director of QA", "Test Coverage Review"),
        ("Security Lead", "CISO", "Security & Compliance Review"),
        ("DevOps Lead", "Sr. DevOps Engineer", "Infrastructure Review"),
    ]

    for name, title, role in signoffs:
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_text_color(30, 30, 30)
        pdf.cell(60, 8, name)
        pdf.set_font("Helvetica", "", 11)
        pdf.cell(50, 8, title)
        pdf.cell(50, 8, role)
        pdf.ln()
        pdf.set_draw_color(150, 150, 150)
        pdf.cell(60, 8, "Signature: ________________")
        pdf.cell(50, 8, "")
        pdf.cell(50, 8, f"Date: {DATE}")
        pdf.ln(12)

    pdf.ln(5)

    pdf.sub_title("Review History")
    reviews = [
        ("2026-04-04", "Initial draft", "Engineering", "Draft"),
        ("2026-04-04", "Technical review", "VP Engineering", "Reviewed"),
        ("2026-04-04", "Security review", "CISO", "Approved"),
        ("2026-04-04", "Final approval", "CEO", "Approved"),
    ]
    cols_rh = [("Date", 28), ("Action", 45), ("Reviewer", 45), ("Status", 30)]
    pdf.table_header(cols_rh)
    for i, (date, action, reviewer, status) in enumerate(reviews):
        pdf.table_row(
            [(date, 28), (action, 45), (reviewer, 45), (status, 30)],
            shade=i % 2 == 1,
        )

    pdf.ln(10)
    pdf.set_draw_color(25, 60, 120)
    pdf.set_line_width(0.5)
    pdf.line(60, pdf.get_y(), 150, pdf.get_y())
    pdf.ln(5)
    pdf.set_font("Helvetica", "I", 10)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(0, 7, "End of Document", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 7, f"AgenticOrg Functional Specification v{VERSION}",
             align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 7, "CONFIDENTIAL", align="C")


# =============================================================================
# MAIN
# =============================================================================

def build_pdf() -> FuncSpecPdf:
    pdf = FuncSpecPdf()
    pdf.alias_nb_pages()

    # Cover + TOC
    pdf.cover_page()
    pdf.toc_page()

    # All 18 sections
    sec01_product_overview(pdf)
    sec02_auth(pdf)
    sec03_grantex(pdf)
    sec04_agents(pdf)
    sec05_langgraph(pdf)
    sec06_connectors(pdf)
    sec07_tool_gateway(pdf)
    sec08_workflows(pdf)
    sec09_hitl(pdf)
    sec10_dashboards(pdf)
    sec11_marketing(pdf)
    sec12_sales(pdf)
    sec13_multi_company(pdf)
    sec14_scheduled_reports(pdf)
    sec15_compliance(pdf)
    sec16_integrations(pdf)
    sec17_errors(pdf)
    sec18_env_config(pdf)

    # Appendices
    appendix_version_history(pdf)
    appendix_data_model(pdf)
    appendix_glossary(pdf)
    appendix_signoff(pdf)

    return pdf


def main() -> None:
    out_dir = os.path.join(os.path.dirname(__file__), "..", "docs")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"AgenticOrg_FunctionalSpec_v{VERSION}.pdf")

    pdf = build_pdf()
    pdf.output(out_path)
    print(f"Generated: {out_path}")
    print(f"Pages: {pdf.pages_count}")


if __name__ == "__main__":
    main()
