"""Generate CA Workflow Manual + Go-Live Checklist PDFs."""
from fpdf import FPDF


class PDF(FPDF):
    def header(self):
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(100, 100, 100)
        self.cell(0, 8, "AgenticOrg  |  Confidential", align="R", new_x="LMARGIN", new_y="NEXT")
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(4)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(150, 150, 150)
        self.cell(0, 10, f"Page {self.page_no()}/{{nb}}", align="C")

    def section_title(self, title):
        self.set_font("Helvetica", "B", 14)
        self.set_text_color(31, 78, 121)
        self.cell(0, 10, title, new_x="LMARGIN", new_y="NEXT")
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(4)

    def sub_title(self, title):
        self.set_font("Helvetica", "B", 11)
        self.set_text_color(50, 50, 50)
        self.cell(0, 8, title, new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def body_text(self, text):
        self.set_font("Helvetica", "", 10)
        self.set_text_color(30, 30, 30)
        self.multi_cell(0, 5.5, text)
        self.ln(2)

    def bullet(self, text):
        self.set_font("Helvetica", "", 10)
        self.set_text_color(30, 30, 30)
        self.get_x()
        self.cell(6, 5.5, "-")
        self.multi_cell(0, 5.5, text)
        self.ln(1)

    def checklist_item(self, text, checked=False):
        mark = "[X]" if checked else "[ ]"
        self.set_font("Helvetica", "", 10)
        self.set_text_color(30, 30, 30)
        self.cell(7, 6, mark)
        self.multi_cell(0, 6, text)
        self.ln(1)

    def table_row(self, cells, widths, bold=False):
        self.set_font("Helvetica", "B" if bold else "", 9)
        h = 7
        for i, (cell, w) in enumerate(zip(cells, widths, strict=False)):
            if bold:
                self.set_fill_color(31, 78, 121)
                self.set_text_color(255, 255, 255)
            else:
                self.set_fill_color(245, 245, 245) if self.get_y() % 2 == 0 else self.set_fill_color(255, 255, 255)
                self.set_text_color(30, 30, 30)
            self.cell(w, h, str(cell)[:int(w/2)], border=1, fill=bold, align="L" if i > 0 else "C")
        self.ln(h)


# ═══════════════════════════════════════════════════════════════
# PDF 1: CA Workflow Manual (Customer Demo + E2E Guide)
# ═══════════════════════════════════════════════════════════════
pdf1 = PDF()
pdf1.alias_nb_pages()
pdf1.set_auto_page_break(auto=True, margin=20)

# Title Page
pdf1.add_page()
pdf1.ln(40)
pdf1.set_font("Helvetica", "B", 28)
pdf1.set_text_color(31, 78, 121)
pdf1.cell(0, 15, "CA Firm AI Agent", align="C", new_x="LMARGIN", new_y="NEXT")
pdf1.cell(0, 15, "Workflow Manual", align="C", new_x="LMARGIN", new_y="NEXT")
pdf1.ln(10)
pdf1.set_font("Helvetica", "", 14)
pdf1.set_text_color(100, 100, 100)
pdf1.cell(0, 8, "End-to-End: Invoice to Tally in 8 Days", align="C", new_x="LMARGIN", new_y="NEXT")
pdf1.ln(20)
pdf1.set_font("Helvetica", "", 11)
pdf1.cell(0, 7, "AgenticOrg Platform v2.2.0", align="C", new_x="LMARGIN", new_y="NEXT")
pdf1.cell(0, 7, "Date: April 2, 2026", align="C", new_x="LMARGIN", new_y="NEXT")
pdf1.cell(0, 7, "Classification: Customer-Facing", align="C", new_x="LMARGIN", new_y="NEXT")

# Section 1: Overview
pdf1.add_page()
pdf1.section_title("1. Overview")
pdf1.body_text(
    "This manual describes the complete end-to-end AI agent workflow for "
    "Chartered Accountant firms. Four AI agents work in sequence to automate "
    "the monthly accounting cycle: invoice processing, bank reconciliation, "
    "GST filing, and Tally synchronization."
)
pdf1.body_text(
    "The pipeline processes a full month of transactions in under 3 minutes - "
    "work that previously takes 2-3 days manually."
)

# Section 2: Pipeline Stages
pdf1.section_title("2. The 4-Stage Pipeline")

pdf1.sub_title("Stage 1: Invoice Creation (Zoho Books)")
pdf1.bullet("Agent: AR Collections")
pdf1.bullet("Connector: Zoho Books (OAuth2, books.zoho.in)")
pdf1.bullet("Action: Creates invoices with GSTIN, HSN codes, tax breakup (CGST/SGST/IGST)")
pdf1.bullet("Output: Invoice ID (e.g., INV-2026-0042) that flows through entire pipeline")
pdf1.bullet("HITL: Invoices above 5 lakhs trigger CFO approval")

pdf1.sub_title("Stage 2: Bank Reconciliation (Account Aggregator)")
pdf1.bullet("Agent: Recon Agent")
pdf1.bullet("Connector: Finvu Account Aggregator (RBI-regulated, consent-based)")
pdf1.bullet("Action: Fetches bank statements via AA consent flow, auto-matches invoice amounts to bank debits")
pdf1.bullet("Match Logic: Amount matching + narration parsing (e.g., 'NEFT-INV-2026-0042-ACME')")
pdf1.bullet("Accuracy: 99.7% auto-match rate")
pdf1.bullet("Important: AA is READ-ONLY. No payment initiation. Client must approve data sharing via Finvu consent UI.")

pdf1.sub_title("Stage 3: GST Filing (GSTN via Adaequare GSP)")
pdf1.bullet("Agent: Tax Compliance")
pdf1.bullet("Connector: GSTN via Adaequare GSP (2-step auth + DSC signing)")
pdf1.bullet("Auth Flow: POST to /authenticate with aspid -> get session token -> use for all API calls")
pdf1.bullet("GSTR-1: Push outbound supply data (B2B invoices with GSTIN, tax amounts)")
pdf1.bullet("GSTR-3B/9: Filing is DSC-signed (RSA-SHA256 PKCS#1 v1.5) using CA firm's .pfx certificate")
pdf1.bullet("Sandbox: Test first at gsp.adaequare.com/test/enriched/gsp with test GSTINs")

pdf1.sub_title("Stage 4: Tally Sync (XML/TDL Protocol)")
pdf1.bullet("Agent: AP Processor")
pdf1.bullet("Connector: Tally Prime via native XML/TDL protocol (NOT REST/JSON)")
pdf1.bullet("Protocol: Single HTTP POST to localhost:9000 with XML envelope containing TDL commands")
pdf1.bullet("Bridge: Since Tally runs locally, a bridge agent tunnels requests via WebSocket from cloud to local machine")
pdf1.bullet("Output: Voucher posted to Tally, CREATED=1 in XML response")

# Section 3: Demo Script
pdf1.add_page()
pdf1.section_title("3. Customer Demo Script (30 minutes)")

steps = [
    ("Show the Problem (2 min)", "Open Tally, show manual voucher entry. Open GSTN portal, show manual login. Show Excel bank reconciliation."),
    ("Create an Invoice (3 min)", "Use dashboard to create test invoice via Zoho Books connector. Show GSTIN validation happening in real-time."),
    ("Bank Reconciliation (5 min)", "Trigger bank statement fetch via AA. Show auto-matching: invoice amount matched to bank debit by narration."),
    ("GST Filing (5 min)", "Push GSTR-1 data to sandbox. Show filing status check. Show DSC certificate info (expiry, issuer)."),
    ("Tally Sync (5 min)", "Post voucher to Tally via bridge. Open Tally on the machine, verify the voucher appeared with correct amounts."),
    ("HITL Demo (5 min)", "Create a 6-lakh invoice. Show it triggers CFO approval. Approve with one click. Show audit trail."),
    ("Dashboard & ROI (5 min)", "Show agent activity feed, confidence metrics, processing times. Use ROI calculator: input their monthly volume, show time saved."),
]
for i, (title, desc) in enumerate(steps, 1):
    pdf1.sub_title(f"Step {i}: {title}")
    pdf1.body_text(desc)

# Section 4: Credentials Needed
pdf1.add_page()
pdf1.section_title("4. Credentials Checklist")
pdf1.sub_title("From the CA Firm:")
creds = [
    "Tally Prime with XML server enabled (port 9000)",
    "Tally company name (exact match)",
    "Adaequare GSP credentials (aspid, username, password)",
    "GSTINs for all client entities (15-character)",
    "DSC .pfx file + password (Class 2 or 3, PAN-linked)",
    "Zoho Books / accounting software API key",
    "Client consent for bank data (via Finvu AA consent UI)",
]
for c in creds:
    pdf1.checklist_item(c)

pdf1.ln(4)
pdf1.sub_title("We Provide:")
for c in ["AgenticOrg API key + Tenant ID", "Bridge ID + Bridge Token (after registration)", "Finvu FIU ID + Client credentials"]:
    pdf1.checklist_item(c)

# Section 5: Setup Timeline
pdf1.section_title("5. Setup Timeline")
timeline = [
    ("Day 1", "Kickoff meeting. Collect credentials. Verify Tally version. Explain consent flow."),
    ("Day 2-3", "Platform setup: create tenant, configure connectors, install Tally bridge."),
    ("Day 4-5", "Shadow mode: agents process real data in parallel without taking action."),
    ("Day 6-7", "CA firm validates shadow results against their manual work."),
    ("Day 8", "Go-live: promote agents to active, enable HITL approvals."),
]
for day, desc in timeline:
    pdf1.sub_title(day)
    pdf1.body_text(desc)

# Section 6: Troubleshooting
pdf1.add_page()
pdf1.section_title("6. Troubleshooting")
issues = [
    ("Bridge can't reach Tally", "Verify XML server enabled: Tally > F12 > Data Config > Allow XML Server = Yes"),
    ("GSTN auth fails", "Check aspid, username, password. Try sandbox first. Verify credentials with Adaequare support."),
    ("DSC signing fails", "Check .pfx password. Verify certificate expiry via GET /connectors/gstn/dsc-info endpoint."),
    ("AA consent times out", "Client must approve on Finvu UI within 5 minutes. Resend consent request if expired."),
    ("Tally voucher rejected", "Verify company name matches exactly. Check voucher type exists in Tally."),
]
for issue, fix in issues:
    pdf1.sub_title(issue)
    pdf1.body_text(fix)

pdf1.output("C:/Users/mishr/Downloads/CA_Workflow_Manual.pdf")
print("Saved: C:/Users/mishr/Downloads/CA_Workflow_Manual.pdf")


# ═══════════════════════════════════════════════════════════════
# PDF 2: Go-Live Checklist
# ═══════════════════════════════════════════════════════════════
pdf2 = PDF()
pdf2.alias_nb_pages()
pdf2.set_auto_page_break(auto=True, margin=20)

# Title Page
pdf2.add_page()
pdf2.ln(40)
pdf2.set_font("Helvetica", "B", 28)
pdf2.set_text_color(31, 78, 121)
pdf2.cell(0, 15, "Go-Live Checklist", align="C", new_x="LMARGIN", new_y="NEXT")
pdf2.ln(10)
pdf2.set_font("Helvetica", "", 14)
pdf2.set_text_color(100, 100, 100)
pdf2.cell(0, 8, "CA Firm Production Deployment", align="C", new_x="LMARGIN", new_y="NEXT")
pdf2.ln(20)
pdf2.set_font("Helvetica", "", 11)
pdf2.cell(0, 7, "AgenticOrg Platform v2.2.0", align="C", new_x="LMARGIN", new_y="NEXT")
pdf2.cell(0, 7, "Date: April 2, 2026", align="C", new_x="LMARGIN", new_y="NEXT")
pdf2.cell(0, 7, "Classification: Internal - Engineering + QA", align="C", new_x="LMARGIN", new_y="NEXT")

# Pre-Deployment
pdf2.add_page()
pdf2.section_title("A. Pre-Deployment Checks")
pdf2.sub_title("Code & Tests")
for item in [
    "All 870 tests passing (pytest tests/unit/ tests/integration/ tests/e2e/)",
    "ruff check passes on all changed files",
    "TypeScript compiles clean (npx tsc --noEmit)",
    "Git commit pushed to origin/main",
    "No secrets in code (.env, credentials, API keys)",
    "Coverage >= 67% (current: 67%)",
]:
    pdf2.checklist_item(item)

pdf2.sub_title("Infrastructure")
for item in [
    "Docker image builds successfully (docker build -t agenticorg .)",
    "docker-compose up passes health checks",
    "PostgreSQL migrations run clean (alembic upgrade head)",
    "Redis connection verified",
    "GCP Secret Manager secrets configured for tenant",
]:
    pdf2.checklist_item(item)

# Connector Setup
pdf2.section_title("B. Connector Configuration")
pdf2.sub_title("Tally (Bridge)")
for item in [
    "Bridge agent installed on CA machine (pip install agenticorg-bridge)",
    "Bridge registered: agenticorg-bridge register --api-key ... --tenant-id ...",
    "Bridge started and connected (agenticorg-bridge status shows REACHABLE)",
    "Tally XML server enabled (port 9000)",
    "Test voucher posted and verified in Tally",
]:
    pdf2.checklist_item(item)

pdf2.sub_title("GSTN (Adaequare)")
for item in [
    "Adaequare sandbox tested first (test/enriched/gsp)",
    "Production aspid, username, password configured in Secret Manager",
    "DSC .pfx file uploaded and password stored in Secret Manager",
    "DSC expiry verified (GET /connectors/gstn/dsc-info, days_until_expiry > 30)",
    "Test GSTR-1 push successful in sandbox",
    "Auth flow verified: session token obtained",
]:
    pdf2.checklist_item(item)

pdf2.sub_title("Banking AA (Finvu)")
for item in [
    "FIU registration with Finvu complete",
    "Client credentials (client_id, client_secret) in Secret Manager",
    "callback_url configured and accessible from internet",
    "Test consent request created and approved",
    "Bank statement fetch verified for test account",
]:
    pdf2.checklist_item(item)

pdf2.sub_title("Zoho Books")
for item in [
    "OAuth2 app registered at api-console.zoho.in",
    "Refresh token obtained and stored",
    "Test invoice creation successful",
]:
    pdf2.checklist_item(item)

# Agent Configuration
pdf2.add_page()
pdf2.section_title("C. Agent Configuration")
for item in [
    "AP Processor agent created with correct tools (4 tools, no initiate_neft)",
    "Recon Agent created with banking_aa read-only tools",
    "Tax Compliance agent created with GSTN tools",
    "AR Collections agent created with Zoho Books tools",
    "HITL threshold set (e.g., 500000 for invoices > 5 lakhs)",
    "Confidence floor set (e.g., 0.88)",
    "Grantex authorization scopes configured per agent",
    "Agent prompts reviewed and customized for CA firm's terminology",
]:
    pdf2.checklist_item(item)

# Shadow Mode
pdf2.section_title("D. Shadow Mode Validation")
for item in [
    "Shadow mode enabled for all 4 agents (POST /agents/{id}/shadow)",
    "Run for minimum 48 hours with real data",
    "Bank reconciliation match rate >= 99%",
    "GSTR-1 data matches manual filing",
    "Tally voucher amounts match manual entry",
    "No false HITL escalations (confidence calibration)",
    "CA firm signs off on shadow results",
]:
    pdf2.checklist_item(item)

# Go-Live
pdf2.section_title("E. Go-Live Day")
for item in [
    "All shadow validations signed off by CA firm",
    "Agents promoted to active mode (POST /agents/{id}/promote)",
    "HITL approval flow tested with CA firm contact",
    "Monitoring dashboard accessible to operations team",
    "Alert channels configured (Slack/email for failures)",
    "Rollback plan documented (shadow mode revert in < 5 min)",
    "CA firm trained on dashboard, HITL approvals, and escalation",
]:
    pdf2.checklist_item(item)

# Post Go-Live
pdf2.section_title("F. Post Go-Live Monitoring (Week 1)")
for item in [
    "Daily check: agent health endpoint returns healthy",
    "Daily check: bridge connection active (GET /bridge/{id}/status)",
    "Daily check: no failed HITL escalations pending > 4 hours",
    "Weekly: review confidence scores, tune thresholds if needed",
    "Weekly: verify DSC certificate expiry > 30 days",
    "Monthly: AA consent renewal check (before consent expiry)",
    "Monthly: review audit logs for anomalies",
]:
    pdf2.checklist_item(item)

# Emergency Contacts
pdf2.add_page()
pdf2.section_title("G. Emergency Procedures")
pdf2.sub_title("Rollback to Shadow Mode")
pdf2.body_text("POST /api/v1/agents/{agent_id}/shadow - Immediately stops active processing. No data loss. Agents continue in observation mode.")

pdf2.sub_title("Bridge Disconnect")
pdf2.body_text("If Tally bridge disconnects: check CA machine is on, Tally is running, internet is connected. Bridge auto-reconnects with exponential backoff (1s, 2s, 4s... max 60s).")

pdf2.sub_title("DSC Expiry")
pdf2.body_text("If DSC expires mid-filing: GET /connectors/gstn/dsc-info will show is_expired=true. CA firm must renew DSC with their certifying authority, upload new .pfx, update Secret Manager.")

pdf2.sub_title("Support Escalation")
pdf2.body_text("L1: Dashboard alerts -> Operations team (Slack #ca-firm-alerts)")
pdf2.body_text("L2: Connector failures -> Engineering on-call")
pdf2.body_text("L3: Data integrity issues -> Sanjeev Kumar (direct)")

pdf2.output("C:/Users/mishr/Downloads/GoLive_Checklist.pdf")
print("Saved: C:/Users/mishr/Downloads/GoLive_Checklist.pdf")
