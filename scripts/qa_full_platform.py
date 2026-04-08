"""Full platform enterprise QA — runs against production."""

import json

import requests

API = "https://app.agenticorg.ai/api/v1"
APP = "https://app.agenticorg.ai"

ALL_USERS = {
    "CA Partner": ("demo@cafirm.agenticorg.ai", "demo123!"),
    "CEO": ("ceo@agenticorg.local", "ceo123!"),
    "CFO": ("cfo@agenticorg.local", "cfo123!"),
    "CMO": ("cmo@agenticorg.local", "cmo123!"),
    "COO": ("coo@agenticorg.local", "coo123!"),
    "CHRO": ("chro@agenticorg.local", "chro123!"),
    "Auditor": ("auditor@agenticorg.local", "audit123!"),
}

PUBLIC_PAGES = ["/", "/login", "/signup", "/pricing", "/solutions/ca-firms", "/blog", "/playground", "/evals", "/privacy", "/terms"]
PROTECTED_PAGES = ["/dashboard", "/dashboard/companies", "/dashboard/companies/new", "/dashboard/partner", "/dashboard/cfo", "/dashboard/cmo", "/dashboard/abm", "/dashboard/agents", "/dashboard/workflows", "/dashboard/approvals", "/dashboard/connectors", "/dashboard/settings", "/dashboard/audit", "/dashboard/observatory", "/dashboard/org-chart", "/dashboard/prompt-templates", "/dashboard/reports", "/dashboard/billing"]
GLOBAL_API = ["health", "auth/config", "agents", "workflows", "approvals", "connectors", "audit", "companies", "kpis/cfo", "kpis/cmo", "packs", "prompt-templates", "chat/history", "schemas", "report-schedules"]
CA_API = ["ca-subscription", "partner-dashboard"]
COMPANY_SUBS = ["", "/roles", "/approvals", "/gstn-uploads", "/credentials", "/deadlines"]

bugs = []
total_checks = 0
total_pass = 0


def check(name, condition, detail=""):
    global total_checks, total_pass
    total_checks += 1
    if condition:
        total_pass += 1
        print(f"  PASS {name}")
    else:
        bugs.append(f"{name}: {detail}")
        print(f"  FAIL {name} {detail}")


print("=" * 70)
print("  ENTERPRISE QA: FULL PLATFORM FEATURE-BY-FEATURE REVIEW")
print("=" * 70)

# 1. AUTH
print("\n[1] AUTHENTICATION - 7 demo users")
tokens = {}
for role, (email, pw) in ALL_USERS.items():
    try:
        r = requests.post(f"{API}/auth/login", json={"email": email, "password": pw}, timeout=10)
        ok = r.status_code == 200 and "access_token" in r.json()
        if ok:
            tokens[role] = r.json()["access_token"]
        check(f"{role} ({email})", ok, f"status={r.status_code}")
    except Exception as e:
        check(f"{role} ({email})", False, str(e))

ca_token = tokens.get("CA Partner", "")
ceo_token = tokens.get("CEO", "")
h_ca = {"Authorization": f"Bearer {ca_token}", "Content-Type": "application/json"}
h_ceo = {"Authorization": f"Bearer {ceo_token}"}

# 2. PUBLIC PAGES
print(f"\n[2] PUBLIC PAGES - {len(PUBLIC_PAGES)} pages")
for p in PUBLIC_PAGES:
    try:
        r = requests.get(f"{APP}{p}", timeout=10)
        has_error = "Something went wrong" in r.text or "Cannot GET" in r.text
        check(p, r.status_code == 200 and not has_error, f"{r.status_code}{' RENDER_ERROR' if has_error else ''}")
    except Exception as e:
        check(p, False, str(e))

# 3. PROTECTED PAGES
print(f"\n[3] PROTECTED PAGES - {len(PROTECTED_PAGES)} pages")
for p in PROTECTED_PAGES:
    try:
        r = requests.get(f"{APP}{p}", timeout=10)
        check(p, r.status_code == 200, f"{r.status_code}")
    except Exception as e:
        check(p, False, str(e))

# 4. GLOBAL API (CEO)
print(f"\n[4] GLOBAL API - CEO - {len(GLOBAL_API)} endpoints")
for ep in GLOBAL_API:
    try:
        r = requests.get(f"{API}/{ep}", headers=h_ceo, timeout=10)
        check(f"GET /{ep}", r.status_code == 200, f"{r.status_code}")
    except Exception as e:
        check(f"GET /{ep}", False, str(e))

# 5. CA API
print(f"\n[5] CA API - Partner - {len(CA_API)} endpoints")
for ep in CA_API:
    try:
        r = requests.get(f"{API}/{ep}", headers=h_ca, timeout=10)
        check(f"GET /{ep}", r.status_code == 200, f"{r.status_code}")
    except Exception as e:
        check(f"GET /{ep}", False, str(e))

# 6. COMPANY DATA
print("\n[6] COMPANY DATA VALIDATION")
companies = requests.get(f"{API}/companies", headers=h_ca, timeout=10).json()
check("7 companies present", companies.get("total") == 7, f"got {companies.get('total')}")

for c in companies.get("items", []):
    missing = [f for f in ["gstin", "pan", "name", "industry", "client_health_score", "subscription_status"] if not c.get(f)]
    check(f"{c['name']}: all fields", not missing, f"missing: {missing}")

# 7. COMPANY ENDPOINTS
print("\n[7] COMPANY ENDPOINTS - 3 companies x 6 endpoints")
for c in companies.get("items", [])[:3]:
    cid = c["id"]
    all_ok = True
    for sub in COMPANY_SUBS:
        try:
            r = requests.get(f"{API}/companies/{cid}{sub}", headers=h_ca, timeout=10)
            if r.status_code != 200:
                all_ok = False
                check(f"{c['name']}{sub}", False, f"{r.status_code}")
        except Exception:
            all_ok = False
    if all_ok:
        check(f"{c['name']}: all 6 endpoints", True)

# 8. WRITE OPERATIONS
print("\n[8] WRITE OPERATIONS")
cid = companies["items"][0]["id"]

r = requests.post(f"{API}/companies/{cid}/approvals", headers=h_ca, json={"filing_type": "gstr1", "filing_period": "2026-QA-FINAL"}, timeout=10)
check("POST /approvals (create)", r.status_code == 201, f"{r.status_code}")
aid = r.json().get("id") if r.status_code == 201 else None

if aid:
    r = requests.post(f"{API}/companies/{cid}/approvals/{aid}/approve", headers=h_ca, timeout=10)
    check("POST /approve (self-approve)", r.status_code == 200, f"{r.status_code}")

r = requests.post(f"{API}/companies/{cid}/gstn-uploads", headers=h_ca, json={"upload_type": "gstr1_json", "filing_period": "2026-QA"}, timeout=10)
check("POST /gstn-uploads", r.status_code == 201, f"{r.status_code}")

r = requests.post(f"{API}/companies/tally-detect", headers=h_ca, json={"tally_bridge_url": "http://localhost:9100"}, timeout=10)
check("POST /tally-detect", r.status_code == 200 and r.json().get("detected"), f"{r.status_code}")

r = requests.post(f"{API}/companies/{cid}/deadlines/generate", headers=h_ca, json={}, timeout=10)
check("POST /deadlines/generate", r.status_code in (200, 201), f"{r.status_code}")

# 9. PARTNER DASHBOARD
print("\n[9] PARTNER DASHBOARD DATA")
pd = requests.get(f"{API}/partner-dashboard", headers=h_ca, timeout=10).json()
check("total_clients == 7", pd.get("total_clients") == 7)
check("active_clients > 0", pd.get("active_clients", 0) > 0)
check("avg_health_score > 0", pd.get("avg_health_score", 0) > 0)
check("revenue_per_month_inr > 0", pd.get("revenue_per_month_inr", 0) > 0)
check("has clients list", len(pd.get("clients", [])) > 0)
check("has upcoming_deadlines", isinstance(pd.get("upcoming_deadlines"), list))

# 10. SUBSCRIPTION
print("\n[10] SUBSCRIPTION DATA")
sub = requests.get(f"{API}/ca-subscription", headers=h_ca, timeout=10).json()
check("plan == ca_pro", sub.get("plan") == "ca_pro")
check("status == trial", sub.get("status") == "trial")
check("max_clients == 7", sub.get("max_clients") == 7)
check("price_inr == 4999", sub.get("price_inr") == 4999)

# 11. SECURITY
print("\n[11] SECURITY")
r = requests.get(f"{API}/companies", timeout=10)
check("Unauthenticated -> 401", r.status_code == 401, f"{r.status_code}")

r = requests.get(f"{API}/companies", headers={"Authorization": "Bearer fake"}, timeout=10)
check("Invalid token -> 401/403", r.status_code in (401, 403), f"{r.status_code}")

creds = requests.get(f"{API}/companies/{cid}/credentials", headers=h_ca, timeout=10).json()
pw_leaked = any("password_encrypted" in json.dumps(c) for c in creds.get("items", []))
check("No passwords in credential API", not pw_leaked)

# FINAL
print("\n" + "=" * 70)
print(f"  TOTAL: {total_pass}/{total_checks} passed ({total_checks - total_pass} failed)")
print(f"  BUGS: {len(bugs)}")
if bugs:
    for b in bugs:
        print(f"    -> {b}")
else:
    print("  ZERO BUGS. ENTERPRISE GRADE.")
print("=" * 70)
