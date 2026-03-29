"""Production connector & workflow test — hits real APIs on app.agenticorg.ai."""

import json
import time

import httpx

API = "https://app.agenticorg.ai/api/v1"

OPS_AGENT = "7e27c75b-831a-4a4e-a32c-4dfd64e93e75"  # Ops Commander (Jira)
CRM_AGENT = "f2faa6c0-53fe-4104-839d-ebab1555dc8a"  # CRM Intelligence (HubSpot)
DEV_AGENT = "8f430064-87b0-47c3-9e6e-fdb9f03736f6"  # DevOps Scout (GitHub+Jira)

WF_INCIDENT = "bc5bf2d7-0840-4a9d-8c6b-546b1773e675"
WF_LEAD = "c31451d5-7dd5-4dbe-9baa-31cae83d1819"
WF_DEVOPS = "4415a7d0-1009-484a-b631-b193bef1edf1"

results = []


def get_token():
    r = httpx.post(f"{API}/auth/login", json={"email": "ceo@agenticorg.local", "password": "ceo123!"})
    return r.json()["access_token"]


def run_agent(token, agent_id, inputs, label):
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
    start = time.time()
    try:
        r = httpx.post(f"{API}/agents/{agent_id}/run", headers=headers, json={"inputs": inputs}, timeout=90)
        elapsed = round(time.time() - start, 1)
        if r.status_code != 200:
            results.append({"test": label, "status": "FAIL", "detail": f"HTTP {r.status_code}: {r.text[:80]}", "time": elapsed})  # noqa: E501
            return None
        d = r.json()
        trace = d.get("reasoning_trace", [])
        tool_traces = [t for t in trace if "[tool]" in t]
        tool_results = d.get("output", {}).get("tool_results", [])

        tool_ok = all("error" not in json.dumps(tr.get("result", {})) for tr in tool_results) if tool_results else True
        has_tools = len(tool_traces) > 0

        status = "PASS" if d["status"] in ("completed", "hitl_triggered") and (not has_tools or tool_ok) else "FAIL"
        detail = f"{d['status']} | conf={d.get('confidence', 0):.2f} | tools={len(tool_traces)}"

        for tr in tool_results:
            res = tr.get("result", {})
            tool_label = f"{tr.get('connector', '')}.{tr.get('tool', '')}"
            if isinstance(res, dict) and "error" in res:
                err = res["error"]
                msg = err.get("message", str(err))[:60] if isinstance(err, dict) else str(err)[:60]
                detail += f" | ERR:{tool_label}={msg}"
                status = "FAIL"
            elif isinstance(res, dict) and "key" in res:
                detail += f" | {tool_label}={res['key']}"
            elif isinstance(res, dict):
                for k in ("projects", "repos", "contacts", "deals", "companies", "issues"):
                    if k in res:
                        detail += f" | {tool_label}:{k}={len(res[k])}"
                        break
                else:
                    if "total" in res:
                        detail += f" | {tool_label}:total={res['total']}"
                    elif "count" in res:
                        detail += f" | {tool_label}:count={res['count']}"
                    else:
                        detail += f" | {tool_label}=ok"

        results.append({"test": label, "status": status, "detail": detail, "time": elapsed})
        return d
    except Exception as e:
        elapsed = round(time.time() - start, 1)
        results.append({"test": label, "status": "FAIL", "detail": str(e)[:80], "time": elapsed})
        return None


def run_workflow(token, wf_id, payload, label):
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
    start = time.time()
    try:
        r = httpx.post(f"{API}/workflows/{wf_id}/run", headers=headers, json={"payload": payload}, timeout=30)
        elapsed = round(time.time() - start, 1)
        if r.status_code not in (200, 201):
            results.append({"test": label, "status": "FAIL", "detail": f"HTTP {r.status_code}", "time": elapsed})
            return
        d = r.json()
        run_id = d.get("run_id", "")

        # Wait a few seconds for background execution
        time.sleep(5)

        r2 = httpx.get(f"{API}/workflows/runs/{run_id}", headers=headers, timeout=30)
        if r2.status_code == 200:
            run_data = r2.json()
            wf_status = run_data.get("status", "?")
            steps_done = run_data.get("steps_completed", 0)
            steps_total = run_data.get("steps_total", 0)
            steps = run_data.get("steps", [])
            step_info = ", ".join(f"{s['step_id']}={s['status']}" for s in steps) if steps else "pending"
            detail = f"run={run_id[:12]} | {wf_status} | {steps_done}/{steps_total} | [{step_info}]"
            status = "PASS" if wf_status in ("completed", "running", "waiting_hitl") else "FAIL"
        else:
            detail = f"run={run_id[:12]} | fetch failed HTTP {r2.status_code}"
            status = "FAIL"

        results.append({"test": label, "status": status, "detail": detail, "time": elapsed + 5})
    except Exception as e:
        elapsed = round(time.time() - start, 1)
        results.append({"test": label, "status": "FAIL", "detail": str(e)[:80], "time": elapsed})


def main():
    print("=" * 80)
    print("  AgenticOrg PRODUCTION CONNECTOR & WORKFLOW TEST")
    print(f"  Target: {API}")
    print(f"  Time: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")
    print("=" * 80)

    token = get_token()
    print(f"  Auth: token acquired ({len(token)} chars)")

    # ═══ JIRA TESTS ═══
    print("\n=== JIRA CONNECTOR (Ops Commander — Aria Singh) ===")

    run_agent(token, OPS_AGENT, {
        "task": 'List all Jira projects. Include tool_calls: [{"connector":"jira","tool":"list_projects","params":{}}]'
    }, "JIRA-01: list_projects")

    run_agent(token, OPS_AGENT, {
        "task": 'Search for issues in KAN project. Include tool_calls: [{"connector":"jira","tool":"search_issues","params":{"jql":"project = KAN order by created DESC","max_results":10}}]'  # noqa: E501
    }, "JIRA-02: search_issues")

    run_agent(token, OPS_AGENT, {
        "task": 'Get details of issue KAN-5. Include tool_calls: [{"connector":"jira","tool":"get_issue","params":{"issue_key":"KAN-5"}}]'  # noqa: E501
    }, "JIRA-03: get_issue")

    run_agent(token, OPS_AGENT, {
        "task": 'Add comment to KAN-5: "Reviewed by AI — monitoring resolution". Include tool_calls: [{"connector":"jira","tool":"add_comment","params":{"issue_key":"KAN-5","body":"Reviewed by AgenticOrg AI. Monitoring for resolution."}}]'  # noqa: E501
    }, "JIRA-04: add_comment")

    run_agent(token, OPS_AGENT, {
        "task": 'Get project metrics for KAN. Include tool_calls: [{"connector":"jira","tool":"get_project_metrics","params":{"project_key":"KAN"}}]'  # noqa: E501
    }, "JIRA-05: get_project_metrics")

    run_agent(token, OPS_AGENT, {
        "incident": "Redis cluster OOM at 98% memory. Eviction policy not configured. Cache hit rate dropped to 40%.",
        "source": "infra_alert"
    }, "JIRA-06: create_issue (AI-decided)")

    # ═══ HUBSPOT TESTS ═══
    print("\n=== HUBSPOT CONNECTOR (CRM Intelligence — Ravi Kapoor) ===")

    run_agent(token, CRM_AGENT, {
        "task": 'List contacts from HubSpot. Include tool_calls: [{"connector":"hubspot","tool":"list_contacts","params":{"limit":10}}]'  # noqa: E501
    }, "HUBSPOT-01: list_contacts")

    run_agent(token, CRM_AGENT, {
        "task": 'List all deals from HubSpot. Include tool_calls: [{"connector":"hubspot","tool":"list_deals","params":{"limit":10}}]'  # noqa: E501
    }, "HUBSPOT-02: list_deals")

    run_agent(token, CRM_AGENT, {
        "task": 'List companies from HubSpot. Include tool_calls: [{"connector":"hubspot","tool":"list_companies","params":{"limit":10}}]'  # noqa: E501
    }, "HUBSPOT-03: list_companies")

    run_agent(token, CRM_AGENT, {
        "task": 'Search contacts with email containing "agenticorg". Include tool_calls: [{"connector":"hubspot","tool":"search_contacts","params":{"query":"agenticorg"}}]'  # noqa: E501
    }, "HUBSPOT-04: search_contacts")

    # ═══ GITHUB TESTS ═══
    print("\n=== GITHUB CONNECTOR (DevOps Scout — Kiran Rao) ===")

    run_agent(token, DEV_AGENT, {
        "task": 'List GitHub repos. Include tool_calls: [{"connector":"github","tool":"list_repos","params":{"per_page":5}}]'  # noqa: E501
    }, "GITHUB-01: list_repos")

    run_agent(token, DEV_AGENT, {
        "task": 'Get stats for mishrasanjeev/agentic-org. Include tool_calls: [{"connector":"github","tool":"get_repository_statistics","params":{"owner":"mishrasanjeev","repo":"agentic-org"}}]'  # noqa: E501
    }, "GITHUB-02: get_repo_statistics")

    run_agent(token, DEV_AGENT, {
        "task": 'List issues in mishrasanjeev/agentic-org. Include tool_calls: [{"connector":"github","tool":"list_repository_issues","params":{"owner":"mishrasanjeev","repo":"agentic-org","per_page":5}}]'  # noqa: E501
    }, "GITHUB-03: list_issues")

    run_agent(token, DEV_AGENT, {
        "task": 'Search code for "WorkflowEngine" in mishrasanjeev/agentic-org. Include tool_calls: [{"connector":"github","tool":"search_code","params":{"query":"WorkflowEngine repo:mishrasanjeev/agentic-org"}}]'  # noqa: E501
    }, "GITHUB-04: search_code")

    # ═══ WORKFLOW TESTS ═══
    print("\n=== WORKFLOW EXECUTION TESTS ===")

    run_workflow(token, WF_INCIDENT, {
        "incident": "SSL certificate expiring in 3 days for api.agenticorg.ai",
        "severity": "medium"
    }, "WORKFLOW-01: Incident Response Pipeline")

    run_workflow(token, WF_LEAD, {
        "trigger": "weekly_review",
        "segment": "enterprise"
    }, "WORKFLOW-02: Lead-to-Revenue Pipeline")

    run_workflow(token, WF_DEVOPS, {
        "repos": ["mishrasanjeev/agentic-org"],
        "week": "2026-W13"
    }, "WORKFLOW-03: Weekly DevOps Health Report")

    # ═══ REPORT ═══
    print("\n" + "=" * 80)
    print("  FULL TEST REPORT")
    print("=" * 80)

    passed = sum(1 for r in results if r["status"] == "PASS")
    failed = sum(1 for r in results if r["status"] == "FAIL")
    total = len(results)

    for r in results:
        icon = "PASS" if r["status"] == "PASS" else "FAIL"
        print(f"  [{icon}] {r['test']:<45} {r['time']:>6.1f}s  {r['detail']}")

    print(f"\n  {'=' * 76}")
    print(f"  Total: {total} | Passed: {passed} | Failed: {failed} | Rate: {passed * 100 // total if total else 0}%")
    print(f"  {'=' * 76}")

    if failed > 0:
        print("\n  FAILED TESTS:")
        for r in results:
            if r["status"] == "FAIL":
                print(f"    - {r['test']}: {r['detail']}")

    return 1 if failed > 0 else 0


if __name__ == "__main__":
    exit(main())
