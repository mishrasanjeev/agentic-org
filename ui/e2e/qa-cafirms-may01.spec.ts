/**
 * QA-RU-May01: CA Firms agent runtime — post-deploy verification.
 *
 * The Ramesh/Uday CA Firms tester (Uday Chauhan) reported on 1-May-2026
 * that the Zoho Books agent returned `confidence: 0.24` and empty
 * `tool_calls` for every run. Four root causes were identified +
 * fixed in the same PR; this spec is the post-deploy proof contract.
 *
 * Pre-fix observed (commit f0bacf2):
 *   confidence:     0.24
 *   tool_calls:     []
 *   reasoning_trace contains "Confidence capped to 0.5 (tool_call_failed)"
 *
 * Post-fix expected (after this PR's deploy):
 *   confidence:     > 0.5  (tester wants > 0.6, but > 0.5 is the "tools fired" signal)
 *   tool_calls:     non-empty (zoho_books.list_invoices invoked)
 *   reasoning_trace MUST NOT contain "tool_call_failed"
 *
 * Per Rule 6 of docs/bug_triage_skill.md and Rule 7 of
 * feedback_28apr_reopen_autopsy.md: this spec must run against the
 * DEPLOYED app, not localhost. Without `E2E_TOKEN`, the spec skips —
 * the verification is post-deploy, not part of every PR run.
 */
import { test, expect, type APIRequestContext } from "@playwright/test";

const APP = process.env.BASE_URL || "https://agenticorg.ai";
const E2E_TOKEN = process.env.E2E_TOKEN || "";
const AGENT_ID = "02ca34a7-2835-43e5-992d-cda4817c1497";

// We log in directly via the API to keep the spec deterministic — UI
// flake on a slow login redirect would obscure whether the BUG-01..04
// fixes deployed. The login itself isn't what we're verifying.
const TESTER_EMAIL =
  process.env.RU_TESTER_EMAIL || "uday.chauhan@edumatica.io";
const TESTER_PASSWORD = process.env.RU_TESTER_PASSWORD || "";

async function getTesterToken(
  request: APIRequestContext,
): Promise<string> {
  if (E2E_TOKEN) return E2E_TOKEN;
  if (!TESTER_PASSWORD) {
    throw new Error(
      "Set E2E_TOKEN or RU_TESTER_PASSWORD env var so the spec can " +
        "authenticate as uday.chauhan@edumatica.io.",
    );
  }
  const resp = await request.post(`${APP}/api/v1/auth/login`, {
    data: { email: TESTER_EMAIL, password: TESTER_PASSWORD },
  });
  expect(resp.ok()).toBeTruthy();
  const body = await resp.json();
  expect(body.access_token).toBeTruthy();
  return body.access_token;
}

test.describe("CA Firms — RU-May01 agent runtime", () => {
  test.skip(
    !E2E_TOKEN && !process.env.RU_TESTER_PASSWORD,
    "Set E2E_TOKEN or RU_TESTER_PASSWORD to run the post-deploy " +
      "verification spec.",
  );

  test("agent run produces non-empty tool_calls + confidence > 0.5", async ({
    request,
  }) => {
    const token = await getTesterToken(request);

    // Replay the tester's exact 1-May-2026 input.
    const resp = await request.post(
      `${APP}/api/v1/agents/${AGENT_ID}/run`,
      {
        headers: { Authorization: `Bearer ${token}` },
        data: { inputs: { task: "List my latest invoices" } },
        timeout: 60_000,
      },
    );
    expect(
      resp.ok(),
      `agent run returned ${resp.status()} ${await resp.text()}`,
    ).toBeTruthy();
    const body = await resp.json();

    // ── BUG-01 verification: no tool_call_failed in trace ────────
    const trace: string[] = body.reasoning_trace || [];
    const traceStr = trace.join(" | ");
    expect(
      traceStr,
      "reasoning_trace contains 'tool_call_failed' — BUG-01 stale " +
        "cache reconnect path didn't fire on this run. Re-check " +
        "deploy + tail server logs for connector_transport_error_reconnecting.",
    ).not.toContain("tool_call_failed");

    // ── BUG-02 + BUG-03 + BUG-04 verification: tools actually invoked ────
    expect(
      body.tool_calls,
      "tool_calls is empty — at least one of BUG-02 (Zoho org_id), " +
        "BUG-03 (UUID lookup), BUG-04 (QB realm_id) is unfixed in this " +
        "deploy. Check /health.commit matches the merged-fix SHA.",
    ).toBeDefined();
    expect((body.tool_calls || []).length).toBeGreaterThan(0);

    // ── Confidence floor — proves the (llm × 0.6 + tools × 0.4) math
    //    actually saw a successful tool call.
    expect(
      body.confidence,
      `confidence is ${body.confidence} — pre-fix value was 0.24 ` +
        "(0.4 LLM × 0.6 + 0 tools × 0.4). Anything > 0.5 means tools fired.",
    ).toBeGreaterThan(0.5);
  });

  test("connector test endpoint stays healthy (regression — was passing pre-fix)", async ({
    request,
  }) => {
    // Per the 1-May report: the test endpoint always passed because it
    // builds a fresh connector and bypasses the cache. Pin that the
    // BUG-01 fix didn't accidentally break this path.
    const token = await getTesterToken(request);
    const connId = "a7e25e67-0133-44cf-882d-5e561656feba";
    const resp = await request.post(
      `${APP}/api/v1/connectors/${connId}/test`,
      { headers: { Authorization: `Bearer ${token}` }, timeout: 30_000 },
    );
    // 200 ok, body indicates healthy. Don't assert on exact body shape —
    // /test endpoints carry connector-specific health detail.
    expect(resp.ok()).toBeTruthy();
  });
});
