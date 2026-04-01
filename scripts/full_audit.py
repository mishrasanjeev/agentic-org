"""Full 51-point production audit for AgenticOrg."""
import json
import subprocess

import requests

results = []
BASE = "https://app.agenticorg.ai/api/v1"
FE = "https://agenticorg.ai"

# 1. API HEALTH
r = requests.get(f"{BASE}/health", timeout=10)
h = r.json()
results.append(("API: Health status", h.get("status") in ("healthy", "degraded")))
results.append(("API: Version 2.2.0", h.get("version") == "2.2.0"))
results.append(("API: DB healthy", h.get("checks", {}).get("db") == "healthy"))
results.append(("API: Redis healthy", h.get("checks", {}).get("redis") == "healthy"))
results.append(("API: 43 connectors", h.get("checks", {}).get("connectors", {}).get("registered") == 43))
r = requests.get(f"{BASE}/health/liveness", timeout=10)
results.append(("API: Liveness alive", r.json().get("status") == "alive"))

# 2. A2A + MCP
r = requests.get(f"{BASE}/a2a/agent-card", timeout=10)
c = r.json()
results.append(("A2A: Card 200", r.status_code == 200))
results.append(("A2A: 25 agents in desc", "25 agents" in c.get("description", "")))
results.append(("A2A: 43 connectors in desc", "43 connectors" in c.get("description", "")))
results.append(("A2A: 273 tools in desc", "273 tools" in c.get("description", "")))
r = requests.get(f"{BASE}/a2a/agents", timeout=10)
ag = r.json().get("agents", [])
results.append(("A2A: 25 agents", len(ag) == 25))
domains = {a.get("domain", "") for a in ag}
results.append(("A2A: 5 domains", domains == {"finance", "hr", "marketing", "ops", "backoffice"}))
r = requests.get(f"{BASE}/mcp/tools", timeout=10)
results.append(("MCP: Tools 200", r.status_code == 200))
r = requests.get(f"{BASE}/auth/config", timeout=10)
results.append(("Auth: google_client_id", bool(r.json().get("google_client_id"))))

# 3. ERROR HANDLING
r = requests.post(f"{BASE}/auth/login", json={}, timeout=10)
results.append(("Error: Login empty=422", r.status_code == 422))
r = requests.post(f"{BASE}/auth/login", json={"email": "fake@x.com", "password": "wrong"}, timeout=10)
results.append(("Error: Bad creds=401", r.status_code == 401))
r = requests.post(f"{BASE}/auth/signup", json={}, timeout=10)
results.append(("Error: Signup empty=422", r.status_code == 422))
r = requests.get(f"{BASE}/agents/not-a-uuid", timeout=10)
results.append(("Error: Bad UUID not 500", r.status_code != 500))

# 4. AUTH PROTECTION
for p in ["agents", "connectors", "org/api-keys", "org/profile", "org/members",
          "approvals", "audit", "workflows", "prompt-templates", "config/fleet_limits"]:
    r = requests.get(f"{BASE}/{p}", timeout=10)
    results.append((f"Auth: /{p}=401", r.status_code == 401))
r = requests.post(f"{BASE}/agents/00000000-0000-0000-0000-000000000000/retest", timeout=10)
results.append(("Auth: Retest=401", r.status_code == 401))

# 5. FRONTEND PAGES
for path in ["/", "/login", "/signup", "/pricing", "/playground",
             "/blog", "/integration-workflow", "/evals", "/resources"]:
    r = requests.get(f"{FE}{path}", timeout=10)
    results.append((f"FE: {path}=200", r.status_code == 200))

# 6. SEO FILES
r = requests.get(f"{FE}/sitemap.xml", timeout=10)
urls = r.text.count("<url>")
results.append((f"SEO: Sitemap {urls} URLs", urls >= 40))
results.append(("SEO: Sitemap integration-workflow", "integration-workflow" in r.text))

r = requests.get(f"{FE}/llms.txt", timeout=10)
results.append(("SEO: llms.txt 25 agents", "25 pre-built" in r.text or "25 agents" in r.text))
results.append(("SEO: llms.txt 43 connectors", "43" in r.text))
results.append(("SEO: llms.txt 273 tools", "273" in r.text))
results.append(("SEO: llms.txt SDKs section", "Developer SDKs" in r.text))

r = requests.get(f"{FE}/llms-full.txt", timeout=10)
results.append(("SEO: llms-full MCP Server", "MCP Server" in r.text))
results.append(("SEO: llms-full API Keys", "API Keys" in r.text))

r = requests.get(f"{FE}/robots.txt", timeout=10)
results.append(("SEO: robots.txt exists", r.status_code == 200))

# 7. CONTENT INTEGRITY
r = requests.get(f"{FE}/signup", timeout=10)
has_mailto_terms = "mailto:" in r.text and "Terms" in r.text
results.append(("Content: No mailto Terms", not has_mailto_terms))

# 8. PACKAGES
r = requests.get("https://registry.npmjs.org/agenticorg-sdk", timeout=10)
results.append(("Pkg: npm SDK", r.status_code == 200))
r = requests.get("https://registry.npmjs.org/agenticorg-mcp-server", timeout=10)
d = r.json()
results.append(("Pkg: npm MCP v0.1.1", d.get("dist-tags", {}).get("latest") == "0.1.1"))
results.append(("Pkg: MCP has mcpName", "mcpName" in json.dumps(d.get("versions", {}).get("0.1.1", {}))))
r = requests.get("https://pypi.org/pypi/agenticorg/json", timeout=10)
results.append(("Pkg: PyPI exists", r.status_code == 200))

# 9. GKE
pods = subprocess.run(
    ["kubectl", "get", "pods", "-n", "agenticorg", "--no-headers"],  # noqa: S607, S603
    capture_output=True, text=True,
).stdout
running = sum(1 for line in pods.strip().split("\n") if "Running" in line)
results.append((f"GKE: {running} pods Running", running >= 4))

# RESULTS
passed = sum(1 for _, ok in results if ok)
failed = [(n, ok) for n, ok in results if not ok]

print()
print(f"{'=' * 52}")
print(f"  FULL AUDIT: {passed}/{len(results)} PASSED")
print(f"{'=' * 52}")
print()
for n, ok in results:
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {n}")
print()
if failed:
    print(f"FAILURES ({len(failed)}):")
    for n, _ in failed:
        print(f"  X {n}")
else:
    print("ALL CHECKS PASSED. ZERO ISSUES. PRODUCTION CLEAN.")
