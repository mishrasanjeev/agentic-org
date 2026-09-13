/**
 * UI contract audit 2026-09-13 — pure-helper pins for the pages whose
 * request/response shapes drifted from the FastAPI handlers.
 */
import { describe, expect, it } from "vitest";

import { isHitlTriggered, parseTraceLines } from "@/pages/Playground";
import { ONBOARDING_COMPLETE_PAYLOAD, partitionInviteResults } from "@/pages/Onboarding";
import { auditEntryTimestamp, classifyAuditEventType } from "@/pages/Observatory";
import { deadlineStatus, parseLocalDate } from "@/pages/CompanyDetail";
import { isSeededIntent } from "@/pages/ABMDashboard";
import { normalizeSearchResult } from "@/pages/KnowledgeBase";

describe("Playground run summary (J2)", () => {
  it("reads status=hitl_triggered / hitl_trigger from POST /agents/{id}/run", () => {
    expect(isHitlTriggered({ status: "hitl_triggered", hitl_trigger: "confidence < 0.88" })).toBe(true);
    expect(isHitlTriggered({ status: "completed", hitl_trigger: "budget_tracking_failed" })).toBe(true);
    expect(isHitlTriggered({ status: "completed", hitl_trigger: null })).toBe(false);
    // The never-emitted legacy flags must not be what we depend on.
    expect(isHitlTriggered({ status: "completed", hitl_triggered: true })).toBe(false);
  });

  it("renders the HITL reason line from hitl_trigger", () => {
    const lines = parseTraceLines("Agent", {
      status: "hitl_triggered",
      hitl_trigger: "confidence < 0.88",
      output: {},
      reasoning_trace: [],
    });
    expect(lines.some((l) => l.text === "Human approval required: confidence < 0.88" && l.color === "red")).toBe(true);
  });
});

describe("Onboarding payloads (K1/K2)", () => {
  it("PUT /org/onboarding uses the OnboardingUpdate field names", () => {
    expect(ONBOARDING_COMPLETE_PAYLOAD).toEqual({ onboarding_complete: true, onboarding_step: 4 });
  });

  it("drops fulfilled and 409 rows, keeps other failures for retry", () => {
    const rows = [
      { role: "CFO", email: "cfo@x.com" },
      { role: "CHRO", email: "chro@x.com" },
      { role: "CMO", email: "cmo@x.com" },
    ];
    const results: PromiseSettledResult<unknown>[] = [
      { status: "fulfilled", value: { data: {} } },
      { status: "rejected", reason: { response: { status: 409, data: { detail: "User already exists in organization" } } } },
      { status: "rejected", reason: { response: { status: 500, data: { detail: "smtp down" } } } },
    ];
    const out = partitionInviteResults(rows, results);
    expect(out.remaining.map((r) => r.email)).toEqual(["cmo@x.com"]);
    expect(out.alreadyInvited.map((r) => r.email)).toEqual(["chro@x.com"]);
    expect(out.errors).toEqual(["cmo@x.com: smtp down"]);
  });
});

describe("Observatory audit mapping (L)", () => {
  it("classifies the real prefixed event types", () => {
    expect(classifyAuditEventType("agent.run")).toBe("result");
    expect(classifyAuditEventType("tool.zoho_books.get_ledger_balance")).toBe("tool_call");
    expect(classifyAuditEventType("hitl.decided")).toBe("hitl_trigger");
    expect(classifyAuditEventType("agent.create")).toBe("thinking");
    // Legacy bare values still classify.
    expect(classifyAuditEventType("hitl_trigger")).toBe("hitl_trigger");
    expect(classifyAuditEventType("tool_call")).toBe("tool_call");
  });

  it("prefers created_at (what /audit emits) over the legacy timestamp key", () => {
    expect(auditEntryTimestamp({ created_at: "2026-09-13T10:00:00+00:00" })).toBe("2026-09-13T10:00:00+00:00");
    expect(auditEntryTimestamp({ timestamp: "2026-09-13T09:00:00+00:00" })).toBe("2026-09-13T09:00:00+00:00");
    expect(auditEntryTimestamp({})).toBeNull();
  });
});

describe("CompanyDetail due dates (U)", () => {
  it("parses YYYY-MM-DD as a local calendar day", () => {
    const d = parseLocalDate("2026-04-20");
    expect([d.getFullYear(), d.getMonth(), d.getDate()]).toEqual([2026, 3, 20]);
    expect(d.getHours()).toBe(0);
  });

  it("is not overdue until the due day has fully elapsed locally", () => {
    const deadline = { due_date: "2026-04-20", filed: false } as Parameters<typeof deadlineStatus>[0];
    expect(deadlineStatus(deadline, new Date(2026, 3, 20, 23, 30))).toBe("pending");
    expect(deadlineStatus(deadline, new Date(2026, 3, 21, 0, 0))).toBe("overdue");
    expect(deadlineStatus({ ...deadline, filed: true }, new Date(2026, 4, 1))).toBe("filed");
  });
});

describe("ABM seeded intent (X1)", () => {
  it("treats null intent_data and source=seeded as placeholders", () => {
    expect(isSeededIntent({ intent_data: null })).toBe(true);
    expect(isSeededIntent({ intent_data: { source: "seeded", composite_score: 72 } })).toBe(true);
    expect(isSeededIntent({ intent_data: { source: "aggregator", composite_score: 72 } })).toBe(false);
  });
});

describe("KnowledgeBase search rows (P)", () => {
  it("normalises {chunk_text, score, document_name} and legacy strings", () => {
    expect(normalizeSearchResult({ chunk_text: "GST rate is 18%", score: 0.91, document_name: "gst.pdf" })).toEqual({
      chunk_text: "GST rate is 18%",
      score: 0.91,
      document_name: "gst.pdf",
    });
    expect(normalizeSearchResult("plain text")).toEqual({ chunk_text: "plain text" });
    expect(normalizeSearchResult({ score: 0.1 })).toBeNull();
    expect(normalizeSearchResult(null)).toBeNull();
  });
});
