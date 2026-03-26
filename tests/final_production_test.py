"""Final production E2E test — fresh company, all 30 agents, full lifecycle."""

import json
import ssl
import time
import urllib.error
import urllib.request

ctx = ssl.create_default_context()
BASE = "https://app.agenticorg.ai/api/v1"


def api(method, path, body=None, token=None):
    data = json.dumps(body).encode() if body else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{BASE}/{path}", data=data, method=method, headers=headers)
    try:
        resp = urllib.request.urlopen(req, context=ctx, timeout=30)
        return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read()) if e.readable() else {}


def main():
    ts = int(time.time())
    results = {"pass": 0, "fail": 0, "details": []}

    def check(name, passed, detail=""):
        results["pass" if passed else "fail"] += 1
        mark = "PASS" if passed else "FAIL"
        results["details"].append(f"{mark}: {name}")
        print(f"  {mark} {name}" + (f" — {detail}" if detail else ""))

    print("=" * 65)
    print("  FINAL PRODUCTION E2E TEST")
    print("=" * 65)

    # 1. Health
    print("\n--- 1. Infrastructure ---")
    code, data = api("GET", "health/liveness")
    check("Health liveness", code == 200)
    code, data = api("GET", "health")
    check("Health readiness", code == 200 and data.get("checks", {}).get("db") == "healthy")

    # 2. Signup
    print("\n--- 2. Signup ---")
    email = f"final-{ts}@finaltest.test"
    code, data = api("POST", "auth/signup", {
        "org_name": f"FinalTest {ts}", "admin_name": "Final Tester",
        "admin_email": email, "password": "Final@2026!"
    })
    token = data.get("access_token", "")
    check("Signup", code == 201 and len(token) > 20)

    # 3. Login
    code, data = api("POST", "auth/login", {"email": email, "password": "Final@2026!"})
    check("Login", code == 200 and data.get("access_token"))

    # 4. Empty tenant
    print("\n--- 3. Empty Tenant ---")
    code, data = api("GET", "agents", token=token)
    check("Empty agents", data.get("total") == 0)
    code, data = api("GET", "prompt-templates", token=token)
    templates = data if isinstance(data, list) else data.get("items", [])
    check("Empty templates", len(templates) == 0)

    # 5. Create 30 agents
    print("\n--- 4. Create 30 Agents ---")
    agents_def = [
        ("ap_processor", "finance", "Priya", "AP Analyst"),
        ("ar_collections", "finance", "Rohit", "AR Specialist"),
        ("recon_agent", "finance", "Meera", "Recon Analyst"),
        ("tax_compliance", "finance", "Sunil", "Tax Analyst"),
        ("close_agent", "finance", "Kavita", "Close Manager"),
        ("fpa_agent", "finance", "Amit", "FP&A Analyst"),
        ("talent_acquisition", "hr", "Neha", "Recruiter"),
        ("onboarding_agent", "hr", "Ravi", "Onboarding"),
        ("payroll_engine", "hr", "Pooja", "Payroll"),
        ("performance_coach", "hr", "Arjun", "Performance"),
        ("ld_coordinator", "hr", "Anita", "L&D"),
        ("offboarding_agent", "hr", "Deepak", "Offboarding"),
        ("content_factory", "marketing", "Sana", "Content"),
        ("campaign_pilot", "marketing", "Karan", "Campaigns"),
        ("seo_strategist", "marketing", "Divya", "SEO"),
        ("crm_intelligence", "marketing", "Vijay", "CRM"),
        ("brand_monitor", "marketing", "Ishita", "Brand"),
        ("vendor_manager", "ops", "Rahul", "Vendor Mgmt"),
        ("contract_intelligence", "ops", "Nandini", "Contracts"),
        ("support_triage", "ops", "Dev", "Triage"),
        ("compliance_guard", "ops", "Shweta", "Compliance"),
        ("it_operations", "ops", "Manish", "IT Ops"),
        ("legal_ops", "backoffice", "Aditi", "Legal"),
        ("risk_sentinel", "backoffice", "Sanjay", "Risk"),
        ("facilities_agent", "backoffice", "Rekha", "Facilities"),
        ("customer_success", "ops", "Maya", "CS"),
        ("data_analyst", "finance", "Isha", "Data"),
        ("social_media", "marketing", "Prateek", "Social"),
        ("procurement", "ops", "Vikrant", "Procurement"),
        ("internal_audit", "backoffice", "Geeta", "Audit"),
    ]

    agent_ids = {}
    for atype, domain, name, title in agents_def:
        code, data = api("POST", "agents", {
            "name": f"{name} - {title}", "agent_type": atype, "domain": domain,
            "employee_name": name, "designation": title,
            "specialization": f"{title} specialist",
            "system_prompt_text": f"You are {name}, {title}. Domain: {domain}. Return JSON with status, confidence, processing_trace.",
            "hitl_policy": {"condition": "confidence < 0.85"},
        }, token=token)
        aid = data.get("agent_id", "")
        agent_ids[atype] = aid
        if not aid:
            print(f"  FAIL create {name} ({atype})")

    created = sum(1 for v in agent_ids.values() if v)
    check(f"Create 30 agents", created == 30, f"{created}/30")

    # 6. Run all 30
    print("\n--- 5. Run All 30 Agents ---")
    run_pass = 0
    run_fail = 0
    hitl_count = 0
    domains_ok = set()

    for atype, domain, name, title in agents_def:
        aid = agent_ids.get(atype)
        if not aid:
            run_fail += 1
            continue
        code, data = api("POST", f"agents/{aid}/run", {
            "action": "process", "inputs": {"test_id": f"FT-{ts}"}
        }, token=token)
        status = data.get("status", "?")
        if status in ("completed", "hitl_triggered"):
            run_pass += 1
            domains_ok.add(domain)
            if data.get("hitl_request"):
                hitl_count += 1
        else:
            run_fail += 1
            print(f"  FAIL run {name} ({atype}): {status}")

    check(f"Run 30 agents", run_pass == 30, f"{run_pass}/30, {hitl_count} HITL")
    check("All 5 domains covered", len(domains_ok) == 5, str(sorted(domains_ok)))

    # 7. Agent detail + persona
    print("\n--- 6. Agent Detail ---")
    first_id = agent_ids.get("ap_processor", "")
    code, data = api("GET", f"agents/{first_id}", token=token)
    check("Agent detail", code == 200)
    check("Persona fields", all(k in data for k in ["employee_name", "designation", "specialization", "routing_filter", "is_builtin"]))

    # 8. Prompt template CRUD
    print("\n--- 7. Prompt Templates ---")
    code, data = api("POST", "prompt-templates", {
        "name": f"ft_tpl_{ts}", "agent_type": f"ft_{ts}",
        "domain": "finance", "template_text": "Final test template",
    }, token=token)
    tpl_id = data.get("id", "")
    check("Create template", code == 201 and tpl_id)

    code, _ = api("PUT", f"prompt-templates/{tpl_id}", {"template_text": "Updated"}, token=token)
    check("Update template", code == 200)

    code, _ = api("DELETE", f"prompt-templates/{tpl_id}", token=token)
    check("Delete template", code == 200)

    # 9. Prompt lock
    print("\n--- 8. Prompt Lock + Lifecycle ---")
    code, data = api("POST", "agents", {
        "name": "Active Bot", "agent_type": f"active_{ts}", "domain": "finance",
        "employee_name": "ActiveBot", "system_prompt_text": "Active",
        "initial_status": "active", "hitl_policy": {"condition": "confidence < 0.88"},
    }, token=token)
    active_id = data.get("agent_id", "")

    code, _ = api("PATCH", f"agents/{active_id}", {"system_prompt_text": "fail"}, token=token)
    check("Prompt lock (409)", code == 409)

    code, _ = api("PATCH", f"agents/{active_id}", {"designation": "Updated"}, token=token)
    check("Non-prompt edit active", code == 200)

    # Clone
    code, data = api("POST", f"agents/{first_id}/clone", {
        "name": "Clone Bot", "agent_type": "ap_processor",
        "overrides": {"employee_name": "CloneBot"},
    }, token=token)
    check("Clone agent", code == 200 and data.get("clone_id"))

    # 10. Audit trail
    print("\n--- 9. Audit + Approvals ---")
    code, data = api("GET", "audit", token=token)
    items = data.get("items", data) if isinstance(data, dict) else data
    items = items if isinstance(items, list) else []
    check("Audit trail", len(items) > 0, f"{len(items)} entries")

    code, data = api("GET", "approvals", token=token)
    check("Approvals endpoint", code == 200)

    # 11. Cross-tenant
    print("\n--- 10. Cross-Tenant ---")
    code, _ = api("GET", "agents/a0000001-0000-0000-0001-000000000001", token=token)
    check("Cross-tenant blocked", code == 404)

    # 12. Other endpoints
    print("\n--- 11. Other APIs ---")
    for ep in ["workflows", "connectors", "sales/pipeline", "sales/metrics"]:
        code, _ = api("GET", ep, token=token)
        check(f"GET /{ep}", code == 200)

    # 13. Demo request
    print("\n--- 12. Demo Request ---")
    code, data = api("POST", "demo-request", {
        "name": "Final Test", "email": f"demo-{ts}@test.test",
        "company": "Final Corp", "role": "CFO",
    })
    check("Demo request", code == 201 and data.get("lead_id"))

    # Summary
    print("\n" + "=" * 65)
    print(f"  TOTAL: {results['pass'] + results['fail']} checks")
    print(f"  PASSED: {results['pass']}")
    print(f"  FAILED: {results['fail']}")
    print("=" * 65)

    if results["fail"] > 0:
        print("\nFailed checks:")
        for d in results["details"]:
            if d.startswith("FAIL"):
                print(f"  {d}")


if __name__ == "__main__":
    main()
