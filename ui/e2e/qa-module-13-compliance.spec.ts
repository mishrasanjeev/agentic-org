/**
 * Module 13: Compliance (DSAR + SOC-2 evidence) — 4 TCs.
 *
 * All four are pure API contract tests — no UI navigation required.
 * Reference each TC id in the test name so
 * `python -m scripts.qa_matrix generate` flips the matrix to
 * ``automated``.
 *
 * DSAR safety: subject_email uses a guaranteed-non-matching
 * `.invalid` TLD so no real customer data is touched. The DSAR
 * endpoints respond "processing" — actual erase / export side
 * effects only fire when a matching subject exists, and `.invalid`
 * never resolves to anyone.
 */
import { expect, test } from "@playwright/test";

import { APP, E2E_TOKEN, requireAuth } from "./helpers/auth";

test.describe("Module 13: Compliance @qa @compliance @audit", () => {
  test.beforeEach(() => {
    requireAuth();
  });

  // -------------------------------------------------------------------------
  // TC-COMP-001: DSAR access request
  // -------------------------------------------------------------------------

  test("TC-COMP-001 POST /dsar/access returns request_id, type=access, status=processing", async ({
    request,
  }) => {
    const ts = Date.now();
    const subject = `qa-dsar-access-${ts}@agenticorg-test.invalid`;
    const resp = await request.post(`${APP}/api/v1/dsar/access`, {
      headers: {
        Authorization: `Bearer ${E2E_TOKEN}`,
        "Content-Type": "application/json",
      },
      data: { subject_email: subject },
      failOnStatusCode: false,
    });
    expect(resp.status(), `unexpected status ${resp.status()}`).toBeLessThan(300);
    const payload = await resp.json();
    expect(payload).toHaveProperty("request_id");
    expect(payload.type).toBe("access");
    expect(payload.status).toBe("processing");
    expect(payload.subject_email).toBe(subject);
    expect(payload).toHaveProperty("created_at");
  });

  // -------------------------------------------------------------------------
  // TC-COMP-002: DSAR erase request — 30-day deadline
  // -------------------------------------------------------------------------

  test("TC-COMP-002 POST /dsar/erase returns 30-day deadline payload", async ({
    request,
  }) => {
    const ts = Date.now();
    const subject = `qa-dsar-erase-${ts}@agenticorg-test.invalid`;
    const resp = await request.post(`${APP}/api/v1/dsar/erase`, {
      headers: {
        Authorization: `Bearer ${E2E_TOKEN}`,
        "Content-Type": "application/json",
      },
      data: { subject_email: subject },
      failOnStatusCode: false,
    });
    expect(resp.status()).toBeLessThan(300);
    const payload = await resp.json();
    expect(payload).toHaveProperty("request_id");
    expect(payload.type).toBe("erase");
    expect(payload.status).toBe("processing");
    expect(payload.deadline_days).toBe(30);
    expect(payload).toHaveProperty("deadline");
  });

  // -------------------------------------------------------------------------
  // TC-COMP-003: DSAR export request — json format
  // -------------------------------------------------------------------------

  test("TC-COMP-003 POST /dsar/export returns json format + size estimates", async ({
    request,
  }) => {
    const ts = Date.now();
    const subject = `qa-dsar-export-${ts}@agenticorg-test.invalid`;
    const resp = await request.post(`${APP}/api/v1/dsar/export`, {
      headers: {
        Authorization: `Bearer ${E2E_TOKEN}`,
        "Content-Type": "application/json",
      },
      data: { subject_email: subject },
      failOnStatusCode: false,
    });
    expect(resp.status()).toBeLessThan(300);
    const payload = await resp.json();
    expect(payload).toHaveProperty("request_id");
    expect(payload.type).toBe("export");
    expect(payload.status).toBe("processing");
    expect(payload.format).toBe("json");
    // Size estimate may be 0 for a non-existing subject — assert
    // the FIELD is present, not that it's non-zero.
    expect(payload).toHaveProperty("estimated_records");
    expect(payload).toHaveProperty("estimated_size_mb");
  });

  // -------------------------------------------------------------------------
  // TC-COMP-004: SOC-2 evidence package
  // -------------------------------------------------------------------------

  test("TC-COMP-004 GET /compliance/evidence-package returns the documented sections", async ({
    request,
  }) => {
    const resp = await request.get(`${APP}/api/v1/compliance/evidence-package`, {
      headers: { Authorization: `Bearer ${E2E_TOKEN}` },
      failOnStatusCode: false,
    });
    expect(resp.status()).toBe(200);
    const payload = await resp.json();
    expect(payload).toHaveProperty("package_id");
    expect(payload).toHaveProperty("generated_at");
    expect(payload).toHaveProperty("sections");
    // Each documented section must exist with a `status` field.
    // The manual TC says "each with event_count and status" but the
    // actual contract uses section-specific metrics (audit_logs has
    // total_entries, encryption_* sections expose algorithm/protocol,
    // etc.). The truly cross-section invariants are `status` and
    // `control_id`. Pin those.
    const sections = payload.sections;
    for (const section of [
      "access_controls",
      "audit_logs",
      "deployment_records",
      "incident_history",
    ]) {
      expect(
        sections,
        `evidence package missing section ${section}`,
      ).toHaveProperty(section);
      expect(sections[section]).toHaveProperty("status");
      expect(sections[section]).toHaveProperty("control_id");
    }
  });
});
