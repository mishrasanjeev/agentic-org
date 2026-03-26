"""Test all 4 new Paperclip features on production + backward compatibility."""

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
    url = f"{BASE}/{path}"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)  # noqa: S310
    try:
        resp = urllib.request.urlopen(req, context=ctx, timeout=30)  # noqa: S310
        return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read()) if e.readable() else {}


def main():
    ts = int(time.time())
    r = {"pass": 0, "fail": 0}

    def check(name, passed, detail=""):
        r["pass" if passed else "fail"] += 1
        print(f"  {'PASS' if passed else 'FAIL'} {name}" + (f" — {detail}" if detail else ""))

    print("=" * 65)
    print("  NEW FEATURES + BACKWARD COMPAT — PRODUCTION")
    print("=" * 65)

    # Signup
    email = f"feat-{ts}@company.test"
    code, data = api("POST", "auth/signup", {
        "org_name": f"FeatureTest {ts}", "admin_name": "Tester",
        "admin_email": email, "password": "Feat@2026!",
    })
    token = data.get("access_token", "")
    check("Signup", code == 201)

    # ── Feature 1: LLM Agnostic ──
    print("\n--- Feature 1: Agent-Agnostic LLM ---")

    code, data = api("POST", "agents", {
        "name": "Gemini Agent", "agent_type": f"gem_{ts}", "domain": "finance",
        "employee_name": "GeminiBot",
        "system_prompt_text": "Return {\"status\":\"completed\",\"confidence\":0.95}",
        "llm": {"model": "gemini-2.5-flash"},
        "hitl_policy": {"condition": "confidence < 0.5"},
    }, token=token)
    gem_id = data.get("agent_id", "")
    check("Create Gemini agent", bool(gem_id))

    code, data = api("POST", "agents", {
        "name": "Claude Agent", "agent_type": f"cld_{ts}", "domain": "finance",
        "employee_name": "ClaudeBot",
        "system_prompt_text": "Return {\"status\":\"completed\",\"confidence\":0.95}",
        "llm": {"model": "claude-3-5-sonnet-20241022"},
        "hitl_policy": {"condition": "confidence < 0.5"},
    }, token=token)
    cld_id = data.get("agent_id", "")
    check("Create Claude agent", bool(cld_id))

    code, data = api("POST", f"agents/{gem_id}/run", {"action": "test"}, token=token)
    trace = data.get("reasoning_trace", [])
    model_line = trace[0] if trace else ""
    check("Gemini agent uses Gemini", "gemini" in model_line.lower(), model_line[:60])

    code, data = api("POST", f"agents/{cld_id}/run", {"action": "test"}, token=token)
    trace = data.get("reasoning_trace", [])
    model_line = trace[0] if trace else ""
    uses_fallback = "default" in model_line.lower() or "gemini" in model_line.lower()
    check("Claude agent falls back (no key)", uses_fallback, model_line[:60])

    # ── Feature 2: Org Chart ──
    print("\n--- Feature 2: Org Chart Hierarchy ---")

    code, data = api("POST", "agents", {
        "name": "VP Finance", "agent_type": f"vp_{ts}", "domain": "finance",
        "employee_name": "VP Finance", "designation": "VP Finance",
        "system_prompt_text": "You are VP Finance.",
        "hitl_policy": {"condition": "confidence < 0.5"},
    }, token=token)
    parent_id = data.get("agent_id", "")
    check("Create parent agent", bool(parent_id))

    code, data = api("POST", "agents", {
        "name": "AP Analyst", "agent_type": f"ap_{ts}", "domain": "finance",
        "employee_name": "Priya", "designation": "AP Analyst",
        "system_prompt_text": "AP Analyst reporting to VP Finance.",
        "parent_agent_id": parent_id, "reporting_to": "VP Finance",
        "hitl_policy": {"condition": "confidence < 0.5"},
    }, token=token)
    child_id = data.get("agent_id", "")
    check("Create child with parent", bool(child_id))

    code, data = api("GET", f"agents/{child_id}", token=token)
    check("parent_agent_id in response", data.get("parent_agent_id") == parent_id)
    check("reporting_to = VP Finance", data.get("reporting_to") == "VP Finance")

    code, data = api("POST", f"agents/{child_id}/run", {"action": "test"}, token=token)
    check("Child runs independently", data.get("status") in ("completed", "hitl_triggered"))

    # ── Feature 3: Budget ──
    print("\n--- Feature 3: Per-Agent Budget ---")

    code, data = api("POST", "agents", {
        "name": "Budget Agent", "agent_type": f"bgt_{ts}", "domain": "finance",
        "employee_name": "BudgetBot",
        "system_prompt_text": "Budget test agent.",
        "cost_controls": {"monthly_cost_cap_usd": 0.001, "daily_token_budget": 100},
        "hitl_policy": {"condition": "confidence < 0.5"},
    }, token=token)
    bgt_id = data.get("agent_id", "")
    check("Create budget agent (cap $0.001)", bool(bgt_id))

    code, data = api("GET", f"agents/{bgt_id}/budget", token=token)
    check("Budget endpoint works", code == 200 and "monthly_cap_usd" in data)
    check("Budget cap = 0.001", data.get("monthly_cap_usd") == 0.001)
    check("Spent = 0 before run", data.get("monthly_spent_usd") == 0)

    code, data = api("POST", f"agents/{bgt_id}/run", {"action": "test"}, token=token)
    check("Budget agent runs", data.get("status") in ("completed", "hitl_triggered"))

    # No-budget agent runs unlimited
    code, data = api("POST", "agents", {
        "name": "Free Agent", "agent_type": f"free_{ts}", "domain": "ops",
        "employee_name": "FreeBird", "system_prompt_text": "No limits.",
        "hitl_policy": {"condition": "confidence < 0.5"},
    }, token=token)
    free_id = data.get("agent_id", "")
    code, data = api("POST", f"agents/{free_id}/run", {"action": "test"}, token=token)
    check("No-budget agent unlimited", data.get("status") in ("completed", "hitl_triggered"))

    # ── Feature 4: Open Source Docs ──
    print("\n--- Feature 4: Open Source Docs ---")
    for doc in ["CODE_OF_CONDUCT.md", "ROADMAP.md", "SECURITY.md"]:
        url = f"https://raw.githubusercontent.com/mishrasanjeev/agentic-org/main/{doc}"
        try:
            req = urllib.request.Request(url)  # noqa: S310
            resp = urllib.request.urlopen(req, context=ctx, timeout=10)  # noqa: S310
            size = len(resp.read())
            check(f"{doc} on GitHub", size > 100, f"{size} bytes")
        except Exception:
            check(f"{doc} on GitHub", False, "not found")

    # ── Backward Compat ──
    print("\n--- Backward Compatibility ---")

    code, data = api("POST", "auth/login", {"email": "ceo@agenticorg.local", "password": "ceo123!"})
    ceo_token = data.get("access_token", "")
    check("CEO demo login", bool(ceo_token))

    code, data = api("GET", "agents?per_page=1", token=ceo_token)
    check("CEO sees agents", data.get("total", 0) > 40, f"total={data.get('total')}")

    code, data = api("POST", "agents/a0000001-0000-0000-0001-000000000001/run", {"action": "test"}, token=ceo_token)
    trace = data.get("reasoning_trace", [])
    model_line = trace[0] if trace else ""
    uses_gemini = "gemini" in model_line.lower() or "default" in model_line.lower()
    check("Existing AP Processor uses Gemini", uses_gemini, model_line[:60])

    code, data = api("POST", "auth/login", {"email": "cfo@agenticorg.local", "password": "cfo123!"})
    cfo_token = data.get("access_token", "")
    code, data = api("GET", "agents", token=cfo_token)
    domains = set(a["domain"] for a in data.get("items", []))
    check("CFO RBAC finance only", domains == {"finance"}, str(domains))

    code, _ = api("GET", "agents/a0000001-0000-0000-0001-000000000001", token=token)
    check("Cross-tenant blocked", code == 404)

    code, data = api("POST", "demo-request", {
        "name": "BC Test", "email": f"bc-{ts}@test.test", "role": "CFO",
    })
    check("Demo request", code == 201 and data.get("lead_id"))

    # Summary
    print(f"\n{'=' * 65}")
    print(f"  TOTAL: {r['pass'] + r['fail']} checks")
    print(f"  PASSED: {r['pass']}")
    print(f"  FAILED: {r['fail']}")
    print(f"{'=' * 65}")

    if r["fail"] > 0:
        print("\n  FAILURES — investigate immediately")


if __name__ == "__main__":
    main()
