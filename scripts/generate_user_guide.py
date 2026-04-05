"""Generate AgenticOrg v4.0.0 End User Guide PDF.

A comprehensive, non-technical guide for business users to understand
and use every feature of the AgenticOrg platform.

Output: docs/AgenticOrg_User_Guide_v4.0.0.pdf
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
PURPLE = (100, 40, 160)
L_PURPLE = (240, 230, 255)
TEAL = (0, 128, 128)
GRAY = (100, 100, 100)
L_GRAY = (245, 245, 250)
BLACK = (30, 30, 30)
WHITE = (255, 255, 255)


class UserGuide(FPDF):
    def __init__(self):
        super().__init__()
        self.set_auto_page_break(auto=True, margin=22)

    def header(self):
        if self.page_no() == 1:
            return
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(*GRAY)
        self.cell(0, 5, "AgenticOrg User Guide", align="L")
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

    def tip_box(self, text):
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

    def step_box(self, num, title, desc, color):
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


def build():
    pdf = UserGuide()

    # ---- COVER ----
    pdf.add_page()
    pdf.set_fill_color(*NAVY)
    pdf.rect(0, 0, 210, 297, style="F")
    pdf.set_fill_color(*WHITE)
    pdf.rect(15, 80, 180, 0.8, style="F")
    pdf.set_font("Helvetica", "B", 34)
    pdf.set_text_color(*WHITE)
    pdf.set_xy(15, 90)
    pdf.cell(180, 16, "AgenticOrg", align="C")
    pdf.set_font("Helvetica", "", 16)
    pdf.set_xy(15, 108)
    pdf.cell(180, 10, "End User Guide", align="C")
    pdf.set_font("Helvetica", "I", 12)
    pdf.set_text_color(180, 200, 255)
    pdf.set_xy(15, 122)
    pdf.cell(180, 8, f"Version {VERSION} - Project Apex", align="C")
    stats = [("50+", "AI Agents"), ("1000+", "Integrations"), ("20+", "Workflows"), ("4", "Industry Packs")]
    sx = 25
    for num, label in stats:
        pdf.set_font("Helvetica", "B", 20)
        pdf.set_text_color(100, 180, 255)
        pdf.set_xy(sx, 145)
        pdf.cell(40, 10, num, align="C")
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(180, 200, 255)
        pdf.set_xy(sx, 157)
        pdf.cell(40, 6, label, align="C")
        sx += 42
    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(180, 200, 255)
    pdf.set_xy(15, 185)
    pdf.cell(180, 7, f"{DATE}", align="C")

    # ---- TOC ----
    pdf.add_page()
    pdf.section("", "Table of Contents")
    toc = [
        ("1", "Getting Started"),
        ("2", "The Dashboard"),
        ("3", "Creating Your First AI Agent"),
        ("4", "Understanding Agent Permissions (Scopes)"),
        ("5", "Running an Agent"),
        ("6", "Workflows - Automating Business Processes"),
        ("7", "Creating Workflows in Plain English"),
        ("8", "Human-in-the-Loop (HITL) Approvals"),
        ("9", "Knowledge Base - Teaching Agents Your Documents"),
        ("10", "Voice Agents - Phone-Based AI"),
        ("11", "Browser Automation (RPA)"),
        ("12", "Industry Packs"),
        ("13", "Dashboards (CFO, CMO, ABM)"),
        ("14", "Multi-Language Support"),
        ("15", "Organization Chart"),
        ("16", "Billing and Plans"),
        ("17", "Settings and Security"),
        ("18", "Integrations (SDK, MCP, API)"),
        ("19", "Troubleshooting"),
        ("20", "Glossary"),
    ]
    for num, title in toc:
        pdf.set_font("Helvetica", "", 11)
        pdf.set_text_color(*BLACK)
        pdf.cell(10, 7, f"{num}.")
        pdf.cell(0, 7, title, new_x="LMARGIN", new_y="NEXT")

    # ---- 1. GETTING STARTED ----
    pdf.add_page()
    pdf.section("1", "Getting Started")
    pdf.body(
        "AgenticOrg is an AI Virtual Employee Platform. Think of it as hiring a team of AI workers that "
        "handle repetitive business tasks 24/7 -- processing invoices, answering customer questions, "
        "managing HR onboarding, monitoring marketing campaigns, and more."
    )
    pdf.sub("Signing Up")
    pdf.body("1. Go to agenticorg.ai and click 'Start Free'\n2. Enter your organization name, admin name, email, and password\n3. You'll be taken to the dashboard immediately")
    pdf.sub("First Login")
    pdf.body("After signing up, you can log in with your email and password, or use Google Sign-In if your organization uses Google Workspace.")
    pdf.tip_box("The free plan includes 3 AI agents, 5 workflows, and 1,000 agent runs per month.")
    pdf.sub("Roles")
    pdf.body("AgenticOrg has 6 roles that control what you can see:")
    pdf.bold_bullet("Admin/CEO", "Sees everything across all departments")
    pdf.bold_bullet("CFO", "Sees finance agents, dashboards, and connectors only")
    pdf.bold_bullet("CHRO", "Sees HR agents and dashboards only")
    pdf.bold_bullet("CMO", "Sees marketing agents and dashboards only")
    pdf.bold_bullet("COO", "Sees operations agents and dashboards only")
    pdf.bold_bullet("Auditor", "Read-only access to the audit log")

    # ---- 2. DASHBOARD ----
    pdf.add_page()
    pdf.section("2", "The Dashboard")
    pdf.body(
        "The main dashboard gives you an overview of your AI workforce. You'll see:"
    )
    pdf.bullet("Agent count and status (active, shadow, paused)")
    pdf.bullet("Recent activity (which agents ran, what they did)")
    pdf.bullet("Quick actions (create agent, run workflow, view approvals)")
    pdf.ln(2)
    pdf.sub("Sidebar Navigation")
    pdf.body("The left sidebar has links to all platform features:")
    pdf.bullet("Dashboard - Overview")
    pdf.bullet("Agents - Manage your AI employees")
    pdf.bullet("Org Chart - See your agent hierarchy")
    pdf.bullet("Workflows - Automate multi-step processes")
    pdf.bullet("Knowledge Base - Upload documents for agents")
    pdf.bullet("Voice Agents - Phone-based AI setup")
    pdf.bullet("RPA Scripts - Browser automation")
    pdf.bullet("Industry Packs - Pre-built agent bundles")
    pdf.bullet("Connectors - Connected business systems")
    pdf.bullet("Approvals - HITL decision queue")
    pdf.bullet("Scope Dashboard - Permission enforcement overview")
    pdf.bullet("Billing - Plan and usage management")
    pdf.bullet("Settings - API keys, team, config")

    # ---- 3. CREATING AN AGENT ----
    pdf.add_page()
    pdf.section("3", "Creating Your First AI Agent")
    pdf.body("There are two ways to create an agent:")
    pdf.sub("Option A: Describe in Plain English (Recommended)")
    pdf.body(
        "Click 'Create Agent' and you'll see a large text box asking 'Describe the employee you need'. "
        "Just type what you want in plain English. For example:"
    )
    pdf.bullet('"I need someone who processes invoices and matches them with purchase orders"')
    pdf.bullet('"Customer support agent that handles refund requests and tracks SLAs"')
    pdf.bullet('"An HR coordinator who manages onboarding for new hires"')
    pdf.ln(2)
    pdf.body("Click 'Generate' and the system will create a full agent configuration for you to review.")
    pdf.tip_box("You can edit any auto-generated field before creating the agent.")
    pdf.sub("Option B: Manual 5-Step Wizard")
    pdf.step_box("1", "Persona", "Name your AI employee, set designation and domain", BLUE)
    pdf.step_box("2", "Role", "Choose agent type (or create custom) and specialization", GREEN)
    pdf.step_box("3", "Prompt", "Select a template or write custom instructions", ORANGE)
    pdf.step_box("4", "Behavior", "Set LLM model, confidence floor, HITL conditions, tools", PURPLE)
    pdf.step_box("5", "Review", "Preview everything and deploy in Shadow Mode", TEAL)
    pdf.ln(2)
    pdf.sub("Shadow Mode")
    pdf.body(
        "All new agents start in Shadow Mode. This means they observe and produce outputs WITHOUT "
        "taking any real actions. Once the agent passes quality checks (accuracy, confidence, "
        "no hallucinations), you can promote it to Active mode."
    )

    # ---- 4. PERMISSIONS ----
    pdf.add_page()
    pdf.section("4", "Understanding Agent Permissions")
    pdf.body(
        "Every tool an agent can use has a permission level. This prevents an agent with 'read-only' "
        "access from accidentally deleting data."
    )
    pdf.sub("Permission Levels (Highest to Lowest)")
    pdf.bold_bullet("ADMIN", "Can do everything - use with extreme caution")
    pdf.bold_bullet("DELETE", "Can delete data + write + read")
    pdf.bold_bullet("WRITE", "Can create/update data + read")
    pdf.bold_bullet("READ", "Can only view data - safest level")
    pdf.ln(2)
    pdf.body(
        "When you create an agent, you'll see colored badges next to each tool showing its permission "
        "level. If you select tools with DELETE or ADMIN permissions, a yellow warning banner appears."
    )
    pdf.tip_box("A higher permission covers all lower ones. WRITE scope can also READ.")
    pdf.sub("How It Works Behind the Scenes")
    pdf.body(
        "Every time an agent tries to use a tool, the system checks the agent's grant token against "
        "53 connector manifests. This check takes less than 1 millisecond and happens offline "
        "(no internet call needed). If the agent doesn't have permission, the tool call is blocked "
        "and logged."
    )

    # ---- 5. RUNNING AN AGENT ----
    pdf.add_page()
    pdf.section("5", "Running an Agent")
    pdf.body("To run an agent, go to its detail page and click 'Run'. You can also trigger runs via:")
    pdf.bullet("API call (POST /agents/{id}/run)")
    pdf.bullet("Workflow step (agent runs as part of a workflow)")
    pdf.bullet("Schedule (via workflow triggers)")
    pdf.bullet("CDC event (when data changes in a connected system)")
    pdf.ln(2)
    pdf.sub("Understanding Results")
    pdf.body("After a run completes, you'll see:")
    pdf.bold_bullet("Output", "The structured result the agent produced")
    pdf.bold_bullet("Confidence", "How sure the agent is (0-100%)")
    pdf.bold_bullet("Why? Panel", "Plain English explanation of the decision")
    pdf.bold_bullet("Tools Used", "Which connectors/tools were called")
    pdf.bold_bullet("Reasoning Trace", "Step-by-step thinking (for developers)")
    pdf.ln(2)
    pdf.sub("Giving Feedback")
    pdf.body(
        "On every run result, you'll see thumbs up/down buttons. Your feedback helps the agent "
        "learn and improve. After enough feedback, the system automatically suggests prompt "
        "improvements that you can apply or dismiss."
    )

    # ---- 6. WORKFLOWS ----
    pdf.add_page()
    pdf.section("6", "Workflows")
    pdf.body(
        "Workflows chain multiple agents together to complete complex business processes. "
        "For example, an invoice-to-pay workflow might: OCR the invoice, validate GSTIN, "
        "3-way match with PO, get approval if amount > 5L, then execute payment."
    )
    pdf.sub("20 Pre-Built Templates")
    pdf.body("AgenticOrg ships with 20 workflow templates including:")
    pdf.bullet("Invoice-to-Pay (AP automation)")
    pdf.bullet("Month-End Close")
    pdf.bullet("Employee Onboarding")
    pdf.bullet("Campaign Launch")
    pdf.bullet("Lead Nurture (with email drip)")
    pdf.bullet("Incident Response")
    pdf.bullet("Expense Reimbursement")
    pdf.bullet("Vendor Onboarding")
    pdf.bullet("Compliance Review")
    pdf.bullet("IT Incident Escalation")
    pdf.ln(2)
    pdf.sub("Adaptive Re-planning")
    pdf.body(
        "If a workflow step fails, the system can automatically re-plan the remaining steps. "
        "For example, if PineLabs payment fails, it might route to NEFT instead. Enable this "
        "with the 'Adaptive Replanning' toggle when creating a workflow."
    )

    # ---- 7. NL WORKFLOWS ----
    pdf.add_page()
    pdf.section("7", "Creating Workflows in Plain English")
    pdf.body(
        "Instead of configuring workflows manually, you can describe what you want in English:"
    )
    pdf.body('"Automate invoice approval when amount exceeds 5 lakhs, route to CFO for anything above 10 lakhs"')
    pdf.body(
        "The system generates a complete workflow with condition steps, approval gates, and agent "
        "assignments. You can preview it before deploying."
    )
    pdf.tip_box("Go to Workflows > Create > 'Describe in English' tab")

    # ---- 8. HITL ----
    pdf.section("8", "Human-in-the-Loop Approvals")
    pdf.body(
        "When an agent is unsure (confidence below threshold) or a condition is met (e.g., amount > 5L), "
        "execution pauses and waits for human approval."
    )
    pdf.sub("How to Approve/Reject")
    pdf.bullet("Browser push notification (one-tap approve/reject)")
    pdf.bullet("Approvals page in dashboard (queue of pending decisions)")
    pdf.bullet("Email notification with approve/reject links")
    pdf.ln(2)
    pdf.body("All decisions are logged with timestamp, user, and reason for compliance.")

    # ---- 9. KNOWLEDGE BASE ----
    pdf.add_page()
    pdf.section("9", "Knowledge Base")
    pdf.body(
        "Upload your company documents and agents will use them to answer questions. "
        "Supported formats: PDF, Word, Excel, TXT, HTML, Markdown."
    )
    pdf.sub("How to Upload")
    pdf.bullet("Go to Knowledge Base in the sidebar")
    pdf.bullet("Drag and drop files or click to browse")
    pdf.bullet("Documents are automatically chunked and indexed")
    pdf.bullet("Status shows: Processing > Indexed > Ready")
    pdf.ln(2)
    pdf.sub("How Agents Use It")
    pdf.body(
        "Any agent can search the knowledge base during execution. If you ask 'What is our refund policy?', "
        "the agent searches your uploaded policy documents and answers from them."
    )
    pdf.tip_box("Documents are isolated per company. Other tenants cannot see your files.")

    # ---- 10. VOICE ----
    pdf.section("10", "Voice Agents")
    pdf.body(
        "Turn any AI agent into a phone-based assistant. Customers call a phone number, "
        "the voice agent answers, processes the request using the same tools as regular agents, "
        "and speaks the response."
    )
    pdf.sub("Setup (5-Step Wizard)")
    pdf.step_box("1", "Choose Provider", "Twilio, Vonage, or custom SIP", BLUE)
    pdf.step_box("2", "Enter Credentials", "Account SID + auth token (encrypted)", GREEN)
    pdf.step_box("3", "Phone Number", "Select or enter your number", ORANGE)
    pdf.step_box("4", "Voice Settings", "STT (Whisper local) + TTS (Piper local)", PURPLE)
    pdf.step_box("5", "Test Call", "Call your agent to verify", TEAL)

    # ---- 11. RPA ----
    pdf.add_page()
    pdf.section("11", "Browser Automation (RPA)")
    pdf.body(
        "Some systems don't have APIs (like Indian government portals). RPA lets agents "
        "navigate these websites automatically -- filling forms, downloading data, and extracting information."
    )
    pdf.sub("Pre-Built Scripts")
    pdf.bullet("EPFO ECR Download - Downloads provident fund data")
    pdf.bullet("MCA Company Search - Searches the MCA portal for company details")
    pdf.bullet("Income Tax 26AS - Downloads tax credit statement")
    pdf.bullet("GST Return Status - Checks filing status")
    pdf.ln(2)
    pdf.body("All RPA runs capture screenshots at every step for audit compliance.")

    # ---- 12. INDUSTRY PACKS ----
    pdf.section("12", "Industry Packs")
    pdf.body("One-click install of industry-specific agent bundles:")
    pdf.ln(1)
    pdf.bold_bullet("Healthcare", "Patient intake, claims processing, appointment scheduling, medical records (HIPAA-aware)")
    pdf.bold_bullet("Legal", "Contract review, case research, document drafting, compliance checking")
    pdf.bold_bullet("Insurance", "Underwriting, claims adjudication, policy renewal, fraud detection")
    pdf.bold_bullet("Manufacturing", "Production planning, quality inspection, supply chain, maintenance scheduling")
    pdf.ln(2)
    pdf.body("Go to Industry Packs in the sidebar, click 'Install', and all agents + workflows deploy in shadow mode.")

    # ---- 13. DASHBOARDS ----
    pdf.add_page()
    pdf.section("13", "Dashboards")
    pdf.sub("CFO Dashboard")
    pdf.body("Cash Runway, Burn Rate, DSO, DPO, AR/AP Aging (30/60/90/120+ days), P&L Summary, Bank Balances, Tax Calendar")
    pdf.sub("CMO Dashboard")
    pdf.body("CAC by Channel, MQL/SQL Pipeline, ROAS by Channel, Email Performance, Brand Sentiment, Content Metrics")
    pdf.sub("ABM Dashboard")
    pdf.body("Target account management, intent scoring heatmap (Bombora + G2 + TrustRadius), campaign launch")
    pdf.sub("Scope Dashboard")
    pdf.body("Permission enforcement overview: total agents, tool calls today, denial rate, per-agent scope coverage")
    pdf.sub("Enforce Audit Log")
    pdf.body("Real-time feed of every scope enforcement decision. Filter by denied, export to CSV.")

    # ---- 14. MULTI-LANGUAGE ----
    pdf.section("14", "Multi-Language Support")
    pdf.body(
        "The platform is available in English and Hindi. Use the language picker in the top-right "
        "header to switch. Agents can also respond in your preferred language."
    )

    # ---- 15. ORG CHART ----
    pdf.add_page()
    pdf.section("15", "Organization Chart")
    pdf.body(
        "The Org Chart shows how your AI agents are structured -- who reports to whom, "
        "and how scopes narrow from parent to child. The CEO/Admin sees all departments. "
        "CXOs see their domain only."
    )
    pdf.sub("Smart Escalation")
    pdf.body("When an agent's confidence is low, it automatically escalates to its parent agent in the hierarchy.")
    pdf.sub("Scope Narrowing")
    pdf.body(
        "If a parent agent has WRITE scope, it can delegate only READ to a child. "
        "Children can never have more permissions than their parent."
    )

    # ---- 16. BILLING ----
    pdf.section("16", "Billing and Plans")
    pdf.sub("Plans")
    pdf.bold_bullet("Free", "3 agents, 5 workflows, 1K runs/month, 100MB knowledge base")
    pdf.bold_bullet("Pro ($49/mo)", "15 agents, 25 workflows, 10K runs/month, 1GB KB")
    pdf.bold_bullet("Enterprise ($299/mo)", "Unlimited everything, SSO, priority support")
    pdf.ln(1)
    pdf.body("India pricing: Free / Rs 999/mo / Rs 4999/mo. Toggle between USD and INR on the billing page.")
    pdf.sub("Self-Hosted (Free Forever)")
    pdf.body("If you deploy AgenticOrg on your own servers, it is completely free. No license fees. No per-seat charges. Open source (Apache 2.0).")

    # ---- 17. SETTINGS ----
    pdf.add_page()
    pdf.section("17", "Settings and Security")
    pdf.sub("API Keys")
    pdf.body("Generate API keys (ao_sk_ prefix) for programmatic access. Keys are bcrypt-hashed at rest.")
    pdf.sub("Team Management")
    pdf.body("Invite team members via email. Assign roles (CFO, CMO, etc.) to control what they see.")
    pdf.sub("PII Protection")
    pdf.body(
        "All sensitive data (Aadhaar, PAN, GSTIN, UPI, email, phone) is automatically "
        "scrubbed BEFORE it reaches the AI model. The AI never sees your actual personal data."
    )
    pdf.sub("Content Safety")
    pdf.body("Generated content is checked for PII leakage, toxicity, and near-duplicates before delivery.")
    pdf.sub("Audit Log")
    pdf.body("Every action is logged in a tamper-proof, append-only audit trail with 7-year retention.")

    # ---- 18. INTEGRATIONS ----
    pdf.section("18", "Integrations")
    pdf.sub("1000+ Integrations via Composio")
    pdf.body("Connect to virtually any business tool: Salesforce, HubSpot, Jira, Slack, Teams, Notion, Asana, and 1000+ more.")
    pdf.sub("54 Native Connectors")
    pdf.body("Deep integrations for: Oracle, SAP, Tally, GSTN, Banking AA, Stripe, PineLabs, Darwinbox, and more.")
    pdf.sub("SDK & API")
    pdf.bullet("Python SDK: pip install agenticorg")
    pdf.bullet("TypeScript SDK: npm install agenticorg-sdk")
    pdf.bullet("REST API: full programmatic control")
    pdf.sub("MCP Server")
    pdf.body("Connect AgenticOrg tools to Claude Desktop, Cursor, or ChatGPT via MCP: npx agenticorg-mcp-server")

    # ---- 19. TROUBLESHOOTING ----
    pdf.add_page()
    pdf.section("19", "Troubleshooting")
    pdf.bold_bullet("Agent stuck in Shadow", "Check shadow accuracy and sample count. Promote when quality gates pass.")
    pdf.bold_bullet("Tool call denied", "Check Scope Dashboard. The agent may lack the required permission level.")
    pdf.bold_bullet("Low confidence", "Review the 'Why?' panel. Consider adjusting the system prompt or adding more context.")
    pdf.bold_bullet("HITL not triggering", "Check the confidence floor (default 88%) and HITL condition expression.")
    pdf.bold_bullet("Knowledge base empty results", "Ensure documents are 'Indexed' (not 'Processing'). Try rephrasing the query.")
    pdf.bold_bullet("Voice agent not answering", "Verify SIP credentials in Voice Setup. Run 'Test Connection'.")
    pdf.bold_bullet("Workflow step failed", "Enable 'Adaptive Replanning' so the system automatically finds an alternative path.")
    pdf.bold_bullet("Invoice processing errors", "Check GSTIN validation. Ensure Banking AA consent is active.")

    # ---- 20. GLOSSARY ----
    pdf.add_page()
    pdf.section("20", "Glossary")
    terms = [
        ("Agent", "An AI virtual employee that performs tasks using tools and LLM reasoning"),
        ("Connector", "A bridge between an agent and an external system (Jira, Salesforce, etc.)"),
        ("Tool", "A specific action a connector provides (e.g., create_ticket, get_contact)"),
        ("Workflow", "A multi-step process that chains multiple agents together"),
        ("HITL", "Human-in-the-Loop -- when an agent pauses for human approval"),
        ("Shadow Mode", "Agent observes without acting; used for quality validation before going live"),
        ("Scope", "Permission level assigned to an agent (READ, WRITE, DELETE, ADMIN)"),
        ("Grantex", "The authorization system that enforces scopes on every tool call"),
        ("Manifest", "A file that defines which permission each tool requires"),
        ("RAG", "Retrieval-Augmented Generation -- agents search your documents for answers"),
        ("RPA", "Robotic Process Automation -- browser automation for legacy systems"),
        ("CDC", "Change Data Capture -- real-time sync when data changes in connected systems"),
        ("LLM", "Large Language Model -- the AI brain (Gemini, Claude, GPT)"),
        ("Confidence Floor", "Minimum confidence before HITL is triggered (default 88%)"),
        ("Industry Pack", "Pre-built bundle of agents + workflows for a specific industry"),
        ("SIP", "Session Initiation Protocol -- how phone calls connect to voice agents"),
        ("PII", "Personally Identifiable Information -- scrubbed before reaching the LLM"),
        ("WORM", "Write Once Read Many -- audit log cannot be modified after writing"),
    ]
    for term, defn in terms:
        pdf.bold_bullet(term, defn)

    # Final page
    pdf.add_page()
    pdf.ln(40)
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(*NAVY)
    pdf.cell(0, 10, "Thank you for using AgenticOrg", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 12)
    pdf.set_text_color(*GRAY)
    pdf.cell(0, 8, "agenticorg.ai | support@agenticorg.ai", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 8, f"Version {VERSION} | {DATE}", align="C")

    return pdf


if __name__ == "__main__":
    out = os.path.join(os.path.dirname(__file__), "..", "docs")
    os.makedirs(out, exist_ok=True)
    path = os.path.join(out, f"AgenticOrg_User_Guide_v{VERSION}.pdf")
    print("Generating user guide...")
    p = build()
    p.output(path)
    print(f"Done! {p.pages_count} pages -> {path}")
