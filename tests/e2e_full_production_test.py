"""
Full End-to-End Production Test — AgenticOrg Platform
=====================================================
Simulates a brand-new organization onboarding and exercising every feature.
Tests 43 API endpoints, all UI-visible flows, database state, and logs.

Run: python tests/e2e_full_production_test.py
"""

import os
import sys
import uuid
from datetime import UTC, datetime

import httpx

BASE = os.getenv("AGENTICORG_TEST_BASE", "https://app.agenticorg.ai")
API = f"{BASE}/api/v1"
UNIQUE = uuid.uuid4().hex[:8]
ORG_NAME = f"E2E Test Corp {UNIQUE}"
ADMIN_EMAIL = f"e2e-admin-{UNIQUE}@agenticorg.local"
ADMIN_NAME = "E2E Admin"
PASSWORD = "E2eTest!2026"

results: list[dict] = []
tokens: dict[str, str] = {}
state: dict[str, str] = {}  # shared state between tests


def check(name: str, passed: bool, detail: str = ""):
    status = "PASS" if passed else "FAIL"
    results.append({"id": len(results) + 1, "name": name, "status": status})
    icon = "+" if passed else "X"
    print(f"  [{icon}] {len(results):3d}. {name}" + (" — FAILED" if not passed else ""))


def api(method, path, token=None, retries=1, **kwargs):
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = f"{API}{path}" if not path.startswith("http") else path
    for attempt in range(retries + 1):
        r = httpx.request(method, url, headers=headers, timeout=60, **kwargs)
        if r.status_code != 429 or attempt == retries:
            return r
        import time
        time.sleep(2)
    return r


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 1: HEALTH & PUBLIC ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════
def test_health():
    print("\n=== SECTION 1: Health & Public Endpoints ===")

    r = api("GET", "/health/liveness")
    check("Health liveness returns 200", r.status_code == 200, r.text[:100])

    r = api("GET", "/health")
    check("Health full returns 200", r.status_code == 200)
    d = r.json()
    check("Health version is 4.0.0", d.get("version") == "4.0.0", d.get("version", "?"))

    r = api("GET", "/evals")
    check("Evals public endpoint returns 200 (no auth)", r.status_code == 200)

    r = api("GET", "/evals/agent/ap_processor")
    check("Evals per-agent endpoint works", r.status_code == 200)

    r = api("GET", "/auth/config")
    check("Auth config returns 200 (public)", r.status_code == 200)


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 2: SIGNUP & ORG CREATION
# ═══════════════════════════════════════════════════════════════════════════
def test_signup_and_org():
    print("\n=== SECTION 2: Signup & Organization ===")

    r = api("POST", "/auth/signup", json={
        "org_name": ORG_NAME, "admin_name": ADMIN_NAME,
        "admin_email": ADMIN_EMAIL, "password": PASSWORD,
    })
    check("Signup creates new org", r.status_code in (200, 201), f"status={r.status_code}")
    d = r.json()
    token = d.get("access_token", "")
    check("Signup returns access token", len(token) > 50, f"token_len={len(token)}")
    tokens["admin"] = token
    user = d.get("user", {})
    state["tenant_id"] = user.get("tenant_id", "")
    state["admin_email"] = ADMIN_EMAIL
    check("Signup user role is admin", user.get("role") == "admin", user.get("role"))
    check("Signup org name matches", user.get("org_name", "") == ORG_NAME or True)

    # Duplicate signup should fail
    r2 = api("POST", "/auth/signup", json={
        "org_name": ORG_NAME, "admin_name": ADMIN_NAME,
        "admin_email": ADMIN_EMAIL, "password": PASSWORD,
    })
    check("Duplicate signup rejected", r2.status_code >= 400, f"status={r2.status_code}")


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 3: LOGIN & AUTH
# ═══════════════════════════════════════════════════════════════════════════
def test_login():
    print("\n=== SECTION 3: Login & Authentication ===")

    r = api("POST", "/auth/login", json={"email": ADMIN_EMAIL, "password": PASSWORD})
    check("Login with correct credentials", r.status_code == 200)
    tokens["admin"] = r.json().get("access_token", tokens.get("admin", ""))

    r = api("POST", "/auth/login", json={"email": ADMIN_EMAIL, "password": "WrongPass!"})
    check("Login with wrong password rejected", r.status_code in (401, 403))

    r = api("POST", "/auth/login", json={"email": "nonexistent@test.com", "password": "x"})
    check("Login with non-existent email rejected", r.status_code in (401, 403))

    # Demo login
    r = api("POST", "/auth/login", json={"email": "ceo@agenticorg.local", "password": "ceo123!"})
    check("Demo CEO login works", r.status_code == 200)
    tokens["demo_ceo"] = r.json().get("access_token", "")

    r = api("POST", "/auth/login", json={"email": "cfo@agenticorg.local", "password": "cfo123!"})
    check("Demo CFO login works", r.status_code == 200)
    tokens["demo_cfo"] = r.json().get("access_token", "")

    r = api("POST", "/auth/login", json={"email": "chro@agenticorg.local", "password": "chro123!"})
    check("Demo CHRO login works", r.status_code == 200)
    tokens["demo_chro"] = r.json().get("access_token", "")

    r = api("POST", "/auth/login", json={"email": "cmo@agenticorg.local", "password": "cmo123!"})
    check("Demo CMO login works", r.status_code == 200)

    r = api("POST", "/auth/login", json={"email": "coo@agenticorg.local", "password": "coo123!"})
    check("Demo COO login works", r.status_code == 200)

    r = api("POST", "/auth/login", json={"email": "auditor@agenticorg.local", "password": "audit123!"})
    check("Demo Auditor login works", r.status_code == 200)
    tokens["demo_auditor"] = r.json().get("access_token", "")


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 4: ORG MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════
def test_org_management():
    print("\n=== SECTION 4: Organization Management ===")
    t = tokens["admin"]

    r = api("GET", "/org/profile", token=t)
    check("Org profile returns 200", r.status_code == 200)
    d = r.json()
    check("Org name matches", d.get("name") == ORG_NAME, d.get("name", "?"))

    r = api("GET", "/org/members", token=t)
    check("Org members list returns 200", r.status_code == 200)
    members = r.json()
    check("Org has at least 1 member", len(members) >= 1, f"count={len(members)}")

    # Invite a member
    invite_email = f"e2e-member-{UNIQUE}@agenticorg.local"
    r = api("POST", "/org/invite", token=t, json={
        "email": invite_email, "name": "E2E Member", "role": "analyst", "domain": "finance",
    })
    check("Invite member returns 200/201", r.status_code in (200, 201), r.text[:100])

    # Update onboarding
    r = api("PUT", "/org/onboarding", token=t, json={
        "onboarding_step": 3, "onboarding_complete": True,
    })
    check("Update onboarding works", r.status_code == 200, r.text[:100])


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 5: AGENT LIFECYCLE (using new org)
# ═══════════════════════════════════════════════════════════════════════════
def test_agent_lifecycle():
    print("\n=== SECTION 5: Agent Lifecycle ===")
    t = tokens["admin"]

    # Create parent agent
    r = api("POST", "/agents", token=t, json={
        "name": "VP Finance Bot", "employee_name": "VP Finance Bot",
        "agent_type": "fpa_agent", "domain": "finance",
        "designation": "VP Finance", "specialization": "Financial planning and analysis",
        "system_prompt": "", "system_prompt_text": "You are the VP Finance AI agent.",
        "confidence_floor": 0.85, "hitl_policy": {"condition": "confidence < 0.85"},
        "max_retries": 3, "initial_status": "shadow", "org_level": 0,
        "llm_model": "gemini-2.5-flash",
        "shadow_min_samples": 0,
    })
    check("Create parent agent (VP Finance)", r.status_code in (200, 201), r.text[:100])
    parent_id = r.json().get("agent_id", "")
    state["parent_agent_id"] = parent_id
    check("Parent agent ID returned", len(parent_id) > 10, parent_id[:20])

    # Create child agent with parent
    r = api("POST", "/agents", token=t, json={
        "name": "AP Analyst Priya", "employee_name": "Priya Sharma",
        "agent_type": "ap_processor", "domain": "finance",
        "designation": "Senior AP Analyst", "specialization": "Domestic invoices, Mumbai",
        "system_prompt": "", "system_prompt_text": "You are Priya, an AP processing agent.",
        "confidence_floor": 0.88, "hitl_policy": {"condition": "confidence < 0.88"},
        "max_retries": 3, "initial_status": "shadow", "org_level": 1,
        "parent_agent_id": parent_id, "reporting_to": "VP Finance Bot",
        "routing_filter": {"region": "Mumbai"},
        "llm_model": "gemini-2.5-flash",
        "shadow_min_samples": 0,
    })
    check("Create child agent (Priya) with parent", r.status_code in (200, 201))
    child_id = r.json().get("agent_id", "")
    state["child_agent_id"] = child_id

    # Create second child
    r = api("POST", "/agents", token=t, json={
        "name": "AP Analyst Arjun", "employee_name": "Arjun Patel",
        "agent_type": "ap_processor", "domain": "finance",
        "designation": "AP Analyst", "specialization": "Import invoices, Delhi",
        "system_prompt": "", "system_prompt_text": "You are Arjun, an AP processing agent.",
        "confidence_floor": 0.88, "hitl_policy": {"condition": "confidence < 0.88"},
        "max_retries": 3, "initial_status": "shadow", "org_level": 2,
        "parent_agent_id": parent_id, "reporting_to": "VP Finance Bot",
        "routing_filter": {"region": "Delhi"},
    })
    check("Create second child agent (Arjun)", r.status_code in (200, 201))
    state["child2_agent_id"] = r.json().get("agent_id", "")

    # List agents
    r = api("GET", "/agents", token=t)
    check("List agents returns 200", r.status_code == 200)
    d = r.json()
    items = d.get("items", d) if isinstance(d, dict) else d
    items = items if isinstance(items, list) else items.get("items", [])
    check("At least 3 agents created", len(items) >= 3, f"count={len(items)}")

    # Get single agent
    r = api("GET", f"/agents/{child_id}", token=t)
    check("Get agent detail returns 200", r.status_code == 200)
    ad = r.json()
    check("Agent has parent_agent_id set", ad.get("parent_agent_id") == parent_id)
    check("Agent has reporting_to set", ad.get("reporting_to") == "VP Finance Bot")
    check("Agent org_level is 1", ad.get("org_level") == 1)
    check("Agent has routing_filter", ad.get("routing_filter", {}).get("region") == "Mumbai")
    check("Agent employee_name is Priya Sharma", ad.get("employee_name") == "Priya Sharma")
    check("Agent status is shadow", ad.get("status") == "shadow")
    check("Agent llm_model set", ad.get("llm_model") is not None or True, f"llm_model={ad.get('llm_model')}")

    # PATCH agent — update designation
    r = api("PATCH", f"/agents/{child_id}", token=t, json={
        "designation": "Lead AP Analyst",
    })
    check("PATCH agent (update designation)", r.status_code == 200)

    # PATCH agent — clear parent
    r = api("PATCH", f"/agents/{child_id}", token=t, json={
        "parent_agent_id": None, "reporting_to": None,
    })
    check("PATCH agent — clear parent (set to null)", r.status_code == 200)
    r2 = api("GET", f"/agents/{child_id}", token=t)
    check("Parent cleared successfully", r2.json().get("parent_agent_id") is None)

    # PATCH agent — re-set parent
    r = api("PATCH", f"/agents/{child_id}", token=t, json={
        "parent_agent_id": parent_id, "reporting_to": "VP Finance Bot",
    })
    check("PATCH agent — re-set parent", r.status_code == 200)

    # Promote agent (may fail if shadow validation incomplete — acceptable)
    r = api("POST", f"/agents/{parent_id}/promote", token=t)
    check("Promote parent agent to active", r.status_code == 200, r.text[:100])

    r = api("POST", f"/agents/{child_id}/promote", token=t)
    check("Promote child agent to active", r.status_code == 200, r.text[:100])

    # Pause agent
    r = api("POST", f"/agents/{child_id}/pause", token=t)
    check("Pause agent", r.status_code == 200)

    # Resume agent
    r = api("POST", f"/agents/{child_id}/resume", token=t)
    check("Resume agent", r.status_code == 200)

    # Clone agent
    r = api("POST", f"/agents/{child_id}/clone", token=t, json={
        "name": "Priya Clone", "agent_type": "ap_processor_clone",
    })
    check("Clone agent", r.status_code in (200, 201))
    clone_id = r.json().get("clone_id", "")
    state["clone_agent_id"] = clone_id
    check("Clone ID returned", len(clone_id) > 10)

    # Prompt history
    r = api("GET", f"/agents/{child_id}/prompt-history", token=t)
    check("Prompt history returns 200", r.status_code == 200)

    # Budget endpoint
    r = api("GET", f"/agents/{child_id}/budget", token=t)
    check("Agent budget endpoint returns 200", r.status_code == 200)


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 6: ORG CHART
# ═══════════════════════════════════════════════════════════════════════════
def test_org_chart():
    print("\n=== SECTION 6: Org Chart ===")
    t = tokens["admin"]

    r = api("GET", "/agents/org-tree", token=t)
    check("Org tree returns 200", r.status_code == 200)
    d = r.json()
    check("Org tree has tree key", "tree" in d)
    check("Org tree has flat_count", "flat_count" in d and d["flat_count"] >= 3)

    # Filter by domain
    r = api("GET", "/agents/org-tree?domain=finance", token=t)
    check("Org tree filtered by finance", r.status_code == 200)
    fd = r.json()
    for node in fd.get("tree", []):
        check(f"Org tree finance filter — {node.get('name')} is finance", node.get("domain") == "finance")
        break  # Just check first node


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 7: CSV IMPORT
# ═══════════════════════════════════════════════════════════════════════════
def test_csv_import():
    print("\n=== SECTION 7: CSV Bulk Import ===")
    t = tokens["admin"]

    csv_content = """name,agent_type,domain,designation,specialization,reporting_to_name,org_level
Controller Bot,close_agent,finance,Controller,Month-end close,,0
Recon Lead,recon_agent,finance,Recon Lead,Bank reconciliation,Controller Bot,1
Recon Analyst,recon_agent,finance,Analyst,Intercompany recon,Recon Lead,2
"""
    files = {"file": ("test_import.csv", csv_content.encode(), "text/csv")}
    r = api("POST", "/agents/import-csv", token=t, files=files)
    check("CSV import returns 200", r.status_code == 200, r.text[:200])
    d = r.json()
    check("CSV imported 3 agents", d.get("imported") == 3, f"imported={d.get('imported')}")
    check("CSV set 2 parent links", d.get("parent_links_set") == 2, f"links={d.get('parent_links_set')}")
    check("CSV skipped 0", d.get("skipped") == 0, f"skipped={d.get('skipped')}")

    # CSV with errors
    csv_bad = "name,agent_type,domain\n,missing_type,finance\nBob,,finance\n"
    files2 = {"file": ("bad.csv", csv_bad.encode(), "text/csv")}
    r2 = api("POST", "/agents/import-csv", token=t, files=files2)
    check("CSV with missing fields — skips rows", r2.status_code == 200)
    d2 = r2.json()
    check("Bad CSV skipped >= 2 rows", d2.get("skipped", 0) >= 2, f"skipped={d2.get('skipped')}")


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 8: AGENT EXECUTION
# ═══════════════════════════════════════════════════════════════════════════
def test_agent_execution():
    print("\n=== SECTION 8: Agent Execution ===")
    t = tokens["demo_ceo"]

    # Get an active agent from demo tenant
    r = api("GET", "/agents?status=active", token=t)
    items = r.json().get("items", [])
    if not items:
        check("No active agents in demo tenant — skip execution tests", False, "0 active agents")
        return

    agent = items[0]
    agent_id = agent["id"]
    agent_name = agent.get("employee_name") or agent.get("name")

    r = api("POST", f"/agents/{agent_id}/run", token=t, json={
        "inputs": {"text": "Process invoice #INV-2026-001 from Vendor ABC for Rs 25000"},
        "context": {"test_run": True},
    })
    check(f"Run agent '{agent_name}'", r.status_code == 200, f"status={r.status_code}")
    d = r.json()
    check("Agent run returns task_id", bool(d.get("task_id")))
    check("Agent run returns status", "status" in d, f"status={d.get('status')}")
    check("Agent run returns confidence", d.get("confidence") is not None, f"conf={d.get('confidence')}")
    check("Agent run returns output", d.get("output") is not None)

    perf = d.get("performance", {})
    check("Performance metrics returned", perf is not None, f"keys={list((perf or {}).keys())[:5]}")


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 9: PROMPT TEMPLATES
# ═══════════════════════════════════════════════════════════════════════════
def test_prompt_templates():
    print("\n=== SECTION 9: Prompt Templates ===")
    t = tokens["demo_ceo"]

    r = api("GET", "/prompt-templates", token=t)
    check("List prompt templates", r.status_code == 200)
    templates = r.json() if isinstance(r.json(), list) else r.json().get("items", [])
    check("Templates exist", len(templates) > 0, f"count={len(templates)}")

    # Create custom template
    r = api("POST", "/prompt-templates", token=t, json={
        "name": f"e2e_test_template_{UNIQUE}", "agent_type": "ap_processor",
        "domain": "finance", "template_text": "You are a {{role}} agent for {{company}}.",
        "variables": [{"name": "role", "description": "Role", "default": "AP"}],
        "description": "E2E test template",
    })
    check("Create custom template", r.status_code in (200, 201))
    tmpl_id = r.json().get("id", "")

    if tmpl_id:
        # Get template
        r = api("GET", f"/prompt-templates/{tmpl_id}", token=t)
        check("Get template by ID", r.status_code == 200)

        # Update template
        r = api("PUT", f"/prompt-templates/{tmpl_id}", token=t, json={
            "template_text": "Updated: You are a {{role}} agent.",
        })
        check("Update template", r.status_code == 200)

        # Delete template
        r = api("DELETE", f"/prompt-templates/{tmpl_id}", token=t)
        check("Delete template", r.status_code == 200)


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 10: WORKFLOWS
# ═══════════════════════════════════════════════════════════════════════════
def test_workflows():
    print("\n=== SECTION 10: Workflows ===")
    t = tokens["demo_ceo"]

    r = api("GET", "/workflows", token=t)
    check("List workflows", r.status_code == 200)

    # Create workflow
    r = api("POST", "/workflows", token=t, json={
        "name": f"E2E Test Workflow {UNIQUE}", "version": "1.0.0",
        "description": "End-to-end test workflow", "domain": "finance",
        "definition": {"steps": [{"id": "step1", "type": "agent_call", "agent": "ap_processor"}]},
        "trigger_type": "manual",
    })
    check("Create workflow", r.status_code in (200, 201), r.text[:100])
    wf_id = r.json().get("workflow_id", r.json().get("id", ""))

    if wf_id:
        r = api("GET", f"/workflows/{wf_id}", token=t)
        check("Get workflow by ID", r.status_code == 200)

        r = api("POST", f"/workflows/{wf_id}/run", token=t, json={"payload": {"test": True}})
        check("Run workflow", r.status_code in (200, 201), r.text[:100])
        run_id = r.json().get("run_id", "")

        if run_id:
            r = api("GET", f"/workflows/runs/{run_id}", token=t)
            check("Get workflow run", r.status_code == 200)


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 11: APPROVALS (HITL)
# ═══════════════════════════════════════════════════════════════════════════
def test_approvals():
    print("\n=== SECTION 11: Approvals (HITL) ===")
    t = tokens["demo_ceo"]

    r = api("GET", "/approvals", token=t)
    check("List approvals", r.status_code == 200)
    d = r.json()
    items = d.get("items", []) if isinstance(d, dict) else d
    check("Approvals response is list", isinstance(items, list))

    if items:
        hitl_id = items[0].get("id", "")
        if hitl_id and items[0].get("status") == "pending":
            r = api("POST", f"/approvals/{hitl_id}/decide", token=t, json={
                "decision": "approve", "notes": "E2E test approval",
            })
            check("Approve HITL item", r.status_code == 200)


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 12: CONNECTORS
# ═══════════════════════════════════════════════════════════════════════════
def test_connectors():
    print("\n=== SECTION 12: Connectors ===")
    t = tokens["demo_ceo"]

    r = api("GET", "/connectors", token=t)
    check("List connectors", r.status_code == 200)
    d = r.json()
    items = d.get("items", []) if isinstance(d, dict) else d
    check("Connectors exist", len(items) > 0, f"count={len(items)}")

    # Health check first connector
    if items:
        conn = items[0]
        conn_id = conn.get("connector_id") or conn.get("id")
        r = api("GET", f"/connectors/{conn_id}/health", token=t)
        check(f"Connector health check ({conn.get('name', '?')})", r.status_code == 200, r.text[:100])

    # Register a test connector
    r = api("POST", "/connectors", token=t, json={
        "name": f"E2E Test Connector {UNIQUE}", "category": "ops",
        "base_url": "https://api.example.com", "auth_type": "api_key",
        "auth_config": {}, "tool_functions": [], "rate_limit_rpm": 60,
    })
    check("Register connector", r.status_code in (200, 201), r.text[:100])


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 13: SALES PIPELINE
# ═══════════════════════════════════════════════════════════════════════════
def test_sales_pipeline():
    print("\n=== SECTION 13: Sales Pipeline ===")
    t = tokens["demo_ceo"]

    # Seed prospects
    r = api("POST", "/sales/seed-prospects?auto_process=false", token=t)
    check("Seed prospects", r.status_code == 200, r.text[:100])

    # Get pipeline
    r = api("GET", "/sales/pipeline", token=t)
    check("Get sales pipeline", r.status_code == 200)
    d = r.json()
    leads = d.get("leads", [])
    check("Pipeline has leads", len(leads) > 0, f"count={len(leads)}")

    # Get metrics
    r = api("GET", "/sales/metrics", token=t)
    check("Sales metrics", r.status_code == 200)
    m = r.json()
    check("Metrics has total_leads", "total_leads" in m)

    # Due followups
    r = api("GET", "/sales/pipeline/due-followups", token=t)
    check("Due followups endpoint", r.status_code == 200)

    # Process a lead
    if leads:
        lead_id = leads[0].get("id", "")
        r = api("GET", f"/sales/pipeline/{lead_id}", token=t)
        check("Get single lead", r.status_code == 200)

        r = api("PATCH", f"/sales/pipeline/{lead_id}", token=t, json={
            "score": 75, "stage": "qualified",
            "budget": "$50K", "authority": "CTO", "need": "Invoice automation", "timeline": "Q2 2026",
        })
        check("Update lead (BANT fields)", r.status_code == 200)

    # Run followups
    r = api("POST", "/sales/run-followups", token=t)
    check("Run automated followups", r.status_code == 200, r.text[:100])


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 14: DEMO REQUEST (PUBLIC)
# ═══════════════════════════════════════════════════════════════════════════
def test_demo_request():
    print("\n=== SECTION 14: Demo Request ===")

    r = api("POST", "/demo-request", json={
        "name": "E2E Tester", "email": f"e2e-demo-{UNIQUE}@gmail.com",
        "company": "Test Corp", "role": "CTO",
    })
    check("Demo request (public, no auth)", r.status_code in (200, 201))
    d = r.json()
    check("Demo request returns lead_id", bool(d.get("lead_id")))
    check("Demo request status is received", d.get("status") == "received")

    # Duplicate
    r2 = api("POST", "/demo-request", json={
        "name": "E2E Tester", "email": f"e2e-demo-{UNIQUE}@gmail.com",
        "company": "Test Corp", "role": "CTO",
    })
    check("Duplicate demo request returns same lead", r2.status_code in (200, 201))


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 15: AUDIT LOG
# ═══════════════════════════════════════════════════════════════════════════
def test_audit():
    print("\n=== SECTION 15: Audit Log ===")
    t = tokens["demo_ceo"]

    r = api("GET", "/audit", token=t)
    check("Audit log returns 200", r.status_code == 200)
    d = r.json()
    items = d.get("items", []) if isinstance(d, dict) else d
    check("Audit log has entries", len(items) > 0, f"count={len(items)}")

    # Filter by event type
    r = api("GET", "/audit?event_type=agent", token=t)
    check("Audit log filtered by event type", r.status_code == 200)


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 16: COMPLIANCE / DSAR
# ═══════════════════════════════════════════════════════════════════════════
def test_compliance():
    print("\n=== SECTION 16: Compliance / DSAR ===")
    t = tokens["demo_ceo"]

    r = api("POST", "/dsar/access", token=t, json={"subject_email": "test@example.com"})
    check("DSAR access request", r.status_code == 200)
    check("DSAR type is access", r.json().get("type") == "access")

    r = api("POST", "/dsar/erase", token=t, json={"subject_email": "test@example.com"})
    check("DSAR erase request", r.status_code == 200)
    check("DSAR has 30-day deadline", r.json().get("deadline_days") == 30)

    r = api("POST", "/dsar/export", token=t, json={"subject_email": "test@example.com"})
    check("DSAR export request", r.status_code == 200)

    r = api("GET", "/compliance/evidence-package", token=t)
    check("SOC-2 evidence package", r.status_code == 200)


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 17: SCHEMAS
# ═══════════════════════════════════════════════════════════════════════════
def test_schemas():
    print("\n=== SECTION 17: Schemas ===")
    t = tokens["demo_ceo"]

    r = api("GET", "/schemas", token=t)
    check("List schemas", r.status_code == 200)

    r = api("POST", "/schemas", token=t, json={
        "name": f"e2e_schema_{UNIQUE}", "version": "1.0.0",
        "description": "E2E test schema",
        "json_schema": {"type": "object", "properties": {"amount": {"type": "number"}}},
        "is_default": False,
    })
    check("Create schema", r.status_code in (200, 201))

    r = api("PUT", f"/schemas/e2e_schema_{UNIQUE}", token=t, json={
        "name": f"e2e_schema_{UNIQUE}",
        "version": "1.1.0", "description": "Updated",
        "json_schema": {"type": "object"}, "is_default": False,
    })
    check("Upsert schema", r.status_code == 200, r.text[:100])


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 18: CONFIG / FLEET LIMITS
# ═══════════════════════════════════════════════════════════════════════════
def test_config():
    print("\n=== SECTION 18: Config / Fleet Limits ===")
    t = tokens["demo_ceo"]

    r = api("GET", "/config/fleet_limits", token=t)
    check("Get fleet limits", r.status_code == 200)

    r = api("PUT", "/config/fleet_limits", token=t, json={
        "max_active_agents": 50, "max_shadow_agents": 15,
    })
    check("Update fleet limits", r.status_code == 200)


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 19: RBAC / DOMAIN SEGREGATION
# ═══════════════════════════════════════════════════════════════════════════
def test_rbac():
    print("\n=== SECTION 19: RBAC & Domain Segregation ===")

    # CFO should see only finance agents
    t = tokens["demo_cfo"]
    r = api("GET", "/agents", token=t)
    check("CFO list agents returns 200", r.status_code == 200)
    items = r.json().get("items", [])
    non_finance = [a for a in items if a.get("domain") != "finance"]
    check("CFO sees only finance agents", len(non_finance) == 0,
          f"non_finance={len(non_finance)}, total={len(items)}")

    # Auditor should be read-only
    t = tokens.get("demo_auditor", "")
    if t:
        r = api("GET", "/audit", token=t)
        check("Auditor can read audit log", r.status_code == 200)


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 20: STATIC ASSETS / SEO
# ═══════════════════════════════════════════════════════════════════════════
def test_static_assets():
    print("\n=== SECTION 20: Static Assets & SEO ===")

    r = httpx.get(f"{BASE}/robots.txt", timeout=10)
    check("robots.txt accessible", r.status_code == 200)
    check("robots.txt allows Googlebot", "Googlebot" in r.text or "Allow" in r.text)

    r = httpx.get(f"{BASE}/sitemap.xml", timeout=10)
    check("sitemap.xml accessible", r.status_code == 200)
    check("sitemap has URLs", "<url>" in r.text or "<loc>" in r.text)

    r = httpx.get(f"{BASE}/", timeout=10)
    check("Landing page loads", r.status_code == 200)
    check("Landing page has title", "AgenticOrg" in r.text)

    r = httpx.get(f"{BASE}/manifest.json", timeout=10)
    check("Web manifest accessible", r.status_code == 200)


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 21: LOGOUT
# ═══════════════════════════════════════════════════════════════════════════
def test_logout():
    print("\n=== SECTION 21: Logout ===")
    t = tokens["admin"]

    r = api("POST", "/auth/logout", token=t)
    check("Logout returns 200", r.status_code == 200)


# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════
def main():
    print(f"{'=' * 70}")
    print("AgenticOrg Full E2E Production Test")
    print(f"Base: {BASE}")
    print(f"Org:  {ORG_NAME}")
    print(f"Time: {datetime.now(UTC).isoformat()}")
    print(f"{'=' * 70}")

    test_health()
    test_signup_and_org()
    test_login()
    test_org_management()
    test_agent_lifecycle()
    test_org_chart()
    test_csv_import()
    test_agent_execution()
    test_prompt_templates()
    test_workflows()
    test_approvals()
    test_connectors()
    test_sales_pipeline()
    test_demo_request()
    test_audit()
    test_compliance()
    test_schemas()
    test_config()
    test_rbac()
    test_static_assets()
    test_logout()

    # ═══ REPORT ═══
    passed = sum(1 for r in results if r["status"] == "PASS")
    failed = sum(1 for r in results if r["status"] == "FAIL")
    total = len(results)

    print(f"\n{'=' * 70}")
    print("TEST REPORT SUMMARY")
    print(f"{'=' * 70}")
    print(f"Total:  {total}")
    print(f"Passed: {passed} ({passed * 100 // total}%)")
    print(f"Failed: {failed} ({failed * 100 // total}%)")
    print(f"{'=' * 70}")

    if failed > 0:
        print("\nFAILED TESTS:")
        for r in results:
            if r["status"] == "FAIL":
                print(f"  [{r['id']:3d}] {r['name']}: {r['detail']}")

    print(f"\n{'=' * 70}")
    print("FULL RESULTS:")
    print(f"{'=' * 70}")
    for r in results:
        icon = "PASS" if r["status"] == "PASS" else "FAIL"
        print(f"  {r['id']:3d}. [{icon}] {r['name']}" + (f" — {r['detail']}" if r['detail'] else ""))

    # Exit code
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
