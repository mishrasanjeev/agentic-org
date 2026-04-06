"""Generate Bug Fix Report PDF for Bugs06April2026.xlsx."""

from __future__ import annotations

import datetime
import os

from fpdf import FPDF

VERSION = "4.0.0"
DATE = datetime.datetime.now(tz=datetime.UTC).strftime("%Y-%m-%d")


class BugReport(FPDF):
    def header(self):
        if self.page_no() <= 1:
            return
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(100, 100, 100)
        self.cell(0, 5, "AgenticOrg Bug Fix Report - 06 April 2026", align="L")
        self.cell(0, 5, f"v{VERSION}", align="R", new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(200, 200, 200)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(3)

    def footer(self):
        if self.page_no() <= 1:
            return
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(128, 128, 128)
        self.cell(0, 10, f"Page {self.page_no()}", align="C")

    def section(self, num, title):
        self.set_font("Helvetica", "B", 15)
        self.set_text_color(20, 40, 80)
        self.cell(0, 10, f"{num}. {title}" if num else title, new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(20, 40, 80)
        self.set_line_width(0.5)
        self.line(10, self.get_y(), 70, self.get_y())
        self.ln(4)

    def body(self, text):
        self.set_font("Helvetica", "", 10)
        self.set_text_color(30, 30, 30)
        self.multi_cell(190, 5.5, text)
        self.ln(2)

    def bullet(self, text):
        self.set_font("Helvetica", "", 10)
        self.set_text_color(30, 30, 30)
        self.set_x(15)
        self.multi_cell(185, 5.5, f"- {text}")

    def bug_detail(self, title, fpath, root_cause, fix):
        if self.get_y() > 235:
            self.add_page()
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(20, 40, 80)
        self.cell(0, 7, title, new_x="LMARGIN", new_y="NEXT")
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(100, 100, 100)
        self.cell(0, 5, fpath, new_x="LMARGIN", new_y="NEXT")
        self.set_font("Helvetica", "", 9)
        self.set_text_color(30, 30, 30)
        self.set_x(15)
        self.multi_cell(185, 4.5, f"Root Cause: {root_cause}")
        self.set_text_color(34, 139, 34)
        self.set_x(15)
        self.multi_cell(185, 4.5, f"Fix: {fix}")
        self.set_text_color(30, 30, 30)
        self.ln(3)


def build():
    pdf = BugReport()
    pdf.set_auto_page_break(auto=True, margin=20)

    # Cover
    pdf.add_page()
    pdf.set_fill_color(20, 40, 80)
    pdf.rect(0, 0, 210, 297, style="F")
    pdf.set_font("Helvetica", "B", 30)
    pdf.set_text_color(255, 255, 255)
    pdf.set_xy(15, 80)
    pdf.cell(180, 14, "AgenticOrg v4.0.0", align="C")
    pdf.set_font("Helvetica", "", 16)
    pdf.set_xy(15, 100)
    pdf.cell(180, 10, "Bug Fix Report", align="C")
    pdf.set_font("Helvetica", "I", 12)
    pdf.set_text_color(180, 200, 255)
    pdf.set_xy(15, 115)
    pdf.cell(180, 8, "Bugs06April2026.xlsx -- Complete Analysis & Resolution", align="C")
    pdf.set_xy(15, 135)
    pdf.cell(180, 7, f"Date: {DATE}", align="C")
    pdf.set_xy(15, 148)
    pdf.cell(180, 7, "15 Bugs Reported | 15 Fixed | 0 Open", align="C")
    pdf.set_font("Helvetica", "", 10)
    pdf.set_xy(15, 165)
    pdf.cell(180, 7, "9 files changed | 304 lines added | 74 lines removed", align="C")

    # 1. Executive Summary
    pdf.add_page()
    pdf.section("1", "Executive Summary")
    pdf.body(
        "On April 6, 2026, the QA team reported 15 bugs via Bugs06April2026.xlsx. "
        "All 15 bugs were analyzed, root-caused, and fixed in a single commit. "
        "Several bugs shared root causes (BUG 3 = BUG 8, BUG 4 = BUG 9, BUG 5 = BUG 11). "
        "After deduplication, 10 unique issues were identified and resolved across 9 files."
    )
    pdf.body(
        "Impact: 10 High severity + 2 Medium. Fixes span backend API, UI components, "
        "connector framework, and core LLM logic. A new GET /tools endpoint was created "
        "for function-level tool discovery."
    )

    # 2. Bug Summary Table
    pdf.section("2", "Bug Summary Table")
    bugs = [
        ("DASH-NTF-001", "Notification toggle no state change", "High",
         "useState stored fn ref", "Fixed", "NotificationBell.tsx"),
        ("AGENT-TOOL-002", "UI shows MCP names not tool names", "High",
         "Fetched /mcp/tools not /tools", "Fixed", "AgentCreate.tsx"),
        ("AGENT-CONFIG-003", "config field missing from API", "High",
         "_agent_to_dict() omitted config", "Fixed", "agents.py"),
        ("AGENT-RUN-004", "Agent run missing connector_config", "High",
         "langgraph_run() missing param", "Fixed", "agents.py"),
        ("CHAT-ROUTER-005", "Chat hardcoded agent routing", "High",
         "Static dict not DB query", "Fixed", "chat.py"),
        ("AGENT-006", "Long input parse failure", "High",
         "No retry on LLM parse fail", "Fixed", "agent_generator.py"),
        ("BUG-API-007", "Agent Teams GET 405", "Medium",
         "No GET endpoint defined", "Fixed", "agent_teams.py"),
        ("BUG-API-008", "config missing (= BUG 3)", "High",
         "Same as AGENT-CONFIG-003", "Fixed", "agents.py"),
        ("BUG-API-009", "Gmail agent 500 (= BUG 4)", "High",
         "Same as AGENT-RUN-004", "Fixed", "agents.py"),
        ("BUG-API-010", "51 connectors unhealthy", "High",
         "Empty Bearer token in health", "Fixed", "base_connector.py"),
        ("BUG-CHAT-011", "Chat static templates (= BUG 5)", "High",
         "Same as CHAT-ROUTER-005", "Fixed", "chat.py"),
        ("BUG-MCP-012", "MCP LocalProtocolError", "High",
         "Missing connector_config", "Fixed", "mcp.py"),
        ("BUG-MCP-013", "MCP expects name not tool", "High",
         "Schema mismatch with spec", "Fixed", "mcp.py"),
        ("BUG-UI-TOOLS-014", "No function-level tool API", "High",
         "Endpoint did not exist", "Fixed", "connectors.py"),
        ("BUG-API-AGENT-015", "Misleading error message", "Medium",
         "Pointed to wrong endpoint", "Fixed", "agents.py"),
    ]

    pdf.set_font("Helvetica", "B", 7)
    pdf.set_fill_color(20, 40, 80)
    pdf.set_text_color(255, 255, 255)
    cols_h = [("ID", 28), ("Summary", 55), ("Sev", 12), ("Root Cause", 42), ("Status", 13), ("File", 40)]
    for label, w in cols_h:
        pdf.cell(w, 7, label, border=1, fill=True, align="C")
    pdf.ln()

    pdf.set_font("Helvetica", "", 6.5)
    for i, (bid, summary, sev, root, status, ffile) in enumerate(bugs):
        bg = (240, 245, 255) if i % 2 == 1 else (255, 255, 255)
        pdf.set_fill_color(*bg)
        pdf.set_text_color(30, 30, 30)
        pdf.cell(28, 5.5, bid, border=1, fill=True)
        pdf.cell(55, 5.5, summary[:38], border=1, fill=True)
        sev_c = (200, 40, 40) if sev == "High" else (220, 120, 20)
        pdf.set_text_color(*sev_c)
        pdf.cell(12, 5.5, sev, border=1, fill=True, align="C")
        pdf.set_text_color(30, 30, 30)
        pdf.cell(42, 5.5, root[:28], border=1, fill=True)
        pdf.set_text_color(34, 139, 34)
        pdf.cell(13, 5.5, status, border=1, fill=True, align="C")
        pdf.set_text_color(30, 30, 30)
        pdf.cell(40, 5.5, ffile, border=1, fill=True)
        pdf.ln()

    # 3. Detailed Fix Analysis
    pdf.add_page()
    pdf.section("3", "Detailed Fix Analysis")

    pdf.bug_detail(
        "3.1 DASH-NTF-001: Notification Toggle",
        "File: ui/src/components/NotificationBell.tsx",
        "useState(isPushSupported) stored the function reference instead of calling it. "
        "The toggle state was always truthy (a function object) so the UI never reflected actual push state.",
        "Changed useState(isPushSupported) to useState(() => isPushSupported()). Function is now invoked "
        "during initialization, returning the correct boolean."
    )
    pdf.bug_detail(
        "3.2 AGENT-TOOL-002: Tool Dropdown Shows MCP Names",
        "File: ui/src/pages/AgentCreate.tsx",
        "Tool selector fetched from GET /mcp/tools which returns agent-level names (agenticorg_email_agent). "
        "These are not valid for authorized_tools field which expects function-level names (send_email).",
        "Changed fetch to new GET /tools endpoint (BUG 14 fix) returning function-level tool names "
        "from connector registry. Falls back to GET /connectors/registry."
    )
    pdf.bug_detail(
        "3.3 AGENT-CONFIG-003 + BUG-API-008: Config Field Missing",
        "File: api/v1/agents.py (_agent_to_dict)",
        "_agent_to_dict() serialization function listed 42 fields but omitted config. "
        "This stores connector auth data, API keys, and tool configuration.",
        "Added 'config': agent.config to the serialized dict. Updated test expected keys."
    )
    pdf.bug_detail(
        "3.4 AGENT-RUN-004 + BUG-API-009: connector_config Missing",
        "File: api/v1/agents.py (POST /agents/{id}/run)",
        "langgraph_run() call did not pass connector_config. Tools like Gmail, Slack, Jira "
        "could not authenticate, failing with 500 Internal Server Error.",
        "Added connector_config=agent_config.get('config') to langgraph_run() call. "
        "The runner already accepts this parameter and passes it to tool adapters."
    )
    pdf.bug_detail(
        "3.5 CHAT-ROUTER-005 + BUG-CHAT-011: Hardcoded Chat Router",
        "File: api/v1/chat.py",
        "Chat router used hardcoded _DOMAIN_AGENTS dict mapping keywords to static agent names. "
        "Returned canned template responses. run_agent() was never called.",
        "Replaced with _find_agent_for_domain() that queries agents DB table for active/shadow agents. "
        "Chat endpoint now calls langgraph_run() with matched agent."
    )
    pdf.bug_detail(
        "3.6 AGENT-006: Long Input Parse Failure",
        "File: core/agent_generator.py",
        "Long structured descriptions caused LLM to produce malformed JSON. No retry mechanism existed.",
        "Added retry -- on parse failure, retries with simplified prompt, input truncated to 500 chars, "
        "lower temperature (0.1), smaller max_tokens (1024)."
    )
    pdf.bug_detail(
        "3.7 BUG-API-007: Agent Teams GET 405",
        "File: api/v1/agent_teams.py",
        "Only POST/PUT endpoints defined. GET requests returned 405 Method Not Allowed.",
        "Added GET /agent-teams (list all) and GET /agent-teams/{team_id} (single team)."
    )
    pdf.bug_detail(
        "3.8 BUG-API-010: 51 Connectors Marked Unhealthy",
        "File: connectors/framework/base_connector.py",
        "connect() with empty credentials set 'Authorization: Bearer ' (empty token). httpx rejected with "
        "'Illegal header value'. health_check() returned 'unhealthy' for ALL unconfigured connectors.",
        "Added _has_credentials() method. connect() skips _authenticate() when no credentials. "
        "health_check() returns 'not_configured' status instead of crashing."
    )
    pdf.bug_detail(
        "3.9 BUG-MCP-012: MCP LocalProtocolError",
        "File: api/v1/mcp.py",
        "MCP call handler did not pass connector_config to langgraph_run(). "
        "Same root cause as BUG 4 -- tools requiring auth failed.",
        "Added connector_config parameter to langgraph_run() in MCP handler, "
        "reading from request.state.connector_config."
    )
    pdf.bug_detail(
        "3.10 BUG-MCP-013: MCP Schema Mismatch",
        "File: api/v1/mcp.py (MCPCallRequest model)",
        "MCPCallRequest expected 'name' field but MCP spec uses 'tool'. "
        "Clients sending {'tool': '...'} got 422 Unprocessable Entity.",
        "Added 'tool' field as alias. model_post_init syncs both fields. "
        "Validation ensures at least one is provided."
    )
    pdf.bug_detail(
        "3.11 BUG-UI-TOOLS-014: No Function-Level Tool API",
        "File: api/v1/connectors.py (new endpoint)",
        "No API endpoint returned function-level tool names (send_email, read_inbox). "
        "Only /mcp/tools (agent-level) existed.",
        "Added GET /tools endpoint using _build_tool_index() from tool adapter. "
        "Returns function-level names with connector and description metadata."
    )
    pdf.bug_detail(
        "3.12 BUG-API-AGENT-015: Misleading Error Message",
        "File: api/v1/agents.py (PATCH handler)",
        "Error message said 'Use /mcp/tools' but those return invalid names for authorized_tools.",
        "Changed to 'Use GET /connectors/registry or GET /tools to discover valid tool names.'"
    )

    # 4. Files Changed
    pdf.add_page()
    pdf.section("4", "Files Changed")
    files = [
        ("api/v1/agents.py", "config in _agent_to_dict, connector_config in run, error msg"),
        ("api/v1/chat.py", "DB query routing + real agent execution (replaced hardcoded)"),
        ("api/v1/mcp.py", "tool field alias, connector_config passthrough"),
        ("api/v1/connectors.py", "New GET /tools endpoint for function-level names"),
        ("api/v1/agent_teams.py", "Added GET /agent-teams and GET /agent-teams/{id}"),
        ("connectors/framework/base_connector.py", "_has_credentials(), not_configured status"),
        ("core/agent_generator.py", "Retry with simplified prompt on parse failure"),
        ("ui/src/components/NotificationBell.tsx", "Fixed useState initialization"),
        ("ui/src/pages/AgentCreate.tsx", "Tool fetch from /tools instead of /mcp/tools"),
    ]
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_fill_color(20, 40, 80)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(80, 7, "File", border=1, fill=True)
    pdf.cell(110, 7, "Changes", border=1, fill=True)
    pdf.ln()
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(30, 30, 30)
    for i, (f, c) in enumerate(files):
        bg = (240, 245, 255) if i % 2 == 1 else (255, 255, 255)
        pdf.set_fill_color(*bg)
        pdf.cell(80, 6, f, border=1, fill=True)
        pdf.cell(110, 6, c[:72], border=1, fill=True)
        pdf.ln()

    # 5. Testing
    pdf.ln(8)
    pdf.section("5", "Testing and Verification")
    for item in [
        "ruff lint: All checks passed (0 errors)",
        "Python imports: All 9 changed modules import OK",
        "Functional verification: config field present, GET endpoints work, MCP sync OK",
        "CI Pipeline: lint + unit + integration + security + build + deploy + E2E",
        "Production sweep: 39/39 PASS",
        "Test updates: config in expected keys, not_configured health status accepted",
    ]:
        pdf.bullet(item)

    # 6. Related
    pdf.ln(5)
    pdf.section("6", "Related Fixes Found During Analysis")
    pdf.body("During root cause analysis, these duplicate/related patterns were identified:")
    for item in [
        "BUG 3 = BUG 8: Same _agent_to_dict() omission (config field)",
        "BUG 4 = BUG 9 = BUG 12: Same missing connector_config (agents + MCP)",
        "BUG 5 = BUG 11: Same hardcoded chat router (keywords + NL queries)",
        "BUG 14 caused BUG 2 and BUG 15: No function-level tool API cascaded to UI + error messages",
        "BUG 10 was systemic: All 51 unconfigured connectors affected (not just one)",
    ]:
        pdf.bullet(item)

    # Back page
    pdf.add_page()
    pdf.ln(40)
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(20, 40, 80)
    pdf.cell(0, 10, "All 15 Bugs Resolved", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 12)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(0, 8, "Commits: a658ad8 + eff2bdf", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 8, f"{DATE} | AgenticOrg v{VERSION}", align="C")

    return pdf


if __name__ == "__main__":
    out_dir = os.path.join(os.path.dirname(__file__), "..", "docs")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "AgenticOrg_BugFix_Report_06April2026.pdf")
    print("Generating bug fix report...")
    p = build()
    p.output(path)
    print(f"Done! {p.pages_count} pages -> {path}")
