"""Generate AgenticOrg Complete User Guide v4.0.0 PDF.

A comprehensive, non-technical end-user guide covering every page,
every flow, and every button in the AgenticOrg platform.
Target: 60-70 pages.

Output: docs/AgenticOrg_Complete_User_Guide_v4.0.0.pdf
"""

from __future__ import annotations

import datetime
import os

from fpdf import FPDF

VERSION = "4.0.0"
DATE = datetime.datetime.now(tz=datetime.UTC).strftime("%B %d, %Y")

# Colors
NAVY = (20, 40, 80)
BLUE = (30, 100, 200)
L_BLUE = (220, 235, 255)
GREEN = (34, 139, 34)
L_GREEN = (220, 245, 220)
ORANGE = (220, 120, 20)
L_ORANGE = (255, 240, 220)
RED = (200, 40, 40)
L_RED = (255, 225, 225)
PURPLE = (100, 40, 160)
L_PURPLE = (240, 230, 255)
TEAL = (0, 128, 128)
L_TEAL = (220, 245, 245)
GRAY = (100, 100, 100)
L_GRAY = (245, 245, 250)
BLACK = (30, 30, 30)
WHITE = (255, 255, 255)
DARK_GREEN = (20, 100, 20)


class UserGuide(FPDF):
    def __init__(self):
        super().__init__()
        self.set_auto_page_break(auto=True, margin=22)

    def header(self):
        if self.page_no() == 1:
            return
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(*GRAY)
        self.cell(0, 5, "AgenticOrg Complete User Guide", align="L")
        self.cell(0, 5, f"v{VERSION}", align="R", new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(200, 200, 200)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(3)

    def footer(self):
        if self.page_no() == 1:
            return
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(*GRAY)
        self.cell(0, 10, f"Page {self.page_no()}", align="C")

    def part_title(self, part_num, title):
        self.add_page()
        self.ln(50)
        self.set_font("Helvetica", "B", 28)
        self.set_text_color(*NAVY)
        self.cell(0, 14, f"Part {part_num}", align="C", new_x="LMARGIN", new_y="NEXT")
        self.ln(4)
        self.set_font("Helvetica", "", 18)
        self.set_text_color(*BLUE)
        self.cell(0, 10, title, align="C", new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(*NAVY)
        self.set_line_width(0.6)
        self.line(60, self.get_y() + 4, 150, self.get_y() + 4)
        self.ln(10)

    def chapter(self, num, title, color=NAVY):
        self.add_page()
        self.set_font("Helvetica", "B", 16)
        self.set_text_color(*color)
        label = f"Chapter {num}: {title}"
        self.cell(0, 10, label, new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(*color)
        self.set_line_width(0.5)
        self.line(10, self.get_y(), 90, self.get_y())
        self.ln(5)

    def section(self, num, title, color=NAVY):
        self.set_font("Helvetica", "B", 15)
        self.set_text_color(*color)
        self.cell(0, 10, f"{num}. {title}" if num else title, new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(*color)
        self.set_line_width(0.5)
        self.line(10, self.get_y(), 70, self.get_y())
        self.ln(4)

    def sub(self, title, color=BLACK):
        self.set_font("Helvetica", "B", 11)
        self.set_text_color(*color)
        self.cell(0, 7, title, new_x="LMARGIN", new_y="NEXT")
        self.ln(1)

    def body(self, text):
        self.set_font("Helvetica", "", 10)
        self.set_text_color(*BLACK)
        self.multi_cell(190, 5.5, text)
        self.ln(2)

    def bullet(self, text):
        self.set_font("Helvetica", "", 10)
        self.set_text_color(*BLACK)
        self.set_x(15)
        self.multi_cell(185, 5.5, f"- {text}")

    def bold_bullet(self, label, text):
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(*BLACK)
        self.set_x(15)
        self.multi_cell(185, 5.5, f"- {label}: {text}")

    def numbered(self, num, text):
        self.set_font("Helvetica", "", 10)
        self.set_text_color(*BLACK)
        self.set_x(15)
        self.multi_cell(185, 5.5, f"{num}. {text}")

    def tip_box(self, text):
        y = self.get_y()
        if y > 265:
            self.add_page()
            y = self.get_y()
        self.set_fill_color(*L_BLUE)
        self.set_draw_color(*BLUE)
        self.rect(12, y, 186, 14, style="DF")
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(*BLUE)
        self.set_xy(15, y + 2)
        self.cell(10, 5, "TIP:")
        self.set_font("Helvetica", "", 9)
        self.set_text_color(*BLACK)
        self.cell(170, 5, text)
        self.set_y(y + 17)

    def warn_box(self, text):
        y = self.get_y()
        if y > 265:
            self.add_page()
            y = self.get_y()
        self.set_fill_color(*L_ORANGE)
        self.set_draw_color(*ORANGE)
        self.rect(12, y, 186, 14, style="DF")
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(*ORANGE)
        self.set_xy(15, y + 2)
        self.cell(18, 5, "WARNING:")
        self.set_font("Helvetica", "", 9)
        self.set_text_color(*BLACK)
        self.cell(162, 5, text)
        self.set_y(y + 17)

    def note_box(self, text):
        y = self.get_y()
        if y > 265:
            self.add_page()
            y = self.get_y()
        self.set_fill_color(*L_GREEN)
        self.set_draw_color(*GREEN)
        self.rect(12, y, 186, 14, style="DF")
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(*GREEN)
        self.set_xy(15, y + 2)
        self.cell(14, 5, "NOTE:")
        self.set_font("Helvetica", "", 9)
        self.set_text_color(*BLACK)
        self.cell(166, 5, text)
        self.set_y(y + 17)

    def step_box(self, num, title, desc, color):
        y = self.get_y()
        if y > 268:
            self.add_page()
            y = self.get_y()
        self.set_fill_color(*color)
        self.rect(12, y, 20, 12, style="F")
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(*WHITE)
        self.set_xy(12, y + 2)
        self.cell(20, 8, f"Step {num}", align="C")
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(*BLACK)
        self.set_xy(35, y + 1)
        self.cell(100, 5, title)
        self.set_font("Helvetica", "", 9)
        self.set_text_color(*GRAY)
        self.set_xy(35, y + 6)
        self.cell(160, 5, desc)
        self.set_y(y + 15)

    def table_header(self, cols, widths):
        self.set_fill_color(*NAVY)
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(*WHITE)
        for i, col in enumerate(cols):
            self.cell(widths[i], 7, col, border=1, fill=True, align="C")
        self.ln()

    def table_row(self, cells, widths, fill=False):
        if fill:
            self.set_fill_color(*L_GRAY)
        self.set_font("Helvetica", "", 9)
        self.set_text_color(*BLACK)
        for i, cell in enumerate(cells):
            self.cell(widths[i], 6, cell, border=1, fill=fill, align="C")
        self.ln()


# ---------------------------------------------------------------------------
# BUILD
# ---------------------------------------------------------------------------
def build():
    pdf = UserGuide()

    # ========================================================================
    # COVER PAGE
    # ========================================================================
    pdf.add_page()
    pdf.set_fill_color(*NAVY)
    pdf.rect(0, 0, 210, 297, style="F")

    # Top accent line
    pdf.set_fill_color(*WHITE)
    pdf.rect(15, 75, 180, 0.8, style="F")

    # Title
    pdf.set_font("Helvetica", "B", 36)
    pdf.set_text_color(*WHITE)
    pdf.set_xy(15, 85)
    pdf.cell(180, 16, "AgenticOrg", align="C")

    # Subtitle
    pdf.set_font("Helvetica", "", 18)
    pdf.set_xy(15, 104)
    pdf.cell(180, 10, "Complete User Guide", align="C")

    # Version line
    pdf.set_font("Helvetica", "I", 13)
    pdf.set_text_color(180, 200, 255)
    pdf.set_xy(15, 118)
    pdf.cell(180, 8, f"Version {VERSION}", align="C")

    # Tagline
    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(160, 190, 255)
    pdf.set_xy(15, 130)
    pdf.cell(180, 7, "Your complete guide to every feature, every page, every button", align="C")

    # Stats row
    stats = [
        ("50+", "AI Agents"),
        ("1000+", "Integrations"),
        ("63", "Native Connectors"),
        ("4", "Industry Packs"),
    ]
    sx = 18
    for num, label in stats:
        pdf.set_font("Helvetica", "B", 22)
        pdf.set_text_color(100, 180, 255)
        pdf.set_xy(sx, 150)
        pdf.cell(42, 10, num, align="C")
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(180, 200, 255)
        pdf.set_xy(sx, 163)
        pdf.cell(42, 6, label, align="C")
        sx += 44

    # Bottom accent line
    pdf.set_fill_color(*WHITE)
    pdf.rect(15, 180, 180, 0.5, style="F")

    # Date and copyright
    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(180, 200, 255)
    pdf.set_xy(15, 190)
    pdf.cell(180, 7, DATE, align="C")
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(140, 170, 220)
    pdf.set_xy(15, 200)
    pdf.cell(180, 7, "For end users and business administrators", align="C")
    pdf.set_xy(15, 210)
    pdf.cell(180, 7, "Apache 2.0 License | agenticorg.ai", align="C")

    # ========================================================================
    # TABLE OF CONTENTS
    # ========================================================================
    pdf.add_page()
    pdf.section("", "Table of Contents")
    pdf.ln(2)

    toc_parts = [
        ("", "Part 1: Getting Started", True),
        ("1", "What is AgenticOrg"),
        ("2", "Creating Your Account"),
        ("3", "Logging In"),
        ("4", "The Dashboard"),
        ("", "Part 2: AI Agents", True),
        ("5", "Viewing Your Agent Fleet"),
        ("6", "Creating an Agent - Natural Language"),
        ("7", "Creating an Agent - Manual Wizard"),
        ("8", "Agent Detail Page"),
        ("9", "Shadow Mode and Promotion"),
        ("10", "Agent Feedback and Self-Improvement"),
        ("", "Part 3: Workflows", True),
        ("11", "Viewing Workflows"),
        ("12", "Creating Workflows - Plain English"),
        ("13", "Creating Workflows - Template"),
        ("14", "Running and Monitoring Workflows"),
        ("", "Part 4: Connectors & Integrations", True),
        ("15", "Native Connectors"),
        ("16", "Composio Marketplace"),
        ("17", "Microsoft 365"),
        ("", "Part 5: Knowledge & Intelligence", True),
        ("18", "Knowledge Base"),
        ("19", "Smart LLM Routing"),
        ("20", "Explainable AI"),
        ("", "Part 6: Voice, RPA & Automation", True),
        ("21", "Voice Agents"),
        ("22", "Browser RPA"),
        ("23", "Industry Packs"),
        ("", "Part 7: Approvals & Governance", True),
        ("24", "Human-in-the-Loop Approvals"),
        ("25", "Scope Enforcement"),
        ("26", "Audit Log"),
        ("", "Part 8: Dashboards", True),
        ("27", "CFO Dashboard"),
        ("28", "CMO Dashboard"),
        ("29", "ABM Dashboard"),
        ("30", "Sales Pipeline"),
        ("", "Part 9: Administration", True),
        ("31", "Organization Chart"),
        ("32", "Settings"),
        ("33", "Billing and Plans"),
        ("34", "Report Scheduler"),
        ("35", "Onboarding"),
        ("", "Part 10: Integrations", True),
        ("36", "SDK, MCP & API"),
        ("37", "CDC (Change Data Capture)"),
        ("", "Part 11: Reference", True),
        ("38", "Keyboard Shortcuts & Tips"),
        ("39", "Troubleshooting"),
        ("40", "Glossary"),
    ]

    for entry in toc_parts:
        if len(entry) == 3 and entry[2]:
            pdf.ln(2)
            pdf.set_font("Helvetica", "B", 11)
            pdf.set_text_color(*NAVY)
            pdf.cell(0, 7, entry[1], new_x="LMARGIN", new_y="NEXT")
        else:
            num, title = entry[0], entry[1]
            pdf.set_font("Helvetica", "", 10)
            pdf.set_text_color(*BLACK)
            pdf.set_x(18)
            pdf.cell(12, 6, f"{num}.")
            pdf.cell(0, 6, title, new_x="LMARGIN", new_y="NEXT")

    # ========================================================================
    # PART 1: GETTING STARTED
    # ========================================================================
    pdf.part_title("I", "Getting Started")

    # ---- Chapter 1: What is AgenticOrg ----
    pdf.chapter(1, "What is AgenticOrg")
    pdf.body(
        "AgenticOrg is an AI Virtual Employee Platform. Instead of hiring "
        "more people for repetitive tasks, you deploy AI agents that work "
        "24/7 -- processing invoices, answering customer questions, managing "
        "HR onboarding, monitoring marketing campaigns, reconciling "
        "accounts, and much more."
    )
    pdf.sub("What Makes AgenticOrg Different")
    pdf.bold_bullet(
        "50+ AI Agents",
        "Pre-built agents for Finance, HR, Marketing, Operations, Legal, "
        "and IT -- each trained for its specific role"
    )
    pdf.bold_bullet(
        "1000+ Integrations",
        "Connect to virtually any business tool via our native connectors "
        "and the Composio marketplace (MIT licensed)"
    )
    pdf.bold_bullet(
        "Shadow Mode",
        "Every new agent starts by observing. It produces outputs but takes "
        "no real actions until you promote it. This ensures safety."
    )
    pdf.bold_bullet(
        "Explainable AI",
        "Every decision comes with a plain-English explanation so you "
        "always understand WHY the AI made a choice"
    )
    pdf.bold_bullet(
        "Human-in-the-Loop",
        "Agents pause and ask for human approval when they are unsure or "
        "when business rules require sign-off"
    )
    pdf.bold_bullet(
        "Open Source",
        "Apache 2.0 license. Self-host for free or use our managed cloud. "
        "No vendor lock-in, no per-seat fees for self-hosted"
    )
    pdf.ln(2)
    pdf.sub("Who Is This Guide For?")
    pdf.body(
        "This guide is written for end users and business administrators. "
        "You do not need any technical or coding background. Every feature "
        "is explained in plain language with step-by-step instructions."
    )
    pdf.tip_box("If you are a developer, see our API Reference at docs.agenticorg.ai/api")

    pdf.sub("How AI Agents Work (Simplified)")
    pdf.body(
        "When you give a task to an AI agent, here is what happens behind "
        "the scenes:"
    )
    pdf.numbered(1, "The agent reads your request and its system prompt (instructions)")
    pdf.numbered(
        2,
        "It searches the Knowledge Base for relevant company documents"
    )
    pdf.numbered(
        3,
        "It decides which tools to use (e.g., 'read Tally invoice', "
        "'check GSTIN', 'send email')"
    )
    pdf.numbered(
        4,
        "The scope enforcement system checks if the agent has permission "
        "to use each tool"
    )
    pdf.numbered(
        5,
        "If confidence is below the threshold, the agent pauses and asks "
        "for human approval"
    )
    pdf.numbered(
        6,
        "The agent produces an output with a plain-English explanation"
    )
    pdf.numbered(
        7,
        "You review the output and give thumbs-up or thumbs-down feedback"
    )
    pdf.ln(2)
    pdf.sub("Supported Domains")
    pdf.body("AgenticOrg organizes agents into these business domains:")
    pdf.bullet("Finance -- invoices, payments, reconciliation, tax, treasury")
    pdf.bullet("HR -- onboarding, payroll, leave management, compliance")
    pdf.bullet("Marketing -- campaigns, leads, content, analytics, ABM")
    pdf.bullet("Operations -- project management, IT support, ticketing")
    pdf.bullet("Legal -- contracts, compliance, case research")
    pdf.bullet("IT -- infrastructure monitoring, incident response, deployments")
    pdf.bullet("Custom -- any domain you define")
    pdf.ln(2)

    pdf.sub("Platform at a Glance")
    pdf.ln(1)
    cols = ["Feature", "Details"]
    widths = [55, 135]
    pdf.table_header(cols, widths)
    pdf.table_row(["AI Agents", "50+ pre-built, unlimited custom"], widths)
    pdf.table_row(["Native Connectors", "63 across 6 categories"], widths, fill=True)
    pdf.table_row(["Marketplace Integrations", "1000+ via Composio (MIT)"], widths)
    pdf.table_row(["Workflow Templates", "20 pre-built templates"], widths, fill=True)
    pdf.table_row(["Industry Packs", "Healthcare, Legal, Insurance, Mfg"], widths)
    pdf.table_row(["Voice Support", "Twilio, Vonage, Custom SIP"], widths, fill=True)
    pdf.table_row(["RPA Scripts", "4 govt portal automations"], widths)
    pdf.table_row(["LLM Models", "Gemini, Claude, GPT, Llama, Mistral"], widths, fill=True)
    pdf.table_row(["Dashboards", "CFO, CMO, ABM, Sales Pipeline"], widths)
    pdf.table_row(["Languages", "English, Hindi"], widths, fill=True)
    pdf.table_row(["Security", "PII masking, scope enforcement, WORM audit"], widths)
    pdf.table_row(["License", "Apache 2.0 (open source)"], widths, fill=True)

    # ---- Chapter 2: Creating Your Account ----
    pdf.chapter(2, "Creating Your Account")
    pdf.body(
        "To get started with AgenticOrg, you need to create an account. "
        "This takes about 30 seconds."
    )
    pdf.sub("Step-by-Step Signup")
    pdf.numbered(1, "Go to agenticorg.ai and click the 'Start Free' button")
    pdf.numbered(
        2,
        "Fill in the signup form with these fields:"
    )
    pdf.set_x(20)
    pdf.bullet("Organization Name -- your company name (e.g., 'Acme Corp')")
    pdf.set_x(20)
    pdf.bullet("Admin Name -- your full name")
    pdf.set_x(20)
    pdf.bullet("Email Address -- your work email")
    pdf.set_x(20)
    pdf.bullet("Password -- at least 8 characters, one uppercase, one number")
    pdf.numbered(3, "Click 'Create Account'")
    pdf.numbered(
        4,
        "You are taken directly to the dashboard. No email verification required."
    )
    pdf.ln(2)
    pdf.sub("Password Policy")
    pdf.body(
        "Passwords must be at least 8 characters long and include at least "
        "one uppercase letter and one number. Special characters are "
        "recommended but not required."
    )
    pdf.sub("Google OAuth (SSO)")
    pdf.body(
        "Alternatively, click 'Sign in with Google' on the signup or login "
        "page. If your organization uses Google Workspace, this provides a "
        "one-click experience. Your Google profile picture and name are "
        "automatically imported."
    )
    pdf.tip_box("Google OAuth is the fastest way to sign up. One click, no passwords.")

    # ---- Chapter 3: Logging In ----
    pdf.chapter(3, "Logging In")
    pdf.sub("Email and Password Login")
    pdf.body(
        "Go to agenticorg.ai/login. Enter your email and password, then "
        "click 'Sign In'. If you forget your password, click 'Forgot "
        "Password?' to receive a reset link via email."
    )
    pdf.sub("Google SSO")
    pdf.body(
        "Click 'Sign in with Google'. If you are already logged into your "
        "Google account in the browser, you will be taken directly to the "
        "dashboard without entering any credentials."
    )
    pdf.sub("Demo Credentials")
    pdf.body(
        "AgenticOrg provides 6 demo accounts so you can explore the "
        "platform immediately without creating a real account. Each demo "
        "account is tied to a specific role:"
    )
    pdf.ln(1)
    cols = ["Role", "Email", "Password"]
    widths = [30, 80, 80]
    pdf.table_header(cols, widths)
    demo_accounts = [
        ("CEO/Admin", "ceo@demo.agenticorg.ai", "DemoCEO2026!"),
        ("CFO", "cfo@demo.agenticorg.ai", "DemoCFO2026!"),
        ("CHRO", "chro@demo.agenticorg.ai", "DemoCHRO2026!"),
        ("CMO", "cmo@demo.agenticorg.ai", "DemoCMO2026!"),
        ("COO", "coo@demo.agenticorg.ai", "DemoCOO2026!"),
        ("Auditor", "auditor@demo.agenticorg.ai", "DemoAudit2026!"),
    ]
    for i, (role, email, pw) in enumerate(demo_accounts):
        pdf.table_row([role, email, pw], widths, fill=(i % 2 == 1))
    pdf.ln(3)
    pdf.sub("Forgot Password Flow")
    pdf.numbered(1, "Click 'Forgot Password?' on the login page")
    pdf.numbered(2, "Enter your email address and click 'Send Reset Link'")
    pdf.numbered(3, "Check your inbox for an email from noreply@agenticorg.ai")
    pdf.numbered(4, "Click the link in the email (valid for 24 hours)")
    pdf.numbered(5, "Enter your new password and click 'Reset Password'")
    pdf.numbered(6, "You are redirected to the login page. Sign in with your new password.")
    pdf.warn_box("Reset links expire after 24 hours. Request a new one if expired.")

    # ---- Chapter 4: The Dashboard ----
    pdf.chapter(4, "The Dashboard")
    pdf.body(
        "The Dashboard is the first page you see after logging in. It gives "
        "you a complete overview of your AI workforce, integrations, and "
        "recent activity."
    )

    pdf.sub("Top Row: 5 Stat Cards")
    pdf.body("The top of the dashboard shows 5 key metrics at a glance:")
    pdf.bold_bullet("Total Agents", "The total number of AI agents in your organization")
    pdf.bold_bullet(
        "Active Agents",
        "Agents that are fully promoted and executing tasks in production"
    )
    pdf.bold_bullet(
        "Pending Approval",
        "Number of HITL items waiting for a human decision"
    )
    pdf.bold_bullet(
        "Shadow Mode",
        "Agents currently observing without taking real actions"
    )
    pdf.bold_bullet(
        "Deflection Rate",
        "Percentage of tasks successfully handled by AI without human help"
    )
    pdf.ln(2)

    pdf.sub("Integration Status Cards")
    pdf.body("Below the stat cards, you see 3 integration status indicators:")
    pdf.bold_bullet(
        "Native Connectors",
        "Shows how many of the 63 native connectors are connected and healthy"
    )
    pdf.bold_bullet(
        "Composio Apps",
        "Shows how many marketplace apps are linked to your account"
    )
    pdf.bold_bullet(
        "Knowledge Base",
        "Shows document count, total chunks indexed, and storage used"
    )
    pdf.ln(2)

    pdf.sub("Charts and Graphs")
    pdf.body("The dashboard includes 3 interactive charts:")
    pdf.bold_bullet(
        "Agent Activity (7-day)",
        "A line chart showing how many tasks your agents completed each "
        "day over the last 7 days"
    )
    pdf.bold_bullet(
        "Domain Distribution",
        "A pie chart showing what percentage of agents belong to Finance, "
        "HR, Marketing, Operations, and other domains"
    )
    pdf.bold_bullet(
        "Cost Trend",
        "A bar chart showing your monthly LLM token spend broken down by "
        "agent"
    )
    pdf.ln(2)

    pdf.sub("Activity Feed")
    pdf.body(
        "A scrolling feed showing real-time events: agent runs, approval "
        "decisions, workflow completions, connector health changes, and "
        "errors. Each entry shows the timestamp, agent name, event type, "
        "and a brief description."
    )

    pdf.sub("Pending Approvals Widget")
    pdf.body(
        "If any HITL items are waiting for your decision, they appear as "
        "a compact list at the bottom of the dashboard. Click any item to "
        "go directly to the Approvals page."
    )

    pdf.sub("Sidebar Navigation (26 Items)")
    pdf.body(
        "The left sidebar is your primary navigation. It contains the "
        "following 26 items, organized by category:"
    )
    pdf.bullet("Dashboard")
    pdf.bullet("Agents")
    pdf.bullet("Org Chart")
    pdf.bullet("Workflows")
    pdf.bullet("Knowledge Base")
    pdf.bullet("Voice Agents")
    pdf.bullet("RPA Scripts")
    pdf.bullet("Industry Packs")
    pdf.bullet("Connectors (Native)")
    pdf.bullet("Marketplace (Composio)")
    pdf.bullet("Approvals")
    pdf.bullet("Scope Dashboard")
    pdf.bullet("Enforce Audit Log")
    pdf.bullet("CFO Dashboard")
    pdf.bullet("CMO Dashboard")
    pdf.bullet("ABM Dashboard")
    pdf.bullet("Sales Pipeline")
    pdf.bullet("Report Scheduler")
    pdf.bullet("Onboarding")
    pdf.bullet("Billing")
    pdf.bullet("Settings")
    pdf.bullet("API Keys")
    pdf.bullet("Team Management")
    pdf.bullet("Audit Log")
    pdf.bullet("SDK & MCP")
    pdf.bullet("Help & Docs")
    pdf.ln(2)

    pdf.sub("Header Bar")
    pdf.body("The top header bar contains 4 interactive elements:")
    pdf.bold_bullet(
        "Natural Language Query Bar",
        "Type questions in plain English (e.g., 'Show me all finance "
        "agents') and the system searches and navigates for you. Keyboard "
        "shortcut: Cmd+K or Ctrl+K."
    )
    pdf.bold_bullet(
        "Language Picker",
        "Switch between English and Hindi. The entire UI updates instantly."
    )
    pdf.bold_bullet(
        "Notification Bell",
        "Shows unread notifications (HITL requests, agent promotions, "
        "errors). Click to see the full list."
    )
    pdf.bold_bullet(
        "Company Switcher",
        "If you belong to multiple organizations, click here to switch "
        "between them without logging out."
    )

    pdf.sub("Sidebar Behavior")
    pdf.body(
        "The sidebar is collapsible. Click the hamburger icon (three "
        "horizontal lines) at the top-left to collapse it to icons only, "
        "giving more space to the main content area. Click again to "
        "expand. On mobile devices, the sidebar starts collapsed and "
        "opens as an overlay when tapped."
    )
    pdf.body(
        "The active page is highlighted with a blue background in the "
        "sidebar. Hovering over any item shows a tooltip with the full "
        "page name."
    )

    pdf.sub("Role-Based Visibility")
    pdf.body(
        "What you see on the Dashboard depends on your role. The CEO/"
        "Admin sees all stats, all domains, and all sidebar items. CXOs "
        "see only their domain. For example, the CFO sees only Finance "
        "agents, the Finance connector status, and the CFO Dashboard. "
        "The Auditor role sees only the Audit Log and Enforce Audit Log."
    )
    pdf.tip_box("Ask your Admin to change your role if you need access to more pages.")

    # ========================================================================
    # PART 2: AI AGENTS
    # ========================================================================
    pdf.part_title("II", "AI Agents")

    # ---- Chapter 5: Viewing Your Agent Fleet ----
    pdf.chapter(5, "Viewing Your Agent Fleet")
    pdf.body(
        "The Agents page shows every AI agent in your organization as a "
        "card-based grid. Each card displays the agent's name, avatar, "
        "domain, status badge, and a brief description."
    )
    pdf.sub("Search and Filters")
    pdf.bold_bullet(
        "Search Bar",
        "Type any keyword to filter agents by name or description. Results "
        "update as you type."
    )
    pdf.bold_bullet(
        "Domain Filter",
        "Dropdown to filter by domain: All, Finance, HR, Marketing, "
        "Operations, Legal, IT, Custom"
    )
    pdf.bold_bullet(
        "Status Filter",
        "Filter by: All, Active, Shadow, Paused, Error"
    )
    pdf.ln(2)

    pdf.sub("Agent Cards")
    pdf.body("Each agent card shows:")
    pdf.bullet("Avatar (auto-generated or custom uploaded)")
    pdf.bullet("Agent name and designation (e.g., 'Invoice Processor')")
    pdf.bullet("Domain badge (e.g., 'Finance' in blue)")
    pdf.bullet("Status badge: Green for Active, Yellow for Shadow, Gray for Paused, Red for Error")
    pdf.bullet("One-line description of what the agent does")
    pdf.bullet("Click any card to open the Agent Detail page")
    pdf.ln(2)

    pdf.sub("CSV Import")
    pdf.body(
        "To bulk-create agents, click the 'Import CSV' button above the "
        "agent grid. Upload a CSV file with columns: name, designation, "
        "domain, type, specialization, prompt. Each row creates one agent "
        "in Shadow mode. A progress bar shows import status."
    )
    pdf.tip_box("Download the sample CSV template from the Import dialog.")

    # ---- Chapter 6: Creating an Agent - Natural Language ----
    pdf.chapter(6, "Creating an Agent - Natural Language")
    pdf.body(
        "The fastest way to create an agent is by describing what you need "
        "in plain English."
    )
    pdf.sub("How It Works")
    pdf.numbered(
        1,
        "Click 'Create Agent' on the Agents page"
    )
    pdf.numbered(
        2,
        "You see a large text area with the prompt: 'Describe the "
        "employee you need'"
    )
    pdf.numbered(
        3,
        "Type a description of what you want the agent to do"
    )
    pdf.numbered(
        4,
        "Click the 'Generate' button"
    )
    pdf.numbered(
        5,
        "The system auto-fills all wizard fields (persona, role, prompt, "
        "behavior, tools) based on your description"
    )
    pdf.numbered(
        6,
        "Review the auto-generated configuration. Edit any field you want "
        "to change."
    )
    pdf.numbered(
        7,
        "Click 'Create' to deploy the agent in Shadow mode"
    )
    pdf.ln(2)

    pdf.sub("Example Descriptions")
    pdf.bullet(
        '"I need someone who processes invoices and matches them with '
        'purchase orders"'
    )
    pdf.bullet(
        '"Customer support agent that handles refund requests and tracks '
        'SLAs"'
    )
    pdf.bullet(
        '"An HR coordinator who manages onboarding checklists for new hires"'
    )
    pdf.bullet(
        '"Marketing analyst who monitors campaign performance across Google '
        'Ads, Meta, and LinkedIn"'
    )
    pdf.bullet(
        '"IT support agent that triages Jira tickets and escalates P0 '
        'incidents"'
    )
    pdf.ln(2)
    pdf.tip_box("Be specific about tools, thresholds, and domains for best results.")

    # ---- Chapter 7: Creating an Agent - Manual Wizard ----
    pdf.chapter(7, "Creating an Agent - Manual Wizard")
    pdf.body(
        "If you prefer full control, use the 5-step manual wizard. Each "
        "step is described in detail below."
    )

    pdf.sub("Step 0: Persona")
    pdf.step_box("0", "Persona", "Define identity: name, designation, avatar, domain", BLUE)
    pdf.body("Configure the agent's identity:")
    pdf.bold_bullet("Name", "A human-readable name (e.g., 'Riya - Invoice Processor')")
    pdf.bold_bullet(
        "Designation",
        "The job title (e.g., 'Accounts Payable Analyst')"
    )
    pdf.bold_bullet(
        "Avatar",
        "Upload a photo or let the system generate one automatically"
    )
    pdf.bold_bullet(
        "Domain",
        "Select from: Finance, HR, Marketing, Operations, Legal, IT, or "
        "Custom. This determines which CXO dashboard the agent appears on."
    )
    pdf.ln(2)

    pdf.sub("Step 1: Role")
    pdf.step_box("1", "Role", "Choose type, specialization, routing, reporting", GREEN)
    pdf.body("Configure the agent's functional role:")
    pdf.bold_bullet(
        "Agent Type",
        "Select from pre-defined types: Processor, Analyst, Coordinator, "
        "Monitor, Reviewer, Responder, or choose 'Custom Type'"
    )
    pdf.bold_bullet(
        "Custom Type",
        "If you selected 'Custom Type', enter a free-text type name"
    )
    pdf.bold_bullet(
        "Specialization",
        "A more specific focus area (e.g., for Processor type: 'Invoice "
        "Matching', 'Expense Reimbursement', 'Payroll Processing')"
    )
    pdf.bold_bullet(
        "Routing Filters",
        "Rules that determine which tasks reach this agent. For example: "
        "'department=finance AND amount>10000'"
    )
    pdf.bold_bullet(
        "Reporting To",
        "Select the parent agent or human manager in the org chart. This "
        "determines escalation paths."
    )
    pdf.ln(2)

    pdf.sub("Step 2: Prompt")
    pdf.step_box("2", "Prompt", "Select template or write custom instructions", ORANGE)
    pdf.body("Configure the agent's instructions:")
    pdf.bold_bullet(
        "Template Selection",
        "Choose from 20+ pre-built prompt templates organized by domain "
        "and type. Each template has been tested for accuracy."
    )
    pdf.bold_bullet(
        "Template Variables",
        "Templates contain variables like {{company_name}}, {{threshold}}, "
        "{{escalation_email}} that you fill in. Variables are highlighted "
        "in yellow."
    )
    pdf.bold_bullet(
        "Manual Edit",
        "Click 'Edit Manually' to modify the template or write your own "
        "prompt from scratch. The prompt editor supports plain text only."
    )
    pdf.tip_box("Templates are the recommended approach. They encode best practices.")
    pdf.ln(2)

    pdf.sub("Step 3: Behavior")
    pdf.step_box("3", "Behavior", "LLM model, routing, confidence, HITL, tools", PURPLE)
    pdf.body("Configure how the agent thinks and acts:")
    pdf.bold_bullet(
        "LLM Model",
        "Select the AI model: Gemini 2.0 Flash (fast, cheap), Claude "
        "Sonnet (balanced), GPT-4o (premium). Or let Smart Routing choose."
    )
    pdf.bold_bullet(
        "LLM Routing Dropdown",
        "Choose a routing tier: Economy ($0.10/1K tokens), Standard "
        "($0.50/1K tokens), or Premium ($2.00/1K tokens). Each tier shows "
        "estimated monthly cost based on expected usage."
    )
    pdf.bold_bullet(
        "Confidence Floor Slider",
        "Drag the slider from 0% to 100% (default: 88%). If the agent's "
        "confidence drops below this value, it triggers HITL."
    )
    pdf.bold_bullet(
        "HITL Condition",
        "An optional expression that forces human approval regardless of "
        "confidence. Example: 'amount > 500000' or 'is_new_vendor == true'"
    )
    pdf.bold_bullet(
        "Max Retries",
        "Number of times the agent retries a failed tool call before "
        "giving up (default: 3)"
    )
    pdf.bold_bullet(
        "Authorized Tools",
        "A checklist of tools this agent can use. Each tool shows a "
        "colored permission badge: green (READ), blue (WRITE), orange "
        "(DELETE), red (ADMIN)."
    )
    pdf.bold_bullet(
        "Composio Marketplace Tools",
        "Click 'Add from Marketplace' to browse 1000+ additional tools "
        "from the Composio ecosystem"
    )
    pdf.bold_bullet(
        "Voice Toggle",
        "Enable or disable voice capabilities for this agent. When "
        "enabled, a 'Voice' tab appears on the agent detail page."
    )
    pdf.warn_box("Tools with DELETE or ADMIN badges show a yellow warning banner.")
    pdf.ln(2)

    pdf.sub("Step 4: Review")
    pdf.step_box("4", "Review", "Preview configuration and create", TEAL)
    pdf.body(
        "The final step shows a complete summary of everything you "
        "configured: persona, role, prompt preview, behavior settings, "
        "tool list with permissions, and estimated monthly cost. Review "
        "carefully and click 'Create Agent'. The agent is deployed in "
        "Shadow mode immediately."
    )

    # ---- Chapter 8: Agent Detail Page ----
    pdf.chapter(8, "Agent Detail Page")
    pdf.body(
        "Click any agent card to open its detail page. This page gives "
        "you complete visibility into the agent's configuration, "
        "performance, cost, permissions, and learning history."
    )

    pdf.sub("Header Section")
    pdf.body("The top of the page shows:")
    pdf.bullet("Agent name, avatar, and designation")
    pdf.bullet("Status badge (Active / Shadow / Paused / Error)")
    pdf.bullet("Three action buttons:")
    pdf.set_x(20)
    pdf.bold_bullet("Promote", "Move from Shadow to Active (available only in Shadow mode)")
    pdf.set_x(20)
    pdf.bold_bullet("Rollback", "Revert to Shadow mode (available only in Active mode)")
    pdf.set_x(20)
    pdf.bold_bullet("Kill Switch", "Immediately pause the agent. All in-progress tasks are halted.")
    pdf.ln(2)

    pdf.sub("Tab 1: Overview")
    pdf.body(
        "Shows all persona and role fields: name, designation, domain, "
        "type, specialization, routing filters, reporting chain, creation "
        "date, and last run timestamp."
    )

    pdf.sub("Tab 2: Config")
    pdf.body(
        "Shows behavior settings: LLM model name, routing tier, "
        "confidence floor, max retries, HITL condition, and the full "
        "list of authorized tools with their permission badges."
    )

    pdf.sub("Tab 3: Prompt")
    pdf.body(
        "Shows the current system prompt in a read-only editor. Click "
        "'Edit' to modify. Below the editor, 'Prompt History' shows every "
        "version of the prompt with timestamps and diffs."
    )

    pdf.sub("Tab 4: Shadow")
    pdf.body(
        "Visible only when the agent is in Shadow mode. Shows:"
    )
    pdf.bullet("Accuracy chart: percentage of correct predictions over time")
    pdf.bullet("Sample table: each shadow run with input, output, expected output, and match status")
    pdf.bullet(
        "Quality gates: 6 gates that must all pass before promotion is allowed"
    )
    pdf.ln(2)

    pdf.sub("Tab 5: Cost")
    pdf.body(
        "Shows token usage and cost metrics:"
    )
    pdf.bullet("Total tokens consumed (input + output)")
    pdf.bullet("Cost this month in USD")
    pdf.bullet("Monthly trend chart showing cost over the last 6 months")
    pdf.bullet("Per-run cost breakdown table")
    pdf.ln(2)

    pdf.sub("Tab 6: Scopes")
    pdf.body(
        "Shows the agent's permission enforcement status:"
    )
    pdf.bullet("Permission table: each tool, its required scope, and whether the agent has it")
    pdf.bullet(
        "Token status: shows if the agent's grant token is valid, expired, "
        "or revoked"
    )
    pdf.bullet("Denied calls: list of recent tool calls that were blocked due to insufficient scope")
    pdf.ln(2)

    pdf.sub("Tab 7: Learning")
    pdf.body(
        "Shows the agent's feedback and self-improvement history:"
    )
    pdf.bullet("Feedback timeline: every thumbs up/down with user comments")
    pdf.bullet(
        "Prompt amendments: suggested changes to the system prompt based "
        "on feedback patterns. Each amendment shows the proposed change, "
        "the evidence, and Apply/Dismiss buttons."
    )
    pdf.ln(2)

    pdf.sub("Tab 8: Voice")
    pdf.body(
        "If voice is enabled, shows the call log table: call ID, phone "
        "number, duration, status (completed/failed/in-progress), and "
        "recording link. If voice is not yet configured, shows a 'Set Up "
        "Voice' link that opens the Voice Setup wizard."
    )

    pdf.sub("Agent Actions Summary")
    pdf.body("From the Agent Detail page, you can perform these actions:")
    pdf.ln(1)
    cols = ["Action", "Available In", "What It Does"]
    widths = [30, 35, 125]
    pdf.table_header(cols, widths)
    pdf.table_row(["Promote", "Shadow", "Move agent to Active (live execution)"], widths)
    pdf.table_row(["Rollback", "Active", "Move agent back to Shadow (stop live execution)"], widths, fill=True)
    pdf.table_row(["Kill Switch", "Any", "Immediately pause agent and halt all tasks"], widths)
    pdf.table_row(["Edit Config", "Any", "Modify LLM, tools, HITL, retries"], widths, fill=True)
    pdf.table_row(["Edit Prompt", "Any", "Change the system prompt"], widths)
    pdf.table_row(["Retest", "Shadow", "Clear shadow data and restart observation"], widths, fill=True)
    pdf.table_row(["Delete", "Paused", "Permanently remove the agent"], widths)
    pdf.ln(3)

    # ---- Chapter 9: Shadow Mode and Promotion ----
    pdf.chapter(9, "Shadow Mode and Promotion")
    pdf.body(
        "Every new agent starts in Shadow mode. In this mode, the agent "
        "observes real tasks and produces outputs, but does NOT take any "
        "real actions. This lets you validate quality before going live."
    )

    pdf.sub("The 6 Quality Gates")
    pdf.body(
        "Before an agent can be promoted from Shadow to Active, it must "
        "pass all 6 quality gates:"
    )
    pdf.numbered(1, "Minimum Sample Count: At least 20 shadow runs completed")
    pdf.numbered(
        2,
        "Accuracy Floor: Shadow accuracy must be at least 90% (configurable)"
    )
    pdf.numbered(
        3,
        "No Hallucinations: Zero hallucinated outputs detected in shadow runs"
    )
    pdf.numbered(
        4,
        "Confidence Consistency: Average confidence must be above the "
        "confidence floor"
    )
    pdf.numbered(
        5,
        "Tool Usage: All authorized tools must have been exercised at "
        "least once"
    )
    pdf.numbered(
        6,
        "No PII Leakage: No shadow run output contained unmasked PII"
    )
    pdf.ln(2)

    pdf.sub("Promote Flow")
    pdf.numbered(1, "Go to the agent's Shadow tab")
    pdf.numbered(2, "Verify all 6 quality gates show green checkmarks")
    pdf.numbered(3, "Click the 'Promote' button in the header")
    pdf.numbered(
        4,
        "Confirm in the dialog: 'This agent will start executing real "
        "actions. Continue?'"
    )
    pdf.numbered(5, "The status changes from Shadow (yellow) to Active (green)")
    pdf.ln(2)

    pdf.sub("Retest")
    pdf.body(
        "If quality gates fail, click 'Retest' to clear the shadow data "
        "and restart the observation period. You may want to adjust the "
        "prompt or tools before retesting."
    )
    pdf.tip_box("You can promote/rollback agents any number of times.")

    # ---- Chapter 10: Agent Feedback and Self-Improvement ----
    pdf.chapter(10, "Agent Feedback and Self-Improvement")
    pdf.body(
        "AgenticOrg learns from your feedback. Every agent run shows "
        "thumbs-up and thumbs-down buttons so you can rate the output."
    )

    pdf.sub("Giving Feedback")
    pdf.numbered(1, "View any agent run result")
    pdf.numbered(
        2,
        "Click thumbs-up if the output is correct, or thumbs-down if it "
        "needs improvement"
    )
    pdf.numbered(
        3,
        "When you click thumbs-down, a text box appears where you can "
        "describe what was wrong and what the correct output should have been"
    )
    pdf.numbered(4, "Click 'Submit Feedback'")
    pdf.ln(2)

    pdf.sub("Automatic Prompt Amendments")
    pdf.body(
        "After collecting enough feedback (typically 5+ corrections on a "
        "similar pattern), the system automatically generates a suggested "
        "prompt amendment. This appears on the agent's Learning tab."
    )
    pdf.body("Each amendment shows:")
    pdf.bullet("The proposed change (highlighted diff)")
    pdf.bullet("The evidence (which feedback items triggered this suggestion)")
    pdf.bullet("Two buttons: 'Apply' to accept or 'Dismiss' to reject")
    pdf.ln(1)
    pdf.body(
        "Applying an amendment updates the agent's system prompt immediately. "
        "The previous version is saved in Prompt History so you can revert "
        "if needed."
    )
    pdf.note_box("Amendments never auto-apply. A human must click 'Apply'.")

    # ========================================================================
    # PART 3: WORKFLOWS
    # ========================================================================
    pdf.part_title("III", "Workflows")

    # ---- Chapter 11: Viewing Workflows ----
    pdf.chapter(11, "Viewing Workflows")
    pdf.body(
        "Workflows chain multiple agents together to complete complex "
        "business processes. Go to 'Workflows' in the sidebar to see "
        "your workflow list."
    )

    pdf.sub("List View")
    pdf.body("Each workflow row shows:")
    pdf.bullet("Workflow name and version number")
    pdf.bullet(
        "Status badge: Draft (gray), Active (green), Paused (yellow), "
        "Failed (red)"
    )
    pdf.bullet(
        "Trigger type: Manual, Scheduled (cron), CDC (data change), "
        "or API"
    )
    pdf.bullet("Last run timestamp and duration")
    pdf.bullet("'Run Now' button for manual triggering")
    pdf.ln(2)

    pdf.sub("Sorting and Filtering")
    pdf.body(
        "Click column headers to sort. Use the status dropdown to filter "
        "by workflow state. A search bar lets you find workflows by name."
    )

    pdf.sub("20 Pre-Built Workflow Templates")
    pdf.body(
        "AgenticOrg ships with 20 ready-to-use workflow templates. Select "
        "one and customize it for your needs:"
    )
    pdf.bullet("Invoice-to-Pay (AP Automation) -- OCR, validate, 3-way match, approve, pay")
    pdf.bullet("Month-End Close -- reconcile accounts, generate reports, review")
    pdf.bullet("Employee Onboarding -- create accounts, assign equipment, schedule training")
    pdf.bullet("Employee Offboarding -- revoke access, collect assets, exit interview")
    pdf.bullet("Campaign Launch -- brief review, creative approval, schedule, publish")
    pdf.bullet("Lead Nurture -- email drip, engagement scoring, handoff to sales")
    pdf.bullet("Incident Response -- detect, triage, assign, escalate, resolve")
    pdf.bullet("Expense Reimbursement -- submit, validate, approve, reimburse")
    pdf.bullet("Vendor Onboarding -- collect docs, verify GSTIN, compliance check, approve")
    pdf.bullet("Compliance Review -- gather evidence, check regulations, report findings")
    pdf.bullet("IT Incident Escalation -- alert, diagnose, escalate P0/P1, notify stakeholders")
    pdf.bullet("Payroll Processing -- collect attendance, calculate, validate, disburse")
    pdf.bullet("Tax Filing Preparation -- gather data, compute, validate, generate returns")
    pdf.bullet("Contract Review -- extract terms, flag risks, compare benchmarks, recommend")
    pdf.bullet("Customer Feedback Loop -- collect, categorize, route, respond, track")
    pdf.bullet("Inventory Reorder -- monitor levels, predict demand, generate POs, approve")
    pdf.bullet("Sales Quote Generation -- configure product, price, discount, generate PDF")
    pdf.bullet("Board Report Preparation -- pull KPIs, create charts, compile deck")
    pdf.bullet("Data Quality Check -- profile data, detect anomalies, flag issues, report")
    pdf.bullet("Social Media Publishing -- draft, review, schedule, publish, monitor")
    pdf.ln(2)

    # ---- Chapter 12: Creating Workflows - Plain English ----
    pdf.chapter(12, "Creating Workflows - Plain English")
    pdf.body(
        "The fastest way to create a workflow is by describing it in "
        "plain English."
    )
    pdf.sub("How It Works")
    pdf.numbered(1, "Go to Workflows and click 'Create Workflow'")
    pdf.numbered(2, "Select the 'Describe in English' tab")
    pdf.numbered(
        3,
        "Type a description of the process you want to automate. Example: "
        "'When a new invoice arrives, OCR it, validate the GSTIN, do a "
        "3-way match with the PO, get CFO approval if amount exceeds 5 "
        "lakhs, then schedule payment.'"
    )
    pdf.numbered(4, "Click 'Generate'")
    pdf.numbered(
        5,
        "The system creates a complete workflow with steps, conditions, "
        "agent assignments, and triggers"
    )
    pdf.numbered(
        6,
        "Preview the generated workflow. Edit any step, agent assignment, "
        "or condition."
    )
    pdf.numbered(7, "Click 'Deploy' to activate the workflow")
    pdf.ln(2)

    pdf.sub("Tips for Better Generation")
    pdf.bullet("Be specific about conditions and thresholds (e.g., 'above 5 lakhs')")
    pdf.bullet("Mention which agents or roles should handle each step")
    pdf.bullet("Include approval requirements explicitly")
    pdf.bullet("Specify what happens on failure (e.g., 'retry 3 times, then escalate')")
    pdf.tip_box("You can iterate: generate, review, edit, and re-generate.")

    # ---- Chapter 13: Creating Workflows - Template ----
    pdf.chapter(13, "Creating Workflows - Template")
    pdf.body(
        "For full control, use the Template Builder to create workflows "
        "step by step."
    )

    pdf.sub("Workflow Form Fields")
    pdf.bold_bullet("Name", "A descriptive name (e.g., 'Invoice-to-Pay Automation')")
    pdf.bold_bullet("Version", "Semantic version (e.g., '1.0.0'). Auto-incremented on updates.")
    pdf.bold_bullet(
        "Domain",
        "The business domain: Finance, HR, Marketing, Operations, or Cross-Domain"
    )
    pdf.bold_bullet(
        "Trigger Type",
        "How the workflow starts:"
    )
    pdf.set_x(20)
    pdf.bullet("Manual -- user clicks 'Run Now'")
    pdf.set_x(20)
    pdf.bullet("Scheduled -- runs on a cron expression (e.g., '0 9 * * MON' for every Monday 9am)")
    pdf.set_x(20)
    pdf.bullet("CDC -- triggers when data changes in a connected system")
    pdf.set_x(20)
    pdf.bullet("API -- triggered by an external API call (POST /workflows/{id}/run)")
    pdf.ln(2)

    pdf.sub("Steps Configuration")
    pdf.body(
        "Each workflow contains an ordered list of steps. Steps are defined "
        "in JSON format with these fields:"
    )
    pdf.bold_bullet("step_id", "Unique identifier (e.g., 'step_1')")
    pdf.bold_bullet("agent", "The agent assigned to execute this step")
    pdf.bold_bullet("action", "What the agent does (e.g., 'ocr_invoice', 'validate_gstin')")
    pdf.bold_bullet(
        "condition",
        "An optional expression that must be true for this step to execute "
        "(e.g., 'prev.amount > 500000')"
    )
    pdf.bold_bullet("on_fail", "What to do if the step fails: 'retry', 'skip', or 'abort'")
    pdf.bold_bullet("timeout", "Maximum seconds before the step times out (default: 300)")
    pdf.ln(2)

    pdf.sub("Adaptive Replanning Toggle")
    pdf.body(
        "Enable the 'Adaptive Replanning' toggle to let the system "
        "automatically re-plan remaining steps when a step fails. For "
        "example, if a payment via PineLabs fails, the system might reroute "
        "to NEFT. Replanned steps show a 'Replanned' badge in the execution view."
    )
    pdf.ln(1)

    pdf.sub("Collaboration Step Type")
    pdf.body(
        "A special step type where multiple agents work in parallel on "
        "the same task. Configure:"
    )
    pdf.bold_bullet(
        "Agents",
        "Select 2 or more agents that will each produce an output"
    )
    pdf.bold_bullet(
        "Aggregation",
        "How results are combined: 'majority_vote', 'best_confidence', "
        "'merge', or 'human_pick'"
    )
    pdf.bold_bullet(
        "Timeout",
        "Maximum seconds to wait for all agents (default: 120)"
    )
    pdf.tip_box("Use collaboration steps for high-stakes decisions like fraud review.")

    # ---- Chapter 14: Running and Monitoring Workflows ----
    pdf.chapter(14, "Running and Monitoring Workflows")
    pdf.body(
        "Once a workflow is deployed, you can run it manually or wait for "
        "its trigger to fire."
    )

    pdf.sub("Manual Run")
    pdf.numbered(1, "Go to the Workflows list")
    pdf.numbered(2, "Click the 'Run Now' button next to the workflow")
    pdf.numbered(
        3,
        "If the workflow requires input parameters, a dialog appears to "
        "collect them"
    )
    pdf.numbered(4, "Click 'Start' to begin execution")
    pdf.ln(2)

    pdf.sub("Progress Cards")
    pdf.body("While running, the workflow detail page shows 4 progress cards:")
    pdf.bold_bullet("Steps Completed", "N of M steps finished (e.g., '3 of 7')")
    pdf.bold_bullet("Current Step", "Name and agent of the currently executing step")
    pdf.bold_bullet("Elapsed Time", "How long the workflow has been running")
    pdf.bold_bullet("Status", "Running (blue), Waiting for Approval (yellow), Completed (green), Failed (red)")
    pdf.ln(2)

    pdf.sub("Step Execution Table")
    pdf.body(
        "Below the progress cards, a table shows every step with columns: "
        "step ID, agent, action, status, duration, output preview, and "
        "Replanned badge (if applicable). The table auto-refreshes every "
        "5 seconds while the workflow is running."
    )

    pdf.sub("Cancel a Running Workflow")
    pdf.body(
        "Click the 'Cancel' button in the workflow header. All in-progress "
        "steps are halted. Completed steps are not rolled back. The "
        "workflow status changes to 'Cancelled'."
    )

    # ========================================================================
    # PART 4: CONNECTORS & INTEGRATIONS
    # ========================================================================
    pdf.part_title("IV", "Connectors & Integrations")

    # ---- Chapter 15: Native Connectors ----
    pdf.chapter(15, "Native Connectors")
    pdf.body(
        "AgenticOrg ships with 63 native connectors -- deep, first-party "
        "integrations with popular business tools. Navigate to 'Connectors' "
        "in the sidebar."
    )

    pdf.sub("Connectors by Category")
    pdf.ln(1)
    pdf.bold_bullet(
        "Finance (14)",
        "Stripe, Razorpay, PineLabs, Tally, Zoho Books, Oracle ERP, "
        "SAP S/4HANA, QuickBooks, FreshBooks, Banking AA, GSTN, "
        "PAN Verification, Income Tax, Paytm Biz"
    )
    pdf.bold_bullet(
        "HR (8)",
        "Darwinbox, Keka, greytHR, BambooHR, EPFO, NPS, ESIC, "
        "Workday"
    )
    pdf.bold_bullet(
        "Marketing (16)",
        "HubSpot, Mailchimp, Sendgrid, Google Ads, Meta Ads, LinkedIn "
        "Ads, Mixpanel, Amplitude, Segment, Bombora, G2, TrustRadius, "
        "Hootsuite, Buffer, Semrush, Google Analytics"
    )
    pdf.bold_bullet(
        "Operations (7)",
        "Jira, Asana, Notion, ClickUp, Monday, Trello, ServiceNow"
    )
    pdf.bold_bullet(
        "Communications (11)",
        "Slack, MS Teams, WhatsApp Business, Twilio, Vonage, SendGrid "
        "Email, Gmail, Zoom, Google Meet, Google Chat, Discord"
    )
    pdf.bold_bullet(
        "Microsoft 365 (6)",
        "Outlook, Teams, SharePoint, OneDrive, Excel Online, Power BI"
    )
    pdf.ln(2)

    pdf.sub("Health Checks")
    pdf.body(
        "Each connector shows a health indicator: green circle (healthy), "
        "yellow (degraded), red (down). Health checks run every 5 minutes "
        "automatically. Click a connector to see its last health check "
        "timestamp and latency."
    )

    pdf.sub("Connect / Disconnect")
    pdf.body(
        "To connect a new integration:"
    )
    pdf.numbered(1, "Click the connector card")
    pdf.numbered(
        2,
        "Enter the required credentials (API key, OAuth grant, or "
        "connection string -- varies by connector)"
    )
    pdf.numbered(3, "Click 'Test Connection' to verify")
    pdf.numbered(4, "If the test passes, click 'Save'")
    pdf.body(
        "To disconnect, click the connector card and then 'Disconnect'. "
        "Agents using this connector will show errors until reconnected."
    )

    pdf.sub("Connector Detail Page")
    pdf.body("Click any connector to see its detail page with:")
    pdf.bullet("Connection status and last health check timestamp")
    pdf.bullet("Latency (average response time in milliseconds)")
    pdf.bullet("Tools provided: a list of all tools this connector exposes")
    pdf.bullet("Agents using: which agents have this connector's tools authorized")
    pdf.bullet("Usage stats: number of calls today, this week, this month")
    pdf.bullet("Error log: recent failures with error codes and timestamps")
    pdf.ln(2)

    pdf.sub("Bulk Health Check")
    pdf.body(
        "Click the 'Check All' button at the top of the Connectors page "
        "to trigger an immediate health check on all connectors. Results "
        "update within 10 seconds."
    )

    # ---- Chapter 16: Composio Marketplace ----
    pdf.chapter(16, "Composio Marketplace")
    pdf.body(
        "Beyond the 63 native connectors, AgenticOrg integrates with "
        "1000+ additional apps through the Composio marketplace. Click "
        "the 'Marketplace (1000+)' tab on the Connectors page."
    )

    pdf.sub("Browsing the Marketplace")
    pdf.bold_bullet(
        "Search",
        "Type any app name to find it instantly"
    )
    pdf.bold_bullet(
        "Category Filter",
        "Filter by: CRM, Productivity, Finance, Marketing, HR, DevOps, "
        "Communication, Storage, Analytics, Security"
    )
    pdf.ln(2)

    pdf.sub("22 Most Popular Apps")
    pdf.body(
        "Salesforce, HubSpot, Jira, Slack, Notion, GitHub, GitLab, "
        "Zendesk, Intercom, Freshdesk, Shopify, WooCommerce, Airtable, "
        "Calendly, Typeform, DocuSign, Dropbox, Box, Confluence, Linear, "
        "Figma, Webflow"
    )
    pdf.ln(1)

    pdf.sub("Connecting a Marketplace App")
    pdf.numbered(1, "Find the app and click its card")
    pdf.numbered(2, "Click the 'Connect' button")
    pdf.numbered(
        3,
        "You are redirected to the app's OAuth page to authorize access"
    )
    pdf.numbered(4, "After authorization, you return to AgenticOrg")
    pdf.numbered(
        5,
        "The app's tools are now available to assign to agents"
    )
    pdf.ln(1)
    pdf.note_box("Composio is MIT licensed. No vendor lock-in, fully open source.")

    # ---- Chapter 17: Microsoft 365 ----
    pdf.chapter(17, "Microsoft 365")
    pdf.body(
        "AgenticOrg provides deep integration with the Microsoft 365 "
        "suite. All 6 connectors are native (not marketplace) and support "
        "advanced features."
    )

    pdf.sub("Microsoft Teams")
    pdf.body(
        "Send and receive messages in Teams channels. Agents can post "
        "updates, respond to mentions, and participate in channel "
        "conversations. Supports adaptive cards for rich formatting."
    )

    pdf.sub("Outlook")
    pdf.body(
        "Read, send, and manage emails. Agents can draft replies, "
        "categorize incoming mail, schedule meetings, and manage calendar "
        "events via the Outlook connector."
    )

    pdf.sub("SharePoint")
    pdf.body(
        "Read and write documents in SharePoint libraries. Agents can "
        "upload reports, search documents, and manage list items."
    )

    pdf.sub("OneDrive")
    pdf.body(
        "Upload, download, and manage files in OneDrive. Useful for "
        "agents that generate reports or process uploaded documents."
    )

    pdf.sub("Excel Online")
    pdf.body(
        "Read and write data in Excel workbooks stored in OneDrive or "
        "SharePoint. Agents can update cells, read ranges, and create "
        "tables -- ideal for financial reporting agents."
    )

    pdf.sub("Power BI")
    pdf.body(
        "Trigger dataset refreshes and read dashboard metrics. Finance "
        "and operations agents can pull Power BI KPIs into their analysis."
    )
    pdf.tip_box("All Microsoft connectors use OAuth 2.0 via Azure AD.")

    # ========================================================================
    # PART 5: KNOWLEDGE & INTELLIGENCE
    # ========================================================================
    pdf.part_title("V", "Knowledge & Intelligence")

    # ---- Chapter 18: Knowledge Base ----
    pdf.chapter(18, "Knowledge Base")
    pdf.body(
        "The Knowledge Base lets you upload company documents so agents "
        "can search and reference them. Navigate to 'Knowledge Base' in "
        "the sidebar."
    )

    pdf.sub("Uploading Documents")
    pdf.body("There are two ways to upload:")
    pdf.numbered(
        1,
        "Drag and Drop: Drag files directly onto the upload area on the page"
    )
    pdf.numbered(
        2,
        "Click to Browse: Click the upload area and select files from your computer"
    )
    pdf.ln(1)
    pdf.body("Supported file types:")
    pdf.bullet("PDF (.pdf)")
    pdf.bullet("Microsoft Word (.docx, .doc)")
    pdf.bullet("Microsoft Excel (.xlsx, .xls)")
    pdf.bullet("Plain Text (.txt)")
    pdf.bullet("HTML (.html)")
    pdf.bullet("Markdown (.md)")
    pdf.bullet("CSV (.csv)")
    pdf.ln(2)

    pdf.sub("Document List")
    pdf.body(
        "After uploading, each document appears in a list with these columns:"
    )
    pdf.bullet("File name and type icon")
    pdf.bullet("Upload date and uploader name")
    pdf.bullet(
        "Status: 'Processing' (being chunked and indexed), 'Indexed' "
        "(ready for search), or 'Failed' (error during processing)"
    )
    pdf.bullet("Number of chunks extracted")
    pdf.bullet("File size")
    pdf.bullet("Delete button")
    pdf.ln(2)

    pdf.sub("Knowledge Base Stats")
    pdf.body("At the top of the page, 3 stat cards show:")
    pdf.bold_bullet("Total Documents", "Number of files uploaded")
    pdf.bold_bullet("Total Chunks", "Number of text chunks indexed for search")
    pdf.bold_bullet("Storage Used", "Total file size (e.g., '45 MB of 1 GB')")
    pdf.ln(1)

    pdf.sub("Search")
    pdf.body(
        "Use the search bar at the top to query your knowledge base. "
        "Results show matching chunks with relevance scores. Agents use "
        "the same search during execution."
    )
    pdf.tip_box("Documents are isolated per company. Other tenants cannot see your files.")

    pdf.sub("Best Practices for Knowledge Base")
    pdf.bullet(
        "Upload policy documents, SOPs, process manuals, and FAQ sheets "
        "-- these give agents the context they need to make accurate decisions"
    )
    pdf.bullet(
        "Use clear, descriptive file names (e.g., 'Refund_Policy_2026.pdf' "
        "not 'doc1.pdf') -- this helps search relevance"
    )
    pdf.bullet(
        "Keep documents under 50 pages each. Split large documents into "
        "topic-specific files for better chunking"
    )
    pdf.bullet(
        "Remove documents that are outdated or superseded. Stale data "
        "can cause agents to give incorrect answers."
    )
    pdf.bullet(
        "Check the 'Indexed' status before expecting agents to use new "
        "documents. Processing takes 1-3 minutes depending on file size."
    )
    pdf.ln(2)

    # ---- Chapter 19: Smart LLM Routing ----
    pdf.chapter(19, "Smart LLM Routing")
    pdf.body(
        "Not every task needs the most expensive AI model. Smart LLM "
        "Routing automatically selects the right model tier based on task "
        "complexity, saving you money without sacrificing quality."
    )

    pdf.sub("3 Routing Tiers")
    pdf.ln(1)
    cols = ["Tier", "Models", "Cost", "Best For"]
    widths = [25, 55, 35, 75]
    pdf.table_header(cols, widths)
    pdf.table_row(
        ["Economy", "Gemini Flash, Llama", "$0.10/1K tok", "Simple lookups, data extraction"],
        widths
    )
    pdf.table_row(
        ["Standard", "Claude Sonnet, GPT-4o-mini", "$0.50/1K tok", "Analysis, reports, decisions"],
        widths, fill=True
    )
    pdf.table_row(
        ["Premium", "Claude Opus, GPT-4o", "$2.00/1K tok", "Complex reasoning, strategy"],
        widths
    )
    pdf.ln(3)

    pdf.sub("How It Works")
    pdf.numbered(
        1,
        "When an agent receives a task, the router analyzes its complexity"
    )
    pdf.numbered(
        2,
        "Based on complexity score, it selects the appropriate tier"
    )
    pdf.numbered(
        3,
        "If the selected model's confidence is too low, the router "
        "automatically escalates to the next tier"
    )
    pdf.numbered(
        4,
        "Cost is tracked per agent on the Cost tab"
    )
    pdf.ln(2)

    pdf.sub("Air-Gapped Mode")
    pdf.body(
        "For organizations that cannot send data to external APIs, "
        "AgenticOrg supports fully local models (Llama, Mistral) running "
        "on your own hardware. Enable air-gapped mode in Settings > Fleet "
        "to route all requests to on-premise models."
    )
    pdf.warn_box("Air-gapped mode requires significant GPU resources on your servers.")

    # ---- Chapter 20: Explainable AI ----
    pdf.chapter(20, "Explainable AI")
    pdf.body(
        "Every agent decision in AgenticOrg comes with a 'Why?' panel "
        "that explains the reasoning in plain language. This is critical "
        "for compliance, auditing, and building trust."
    )

    pdf.sub("The 'Why?' Panel")
    pdf.body("Click 'Why?' on any agent output to see:")
    pdf.bullet("Bullet-point summary of the reasoning chain")
    pdf.bullet("Confidence bar showing how sure the agent is (0-100%)")
    pdf.bullet("Tools cited: which connectors/tools were called and what data they returned")
    pdf.bullet(
        "Knowledge sources: which documents from the Knowledge Base "
        "were referenced"
    )
    pdf.bullet(
        "Readability grade: a Flesch-Kincaid score showing how easy the "
        "explanation is to understand (target: Grade 8 or below)"
    )
    pdf.ln(2)

    pdf.sub("Who Sees It")
    pdf.body(
        "The 'Why?' panel is visible to all users who have access to the "
        "agent. Auditors see it in read-only mode. Admins can export "
        "explanations as evidence for compliance audits."
    )
    pdf.tip_box("The 'Why?' panel is auto-generated. No extra configuration needed.")

    pdf.sub("Example 'Why?' Output")
    pdf.body(
        "Here is what a typical 'Why?' panel looks like for an invoice "
        "processing agent:"
    )
    pdf.bullet("Step 1: Read invoice PDF using OCR tool (Tally connector)")
    pdf.bullet("Step 2: Extracted vendor GSTIN: 29ABCDE1234F1Z5")
    pdf.bullet("Step 3: Validated GSTIN against GSTN database -- status: Active")
    pdf.bullet("Step 4: Matched invoice line items with PO #4521 -- 3 of 3 items match")
    pdf.bullet("Step 5: Total amount Rs 2,45,000 is below CFO approval threshold")
    pdf.bullet("Decision: Approved for payment via scheduled batch")
    pdf.bullet("Confidence: 94%")
    pdf.ln(2)
    pdf.body(
        "This level of transparency is critical for compliance in "
        "regulated industries. Auditors can trace every decision back to "
        "its source data and reasoning chain."
    )

    # ========================================================================
    # PART 6: VOICE, RPA & AUTOMATION
    # ========================================================================
    pdf.part_title("VI", "Voice, RPA & Automation")

    # ---- Chapter 21: Voice Agents ----
    pdf.chapter(21, "Voice Agents")
    pdf.body(
        "Turn any AI agent into a phone-based assistant. Customers call a "
        "phone number, the voice agent answers using speech-to-text and "
        "text-to-speech, processes the request using the same tools as "
        "regular agents, and speaks the response aloud."
    )

    pdf.sub("Voice Setup: 5-Step Wizard")
    pdf.body(
        "Navigate to 'Voice Agents' in the sidebar and click 'Set Up Voice' "
        "on any agent. The wizard has 5 steps:"
    )
    pdf.ln(1)

    pdf.step_box(
        "1", "Provider",
        "Choose: Twilio, Vonage, or Custom SIP",
        BLUE
    )
    pdf.body(
        "Select your telephony provider. Twilio and Vonage are fully "
        "supported with pre-built adapters. 'Custom SIP' lets you connect "
        "any SIP-compatible provider by entering a SIP URI."
    )

    pdf.step_box(
        "2", "Credentials",
        "Enter account credentials (encrypted at rest)",
        GREEN
    )
    pdf.body(
        "For Twilio: enter Account SID and Auth Token. For Vonage: enter "
        "API Key and API Secret. Credentials are stored encrypted (AES-256) "
        "and masked in the UI. Click 'Test Connection' to verify before "
        "proceeding."
    )

    pdf.step_box(
        "3", "Phone Number",
        "Select or enter the phone number to use",
        ORANGE
    )
    pdf.body(
        "If your provider has available numbers, they appear in a dropdown. "
        "Otherwise, enter the phone number manually (with country code, "
        "e.g., +91-9876543210)."
    )

    pdf.step_box(
        "4", "STT/TTS Configuration",
        "Speech-to-Text and Text-to-Speech engines",
        PURPLE
    )
    pdf.body(
        "Speech-to-Text (STT): Whisper (local, default) -- runs on your "
        "server, no data leaves your network. Alternative: Google STT or "
        "Azure STT (cloud). Text-to-Speech (TTS): Piper (local, default) "
        "-- runs locally, no cloud dependency. Alternative: Google TTS or "
        "Azure TTS."
    )
    pdf.note_box("Local STT/TTS means voice data never leaves your infrastructure.")

    pdf.step_box(
        "5", "Review and Save",
        "Review configuration and activate",
        TEAL
    )
    pdf.body(
        "Review all settings: provider, phone number, STT/TTS engines. "
        "Click 'Save & Activate' to enable voice for this agent. A test "
        "call option is available to verify everything works."
    )
    pdf.ln(2)

    pdf.sub("Voice Tab in Agent Detail")
    pdf.body(
        "Once voice is enabled, the agent's detail page shows a 'Voice' tab "
        "with a call log table:"
    )
    cols = ["Column", "Description"]
    widths = [40, 150]
    pdf.table_header(cols, widths)
    pdf.table_row(["Call ID", "Unique identifier for each call"], widths)
    pdf.table_row(["Phone Number", "Caller's phone number"], widths, fill=True)
    pdf.table_row(["Duration", "Call length in minutes:seconds"], widths)
    pdf.table_row(["Status", "Completed, Failed, or In Progress"], widths, fill=True)
    pdf.table_row(["Timestamp", "When the call occurred"], widths)
    pdf.table_row(["Recording", "Link to play back the call audio"], widths, fill=True)
    pdf.ln(2)

    pdf.sub("Voice Call Flow")
    pdf.body("When a customer calls the voice agent, this is what happens:")
    pdf.numbered(1, "The call arrives at your telephony provider (Twilio/Vonage)")
    pdf.numbered(2, "The provider forwards the call to AgenticOrg via SIP/WebSocket")
    pdf.numbered(3, "The STT engine converts the caller's speech to text")
    pdf.numbered(
        4,
        "The AI agent processes the text using the same tools and logic "
        "as a regular (text-based) agent"
    )
    pdf.numbered(5, "The agent's text response is converted to speech by the TTS engine")
    pdf.numbered(6, "The spoken response plays back to the caller")
    pdf.numbered(
        7,
        "The conversation continues in real-time until the caller hangs "
        "up or the agent completes the task"
    )
    pdf.ln(1)
    pdf.body(
        "Average response latency is under 2 seconds with local STT/TTS "
        "(Whisper + Piper). Cloud-based STT/TTS may add 0.5-1 second of "
        "additional latency."
    )
    pdf.tip_box("Use local STT/TTS (Whisper + Piper) for lowest latency and best privacy.")

    # ---- Chapter 22: Browser RPA ----
    pdf.chapter(22, "Browser RPA")
    pdf.body(
        "Some government portals and legacy systems do not have APIs. "
        "Browser RPA lets agents navigate these websites automatically -- "
        "filling forms, clicking buttons, and extracting data."
    )

    pdf.sub("4 Pre-Built RPA Scripts")
    pdf.ln(1)
    pdf.bold_bullet(
        "EPFO ECR Download",
        "Logs into the EPFO employer portal, navigates to ECR, downloads "
        "provident fund data for the selected month. Requires: EPFO "
        "username and password."
    )
    pdf.bold_bullet(
        "MCA Company Search",
        "Searches the Ministry of Corporate Affairs portal for company "
        "details by CIN or name. Returns: company name, registration "
        "date, status, directors."
    )
    pdf.bold_bullet(
        "Income Tax 26AS",
        "Logs into the Income Tax portal and downloads Form 26AS (tax "
        "credit statement) for a given PAN and assessment year."
    )
    pdf.bold_bullet(
        "GST Return Status",
        "Checks the GST portal for return filing status. Returns: GSTR-1, "
        "GSTR-3B, and annual return status for the selected period."
    )
    pdf.ln(2)

    pdf.sub("Running an RPA Script")
    pdf.numbered(1, "Go to 'RPA Scripts' in the sidebar")
    pdf.numbered(2, "Click the script you want to run")
    pdf.numbered(
        3,
        "A dialog appears asking for required parameters (e.g., username, "
        "password, date range)"
    )
    pdf.numbered(4, "Click 'Run'")
    pdf.numbered(5, "Watch the execution in real-time or check back later")
    pdf.ln(2)

    pdf.sub("Execution History and Audit")
    pdf.body(
        "Every RPA run is logged with: run ID, script name, start/end "
        "time, status, and a full screenshot at every step. Screenshots "
        "serve as audit evidence for compliance."
    )
    pdf.warn_box("Store RPA credentials in the secure vault. Never hardcode passwords.")

    # ---- Chapter 23: Industry Packs ----
    pdf.chapter(23, "Industry Packs")
    pdf.body(
        "Industry Packs are pre-built bundles of agents, workflows, and "
        "configurations tailored for specific industries. One-click install "
        "deploys everything in Shadow mode."
    )

    pdf.sub("4 Available Packs")
    pdf.ln(1)
    pdf.bold_bullet(
        "Healthcare Pack (6 agents)",
        "Patient Intake Agent, Claims Processing Agent, Appointment "
        "Scheduler, Medical Records Agent, Referral Coordinator, "
        "Compliance Monitor. All agents are HIPAA-aware and scrub PHI."
    )
    pdf.bold_bullet(
        "Legal Pack (5 agents)",
        "Contract Review Agent, Case Research Agent, Document Drafting "
        "Agent, Compliance Checker, Legal Billing Agent. Trained on "
        "Indian and US legal frameworks."
    )
    pdf.bold_bullet(
        "Insurance Pack (5 agents)",
        "Underwriting Agent, Claims Adjudication Agent, Policy Renewal "
        "Agent, Fraud Detection Agent, Customer Service Agent. Supports "
        "IRDAI compliance."
    )
    pdf.bold_bullet(
        "Manufacturing Pack (5 agents)",
        "Production Planning Agent, Quality Inspection Agent, Supply "
        "Chain Agent, Maintenance Scheduler, Inventory Manager. "
        "Integrates with SAP and Oracle ERP."
    )
    pdf.ln(2)

    pdf.sub("Installing a Pack")
    pdf.numbered(1, "Go to 'Industry Packs' in the sidebar")
    pdf.numbered(2, "Click the pack you want (e.g., 'Healthcare')")
    pdf.numbered(3, "Review the list of agents and workflows that will be created")
    pdf.numbered(4, "Click 'Install Pack'")
    pdf.numbered(
        5,
        "All agents are created in Shadow mode. Workflows are created in "
        "Draft mode."
    )
    pdf.ln(1)

    pdf.sub("Uninstalling a Pack")
    pdf.body(
        "To remove a pack, go to Industry Packs, click the installed pack, "
        "and click 'Uninstall'. This removes all agents and workflows "
        "created by the pack. Any customizations you made are lost."
    )
    pdf.warn_box("Uninstalling a pack permanently deletes its agents and workflows.")

    # ========================================================================
    # PART 7: APPROVALS & GOVERNANCE
    # ========================================================================
    pdf.part_title("VII", "Approvals & Governance")

    # ---- Chapter 24: Human-in-the-Loop Approvals ----
    pdf.chapter(24, "Human-in-the-Loop Approvals")
    pdf.body(
        "When an agent is unsure (confidence below threshold) or a "
        "business rule triggers (e.g., amount > 5 lakhs), execution "
        "pauses and waits for human approval."
    )

    pdf.sub("Approval Queue")
    pdf.body(
        "Navigate to 'Approvals' in the sidebar. You see a two-tab layout:"
    )
    pdf.bold_bullet(
        "Pending Tab",
        "All items waiting for your decision, sorted by priority "
        "(Critical > High > Medium > Low)"
    )
    pdf.bold_bullet(
        "Decided Tab",
        "Historical record of all items you have approved, rejected, or "
        "escalated"
    )
    pdf.ln(2)

    pdf.sub("Priority Filter")
    pdf.body(
        "Use the priority dropdown to filter: All, Critical, High, Medium, "
        "Low. Critical items appear with a red left border and a pulsing "
        "indicator."
    )

    pdf.sub("Actions on Each Item")
    pdf.body("For each pending item, you have 3 action buttons:")
    pdf.bold_bullet(
        "Approve",
        "The agent proceeds with its proposed action. You can add a "
        "comment before approving."
    )
    pdf.bold_bullet(
        "Reject",
        "The agent's proposed action is blocked. The rejection reason is "
        "logged and the agent learns from it."
    )
    pdf.bold_bullet(
        "Escalate",
        "Forward the item to a higher authority (e.g., from Manager to "
        "CXO). The escalation path follows the Org Chart hierarchy."
    )
    pdf.ln(2)

    pdf.sub("Web Push Notifications")
    pdf.body(
        "When a new HITL item arrives, you receive a browser push "
        "notification (if enabled). Click the notification to go directly "
        "to the approval item. Enable notifications from Settings > "
        "Notifications."
    )
    pdf.tip_box("Enable notifications to avoid delays on critical approvals.")

    # ---- Chapter 25: Scope Enforcement ----
    pdf.chapter(25, "Scope Enforcement")
    pdf.body(
        "Scope enforcement is AgenticOrg's permission system. It ensures "
        "every agent can only use tools it has been explicitly authorized "
        "for, at the correct permission level."
    )

    pdf.sub("Permission Hierarchy")
    pdf.body(
        "Permissions follow a strict hierarchy. A higher permission "
        "includes all lower permissions:"
    )
    pdf.numbered(1, "ADMIN (highest) -- can configure, delete, write, and read")
    pdf.numbered(2, "DELETE -- can delete, write, and read")
    pdf.numbered(3, "WRITE -- can create, update, and read")
    pdf.numbered(4, "READ (lowest) -- can only view data")
    pdf.ln(2)

    pdf.sub("Scope Dashboard Stats")
    pdf.body("The Scope Dashboard (sidebar) shows 4 key stats:")
    pdf.bold_bullet("Total Agents", "Number of agents with scope assignments")
    pdf.bold_bullet("Tool Calls Today", "Number of tool calls made by all agents today")
    pdf.bold_bullet("Denial Rate", "Percentage of tool calls blocked due to insufficient scope")
    pdf.bold_bullet("Coverage", "Percentage of agents that have complete scope assignments")
    pdf.ln(2)

    pdf.sub("Filters")
    pdf.body(
        "Filter the enforcement table by: agent name, tool name, "
        "permission level, or status (granted/denied)."
    )

    pdf.sub("Enforcement Table")
    pdf.body("Each row in the table shows:")
    pdf.bullet("Agent name")
    pdf.bullet("Tool name")
    pdf.bullet("Required scope (e.g., WRITE)")
    pdf.bullet("Granted scope (e.g., READ)")
    pdf.bullet("Status: Granted (green) or Denied (red)")
    pdf.bullet("Last checked timestamp")
    pdf.ln(2)

    pdf.sub("How Scope Enforcement Works")
    pdf.body(
        "Every time an agent attempts to use a tool, the Grantex engine "
        "performs an offline permission check in under 1 millisecond. "
        "It reads the connector's manifest (which defines the minimum "
        "scope needed for each tool) and compares it against the agent's "
        "grant token. If the agent's scope is insufficient, the tool call "
        "is blocked immediately and logged in the Enforce Audit Log."
    )
    pdf.body(
        "This check happens locally -- no internet call is needed. It "
        "works even in air-gapped deployments. The 63 native connector "
        "manifests cover all tools comprehensively."
    )
    pdf.warn_box("Denied tool calls are always logged. Repeated denials trigger alerts.")

    # ---- Chapter 26: Audit Log ----
    pdf.chapter(26, "Audit Log")
    pdf.body(
        "AgenticOrg maintains two audit logs: the Enforce Audit Log "
        "(scope enforcement decisions) and the General Audit Log (all "
        "platform events)."
    )

    pdf.sub("Enforce Audit Log")
    pdf.body(
        "Navigate to 'Enforce Audit Log' in the sidebar. This log records "
        "every scope enforcement decision with 7 columns:"
    )
    cols7 = ["Column", "Description"]
    widths7 = [40, 150]
    pdf.table_header(cols7, widths7)
    pdf.table_row(["Timestamp", "When the enforcement check occurred"], widths7)
    pdf.table_row(["Agent", "Which agent made the tool call"], widths7, fill=True)
    pdf.table_row(["Tool", "Which tool was requested"], widths7)
    pdf.table_row(["Required Scope", "What permission the tool needs"], widths7, fill=True)
    pdf.table_row(["Granted Scope", "What permission the agent has"], widths7)
    pdf.table_row(["Decision", "ALLOWED or DENIED"], widths7, fill=True)
    pdf.table_row(["Details", "Reason for denial (if applicable)"], widths7)
    pdf.ln(2)

    pdf.body(
        "Filters: Toggle 'Denied Only' to see only blocked calls. "
        "Export to CSV with the 'Export CSV' button. Pagination: 50 rows "
        "per page."
    )

    pdf.sub("General Audit Log")
    pdf.body(
        "Navigate to 'Audit Log' in the sidebar. This log records all "
        "platform events:"
    )
    pdf.bullet("Agent created, updated, promoted, rolled back, deleted")
    pdf.bullet("Workflow created, run, completed, failed, cancelled")
    pdf.bullet("Connector connected, disconnected, health change")
    pdf.bullet("User login, logout, password change, role change")
    pdf.bullet("HITL approval, rejection, escalation")
    pdf.bullet("Knowledge Base upload, deletion, indexing")
    pdf.bullet("Settings change, API key created/revoked")
    pdf.ln(1)
    pdf.body(
        "Each entry shows: timestamp, user/agent, event type, description, "
        "and IP address. Click any entry to see full details. Export "
        "evidence packages for compliance audits."
    )
    pdf.note_box("Audit logs are append-only (WORM) with 7-year retention.")

    # ========================================================================
    # PART 8: DASHBOARDS
    # ========================================================================
    pdf.part_title("VIII", "Dashboards")

    # ---- Chapter 27: CFO Dashboard ----
    pdf.chapter(27, "CFO Dashboard")
    pdf.body(
        "The CFO Dashboard gives finance leaders a real-time view of the "
        "company's financial health. Navigate to 'CFO Dashboard' in the "
        "sidebar."
    )

    pdf.sub("Top Metric Cards")
    pdf.bold_bullet(
        "Cash Runway",
        "Months of cash remaining at current burn rate (e.g., '14.2 months')"
    )
    pdf.bold_bullet(
        "Burn Rate",
        "Monthly cash outflow (e.g., 'Rs 12.5L/month')"
    )
    pdf.bold_bullet(
        "DSO (Days Sales Outstanding)",
        "Average days to collect receivables (e.g., '32 days')"
    )
    pdf.bold_bullet(
        "DPO (Days Payable Outstanding)",
        "Average days to pay vendors (e.g., '45 days')"
    )
    pdf.ln(2)

    pdf.sub("Charts and Visualizations")
    pdf.bold_bullet(
        "AR/AP Aging Pie Charts",
        "Two pie charts showing Accounts Receivable and Accounts Payable "
        "broken down by aging buckets: 0-30, 31-60, 61-90, 90-120, 120+ days"
    )
    pdf.bold_bullet(
        "P&L Line Chart",
        "Revenue vs. Expenses over the last 12 months. Hover to see exact "
        "figures for any month."
    )
    pdf.ln(2)

    pdf.sub("Additional Sections")
    pdf.bold_bullet(
        "Bank Balances",
        "Current balance across all connected bank accounts. Each account "
        "shows bank name, account number (masked), and available balance."
    )
    pdf.bold_bullet(
        "Tax Calendar",
        "Upcoming tax deadlines: GST filing dates, advance tax due dates, "
        "TDS deposit deadlines. Color-coded: green (filed), yellow (upcoming "
        "within 7 days), red (overdue)."
    )
    pdf.bold_bullet(
        "Pending Approvals",
        "Finance-related HITL items waiting for CFO decision: payments, "
        "reimbursements, vendor onboarding."
    )

    pdf.sub("Using the CFO Dashboard")
    pdf.body(
        "The CFO Dashboard auto-refreshes every 60 seconds. All data is "
        "pulled from connected finance connectors (Tally, Zoho Books, "
        "Banking AA, GSTN, etc.). If a connector is disconnected, that "
        "section shows a 'Data unavailable' message with a link to "
        "reconnect."
    )
    pdf.body(
        "Click any metric card to drill down into the underlying data. "
        "For example, clicking 'DSO' opens a table showing each "
        "outstanding invoice with customer name, amount, days overdue, "
        "and a 'Send Reminder' button."
    )
    pdf.tip_box("Export any chart as PNG or the data as CSV using the export menu.")

    # ---- Chapter 28: CMO Dashboard ----
    pdf.chapter(28, "CMO Dashboard")
    pdf.body(
        "The CMO Dashboard provides marketing leaders with a unified "
        "view of all marketing performance data. Navigate to 'CMO "
        "Dashboard' in the sidebar."
    )

    pdf.sub("Top Metric Cards")
    pdf.bold_bullet(
        "CAC (Customer Acquisition Cost)",
        "Average cost to acquire one customer across all channels"
    )
    pdf.bold_bullet(
        "MQLs (Marketing Qualified Leads)",
        "Number of leads that meet marketing qualification criteria this month"
    )
    pdf.bold_bullet(
        "SQLs (Sales Qualified Leads)",
        "Number of leads that have been accepted by sales this month"
    )
    pdf.bold_bullet(
        "Pipeline Value",
        "Total dollar value of all active opportunities in the pipeline"
    )
    pdf.ln(2)

    pdf.sub("Charts and Visualizations")
    pdf.bold_bullet(
        "ROAS by Channel (Bar Chart)",
        "Return on Ad Spend for each channel: Google Ads, Meta Ads, "
        "LinkedIn Ads, Email, Organic. Color-coded bars with ROAS value "
        "labels."
    )
    pdf.bold_bullet(
        "Email Performance",
        "Key email metrics: open rate, click rate, unsubscribe rate, "
        "bounce rate. Trend over last 30 days."
    )
    pdf.ln(2)

    pdf.sub("Additional Sections")
    pdf.bold_bullet(
        "Social Engagement",
        "Likes, shares, comments, and follower growth across LinkedIn, "
        "Twitter, Facebook, and Instagram"
    )
    pdf.bold_bullet(
        "Website Traffic",
        "Sessions, page views, bounce rate, and average session duration "
        "from Google Analytics"
    )
    pdf.bold_bullet(
        "Content Top Pages",
        "Top 10 most-visited pages on your website with pageviews and "
        "time on page"
    )
    pdf.bold_bullet(
        "Brand Sentiment",
        "Positive/Negative/Neutral sentiment breakdown from social "
        "listening and review platforms"
    )

    # ---- Chapter 29: ABM Dashboard ----
    pdf.chapter(29, "ABM Dashboard")
    pdf.body(
        "The ABM (Account-Based Marketing) Dashboard helps you manage "
        "targeted campaigns for high-value accounts. Navigate to 'ABM "
        "Dashboard' in the sidebar."
    )

    pdf.sub("Account List")
    pdf.body(
        "A table of all target accounts showing: company name, tier "
        "(Tier 1/2/3), industry, estimated deal size, intent score, "
        "engagement score, and campaign status."
    )

    pdf.sub("Tier Filter")
    pdf.body(
        "Filter accounts by tier: All, Tier 1 (Strategic), Tier 2 "
        "(Growth), Tier 3 (Scale). Tier 1 accounts get personalized "
        "campaigns; Tier 3 accounts get automated outreach."
    )

    pdf.sub("Intent Score Heatmap")
    pdf.body(
        "A heatmap showing intent signals from 3 data providers:"
    )
    pdf.bold_bullet("Bombora", "Topic-level intent data (what companies are researching)")
    pdf.bold_bullet("G2", "Product comparison and review activity")
    pdf.bold_bullet("TrustRadius", "Vendor evaluation and shortlisting signals")
    pdf.body(
        "Each cell is color-coded: dark red (high intent), orange "
        "(medium), green (low). Click any cell to see the underlying "
        "intent topics."
    )
    pdf.ln(1)

    pdf.sub("CSV Upload")
    pdf.body(
        "Upload a CSV of target accounts with columns: company_name, "
        "domain, tier, industry, deal_size. Click 'Upload Accounts' and "
        "select your file."
    )

    pdf.sub("Launch Campaign Modal")
    pdf.body(
        "Select one or more accounts and click 'Launch Campaign'. A "
        "modal appears with fields: campaign name, channel (email/LinkedIn/"
        "ads), message template, schedule, and budget. Click 'Launch' to "
        "start the campaign."
    )

    # ---- Chapter 30: Sales Pipeline ----
    pdf.chapter(30, "Sales Pipeline")
    pdf.body(
        "The Sales Pipeline page helps you manage leads from first "
        "contact to closed deal. Navigate to 'Sales Pipeline' in the "
        "sidebar."
    )

    pdf.sub("Lead Table")
    pdf.body(
        "A sortable table showing all leads with columns: name, company, "
        "email, phone, source (inbound/outbound/referral), stage, deal "
        "value, assigned rep, and last activity date."
    )

    pdf.sub("Stages Funnel")
    pdf.body("A visual funnel showing lead count at each stage:")
    pdf.numbered(1, "New Lead")
    pdf.numbered(2, "Contacted")
    pdf.numbered(3, "Qualified")
    pdf.numbered(4, "Proposal Sent")
    pdf.numbered(5, "Negotiation")
    pdf.numbered(6, "Closed Won / Closed Lost")
    pdf.ln(2)

    pdf.sub("Process Lead with AI")
    pdf.body(
        "Click 'Process with AI' on any lead to have an AI agent analyze "
        "the lead, enrich company data, score fit, and suggest next steps. "
        "The agent uses connected CRM data, LinkedIn, and intent signals "
        "to produce a comprehensive lead brief."
    )

    pdf.sub("Add Lead")
    pdf.body(
        "Click 'Add Lead' to manually create a new lead. Fill in: name, "
        "company, email, phone, source, and deal value. The lead starts "
        "at the 'New Lead' stage."
    )

    pdf.sub("Follow-Ups")
    pdf.body(
        "Each lead shows upcoming follow-up tasks with due dates. "
        "Overdue follow-ups appear in red. Click 'Add Follow-Up' to "
        "schedule a new task: type (email/call/meeting), date, and notes."
    )

    pdf.sub("AI Lead Scoring")
    pdf.body(
        "The AI scoring model analyzes each lead across 5 dimensions: "
        "company fit (industry, size, geography), engagement level (email "
        "opens, website visits), intent signals (Bombora, G2), budget "
        "indicators, and timing signals. The combined score (0-100) "
        "appears in the 'AI Score' column."
    )
    pdf.body(
        "Leads with AI Score above 80 are auto-tagged as 'Hot Lead' with "
        "a red flame icon. Leads below 30 are tagged 'Cold' with a blue "
        "snowflake icon."
    )

    pdf.sub("Exporting Leads")
    pdf.body(
        "Click 'Export' to download all leads as a CSV file. You can "
        "also export filtered results (e.g., only Hot Leads in Negotiation "
        "stage). The export includes all columns visible in the table."
    )

    # ========================================================================
    # PART 9: ADMINISTRATION
    # ========================================================================
    pdf.part_title("IX", "Administration")

    # ---- Chapter 31: Organization Chart ----
    pdf.chapter(31, "Organization Chart")
    pdf.body(
        "The Org Chart shows how your AI agents are structured "
        "hierarchically. Navigate to 'Org Chart' in the sidebar."
    )

    pdf.sub("Tree View")
    pdf.body(
        "The default view shows a tree diagram with the CEO/Admin agent "
        "at the top, CXO agents below, and specialist agents under each "
        "CXO. Lines connect parent to child agents. Click any node to "
        "see agent details."
    )

    pdf.sub("List View")
    pdf.body(
        "Toggle to 'List View' for a flat, sortable table showing: agent "
        "name, parent, domain, scope level, status, and last activity. "
        "Useful for large organizations with many agents."
    )

    pdf.sub("Domain Filter")
    pdf.body(
        "Filter by domain to see only agents in a specific department: "
        "Finance, HR, Marketing, Operations, Legal, IT, or Custom."
    )

    pdf.sub("Scope Narrowing")
    pdf.body(
        "The Org Chart enforces scope narrowing: a child agent can never "
        "have more permissions than its parent. If the parent has WRITE, "
        "the child can have WRITE or READ, but not DELETE or ADMIN. This "
        "is enforced automatically."
    )

    pdf.sub("Smart Escalation")
    pdf.body(
        "When an agent's confidence is below its threshold, it "
        "automatically escalates to its parent in the Org Chart. If the "
        "parent is also unsure, escalation continues up the chain until "
        "reaching a human decision-maker."
    )

    # ---- Chapter 32: Settings ----
    pdf.chapter(32, "Settings")
    pdf.body(
        "The Settings page lets you configure global platform parameters. "
        "Navigate to 'Settings' in the sidebar."
    )

    pdf.sub("Fleet Limits")
    pdf.body(
        "Set maximum limits for your organization: max agents, max "
        "workflows, max runs per month, and max knowledge base storage. "
        "These limits are determined by your plan but can be adjusted "
        "for Enterprise customers."
    )

    pdf.sub("PII Masking Toggle")
    pdf.body(
        "When enabled (default: ON), all personally identifiable "
        "information (Aadhaar, PAN, GSTIN, UPI ID, email addresses, "
        "phone numbers) is automatically masked BEFORE reaching the AI "
        "model. The AI sees '[AADHAAR_MASKED]' instead of real numbers."
    )

    pdf.sub("Data Region")
    pdf.body(
        "Select where your data is stored: India (Mumbai), US (Virginia), "
        "EU (Frankfurt), or Asia-Pacific (Singapore). Data residency "
        "cannot be changed after initial setup without a support request."
    )

    pdf.sub("API Keys")
    pdf.body("Manage API keys for programmatic access:")
    pdf.numbered(
        1,
        "Click 'Create API Key'. Enter a name (e.g., 'CI/CD Pipeline')."
    )
    pdf.numbered(
        2,
        "The key is generated with an 'ao_sk_' prefix (e.g., "
        "'ao_sk_abc123def456')"
    )
    pdf.numbered(
        3,
        "Copy the key immediately. It is only shown once."
    )
    pdf.numbered(
        4,
        "To revoke, click the trash icon next to the key. Revocation is "
        "immediate and irreversible."
    )
    pdf.ln(1)
    pdf.body(
        "API keys are bcrypt-hashed at rest. The platform stores only "
        "the hash, never the plaintext key."
    )
    pdf.warn_box("Copy your API key immediately. It cannot be retrieved later.")

    pdf.sub("Team Management")
    pdf.body("Invite team members from Settings > Team:")
    pdf.numbered(1, "Click 'Invite Member'")
    pdf.numbered(2, "Enter their email address")
    pdf.numbered(
        3,
        "Select their role: Admin, CFO, CHRO, CMO, COO, or Auditor"
    )
    pdf.numbered(4, "Click 'Send Invite'")
    pdf.body(
        "The invitee receives an email with a link to join your "
        "organization. They can sign up or link their existing account."
    )
    pdf.ln(1)

    pdf.sub("Content Safety")
    pdf.body(
        "All agent outputs are automatically checked for PII leakage, "
        "toxicity, and near-duplicate content before delivery. Content "
        "that fails safety checks is flagged and blocked. This cannot be "
        "disabled."
    )

    # ---- Chapter 33: Billing and Plans ----
    pdf.chapter(33, "Billing and Plans")
    pdf.body(
        "Navigate to 'Billing' in the sidebar to manage your subscription "
        "and usage."
    )

    pdf.sub("Available Plans")
    pdf.ln(1)
    cols = ["Plan", "Price (USD)", "Price (INR)", "Agents", "Workflows", "Runs/Month"]
    widths = [25, 28, 28, 22, 28, 30]
    pdf.table_header(cols, widths)
    pdf.table_row(["Free", "$0", "Rs 0", "3", "5", "1,000"], widths)
    pdf.table_row(
        ["Pro", "$49/mo", "Rs 999/mo", "15", "25", "10,000"],
        widths, fill=True
    )
    pdf.table_row(
        ["Enterprise", "$299/mo", "Rs 4,999/mo", "Unlim.", "Unlim.", "Unlim."],
        widths
    )
    pdf.ln(3)

    pdf.sub("INR Toggle")
    pdf.body(
        "Click the currency toggle at the top of the billing page to "
        "switch between USD and INR pricing. Indian customers are billed "
        "in INR with GST included."
    )

    pdf.sub("Usage Meters")
    pdf.body(
        "The billing page shows usage meters for: agents created vs. "
        "limit, workflows created vs. limit, runs this month vs. limit, "
        "and knowledge base storage used vs. limit. Each meter shows a "
        "progress bar with percentage."
    )

    pdf.sub("Invoices")
    pdf.body(
        "Past invoices are listed with: date, amount, plan, status (paid/"
        "pending/failed), and a 'Download PDF' link."
    )

    pdf.sub("Stripe Checkout")
    pdf.body(
        "Click 'Upgrade' to open the Stripe checkout flow. Enter your "
        "card details or use UPI/Netbanking (India). The subscription "
        "activates immediately after payment."
    )

    pdf.sub("Self-Hosted (Free Forever)")
    pdf.body(
        "If you deploy AgenticOrg on your own servers, it is completely "
        "free. No license fees, no per-seat charges. Apache 2.0 license."
    )

    # ---- Chapter 34: Report Scheduler ----
    pdf.chapter(34, "Report Scheduler")
    pdf.body(
        "The Report Scheduler lets you automate recurring reports. "
        "Navigate to 'Report Scheduler' in the sidebar."
    )

    pdf.sub("Report Types")
    pdf.body("You can schedule these report types:")
    pdf.bullet("Agent Performance Report -- run stats, accuracy, cost per agent")
    pdf.bullet("Financial Summary -- P&L, cash flow, AR/AP aging")
    pdf.bullet("Marketing Report -- campaign performance, lead pipeline, CAC")
    pdf.bullet("HR Report -- headcount, attrition, onboarding progress")
    pdf.bullet("Compliance Report -- audit log summary, scope violations")
    pdf.bullet("Custom Report -- select specific metrics and agents")
    pdf.ln(2)

    pdf.sub("Schedule (Cron)")
    pdf.body(
        "Set the schedule using a cron expression or a friendly picker. "
        "Examples:"
    )
    pdf.bullet("Daily at 9 AM: '0 9 * * *'")
    pdf.bullet("Every Monday at 8 AM: '0 8 * * MON'")
    pdf.bullet("First of every month: '0 9 1 * *'")
    pdf.ln(2)

    pdf.sub("Delivery Channels")
    pdf.body("Reports can be delivered to:")
    pdf.bullet("Email -- sent as an attachment to specified addresses")
    pdf.bullet("Slack -- posted to a specified channel")
    pdf.bullet("WhatsApp -- sent via WhatsApp Business to specified numbers")
    pdf.ln(1)

    pdf.sub("Format")
    pdf.body(
        "Choose the output format: PDF (default, formatted report) or "
        "Excel (raw data with charts). Both formats include the report "
        "title, date range, and generated timestamp."
    )

    # ---- Chapter 35: Onboarding ----
    pdf.chapter(35, "Onboarding")
    pdf.body(
        "When you first sign up, the Onboarding page guides you through "
        "platform setup with a 4-week milestone tracker. Navigate to "
        "'Onboarding' in the sidebar."
    )

    pdf.sub("4-Week Milestone Tracker (16 Tasks)")
    pdf.body("The onboarding is organized into 4 weeks with 4 tasks each:")
    pdf.ln(1)
    pdf.bold_bullet("Week 1: Foundation", "")
    pdf.set_x(20)
    pdf.numbered(1, "Complete company information (name, industry, size)")
    pdf.set_x(20)
    pdf.numbered(2, "Invite team members and assign roles")
    pdf.set_x(20)
    pdf.numbered(3, "Connect your first 3 business tools")
    pdf.set_x(20)
    pdf.numbered(4, "Upload 5 company documents to the Knowledge Base")
    pdf.ln(1)

    pdf.bold_bullet("Week 2: First Agents", "")
    pdf.set_x(20)
    pdf.numbered(5, "Create your first AI agent using natural language")
    pdf.set_x(20)
    pdf.numbered(6, "Review shadow mode outputs and provide feedback")
    pdf.set_x(20)
    pdf.numbered(7, "Promote your first agent to Active")
    pdf.set_x(20)
    pdf.numbered(8, "Set up your first workflow")
    pdf.ln(1)

    pdf.bold_bullet("Week 3: Scale", "")
    pdf.set_x(20)
    pdf.numbered(9, "Create 3 more agents across different domains")
    pdf.set_x(20)
    pdf.numbered(10, "Configure HITL conditions for high-value decisions")
    pdf.set_x(20)
    pdf.numbered(11, "Install an Industry Pack")
    pdf.set_x(20)
    pdf.numbered(12, "Set up your first scheduled report")
    pdf.ln(1)

    pdf.bold_bullet("Week 4: Optimize", "")
    pdf.set_x(20)
    pdf.numbered(13, "Review the CFO/CMO dashboards")
    pdf.set_x(20)
    pdf.numbered(14, "Adjust agent prompts based on feedback")
    pdf.set_x(20)
    pdf.numbered(15, "Configure voice for one agent")
    pdf.set_x(20)
    pdf.numbered(16, "Set up API keys for programmatic access")
    pdf.ln(2)

    pdf.sub("Progress Tracking")
    pdf.body(
        "Each task shows a checkbox. Check off tasks as you complete them. "
        "A progress bar at the top shows overall completion (e.g., "
        "'8 of 16 tasks completed -- 50%'). The onboarding page remains "
        "accessible even after all tasks are done."
    )

    # ========================================================================
    # PART 10: INTEGRATIONS
    # ========================================================================
    pdf.part_title("X", "Integrations")

    # ---- Chapter 36: SDK, MCP & API ----
    pdf.chapter(36, "SDK, MCP & API")
    pdf.body(
        "AgenticOrg provides multiple ways for developers to integrate "
        "programmatically. This chapter covers the SDK, MCP Server, CLI, "
        "and API."
    )

    pdf.sub("Python SDK")
    pdf.body("Install: pip install agenticorg")
    pdf.body("Quick start:")
    pdf.bullet("from agenticorg import AgenticOrg")
    pdf.bullet("client = AgenticOrg(api_key='ao_sk_...')")
    pdf.bullet("result = client.agents.run(agent_id='abc', input='Process this invoice')")
    pdf.bullet("print(result.output)")
    pdf.ln(1)

    pdf.sub("TypeScript SDK")
    pdf.body("Install: npm install agenticorg-sdk")
    pdf.body("Quick start:")
    pdf.bullet("import { AgenticOrg } from 'agenticorg-sdk';")
    pdf.bullet("const client = new AgenticOrg({ apiKey: 'ao_sk_...' });")
    pdf.bullet("const result = await client.agents.run({ agentId: 'abc', input: '...' });")
    pdf.ln(1)

    pdf.sub("MCP Server")
    pdf.body(
        "Connect AgenticOrg tools to Claude Desktop, Cursor, or any "
        "MCP-compatible client:"
    )
    pdf.bullet("npx agenticorg-mcp-server --api-key ao_sk_...")
    pdf.body(
        "This exposes all your connected tools as MCP resources that "
        "external AI assistants can call."
    )
    pdf.ln(1)

    pdf.sub("CLI Commands")
    pdf.body("The AgenticOrg CLI provides these commands:")
    pdf.bullet("agenticorg login -- authenticate via browser")
    pdf.bullet("agenticorg agents list -- list all agents")
    pdf.bullet("agenticorg agents run <id> -- run an agent")
    pdf.bullet("agenticorg workflows list -- list all workflows")
    pdf.bullet("agenticorg workflows run <id> -- run a workflow")
    pdf.bullet("agenticorg connectors status -- check connector health")
    pdf.ln(1)

    pdf.sub("API Keys")
    pdf.body(
        "All programmatic access requires an API key with the 'ao_sk_' "
        "prefix. Create keys in Settings > API Keys. Include the key "
        "in the Authorization header: 'Bearer ao_sk_...'"
    )
    pdf.tip_box("The MCP Server is the fastest way to use AgenticOrg tools in Cursor.")

    # ---- Chapter 37: CDC (Change Data Capture) ----
    pdf.chapter(37, "CDC (Change Data Capture)")
    pdf.body(
        "CDC lets workflows trigger automatically when data changes in "
        "a connected system."
    )

    pdf.sub("Webhook Receivers")
    pdf.body(
        "AgenticOrg provides webhook endpoints for each connected system. "
        "When data changes in the external system, it sends a webhook to "
        "AgenticOrg, which triggers the associated workflow."
    )
    pdf.body("Example: when a new invoice is created in Zoho Books, a webhook fires "
             "and triggers the Invoice-to-Pay workflow automatically.")
    pdf.ln(1)

    pdf.sub("Polling")
    pdf.body(
        "For systems that do not support webhooks, AgenticOrg polls the "
        "system at a configurable interval (e.g., every 5 minutes). When "
        "new or changed records are detected, the associated workflow "
        "triggers."
    )

    pdf.sub("Trigger Workflows on Data Changes")
    pdf.body("To set up CDC:")
    pdf.numbered(1, "Create a workflow with trigger type 'CDC'")
    pdf.numbered(
        2,
        "Select the source connector (e.g., 'Zoho Books')"
    )
    pdf.numbered(
        3,
        "Select the event type (e.g., 'invoice.created', "
        "'employee.onboarded', 'lead.updated')"
    )
    pdf.numbered(
        4,
        "Configure optional filters (e.g., 'amount > 10000')"
    )
    pdf.numbered(5, "Deploy the workflow")
    pdf.ln(1)
    pdf.body(
        "The workflow now runs automatically whenever a matching event "
        "occurs in the connected system."
    )
    pdf.note_box("CDC reduces manual work by reacting to data changes in real time.")

    # ========================================================================
    # PART 11: REFERENCE
    # ========================================================================
    pdf.part_title("XI", "Reference")

    # ---- Chapter 38: Keyboard Shortcuts & Tips ----
    pdf.chapter(38, "Keyboard Shortcuts & Tips")
    pdf.body(
        "AgenticOrg supports keyboard shortcuts for faster navigation."
    )

    pdf.sub("Keyboard Shortcuts")
    pdf.ln(1)
    cols = ["Shortcut", "Action"]
    widths = [50, 140]
    pdf.table_header(cols, widths)
    shortcuts = [
        ("Cmd+K / Ctrl+K", "Open the Natural Language search bar"),
        ("Esc", "Close any open dialog or modal"),
        ("Ctrl+Shift+A", "Go to Agents page"),
        ("Ctrl+Shift+W", "Go to Workflows page"),
        ("Ctrl+Shift+D", "Go to Dashboard"),
        ("Ctrl+Shift+S", "Go to Settings"),
        ("Ctrl+Shift+N", "Open notification panel"),
        ("Ctrl+Enter", "Submit the current form"),
        ("Tab / Shift+Tab", "Move forward/backward between form fields"),
        ("Arrow keys", "Navigate agent cards, table rows, and menu items"),
    ]
    for i, (shortcut, action) in enumerate(shortcuts):
        pdf.table_row([shortcut, action], widths, fill=(i % 2 == 1))
    pdf.ln(3)

    pdf.sub("Tips for Power Users")
    pdf.bold_bullet(
        "NL Search",
        "Use the Cmd+K search for anything: 'show finance agents', "
        "'open CFO dashboard', 'create new workflow'. It understands "
        "natural language."
    )
    pdf.bold_bullet(
        "Language Picker",
        "Switch to Hindi from the header dropdown. All UI labels, "
        "tooltips, and help text update. Agent outputs remain in the "
        "language the agent was configured in."
    )
    pdf.bold_bullet(
        "Notification Bell",
        "Click the bell icon to see all notifications. Unread items show "
        "a red badge. Mark all as read with the 'Mark All Read' button."
    )
    pdf.bold_bullet(
        "Bulk Operations",
        "On the Agents page, select multiple agents using checkboxes, "
        "then use the bulk action menu to pause, resume, or delete them."
    )
    pdf.bold_bullet(
        "Quick Filters",
        "On any table page, click column headers to sort. Use the search "
        "bar for instant filtering."
    )

    # ---- Chapter 39: Troubleshooting ----
    pdf.chapter(39, "Troubleshooting")
    pdf.body(
        "This chapter covers the 15 most common issues and how to resolve "
        "them."
    )
    pdf.ln(1)

    issues = [
        (
            "Agent stuck in Shadow mode",
            "Check the Shadow tab for quality gate failures. Common causes: "
            "sample count below 20, accuracy below 90%, or tool not exercised. "
            "Fix the prompt, run more samples, or click 'Retest' to restart."
        ),
        (
            "Tool call denied (scope error)",
            "Go to the Scope Dashboard and check the agent's permissions. "
            "The agent may need a higher scope level (e.g., WRITE instead of "
            "READ). Edit the agent and update authorized tools."
        ),
        (
            "Agent confidence is too low",
            "Open the 'Why?' panel to understand the reasoning. Common fixes: "
            "add more context to the system prompt, upload relevant documents "
            "to the Knowledge Base, or lower the confidence floor temporarily."
        ),
        (
            "HITL approval not triggering",
            "Check two things: (1) the confidence floor setting on the agent's "
            "Config tab, and (2) the HITL condition expression. Ensure the "
            "condition syntax is correct (e.g., 'amount > 500000')."
        ),
        (
            "Knowledge Base search returns no results",
            "Ensure documents are in 'Indexed' status (not 'Processing'). "
            "Try rephrasing your query. Check that you uploaded relevant "
            "documents. Wait 2-3 minutes for newly uploaded files to index."
        ),
        (
            "Voice agent not answering calls",
            "Verify credentials in the Voice Setup wizard. Click 'Test "
            "Connection' in Step 2. Check that the phone number is correctly "
            "formatted with country code (e.g., +91-XXXXXXXXXX)."
        ),
        (
            "Workflow step failed",
            "Open the workflow execution view and check the failed step's "
            "error message. Enable 'Adaptive Replanning' if not already on. "
            "Common cause: a connector is disconnected or credentials expired."
        ),
        (
            "Connector shows red (unhealthy)",
            "Click the connector to see the error details. Common causes: "
            "expired API key, rate limiting, or service outage. Re-enter "
            "credentials or wait for the external service to recover."
        ),
        (
            "Cannot log in (wrong password)",
            "Use the 'Forgot Password?' flow to reset. If using Google SSO, "
            "ensure you are using the same Google account you signed up with."
        ),
        (
            "Agent producing hallucinations",
            "Roll back the agent to Shadow mode. Review and tighten the "
            "system prompt. Add relevant documents to the Knowledge Base. "
            "Increase the confidence floor to require higher certainty."
        ),
        (
            "Dashboard shows stale data",
            "Click the refresh icon on the dashboard. Data refreshes every "
            "60 seconds by default. Check that connected systems are healthy."
        ),
        (
            "RPA script failing",
            "Government portals frequently change their HTML. Check the "
            "screenshots from the last run to see where it failed. Contact "
            "support if the script needs updating."
        ),
        (
            "API key not working",
            "Ensure the key has the 'ao_sk_' prefix. Keys are case-sensitive. "
            "Check that the key has not been revoked in Settings > API Keys."
        ),
        (
            "Invoice processing errors",
            "Verify GSTIN validation is passing. Check the Banking AA consent "
            "is active. Ensure the OCR agent has access to the relevant "
            "connectors (Tally, Zoho Books, etc.)."
        ),
        (
            "Notification bell not showing updates",
            "Enable browser notifications when prompted. Check Settings > "
            "Notifications to ensure push notifications are turned on. "
            "Clear browser cache if notifications were previously denied."
        ),
    ]

    for i, (title, solution) in enumerate(issues):
        pdf.bold_bullet(f"Issue {i+1}: {title}", "")
        pdf.set_x(18)
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(*BLACK)
        pdf.multi_cell(182, 5, f"Solution: {solution}")
        pdf.ln(2)

    # ---- Chapter 40: Glossary ----
    pdf.chapter(40, "Glossary")
    pdf.body(
        "This glossary defines the key terms used throughout the "
        "AgenticOrg platform."
    )
    pdf.ln(1)

    terms = [
        ("Agent", "An AI virtual employee that performs tasks using tools and LLM reasoning"),
        ("Active Mode", "Agent status where it executes real actions in production"),
        ("Adaptive Replanning", "Workflow feature that re-plans remaining steps when a step fails"),
        ("Air-Gapped Mode", "Running all AI models locally with no external API calls"),
        ("Burn Rate", "Monthly cash outflow, shown on the CFO Dashboard"),
        ("CAC", "Customer Acquisition Cost -- average cost to acquire one customer"),
        ("CDC", "Change Data Capture -- triggering workflows when data changes in connected systems"),
        ("Composio", "Open-source marketplace providing 1000+ tool integrations (MIT license)"),
        ("Confidence Floor", "Minimum confidence before HITL is triggered (default 88%)"),
        ("Connector", "A bridge between an agent and an external system (Jira, Salesforce, etc.)"),
        ("DSO", "Days Sales Outstanding -- average days to collect receivables"),
        ("DPO", "Days Payable Outstanding -- average days to pay vendors"),
        ("Grantex", "The authorization system that enforces scopes on every tool call"),
        ("HITL", "Human-in-the-Loop -- when an agent pauses for human approval"),
        ("Industry Pack", "Pre-built bundle of agents and workflows for a specific industry"),
        ("Kill Switch", "Immediately pauses an agent and halts all in-progress tasks"),
        ("LLM", "Large Language Model -- the AI brain (Gemini, Claude, GPT, Llama)"),
        ("Manifest", "A file defining which permission level each tool requires"),
        ("MCP", "Model Context Protocol -- standard for connecting AI tools to external clients"),
        ("MQL", "Marketing Qualified Lead -- a lead that meets marketing criteria"),
        ("PII", "Personally Identifiable Information -- scrubbed before reaching the LLM"),
        ("Prompt Amendment", "Suggested change to an agent's prompt based on feedback patterns"),
        ("Quality Gate", "A check that must pass before an agent can be promoted from Shadow"),
        ("RAG", "Retrieval-Augmented Generation -- agents search your documents for answers"),
        ("ROAS", "Return on Ad Spend -- revenue generated per dollar of ad spend"),
        ("RPA", "Robotic Process Automation -- browser automation for legacy systems"),
        ("Scope", "Permission level assigned to an agent (READ, WRITE, DELETE, ADMIN)"),
        ("Shadow Mode", "Agent observes without acting; used for quality validation before going live"),
        ("SIP", "Session Initiation Protocol -- how phone calls connect to voice agents"),
        ("Smart Routing", "Automatic selection of the best LLM tier based on task complexity"),
        ("SQL (Sales)", "Sales Qualified Lead -- a lead accepted by the sales team"),
        ("STT", "Speech-to-Text -- converting spoken words to text (default: Whisper local)"),
        ("TTS", "Text-to-Speech -- converting text to spoken words (default: Piper local)"),
        ("Tool", "A specific action a connector provides (e.g., create_ticket, get_contact)"),
        ("Workflow", "A multi-step process that chains multiple agents together"),
        ("WORM", "Write Once Read Many -- audit log cannot be modified after writing"),
    ]

    for i, (term, defn) in enumerate(terms):
        pdf.bold_bullet(term, defn)
        if (i + 1) % 18 == 0 and i < len(terms) - 1:
            pdf.ln(1)

    # ---- Appendix: Security Best Practices ----
    pdf.chapter("A", "Appendix: Security Best Practices")
    pdf.body(
        "This appendix summarizes the key security practices every user "
        "should follow when using AgenticOrg."
    )

    pdf.sub("Account Security")
    pdf.numbered(1, "Use a strong password (8+ characters, uppercase, number, special character)")
    pdf.numbered(2, "Enable Google SSO if your organization uses Google Workspace")
    pdf.numbered(3, "Never share your login credentials with anyone")
    pdf.numbered(4, "Log out after each session on shared computers")
    pdf.ln(2)

    pdf.sub("API Key Security")
    pdf.numbered(1, "Store API keys in environment variables, not in code")
    pdf.numbered(2, "Rotate keys every 90 days")
    pdf.numbered(3, "Create separate keys for each integration (e.g., CI/CD, testing, production)")
    pdf.numbered(4, "Revoke keys immediately if compromised")
    pdf.ln(2)

    pdf.sub("Agent Security")
    pdf.numbered(1, "Start all agents in Shadow mode -- never skip the observation period")
    pdf.numbered(2, "Use the minimum scope needed (READ if possible, WRITE only if necessary)")
    pdf.numbered(3, "Set HITL conditions for any action involving money, PII, or external communication")
    pdf.numbered(4, "Review the Scope Dashboard weekly for unusual denial patterns")
    pdf.numbered(5, "Keep PII masking enabled (it is on by default)")
    pdf.ln(2)

    pdf.sub("Data Security")
    pdf.numbered(1, "Choose the correct data region during initial setup")
    pdf.numbered(
        2,
        "Use local STT/TTS for voice agents to keep audio data within "
        "your infrastructure"
    )
    pdf.numbered(
        3,
        "Review the Audit Log monthly for unauthorized access attempts"
    )
    pdf.numbered(4, "Export compliance evidence before annual audits")
    pdf.ln(2)

    pdf.sub("Connector Security")
    pdf.numbered(1, "Disconnect connectors you no longer use")
    pdf.numbered(
        2,
        "Use OAuth where available instead of API keys (OAuth tokens "
        "expire and can be revoked centrally)"
    )
    pdf.numbered(3, "Monitor connector health daily for unexpected failures")

    # ---- Appendix: Quick Reference Card ----
    pdf.chapter("B", "Appendix: Quick Reference Card")
    pdf.body("A one-page summary of the most common tasks:")
    pdf.ln(1)
    cols = ["Task", "Where to Go", "Key Action"]
    widths = [55, 60, 75]
    pdf.table_header(cols, widths)
    pdf.table_row(["Create an agent", "Sidebar > Agents", "Click 'Create Agent'"], widths)
    pdf.table_row(["Run a workflow", "Sidebar > Workflows", "Click 'Run Now'"], widths, fill=True)
    pdf.table_row(["Approve a task", "Sidebar > Approvals", "Click 'Approve'"], widths)
    pdf.table_row(["Upload documents", "Sidebar > Knowledge Base", "Drag and drop files"], widths, fill=True)
    pdf.table_row(["Connect a tool", "Sidebar > Connectors", "Click connector card"], widths)
    pdf.table_row(["Check agent cost", "Agent Detail > Cost tab", "View monthly trend"], widths, fill=True)
    pdf.table_row(["Generate API key", "Sidebar > Settings", "Click 'Create API Key'"], widths)
    pdf.table_row(["Schedule a report", "Sidebar > Report Scheduler", "Click 'Create Report'"], widths, fill=True)
    pdf.table_row(["Install industry pack", "Sidebar > Industry Packs", "Click 'Install Pack'"], widths)
    pdf.table_row(["Set up voice", "Agent Detail > Voice tab", "Click 'Set Up Voice'"], widths, fill=True)
    pdf.table_row(["View audit log", "Sidebar > Audit Log", "Browse or export events"], widths)
    pdf.table_row(["Invite team member", "Sidebar > Settings > Team", "Click 'Invite Member'"], widths, fill=True)
    pdf.table_row(["Change plan", "Sidebar > Billing", "Click 'Upgrade'"], widths)
    pdf.table_row(["Search anything", "Header bar", "Press Cmd+K / Ctrl+K"], widths, fill=True)
    pdf.ln(3)

    pdf.body(
        "For additional help, contact support@agenticorg.ai or visit "
        "docs.agenticorg.ai."
    )

    # ========================================================================
    # FINAL PAGE
    # ========================================================================
    pdf.add_page()
    pdf.ln(40)
    pdf.set_font("Helvetica", "B", 22)
    pdf.set_text_color(*NAVY)
    pdf.cell(
        0, 12, "Thank you for using AgenticOrg",
        align="C", new_x="LMARGIN", new_y="NEXT"
    )
    pdf.ln(6)
    pdf.set_font("Helvetica", "", 13)
    pdf.set_text_color(*GRAY)
    pdf.cell(
        0, 8, "agenticorg.ai | support@agenticorg.ai",
        align="C", new_x="LMARGIN", new_y="NEXT"
    )
    pdf.cell(
        0, 8, f"Version {VERSION} | {DATE}",
        align="C", new_x="LMARGIN", new_y="NEXT"
    )
    pdf.ln(8)
    pdf.set_font("Helvetica", "I", 10)
    pdf.set_text_color(120, 120, 120)
    pdf.cell(
        0, 7, "Apache 2.0 License | Open Source | Self-Host Free Forever",
        align="C", new_x="LMARGIN", new_y="NEXT"
    )
    pdf.ln(4)
    pdf.set_draw_color(*NAVY)
    pdf.set_line_width(0.6)
    pdf.line(60, pdf.get_y(), 150, pdf.get_y())
    pdf.ln(8)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(*GRAY)
    pdf.cell(
        0, 7, "For technical documentation, visit docs.agenticorg.ai",
        align="C", new_x="LMARGIN", new_y="NEXT"
    )
    pdf.cell(
        0, 7, "For API reference, visit docs.agenticorg.ai/api",
        align="C", new_x="LMARGIN", new_y="NEXT"
    )

    return pdf


if __name__ == "__main__":
    out = os.path.join(os.path.dirname(__file__), "..", "docs")
    os.makedirs(out, exist_ok=True)
    path = os.path.join(out, f"AgenticOrg_Complete_User_Guide_v{VERSION}.pdf")
    print("Generating complete user guide...")
    p = build()
    p.output(path)
    print(f"Done! {p.pages_count} pages -> {path}")
