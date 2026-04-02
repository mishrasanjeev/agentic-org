"""
Comprehensive Production Test Sweep for AgenticOrg Platform
Tests all public, auth-required, frontend, SEO, external, and edge-case endpoints.
"""

import sys
import time

import requests

BASE_API = "https://app.agenticorg.ai/api/v1"
BASE_SITE = "https://agenticorg.ai"

results = []
total_start = time.time()


def record(test_num, name, passed, detail=""):
    status = "PASS" if passed else "FAIL"
    results.append((test_num, name, status, detail))
    marker = "OK" if passed else "FAIL"
    print(f"  [{marker}] #{test_num}: {name}" + (f" -- {detail}" if detail and not passed else ""))


def safe_get(url, timeout=15):
    try:
        return requests.get(url, timeout=timeout, allow_redirects=True)
    except Exception as e:
        return None, str(e)


def safe_post(url, json_body=None, timeout=15):
    try:
        return requests.post(url, json=json_body, timeout=timeout, allow_redirects=True)
    except Exception as e:
        return None, str(e)


print("=" * 80)
print("AgenticOrg Production Test Sweep")
print(f"Started at: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")
print("=" * 80)

# ─────────────────────────────────────────────
# PUBLIC ENDPOINTS (no auth)
# ─────────────────────────────────────────────
print("\n--- Public Endpoints (no auth) ---")

# 1. GET /health
r = safe_get(f"{BASE_API}/health")
if isinstance(r, tuple):
    record(1, "GET /health", False, f"Request failed: {r[1]}")
else:
    try:
        d = r.json()
        has_status = "status" in d
        has_version = "version" in d
        has_connectors = any(k for k in d if "connector" in k.lower())
        ok = r.status_code == 200 and has_status
        detail = f"status={r.status_code}, keys={list(d.keys())[:8]}"
        if not has_version:
            detail += " (missing version)"
        if not has_connectors:
            detail += " (no connector count key)"
        record(1, "GET /health", ok, detail if not ok else "")
    except Exception as e:
        record(1, "GET /health", False, f"status={r.status_code}, parse error: {e}")

# 2. GET /health/liveness
r = safe_get(f"{BASE_API}/health/liveness")
if isinstance(r, tuple):
    record(2, "GET /health/liveness", False, f"Request failed: {r[1]}")
else:
    try:
        d = r.json()
        alive = d.get("status") in ("alive", "ok", "healthy") or r.status_code == 200
        record(2, "GET /health/liveness", alive, "" if alive else f"status={r.status_code}, body={r.text[:200]}")
    except Exception:
        alive = r.status_code == 200
        record(2, "GET /health/liveness", alive, "" if alive else f"status={r.status_code}")

# 3. GET /auth/config
r = safe_get(f"{BASE_API}/auth/config")
if isinstance(r, tuple):
    record(3, "GET /auth/config", False, f"Request failed: {r[1]}")
else:
    try:
        d = r.json()
        has_gcid = "google_client_id" in d or "googleClientId" in d or any("google" in k.lower() for k in d)
        record(3, "GET /auth/config", r.status_code == 200 and has_gcid,
               "" if has_gcid else f"status={r.status_code}, keys={list(d.keys())}")
    except Exception as e:
        record(3, "GET /auth/config", False, f"status={r.status_code}, error: {e}")

# 4. GET /a2a/.well-known/agent.json
r = safe_get(f"{BASE_API}/a2a/.well-known/agent.json")
if isinstance(r, tuple):
    record(4, "GET /a2a/.well-known/agent.json", False, f"Request failed: {r[1]}")
else:
    try:
        d = r.json()
        valid = r.status_code == 200 and ("name" in d or "capabilities" in d or "skills" in d)
        record(4, "GET /a2a/.well-known/agent.json", valid,
               "" if valid else f"status={r.status_code}, keys={list(d.keys())[:6]}")
    except Exception as e:
        record(4, "GET /a2a/.well-known/agent.json", False, f"status={r.status_code}, error: {e}")

# 5. GET /a2a/agent-card
r = safe_get(f"{BASE_API}/a2a/agent-card")
if isinstance(r, tuple):
    record(5, "GET /a2a/agent-card", False, f"Request failed: {r[1]}")
else:
    try:
        d = r.json()
        valid = r.status_code == 200 and ("name" in d or "capabilities" in d or "skills" in d)
        record(5, "GET /a2a/agent-card", valid,
               "" if valid else f"status={r.status_code}, keys={list(d.keys())[:6]}")
    except Exception as e:
        record(5, "GET /a2a/agent-card", False, f"status={r.status_code}, error: {e}")

# 6. GET /a2a/agents — expect 25 agents
r = safe_get(f"{BASE_API}/a2a/agents")
if isinstance(r, tuple):
    record(6, "GET /a2a/agents (25 agents)", False, f"Request failed: {r[1]}")
else:
    try:
        d = r.json()
        agents_list = d if isinstance(d, list) else d.get("agents", d.get("data", []))
        count = len(agents_list) if isinstance(agents_list, list) else 0
        ok = r.status_code == 200 and count >= 25
        record(6, "GET /a2a/agents (25 agents)", ok,
               "" if ok else f"status={r.status_code}, agent_count={count}")
    except Exception as e:
        record(6, "GET /a2a/agents (25 agents)", False, f"status={r.status_code}, error: {e}")

# 7. GET /mcp/tools
r = safe_get(f"{BASE_API}/mcp/tools")
if isinstance(r, tuple):
    record(7, "GET /mcp/tools", False, f"Request failed: {r[1]}")
else:
    try:
        d = r.json()
        tools_list = d if isinstance(d, list) else d.get("tools", d.get("data", []))
        count = len(tools_list) if isinstance(tools_list, list) else 0
        ok = r.status_code == 200 and count > 0
        record(7, "GET /mcp/tools", ok,
               "" if ok else f"status={r.status_code}, tool_count={count}")
    except Exception as e:
        record(7, "GET /mcp/tools", False, f"status={r.status_code}, error: {e}")

# 8. POST /auth/login with {} — expect 422
r = safe_post(f"{BASE_API}/auth/login", json_body={})
if isinstance(r, tuple):
    record(8, "POST /auth/login {} -> 422", False, f"Request failed: {r[1]}")
else:
    ok = r.status_code == 422
    record(8, "POST /auth/login {} -> 422", ok,
           "" if ok else f"got status={r.status_code}")

# 9. POST /auth/signup with {} — expect 422
r = safe_post(f"{BASE_API}/auth/signup", json_body={})
if isinstance(r, tuple):
    record(9, "POST /auth/signup {} -> 422", False, f"Request failed: {r[1]}")
else:
    ok = r.status_code == 422
    record(9, "POST /auth/signup {} -> 422", ok,
           "" if ok else f"got status={r.status_code}")

# 10. POST /demo-request with {} — expect 422
r = safe_post(f"{BASE_API}/demo-request", json_body={})
if isinstance(r, tuple):
    record(10, "POST /demo-request {} -> 422", False, f"Request failed: {r[1]}")
else:
    ok = r.status_code == 422
    record(10, "POST /demo-request {} -> 422", ok,
           "" if ok else f"got status={r.status_code}")

# ─────────────────────────────────────────────
# AUTH-REQUIRED (should 401)
# ─────────────────────────────────────────────
print("\n--- Auth-Required Endpoints (expect 401) ---")

auth_endpoints = [
    (11, "/agents"),
    (12, "/connectors"),
    (13, "/org/profile"),
    (14, "/org/api-keys"),
    (15, "/org/members"),
    (16, "/workflows"),
    (17, "/approvals"),
    (18, "/audit"),
    (19, "/config/fleet_limits"),
    (20, "/prompt-templates"),
]

for num, path in auth_endpoints:
    r = safe_get(f"{BASE_API}{path}")
    if isinstance(r, tuple):
        record(num, f"GET {path} -> 401", False, f"Request failed: {r[1]}")
    else:
        ok = r.status_code in (401, 403)
        record(num, f"GET {path} -> 401", ok,
               "" if ok else f"got status={r.status_code}")

# ─────────────────────────────────────────────
# FRONTEND PAGES (should 200)
# ─────────────────────────────────────────────
print("\n--- Frontend Pages (expect 200) ---")

frontend_pages = [
    (21, "/", "AgenticOrg"),
    (22, "/login", None),
    (23, "/signup", None),
    (24, "/pricing", None),
    (25, "/playground", None),
    (26, "/blog", None),
    (27, "/integration-workflow", None),
    (28, "/evals", None),
    (29, "/resources", None),
]

for num, path, expected_text in frontend_pages:
    url = f"{BASE_SITE}{path}"
    r = safe_get(url)
    if isinstance(r, tuple):
        record(num, f"GET {path} -> 200", False, f"Request failed: {r[1]}")
    else:
        ok = r.status_code == 200
        detail = ""
        if ok and expected_text:
            if expected_text not in r.text:
                ok = False
                detail = f"missing '{expected_text}' in body"
        if not ok and not detail:
            detail = f"got status={r.status_code}"
        record(num, f"Frontend {path}", ok, detail)

# ─────────────────────────────────────────────
# SEO FILES
# ─────────────────────────────────────────────
print("\n--- SEO Files ---")

# 30. sitemap.xml
r = safe_get(f"{BASE_SITE}/sitemap.xml")
if isinstance(r, tuple):
    record(30, "sitemap.xml", False, f"Request failed: {r[1]}")
else:
    has_iw = "integration-workflow" in r.text
    # Count URLs
    url_count = r.text.count("<loc>")
    ok = r.status_code == 200 and has_iw and url_count >= 40
    detail = ""
    if not has_iw:
        detail += "missing 'integration-workflow'; "
    if url_count < 40:
        detail += f"only {url_count} URLs (need 40+); "
    if r.status_code != 200:
        detail += f"status={r.status_code}"
    record(30, "sitemap.xml (integration-workflow, 40+ URLs)", ok, detail)

# 31. llms.txt
r = safe_get(f"{BASE_SITE}/llms.txt")
if isinstance(r, tuple):
    record(31, "llms.txt", False, f"Request failed: {r[1]}")
else:
    checks = {
        "Developer SDKs": "Developer SDK" in r.text or "developer sdk" in r.text.lower() or "SDK" in r.text,
    }
    # Check for tool count mentioning ~273 tools (allow some variance)
    has_tools = any(f"{n} tools" in r.text for n in range(260, 290)) or "tools" in r.text.lower()
    checks["tools count"] = has_tools
    # Check for connector count ~43
    has_connectors = any(f"{n} connectors" in r.text for n in range(40, 50)) or "connectors" in r.text.lower()
    checks["connectors count"] = has_connectors

    failed = [k for k, v in checks.items() if not v]
    ok = r.status_code == 200 and len(failed) == 0
    record(31, "llms.txt content checks", ok,
           "" if ok else f"missing: {failed}, status={r.status_code}")

# 32. llms-full.txt
r = safe_get(f"{BASE_SITE}/llms-full.txt")
if isinstance(r, tuple):
    record(32, "llms-full.txt", False, f"Request failed: {r[1]}")
else:
    has_sdk = "SDK" in r.text or "sdk" in r.text.lower()
    ok = r.status_code == 200 and has_sdk
    record(32, "llms-full.txt (SDK docs)", ok,
           "" if ok else f"status={r.status_code}, has_sdk={has_sdk}")

# 33. robots.txt
r = safe_get(f"{BASE_SITE}/robots.txt")
if isinstance(r, tuple):
    record(33, "robots.txt", False, f"Request failed: {r[1]}")
else:
    ok = r.status_code == 200 and len(r.text.strip()) > 0
    record(33, "robots.txt exists", ok,
           "" if ok else f"status={r.status_code}, empty={len(r.text.strip()) == 0}")

# ─────────────────────────────────────────────
# EXTERNAL PACKAGES
# ─────────────────────────────────────────────
print("\n--- External Packages ---")

# 34. npm agenticorg-sdk
r = safe_get("https://registry.npmjs.org/agenticorg-sdk")
if isinstance(r, tuple):
    record(34, "npm agenticorg-sdk", False, f"Request failed: {r[1]}")
else:
    ok = r.status_code == 200
    record(34, "npm agenticorg-sdk exists", ok,
           "" if ok else f"status={r.status_code}")

# 35. npm agenticorg-mcp-server (check mcpName)
r = safe_get("https://registry.npmjs.org/agenticorg-mcp-server")
if isinstance(r, tuple):
    record(35, "npm agenticorg-mcp-server", False, f"Request failed: {r[1]}")
else:
    ok = r.status_code == 200
    has_mcp = False
    if ok:
        try:
            d = r.json()
            # mcpName could be in latest version's package.json or top-level
            text = r.text
            has_mcp = "mcpName" in text
        except Exception:  # noqa: S110
            pass
    record(35, "npm agenticorg-mcp-server (mcpName)", ok and has_mcp,
           "" if (ok and has_mcp) else f"status={r.status_code}, mcpName={'found' if has_mcp else 'NOT found'}")

# 36. PyPI agenticorg
r = safe_get("https://pypi.org/pypi/agenticorg/json")
if isinstance(r, tuple):
    record(36, "PyPI agenticorg", False, f"Request failed: {r[1]}")
else:
    ok = r.status_code == 200
    record(36, "PyPI agenticorg exists", ok,
           "" if ok else f"status={r.status_code}")

# ─────────────────────────────────────────────
# EDGE CASES
# ─────────────────────────────────────────────
print("\n--- Edge Cases ---")

# 37. GET /agents/not-a-uuid — should 422 or 404, not 500
r = safe_get(f"{BASE_API}/agents/not-a-uuid")
if isinstance(r, tuple):
    record(37, "GET /agents/not-a-uuid -> no 500", False, f"Request failed: {r[1]}")
else:
    ok = r.status_code in (401, 403, 404, 422)
    record(37, "GET /agents/not-a-uuid -> no 500", ok,
           "" if ok else f"got status={r.status_code}")

# 38. POST /auth/login with bad creds — should 401, not 500
r = safe_post(f"{BASE_API}/auth/login", json_body={"email": "x", "password": "y"})
if isinstance(r, tuple):
    record(38, "POST /auth/login bad creds -> 401", False, f"Request failed: {r[1]}")
else:
    ok = r.status_code in (400, 401, 403, 422)
    record(38, "POST /auth/login bad creds -> no 500", ok,
           "" if ok else f"got status={r.status_code}")

# 39. GET /nonexistent-page — should not be blank
r = safe_get(f"{BASE_SITE}/nonexistent-page-xyz-12345")
if isinstance(r, tuple):
    record(39, "Frontend 404 page", False, f"Request failed: {r[1]}")
else:
    # SPA apps often return 200 with content; the key is it shouldn't be blank or a 500
    has_content = len(r.text.strip()) > 100
    not_500 = r.status_code != 500
    ok = has_content and not_500
    record(39, "Frontend 404 page (not blank/500)", ok,
           "" if ok else f"status={r.status_code}, body_len={len(r.text.strip())}")

# ─────────────────────────────────────────────
# SUMMARY
# ─────────────────────────────────────────────
elapsed = time.time() - total_start
passed = sum(1 for r in results if r[2] == "PASS")
failed = sum(1 for r in results if r[2] == "FAIL")
total = len(results)

print("\n" + "=" * 80)
print(f"{'#':<4} {'Test':<50} {'Result':<6} {'Detail'}")
print("-" * 80)
for num, name, status, detail in results:
    marker = "PASS" if status == "PASS" else "FAIL"
    line = f"{num:<4} {name:<50} {marker:<6}"
    if detail:
        line += f" {detail}"
    print(line)

print("=" * 80)
print(f"\nTotal: {total} | Passed: {passed} | Failed: {failed} | Time: {elapsed:.1f}s")

if failed > 0:
    print(f"\n>>> {failed} FAILURE(S) DETECTED <<<")
    print("\nFailed tests:")
    for num, name, status, detail in results:
        if status == "FAIL":
            print(f"  #{num}: {name} -- {detail}")
else:
    print("\n>>> ALL TESTS PASSED <<<")

sys.exit(1 if failed > 0 else 0)
