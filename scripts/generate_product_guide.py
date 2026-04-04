"""Generate AgenticOrg Product Explainer Guide PDF.

Visual, detailed, designed for anyone to understand the product end-to-end.
Output: docs/AgenticOrg_Product_Guide_v3.3.0.pdf
"""

from __future__ import annotations

import datetime
import os
from fpdf import FPDF

VERSION = "3.3.0"
DATE = datetime.date.today().strftime("%B %d, %Y")

# ── Color palette ─────────────────────────────────────────────────────────
C_NAVY = (20, 40, 80)
C_BLUE = (30, 100, 200)
C_LIGHT_BLUE = (220, 235, 255)
C_GREEN = (34, 139, 34)
C_LIGHT_GREEN = (220, 245, 220)
C_ORANGE = (220, 120, 20)
C_LIGHT_ORANGE = (255, 240, 220)
C_RED = (200, 40, 40)
C_LIGHT_RED = (255, 225, 225)
C_PURPLE = (100, 40, 160)
C_LIGHT_PURPLE = (240, 230, 255)
C_GRAY = (100, 100, 100)
C_LIGHT_GRAY = (245, 245, 250)
C_WHITE = (255, 255, 255)
C_BLACK = (30, 30, 30)
C_TEAL = (0, 128, 128)
C_LIGHT_TEAL = (220, 245, 245)


class GuidePdf(FPDF):
    def __init__(self):
        super().__init__()
        self.set_auto_page_break(auto=True, margin=22)

    def header(self):
        if self.page_no() == 1:
            return
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(*C_GRAY)
        self.cell(0, 5, "AgenticOrg Product Guide", align="L")
        self.cell(0, 5, f"v{VERSION}", align="R", new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(200, 200, 200)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(3)

    def footer(self):
        if self.page_no() == 1:
            return
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(*C_GRAY)
        self.cell(0, 10, f"Page {self.page_no()}", align="C")

    # ── Drawing helpers ──

    def colored_box(self, x, y, w, h, fill, border, text, text_color=C_WHITE, font_size=9, bold=True):
        self.set_fill_color(*fill)
        self.set_draw_color(*border)
        self.set_line_width(0.4)
        self.rect(x, y, w, h, style="DF")
        self.set_font("Helvetica", "B" if bold else "", font_size)
        self.set_text_color(*text_color)
        self.set_xy(x, y + (h - font_size * 0.35) / 2 - 1)
        self.cell(w, font_size * 0.35, text, align="C")

    def arrow_right(self, x1, y, x2):
        self.set_draw_color(*C_GRAY)
        self.set_line_width(0.5)
        self.line(x1, y, x2, y)
        self.line(x2 - 2, y - 1.5, x2, y)
        self.line(x2 - 2, y + 1.5, x2, y)

    def arrow_down(self, x, y1, y2):
        self.set_draw_color(*C_GRAY)
        self.set_line_width(0.5)
        self.line(x, y1, x, y2)
        self.line(x - 1.5, y2 - 2, x, y2)
        self.line(x + 1.5, y2 - 2, x, y2)

    def section_title(self, num, text, color=C_NAVY):
        self.set_font("Helvetica", "B", 16)
        self.set_text_color(*color)
        self.cell(0, 10, f"{num}. {text}" if num else text, new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(*color)
        self.set_line_width(0.6)
        self.line(10, self.get_y(), 80, self.get_y())
        self.ln(4)

    def sub_title(self, text, color=C_BLACK):
        self.set_font("Helvetica", "B", 12)
        self.set_text_color(*color)
        self.cell(0, 8, text, new_x="LMARGIN", new_y="NEXT")
        self.ln(1)

    def body(self, text):
        self.set_font("Helvetica", "", 10)
        self.set_text_color(*C_BLACK)
        self.multi_cell(0, 5.5, text)
        self.ln(2)

    def bullet(self, text, indent=10):
        self.set_font("Helvetica", "", 10)
        self.set_text_color(*C_BLACK)
        self.set_x(10 + indent)
        self.multi_cell(190 - indent, 5.5, f"- {text}")

    def bold_bullet(self, label, text, indent=10):
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(*C_BLACK)
        self.set_x(10 + indent)
        self.multi_cell(190 - indent, 5, f"- {label}: {text}")

    def stat_card(self, x, y, w, h, number, label, color):
        self.set_fill_color(*color)
        self.rect(x, y, w, h, style="F")
        self.set_fill_color(255, 255, 255, )
        self.rect(x + 2, y + 2, w - 4, h - 4, style="F")
        self.set_font("Helvetica", "B", 18)
        self.set_text_color(*color)
        self.set_xy(x, y + 5)
        self.cell(w, 8, str(number), align="C")
        self.set_font("Helvetica", "", 8)
        self.set_text_color(*C_GRAY)
        self.set_xy(x, y + 15)
        self.cell(w, 5, label, align="C")

    def info_card(self, x, y, w, h, title, items, bg_color, border_color, title_color=C_WHITE):
        self.set_fill_color(*bg_color)
        self.set_draw_color(*border_color)
        self.set_line_width(0.5)
        self.rect(x, y, w, h, style="DF")
        # title bar
        self.set_fill_color(*border_color)
        self.rect(x, y, w, 8, style="F")
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(*title_color)
        self.set_xy(x, y + 1)
        self.cell(w, 6, title, align="C")
        # items
        self.set_font("Helvetica", "", 8)
        self.set_text_color(*C_BLACK)
        cy = y + 11
        for item in items:
            self.set_xy(x + 3, cy)
            self.cell(w - 6, 4, f"- {item}")
            cy += 4.5


def build_guide() -> GuidePdf:
    pdf = GuidePdf()

    # ═══════════════════════════════════════════════════════════════════════
    # COVER PAGE
    # ═══════════════════════════════════════════════════════════════════════
    pdf.add_page()
    # Navy background
    pdf.set_fill_color(*C_NAVY)
    pdf.rect(0, 0, 210, 297, style="F")

    # White accent bar
    pdf.set_fill_color(255, 255, 255)
    pdf.rect(15, 80, 180, 0.8, style="F")
    pdf.rect(15, 185, 180, 0.4, style="F")

    # Title
    pdf.set_font("Helvetica", "B", 36)
    pdf.set_text_color(255, 255, 255)
    pdf.set_xy(15, 90)
    pdf.cell(180, 18, "AgenticOrg", align="C")
    pdf.set_font("Helvetica", "", 16)
    pdf.set_xy(15, 110)
    pdf.cell(180, 10, "AI Virtual Employee Platform", align="C")
    pdf.set_font("Helvetica", "I", 12)
    pdf.set_text_color(180, 200, 255)
    pdf.set_xy(15, 125)
    pdf.cell(180, 8, "Complete Product Guide", align="C")

    # Stats row
    stats = [("35", "AI Agents"), ("54", "Connectors"), ("340+", "Tools"), ("15", "Workflows")]
    sx = 30
    for num, label in stats:
        pdf.set_font("Helvetica", "B", 22)
        pdf.set_text_color(100, 180, 255)
        pdf.set_xy(sx, 145)
        pdf.cell(35, 10, num, align="C")
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(180, 200, 255)
        pdf.set_xy(sx, 157)
        pdf.cell(35, 6, label, align="C")
        sx += 40

    # Version info
    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(180, 200, 255)
    pdf.set_xy(15, 195)
    pdf.cell(180, 7, f"Version {VERSION}  |  {DATE}", align="C")
    pdf.set_xy(15, 205)
    pdf.cell(180, 7, "Confidential - For Internal Use", align="C")

    # ═══════════════════════════════════════════════════════════════════════
    # TABLE OF CONTENTS
    # ═══════════════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.section_title("", "Table of Contents")
    toc = [
        ("1", "What is AgenticOrg?"),
        ("2", "How It Works - The Big Picture"),
        ("3", "The 6 Domains & 35 AI Agents"),
        ("4", "54 Connectors & 340+ Tools"),
        ("5", "Workflow Engine - Automating Business Processes"),
        ("6", "Security & Grantex Scope Enforcement"),
        ("7", "Human-in-the-Loop (HITL) Governance"),
        ("8", "Dashboards & Intelligence"),
        ("9", "Marketing Automation Suite"),
        ("10", "No-Code Agent Creator"),
        ("11", "Organization Chart & Delegation"),
        ("12", "External Integrations (SDK / MCP / A2A)"),
        ("13", "Landing Page & SEO"),
        ("14", "Platform at a Glance"),
    ]
    for num, title in toc:
        pdf.set_font("Helvetica", "", 11)
        pdf.set_text_color(*C_BLACK)
        pdf.cell(12, 7, num + ".")
        pdf.cell(0, 7, title, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(5)

    # ═══════════════════════════════════════════════════════════════════════
    # 1. WHAT IS AGENTICORG
    # ═══════════════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.section_title("1", "What is AgenticOrg?")
    pdf.body(
        "AgenticOrg is an AI-powered Virtual Employee Platform. Instead of hiring "
        "separate people for every repetitive business task, AgenticOrg deploys AI agents "
        "that work like real employees - each with a name, a role, specific tools, and "
        "clear boundaries on what they can and cannot do."
    )
    pdf.body(
        "Think of it as hiring an entire team of AI specialists that work 24/7, "
        "never make data entry errors, escalate to humans when unsure, and follow "
        "your company's exact standard operating procedures."
    )

    pdf.sub_title("The Problem We Solve")
    problems = [
        ("Manual data entry", "Invoices, reconciliation, payroll take hours of copy-paste work"),
        ("Siloed tools", "Your CRM, ERP, email, Jira don't talk to each other automatically"),
        ("Slow decisions", "Approval chains add days; by the time data reaches the CFO, it's stale"),
        ("Compliance gaps", "Humans skip steps under pressure; audit trails are incomplete"),
        ("Scaling bottleneck", "Hiring and training takes months; AI agents deploy in minutes"),
    ]
    for label, desc in problems:
        pdf.bold_bullet(label, desc)
    pdf.ln(3)

    pdf.sub_title("The AgenticOrg Solution")
    pdf.body(
        "Deploy 35 pre-built AI agents across Finance, HR, Marketing, Operations, Back Office, "
        "and Communications. Each agent connects to your real systems (Oracle, SAP, HubSpot, "
        "Jira, Gmail...) via 54 connectors with 340+ tools. Every action is audited, "
        "every permission is enforced, and humans stay in control."
    )

    # Visual: Before vs After
    y = pdf.get_y() + 5
    pdf.set_font("Helvetica", "B", 10)

    # Before box
    pdf.set_text_color(*C_RED)
    pdf.set_xy(12, y)
    pdf.cell(88, 7, "BEFORE: Manual Process", align="C")
    pdf.set_fill_color(*C_LIGHT_RED)
    pdf.set_draw_color(*C_RED)
    pdf.rect(12, y + 8, 88, 35, style="DF")
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*C_BLACK)
    items_before = [
        "  72 hours for month-end close",
        "  11 seconds per invoice (manual entry)",
        "  60% of HR time on repetitive tasks",
        "  Keyword-guessing for permissions",
        "  No real-time visibility for CXOs",
    ]
    cy = y + 10
    for item in items_before:
        pdf.set_xy(14, cy)
        pdf.cell(84, 4, f"x {item}", align="L")
        cy += 5.5

    # After box
    pdf.set_text_color(*C_GREEN)
    pdf.set_xy(110, y)
    pdf.cell(88, 7, "AFTER: AgenticOrg", align="C")
    pdf.set_fill_color(*C_LIGHT_GREEN)
    pdf.set_draw_color(*C_GREEN)
    pdf.rect(110, y + 8, 88, 35, style="DF")
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*C_BLACK)
    items_after = [
        "  1.5 days for month-end close",
        "  0.3 seconds per invoice (AI OCR)",
        "  AI handles 80% of HR admin",
        "  Manifest-based scope enforcement",
        "  Real-time CFO/CMO dashboards",
    ]
    cy = y + 10
    for item in items_after:
        pdf.set_xy(112, cy)
        pdf.cell(84, 4, f"+ {item}", align="L")
        cy += 5.5

    pdf.set_y(y + 50)

    # ═══════════════════════════════════════════════════════════════════════
    # 2. HOW IT WORKS
    # ═══════════════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.section_title("2", "How It Works - The Big Picture")
    pdf.body(
        "When a task arrives (from a user, a schedule, or an event), AgenticOrg routes it "
        "to the right AI agent. The agent thinks using an LLM (Gemini, Claude, or GPT), "
        "calls real tools on real systems, and produces a result. If the agent is unsure, "
        "it pauses and asks a human. Everything is logged."
    )

    # VISUAL FLOW: 8-layer architecture
    pdf.sub_title("The 8-Layer Architecture")
    y = pdf.get_y() + 2
    layers = [
        ("L8: Auth & Compliance", C_RED, "Grantex OAuth2, JWT, Scope Enforcement, WORM Audit"),
        ("L7: Observability", C_PURPLE, "OpenTelemetry, Prometheus, LangSmith, Alerting"),
        ("L6: Data Layer", C_TEAL, "PostgreSQL + pgvector, Redis 7, S3 Storage"),
        ("L5: Connector Layer", C_ORANGE, "54 Connectors with 340+ Tools (Finance, HR, Marketing, Ops, Comms)"),
        ("L4: Tool Gateway", C_PURPLE, "Scope Enforcement, Rate Limiting, PII Masking, Audit Logging"),
        ("L3: Orchestrator", C_BLUE, "Task Routing, Workflows, Conflict Resolution, Checkpointing"),
        ("L2: Agent Layer", C_GREEN, "35 Specialist Agents, Confidence Scoring, HITL Triggers"),
        ("L1: LLM Backbone", C_GRAY, "Gemini 2.5 Flash (primary), Claude/GPT (fallback), Cost Tracking"),
    ]
    for i, (name, color, desc) in enumerate(layers):
        ly = y + i * 14
        pdf.set_fill_color(*color)
        pdf.rect(12, ly, 45, 12, style="F")
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_text_color(*C_WHITE)
        pdf.set_xy(12, ly + 2)
        pdf.cell(45, 8, name, align="C")
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(*C_BLACK)
        pdf.set_xy(60, ly + 2)
        pdf.cell(138, 8, desc)

    pdf.set_y(y + 8 * 14 + 5)

    # VISUAL FLOW: Request lifecycle
    pdf.sub_title("Request Lifecycle (Visual Flow)")
    y = pdf.get_y() + 3

    flow_boxes = [
        (12, "Task Arrives", C_BLUE, C_WHITE),
        (50, "Agent Thinks\n(LLM)", C_GREEN, C_WHITE),
        (88, "Scope Check\n(Grantex)", C_RED, C_WHITE),
        (126, "Tool Executes\n(Connector)", C_ORANGE, C_WHITE),
        (164, "Result\nReturned", C_TEAL, C_WHITE),
    ]
    for bx, label, color, tc in flow_boxes:
        pdf.set_fill_color(*color)
        pdf.set_draw_color(*color)
        pdf.rect(bx, y, 32, 16, style="DF")
        pdf.set_font("Helvetica", "B", 7)
        pdf.set_text_color(*tc)
        lines = label.split("\n")
        for j, ln in enumerate(lines):
            pdf.set_xy(bx, y + 3 + j * 5)
            pdf.cell(32, 5, ln, align="C")

    # Arrows between boxes
    for bx in [44, 82, 120, 158]:
        pdf.arrow_right(bx, y + 8, bx + 6)

    # HITL branch
    pdf.set_font("Helvetica", "I", 7)
    pdf.set_text_color(*C_PURPLE)
    pdf.set_xy(88, y + 18)
    pdf.cell(32, 5, "Low confidence?", align="C")
    pdf.arrow_down(104, y + 16, y + 28)
    pdf.set_fill_color(*C_PURPLE)
    pdf.rect(88, y + 28, 32, 10, style="F")
    pdf.set_font("Helvetica", "B", 7)
    pdf.set_text_color(*C_WHITE)
    pdf.set_xy(88, y + 29)
    pdf.cell(32, 8, "Human Approves", align="C")

    pdf.set_y(y + 45)

    # ═══════════════════════════════════════════════════════════════════════
    # 3. DOMAINS & AGENTS
    # ═══════════════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.section_title("3", "The 6 Domains & 35 AI Agents")
    pdf.body(
        "AgenticOrg organizes agents into 6 business domains. Each agent has a name, "
        "personality, specific tools, and knows exactly what it's allowed to do."
    )

    domains = [
        ("Finance (10 Agents)", C_GREEN, C_LIGHT_GREEN, [
            "AP Processor - Invoice OCR, GSTIN match, 3-way match, payment",
            "AR Collections - Invoice creation, payment links, follow-up",
            "Reconciliation Agent - Bank statement matching, variance analysis",
            "Tax Compliance - GSTR filing, TDS, advance tax, ITC reconciliation",
            "Close Agent - Month-end close (trial balance to close)",
            "FP&A Agent - Forecasting, variance, scenario modeling",
            "Treasury - Cash position, sweep, forecast, reports",
            "Expense Manager - Receipt OCR, policy check, reimbursement",
            "Rev Rec (ASC 606) - Revenue allocation, performance obligations",
            "Fixed Assets - Depreciation, impairment, disposal",
        ]),
        ("HR (6 Agents)", C_PURPLE, C_LIGHT_PURPLE, [
            "Talent Acquisition - Job posting, candidate search, offers",
            "Onboarding Agent - Darwinbox + Okta + Slack provisioning",
            "Payroll Engine - Payroll computation, ECR, 24Q/26Q",
            "Performance Coach - Feedback, goal tracking, reviews",
            "L&D Coordinator - Training coordination, courses",
            "Offboarding Agent - Exit, access removal, final payouts",
        ]),
        ("Marketing (9 Agents)", C_ORANGE, C_LIGHT_ORANGE, [
            "Content Factory - Blog, social posts, video scripts",
            "Campaign Pilot - Campaign creation, budget optimization",
            "SEO Strategist - Keyword research, technical SEO",
            "CRM Intelligence - HubSpot analysis, lead scoring",
            "Brand Monitor - Social listening, sentiment tracking",
            "Email Marketing - Campaigns, A/B testing, segmentation",
            "Social Media - Scheduling, engagement, analytics",
            "ABM Agent - Account targeting, intent scoring",
            "Competitive Intel - Competitor monitoring, pricing analysis",
        ]),
        ("Operations (5 Agents)", C_BLUE, C_LIGHT_BLUE, [
            "Support Triage - Ticket classification, SLA monitoring",
            "Contract Intelligence - Clause analysis, risk flags",
            "Compliance Guard - Sanctions screening, regulatory checks",
            "IT Operations - Incident response, system health",
            "Vendor Manager - Evaluation, negotiation, performance",
        ]),
        ("Back Office (3 Agents)", C_TEAL, C_LIGHT_TEAL, [
            "Risk Sentinel - Risk assessment, compliance audits",
            "Legal Ops - Document management, legal research",
            "Facilities Agent - Space management, procurement",
        ]),
        ("Communications (2 Agents + Sales)", C_GRAY, C_LIGHT_GRAY, [
            "Ops Commander - Jira triage, severity, assignment",
            "DevOps Scout - GitHub + Jira health, deployment watch",
            "Sales Agent - Lead qualification, email sequences, pipeline",
        ]),
    ]

    for name, color, bg, agents in domains:
        if pdf.get_y() > 230:
            pdf.add_page()
        pdf.sub_title(name, color)
        for agent in agents:
            pdf.bullet(agent, indent=5)
        pdf.ln(3)

    # ═══════════════════════════════════════════════════════════════════════
    # 4. CONNECTORS
    # ═══════════════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.section_title("4", "54 Connectors & 340+ Tools")
    pdf.body(
        "Connectors are bridges between AI agents and real external systems. Each connector "
        "provides tools (API actions) that agents can call. All 54 connectors have real API "
        "endpoints - zero stubs."
    )

    connector_groups = [
        ("Finance (11)", C_GREEN, [
            "Oracle Fusion, SAP, Tally Prime (XML/TDL + Bridge),",
            "GSTN (India tax), QuickBooks, Zoho Books,",
            "Banking AA (RBI-compliant), Income Tax India,",
            "Stripe, PineLabs Plural, NetSuite",
        ]),
        ("HR (8)", C_PURPLE, [
            "Darwinbox, Okta, Greenhouse, LinkedIn Talent,",
            "DocuSign, Keka, Zoom, EPFO (India PF)",
        ]),
        ("Marketing (19)", C_ORANGE, [
            "HubSpot, Salesforce, Google Ads, Meta Ads,",
            "LinkedIn Ads, Ahrefs, GA4, Mixpanel,",
            "Mailchimp, MoEngage, Buffer, Brandwatch,",
            "WordPress, Twitter/X, YouTube, SendGrid,",
            "Bombora, G2, TrustRadius (intent data)",
        ]),
        ("Ops (7)", C_BLUE, [
            "Jira, ServiceNow, Zendesk, PagerDuty,",
            "Confluence, Sanctions API, MCA Portal (India)",
        ]),
        ("Comms (9)", C_TEAL, [
            "Slack, GitHub, Gmail, Google Calendar,",
            "Twilio, WhatsApp, LangSmith, AWS S3, SendGrid",
        ]),
    ]

    for name, color, lines in connector_groups:
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(*color)
        pdf.cell(0, 6, name, new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(*C_BLACK)
        for line in lines:
            pdf.set_x(20)
            pdf.cell(0, 5, line, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)

    # How a connector works - visual
    pdf.ln(3)
    pdf.sub_title("How a Connector Works")
    y = pdf.get_y() + 2
    steps = [
        (15, "Agent decides\nit needs data", C_GREEN),
        (60, "Tool Gateway\nchecks scope", C_RED),
        (105, "Connector calls\nreal API", C_ORANGE),
        (150, "Result returned\n(PII masked)", C_TEAL),
    ]
    for bx, label, color in steps:
        pdf.set_fill_color(*color)
        pdf.rect(bx, y, 38, 16, style="F")
        pdf.set_font("Helvetica", "B", 7)
        pdf.set_text_color(*C_WHITE)
        lines = label.split("\n")
        for j, ln in enumerate(lines):
            pdf.set_xy(bx, y + 2 + j * 5)
            pdf.cell(38, 5, ln, align="C")
    for bx in [53, 98, 143]:
        pdf.arrow_right(bx, y + 8, bx + 7)

    pdf.set_y(y + 25)
    pdf.body(
        "Example: AP Processor agent needs to check a bank statement. It calls "
        "fetch_bank_statement on the Banking AA connector. The Tool Gateway first verifies "
        "the agent has 'tool:banking_aa:read:*' scope via Grantex, then the connector calls "
        "the real AA API with RBI-compliant consent flow. Result is PII-masked before logging."
    )

    # ═══════════════════════════════════════════════════════════════════════
    # 5. WORKFLOW ENGINE
    # ═══════════════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.section_title("5", "Workflow Engine - Automating Business Processes")
    pdf.body(
        "Workflows chain multiple agents together to complete complex business processes. "
        "AgenticOrg ships 15 pre-built workflow templates that can be customized or extended."
    )

    # Visual: Invoice-to-Pay workflow
    pdf.sub_title("Example: Invoice-to-Pay Workflow")
    y = pdf.get_y() + 3
    wf_steps = [
        ("1. Email arrives\nwith invoice PDF", C_BLUE),
        ("2. AP Processor\nOCR extracts data", C_GREEN),
        ("3. GSTIN validated\non GSTN portal", C_ORANGE),
        ("4. 3-way match\n(PO + GRN + Invoice)", C_PURPLE),
        ("5. Amount > 5L?\nHITL approval", C_RED),
        ("6. PineLabs\npayment executed", C_TEAL),
    ]
    bx = 10
    for label, color in wf_steps:
        pdf.set_fill_color(*color)
        pdf.rect(bx, y, 30, 22, style="F")
        pdf.set_font("Helvetica", "B", 6)
        pdf.set_text_color(*C_WHITE)
        lines = label.split("\n")
        for j, ln in enumerate(lines):
            pdf.set_xy(bx, y + 3 + j * 5)
            pdf.cell(30, 5, ln, align="C")
        if bx < 160:
            pdf.arrow_right(bx + 30, y + 11, bx + 33)
        bx += 33

    pdf.set_y(y + 28)

    pdf.sub_title("All 15 Workflow Templates")
    templates = [
        ("Finance", "invoice_to_pay, month_end_close, daily_treasury, tax_calendar"),
        ("Marketing", "campaign_launch, content_pipeline, lead_nurture, email_drip, ab_test, abm_campaign, weekly_report"),
        ("Operations", "incident_response, weekly_devops_health, support_triage"),
        ("HR", "employee_onboarding"),
    ]
    for domain, wfs in templates:
        pdf.bold_bullet(domain, wfs)
    pdf.ln(2)

    pdf.sub_title("Workflow Step Types")
    step_types = [
        ("Agent Step", "Run an AI agent on a task"),
        ("Condition Step", "Branch based on data (if amount > threshold)"),
        ("Wait Step", "Pause for minutes, hours, or days"),
        ("Wait-for-Event", "Pause until email opened, link clicked, form submitted"),
        ("Approval (HITL)", "Pause for human approval before continuing"),
        ("Parallel Step", "Run multiple steps concurrently"),
        ("Sub-workflow", "Nest one workflow inside another"),
    ]
    for label, desc in step_types:
        pdf.bold_bullet(label, desc)

    # ═══════════════════════════════════════════════════════════════════════
    # 6. SECURITY & GRANTEX
    # ═══════════════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.section_title("6", "Security & Grantex Scope Enforcement")
    pdf.body(
        "Security is not an add-on - it's built into every layer. The v3.3.0 release introduced "
        "manifest-based scope enforcement using the Grantex SDK, replacing the old keyword-guessing "
        "system that had critical flaws."
    )

    pdf.sub_title("The Problem (Before v3.3.0)")
    pdf.body(
        "The old system guessed permissions from tool names. A tool called 'process_refund' "
        "had no 'create', 'update', or 'delete' keyword, so it was classified as READ - "
        "meaning an agent with read-only access could process refunds! Also, the LangGraph "
        "execution path (used by every agent) had NO scope enforcement at all."
    )

    pdf.sub_title("The Solution: Grantex Manifest-Based Enforcement")

    # Visual: Permission check flow
    y = pdf.get_y() + 3
    flow = [
        (10, "Agent wants to\ncall a tool", C_BLUE),
        (55, "Grantex looks up\ntool in manifest", C_ORANGE),
        (100, "Manifest says:\ndelete_contact\nrequires DELETE", C_RED),
        (145, "Agent only has\nREAD scope\n= DENIED", C_RED),
    ]
    for bx, label, color in flow:
        pdf.set_fill_color(*color)
        pdf.rect(bx, y, 40, 22, style="F")
        pdf.set_font("Helvetica", "B", 7)
        pdf.set_text_color(*C_WHITE)
        lines = label.split("\n")
        for j, ln in enumerate(lines):
            pdf.set_xy(bx, y + 2 + j * 5)
            pdf.cell(40, 5, ln, align="C")
    for bx in [50, 95, 140]:
        pdf.arrow_right(bx, y + 11, bx + 5)

    pdf.set_y(y + 28)

    pdf.sub_title("Permission Hierarchy")
    y = pdf.get_y() + 2
    # Pyramid visualization
    levels = [
        (70, 18, "ADMIN", C_PURPLE, "Can do everything"),
        (60, 18, "DELETE", C_RED, "Can delete + write + read"),
        (50, 18, "WRITE", C_BLUE, "Can write + read"),
        (40, 18, "READ", C_GREEN, "Can only read data"),
    ]
    for i, (w, h, label, color, desc) in enumerate(levels):
        bx = 105 - w / 2
        by = y + i * 14
        pdf.set_fill_color(*color)
        pdf.rect(bx, by, w, 12, style="F")
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(*C_WHITE)
        pdf.set_xy(bx, by + 2)
        pdf.cell(w, 8, label, align="C")
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(*C_BLACK)
        pdf.set_xy(bx + w + 5, by + 2)
        pdf.cell(60, 8, desc)

    pdf.set_y(y + 4 * 14 + 5)

    pdf.sub_title("Key Security Features")
    security_features = [
        ("53 Connector Manifests", "Every tool has a pre-defined permission level - no guessing"),
        ("<1ms Per Check", "Offline JWT verification with cached keys, no API round-trip"),
        ("Dual-Path Enforcement", "Both LangGraph agents AND API-direct calls are checked"),
        ("Token Expiry", "RS256 JWTs with 60-min TTL, auto-refresh at 50%"),
        ("Delegation Chains", "Parent agents can delegate subsets of scopes to child agents"),
        ("Audit Trail", "Every allow/deny decision logged (WORM, tamper-proof, 7-year retention)"),
        ("PII Masking", "Email, phone, Aadhaar, PAN masked in all logs by default"),
        ("Kill Switch", "Any agent can be paused within 30 seconds"),
    ]
    for label, desc in security_features:
        pdf.bold_bullet(label, desc)

    # ═══════════════════════════════════════════════════════════════════════
    # 7. HITL
    # ═══════════════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.section_title("7", "Human-in-the-Loop (HITL) Governance")
    pdf.body(
        "AI agents don't operate unchecked. When an agent's confidence drops below a threshold "
        "(default: 88%) or a configured condition is met (e.g., 'amount > 5 lakh'), execution "
        "pauses and waits for a human to approve or reject."
    )

    # Visual: HITL flow
    y = pdf.get_y() + 3
    hitl_steps = [
        (10, "Agent produces\na result", C_GREEN),
        (52, "Confidence\n< 88%?", C_ORANGE),
        (94, "Execution\nPAUSES", C_RED),
        (136, "Human reviews\n& approves", C_PURPLE),
        (10, "Agent resumes\nwith approval", C_TEAL),
    ]
    for i, (bx, label, color) in enumerate(hitl_steps[:4]):
        pdf.set_fill_color(*color)
        pdf.rect(bx, y, 36, 16, style="F")
        pdf.set_font("Helvetica", "B", 7)
        pdf.set_text_color(*C_WHITE)
        lines = label.split("\n")
        for j, ln in enumerate(lines):
            pdf.set_xy(bx, y + 2 + j * 5)
            pdf.cell(36, 5, ln, align="C")
    for bx in [46, 88, 130]:
        pdf.arrow_right(bx, y + 8, bx + 6)

    pdf.set_y(y + 22)

    pdf.sub_title("HITL Features")
    hitl_features = [
        ("Configurable threshold", "Set confidence floor per agent (50% to 99%)"),
        ("Custom conditions", "Trigger on expressions like 'amount > 500000'"),
        ("Web Push Notifications", "Browser push alerts for approve/reject (one-tap)"),
        ("Approval queue", "Dashboard page showing all pending decisions"),
        ("Org chart escalation", "Low confidence escalates up the hierarchy automatically"),
        ("Timeout rules", "Auto-escalate if no decision within N minutes"),
        ("Audit trail", "Every decision logged with user, timestamp, and reason"),
    ]
    for label, desc in hitl_features:
        pdf.bold_bullet(label, desc)

    # ═══════════════════════════════════════════════════════════════════════
    # 8. DASHBOARDS
    # ═══════════════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.section_title("8", "Dashboards & Intelligence")

    pdf.sub_title("CFO Dashboard")
    pdf.body(
        "Real-time financial intelligence: Cash Runway (days), Burn Rate, DSO (Days Sales "
        "Outstanding), DPO (Days Payable Outstanding), AR/AP Aging (30/60/90/120+ days), "
        "P&L Summary, Bank Balances via Account Aggregator, Tax Calendar with filing deadlines."
    )

    pdf.sub_title("CMO Dashboard")
    pdf.body(
        "Marketing performance at a glance: CAC by Channel (Google/Meta/LinkedIn), MQL to SQL "
        "pipeline, Pipeline Value by Stage, ROAS by Channel, Email Performance (open rate, CTR, "
        "unsubscribe), Brand Sentiment trend, Content Performance metrics."
    )

    pdf.sub_title("ABM Dashboard")
    pdf.body(
        "Account-Based Marketing: Target account management, intent scoring heatmap "
        "(Bombora 40% + G2 30% + TrustRadius 30%), CSV upload, tier filtering, one-click "
        "campaign launch per account."
    )

    pdf.sub_title("Scope Dashboard (NEW v3.3.0)")
    pdf.body(
        "Scope enforcement visibility: total agents, tool calls today, denials today, denial "
        "rate %. Table showing every agent's scope coverage, permission levels, and enforcement "
        "status. Filter by connector, permission level, or agent."
    )

    pdf.sub_title("Other Dashboards")
    other = [
        ("Observatory", "Cross-agent performance metrics and trends"),
        ("SLA Monitor", "Service level tracking with automated alerting"),
        ("Enforce Audit Log", "Real-time feed of all scope enforcement decisions"),
        ("Audit Log", "All user and agent actions (WORM, tamper-proof)"),
    ]
    for label, desc in other:
        pdf.bold_bullet(label, desc)

    # ═══════════════════════════════════════════════════════════════════════
    # 9. MARKETING AUTOMATION
    # ═══════════════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.section_title("9", "Marketing Automation Suite")

    pdf.sub_title("A/B Testing")
    pdf.body(
        "Create 2+ campaign variants, run tests, auto-select winners by open rate or CTR. "
        "CMO can override the auto-winner before sending to the remaining audience."
    )

    pdf.sub_title("Email Drip Engine")
    pdf.body(
        "Behavior-triggered email sequences: trigger next email on open, click, or time delay. "
        "Re-engage non-openers automatically. Rescore leads after drip completion."
    )

    pdf.sub_title("Intent Data Aggregation")
    pdf.body(
        "Weighted scoring from 3 sources: Bombora (40%), G2 (30%), TrustRadius (30%). "
        "Account-level buying signals power personalized ABM outreach."
    )

    pdf.sub_title("Web Push Notifications")
    pdf.body(
        "ServiceWorker + VAPID push notifications. Users get one-tap approve/reject "
        "directly from browser push alerts. Notification bell in dashboard header."
    )

    # ═══════════════════════════════════════════════════════════════════════
    # 10. NO-CODE AGENT CREATOR
    # ═══════════════════════════════════════════════════════════════════════
    pdf.section_title("10", "No-Code Agent Creator")
    pdf.body(
        "Create custom AI agents without writing code using a 5-step wizard:"
    )

    # Visual: 5 steps
    y = pdf.get_y() + 2
    wizard_steps = [
        ("Step 1\nPersona", "Name, title,\navatar, domain", C_BLUE),
        ("Step 2\nRole", "Agent type,\nspecialization", C_GREEN),
        ("Step 3\nPrompt", "System prompt,\ntemplates", C_ORANGE),
        ("Step 4\nBehavior", "LLM, tools,\nconfidence", C_PURPLE),
        ("Step 5\nReview", "Preview &\ndeploy", C_TEAL),
    ]
    bx = 10
    for title, desc, color in wizard_steps:
        pdf.set_fill_color(*color)
        pdf.rect(bx, y, 35, 12, style="F")
        pdf.set_font("Helvetica", "B", 7)
        pdf.set_text_color(*C_WHITE)
        lines = title.split("\n")
        for j, ln in enumerate(lines):
            pdf.set_xy(bx, y + 1 + j * 4.5)
            pdf.cell(35, 4.5, ln, align="C")
        pdf.set_font("Helvetica", "", 6)
        pdf.set_text_color(*C_BLACK)
        dlines = desc.split("\n")
        for j, ln in enumerate(dlines):
            pdf.set_xy(bx, y + 14 + j * 3.5)
            pdf.cell(35, 3.5, ln, align="C")
        if bx < 155:
            pdf.arrow_right(bx + 35, y + 6, bx + 38)
        bx += 38

    pdf.set_y(y + 28)
    pdf.body(
        "New agents start in Shadow Mode - they observe and produce outputs without taking "
        "any real actions. After passing quality gates (accuracy, confidence calibration, "
        "hallucination checks), they can be promoted to Active."
    )

    # ═══════════════════════════════════════════════════════════════════════
    # 11. ORG CHART
    # ═══════════════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.section_title("11", "Organization Chart & Delegation")
    pdf.body(
        "AgenticOrg mirrors your real org structure. The CEO/Admin sees all departments. "
        "CXOs see their domain. Agents report to other agents - forming an escalation chain."
    )

    # Visual: org tree
    y = pdf.get_y() + 3
    pdf.colored_box(75, y, 60, 12, C_NAVY, C_NAVY, "CEO / Admin (Human)")
    pdf.arrow_down(105, y + 12, y + 20)

    cxos = [("CFO", 20), ("CHRO", 65), ("CMO", 110), ("COO", 155)]
    for label, bx in cxos:
        pdf.colored_box(bx, y + 20, 30, 10, C_PURPLE, C_PURPLE, label)
        pdf.arrow_down(bx + 15, y + 30, y + 36)

    agents_row = [
        ("AP Proc", 5), ("AR Coll", 33),
        ("Talent Acq", 55), ("Payroll", 78),
        ("Content", 100), ("Campaign", 128),
        ("Support", 148), ("IT Ops", 170),
    ]
    for label, bx in agents_row:
        pdf.colored_box(bx, y + 36, 24, 10, C_BLUE, C_BLUE, label, font_size=6)

    pdf.set_y(y + 52)

    pdf.sub_title("Scope Narrowing in Delegation")
    pdf.body(
        "When a parent agent delegates to a child, scopes can only narrow - never widen. "
        "If the CFO agent has WRITE scope on finance connectors, it can delegate READ to "
        "the AP Processor, but the AP Processor can never escalate to WRITE or DELETE."
    )

    features = [
        ("Smart Escalation", "Low-confidence results escalate up the org chart to parent agents"),
        ("CSV Import", "Bulk onboard entire department hierarchies from CSV"),
        ("Scope Ceiling", "Clone agents inherit parent scopes - cannot elevate permissions"),
        ("Visual Indicators", "Org chart shows scope narrowing between parent and child nodes"),
    ]
    for label, desc in features:
        pdf.bold_bullet(label, desc)

    # ═══════════════════════════════════════════════════════════════════════
    # 12. INTEGRATIONS
    # ═══════════════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.section_title("12", "External Integrations")

    integrations = [
        ("Python SDK", "pip install agenticorg",
         "client.agents.run('ap_processor', inputs={...})\nclient.agents.list()\nclient.sop.parse_text('...')"),
        ("TypeScript SDK", "npm install agenticorg-sdk",
         "client.agents.run('ap_processor', { inputs: {...} })\nclient.agents.list()"),
        ("MCP Server", "npx agenticorg-mcp-server",
         "Exposes 340+ tools to Claude Desktop, Cursor, ChatGPT.\nConfigure in MCP settings with API key."),
        ("A2A Protocol", "GET /a2a/agent-card",
         "Cross-platform agent discovery and task execution.\nAny A2A-compatible client can invoke agents."),
        ("CLI", "pip install agenticorg",
         "agenticorg agents list\nagenticorg agents run ap_processor --input '{...}'\nagenticorg mcp tools"),
    ]

    for name, install, usage in integrations:
        pdf.sub_title(name)
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(*C_BLUE)
        pdf.cell(0, 5, f"Install: {install}", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Courier", "", 8)
        pdf.set_text_color(*C_BLACK)
        pdf.set_fill_color(*C_LIGHT_GRAY)
        for line in usage.split("\n"):
            pdf.set_x(15)
            pdf.cell(175, 5, f"  {line}", fill=True, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(3)

    # ═══════════════════════════════════════════════════════════════════════
    # 13. LANDING & SEO
    # ═══════════════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.section_title("13", "Landing Page & SEO")
    pdf.body(
        "The public-facing website at agenticorg.ai includes a feature-rich landing page, "
        "blog, resource hub, pricing page, and interactive playground."
    )
    sections = [
        "Hero section with v3.3.0 release banner",
        "Agent Activity Ticker (real-time scrolling feed)",
        "Before/After pain point comparison with stats",
        "Role cards (CFO, CMO, COO, CHRO, CAO)",
        "6 Agents-in-Action cards (expandable step-by-step)",
        "Interactive demo terminal (4 tabs: Invoice, Onboarding, Support, Reconciliation)",
        "Workflow animation with 5-stage progress bar",
        "Dashboard previews (CFO, CMO)",
        "54 connector grid (scrolling)",
        "HITL & Shadow Mode feature explanations",
        "Developer section (SDK/CLI/MCP quickstart)",
        "Social proof (5 testimonials, auto-rotating)",
        "Pricing cards (Free, Pro, Enterprise)",
        "Blog preview + FAQ with JSON-LD structured data",
        "How Grantex Works explainer page (/how-grantex-works)",
    ]
    for s in sections:
        pdf.bullet(s)

    pdf.ln(3)
    pdf.sub_title("SEO & AI Discoverability")
    seo_items = [
        "sitemap.xml with 39+ URLs",
        "llms.txt (4.6KB product summary for AI crawlers)",
        "llms-full.txt (18.7KB complete docs)",
        "8 blog articles across multiple topic clusters",
        "26 resource pages across 7 content clusters",
        "JSON-LD schemas: Organization, Product, FAQ, Breadcrumb, SoftwareApplication",
        "AI crawler support: GPTBot, ClaudeBot, PerplexityBot, ChatGPT-User, Cohere, Applebot",
    ]
    for s in seo_items:
        pdf.bullet(s)

    # ═══════════════════════════════════════════════════════════════════════
    # 14. PLATFORM AT A GLANCE
    # ═══════════════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.section_title("14", "Platform at a Glance")

    # Big stats
    y = pdf.get_y() + 3
    stats = [
        ("35", "AI Agents", C_BLUE),
        ("54", "Connectors", C_GREEN),
        ("340+", "Tools", C_ORANGE),
        ("15", "Workflows", C_PURPLE),
        ("1,662", "Backend Tests", C_TEAL),
        ("342", "E2E Tests", C_RED),
    ]
    sx = 12
    for num, label, color in stats:
        pdf.stat_card(sx, y, 28, 24, num, label, color)
        sx += 32

    pdf.set_y(y + 32)

    pdf.sub_title("Technology Stack")
    stack = [
        ("Backend", "Python 3.12, FastAPI, SQLAlchemy, AsyncPG, Celery, Redis"),
        ("AI/ML", "LangGraph, LangChain, Gemini 2.5 Flash, Claude, GPT-4o"),
        ("Frontend", "React 18, TypeScript, Vite, Tailwind CSS, Shadcn/ui, Recharts"),
        ("Database", "PostgreSQL 16 + pgvector, Redis 7, S3-compatible storage"),
        ("Auth", "Grantex OAuth2 (RS256), JWT (HS256 legacy), bcrypt, RBAC"),
        ("Observability", "OpenTelemetry, Prometheus, LangSmith, Structured Logging"),
        ("Infrastructure", "GKE (Google Kubernetes Engine), Helm, Docker, CI/CD"),
        ("Compliance", "SOC2 Type II, GDPR, DPDP (India), WORM audit, PII masking"),
    ]
    for label, desc in stack:
        pdf.bold_bullet(label, desc)

    pdf.ln(5)
    pdf.sub_title("Version History")
    versions = [
        ("v3.3.0", "Apr 2026", "Grantex scope enforcement, 53 manifests, Scope Dashboard"),
        ("v3.2.0", "Apr 2026", "Marketing automation: A/B, drip, ABM, push, 3 connectors"),
        ("v3.1.0", "Apr 2026", "CFO/CMO dashboards, NL Query, 8 agents, 7 connectors"),
        ("v2.3.0", "Mar 2026", "SDKs, MCP, CLI, A2A, API keys, shadow enforcement"),
        ("v2.2.0", "Mar 2026", "Agent-to-connector bridge, GitHub/Jira/HubSpot live"),
        ("v2.0.0", "Mar 2026", "Initial release: 24 agents, 43 connectors, workflows"),
    ]
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(*C_NAVY)
    pdf.set_text_color(*C_WHITE)
    pdf.cell(22, 7, "Version", border=1, fill=True, align="C")
    pdf.cell(22, 7, "Date", border=1, fill=True, align="C")
    pdf.cell(146, 7, "Key Changes", border=1, fill=True, align="C")
    pdf.ln()
    for i, (ver, date, changes) in enumerate(versions):
        pdf.set_font("Helvetica", "B" if i == 0 else "", 9)
        pdf.set_text_color(*C_BLACK)
        bg = C_LIGHT_BLUE if i == 0 else (C_LIGHT_GRAY if i % 2 == 0 else C_WHITE)
        pdf.set_fill_color(*bg)
        pdf.cell(22, 6, ver, border=1, fill=True, align="C")
        pdf.cell(22, 6, date, border=1, fill=True, align="C")
        pdf.cell(146, 6, changes, border=1, fill=True)
        pdf.ln()

    # Final note
    pdf.ln(8)
    pdf.set_draw_color(*C_NAVY)
    pdf.set_line_width(0.5)
    pdf.line(60, pdf.get_y(), 150, pdf.get_y())
    pdf.ln(5)
    pdf.set_font("Helvetica", "I", 10)
    pdf.set_text_color(*C_GRAY)
    pdf.cell(0, 7, "AgenticOrg - AI Virtual Employee Platform", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 7, f"Version {VERSION} | {DATE} | agenticorg.ai", align="C")

    return pdf


if __name__ == "__main__":
    out_dir = os.path.join(os.path.dirname(__file__), "..", "docs")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"AgenticOrg_Product_Guide_v{VERSION}.pdf")

    print("Generating product guide PDF...")
    pdf = build_guide()
    pdf.output(path)
    print(f"Done! {pdf.pages_count} pages -> {path}")
