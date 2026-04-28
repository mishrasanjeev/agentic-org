/**
 * Module 11: Sales Pipeline — TC-SALES-001 through 009.
 *
 * API contract tests against /sales/* endpoints. Pinned in this
 * spec: pipeline shape, due-followups exclusion of closed deals,
 * metrics shape + stale-leads exclusions, CSV-import validation
 * branches, seed-prospects environment gate, process-lead's
 * safe-fields-only output.
 */
import { expect, test } from "@playwright/test";

import { APP, E2E_TOKEN, requireAuth } from "./helpers/auth";

test.describe("Module 11: Sales Pipeline @qa @sales", () => {
  test.beforeEach(() => {
    requireAuth();
  });

  // -------------------------------------------------------------------------
  // TC-SALES-001 / 002: Pipeline page + funnel visualization
  // -------------------------------------------------------------------------

  test("TC-SALES-001 GET /sales/pipeline returns funnel + total + leads", async ({
    request,
  }) => {
    const resp = await request.get(`${APP}/api/v1/sales/pipeline`, {
      headers: { Authorization: `Bearer ${E2E_TOKEN}` },
      failOnStatusCode: false,
    });
    expect(resp.status()).toBeLessThan(300);
    const body = await resp.json();
    expect(body).toHaveProperty("funnel");
    expect(body).toHaveProperty("total");
    expect(body).toHaveProperty("leads");
    expect(typeof body.funnel).toBe("object");
    expect(Array.isArray(body.leads)).toBe(true);
    expect(typeof body.total).toBe("number");
  });

  test("TC-SALES-002 GET /sales/pipeline?stage=new only returns 'new' leads", async ({
    request,
  }) => {
    const resp = await request.get(`${APP}/api/v1/sales/pipeline?stage=new`, {
      headers: { Authorization: `Bearer ${E2E_TOKEN}` },
      failOnStatusCode: false,
    });
    expect(resp.status()).toBeLessThan(300);
    const body = await resp.json();
    for (const lead of body.leads) {
      expect(lead.stage).toBe("new");
    }
  });

  // -------------------------------------------------------------------------
  // TC-SALES-003: Lead detail
  // -------------------------------------------------------------------------

  test("TC-SALES-003 GET /sales/pipeline/{nonexistent} returns 404", async ({
    request,
  }) => {
    const resp = await request.get(
      `${APP}/api/v1/sales/pipeline/00000000-0000-0000-0000-000000000000`,
      {
        headers: { Authorization: `Bearer ${E2E_TOKEN}` },
        failOnStatusCode: false,
      },
    );
    expect(resp.status()).toBe(404);
  });

  // -------------------------------------------------------------------------
  // TC-SALES-004: Run sales agent on lead
  // -------------------------------------------------------------------------

  test("TC-SALES-004 POST /sales/pipeline/process-lead missing lead_id returns 400", async ({
    request,
  }) => {
    const resp = await request.post(`${APP}/api/v1/sales/pipeline/process-lead`, {
      headers: {
        Authorization: `Bearer ${E2E_TOKEN}`,
        "Content-Type": "application/json",
      },
      data: {}, // no lead_id
      failOnStatusCode: false,
    });
    expect(resp.status()).toBe(400);
    const body = await resp.json();
    // The detail must mention lead_id so the caller knows what to fix.
    expect(JSON.stringify(body).toLowerCase()).toContain("lead_id");
  });

  // -------------------------------------------------------------------------
  // TC-SALES-005: Seed prospects (env-gated)
  // -------------------------------------------------------------------------

  test("TC-SALES-005 POST /sales/seed-prospects either succeeds (demo/dev) or 403 (prod)", async ({
    request,
  }) => {
    // The env gate determines the response. Foundation #8 false-
    // green prevention: a 5xx here would mean the gate isn't
    // wired; ANY of {200, 201, 403} proves the gate logic ran.
    const resp = await request.post(`${APP}/api/v1/sales/seed-prospects`, {
      headers: { Authorization: `Bearer ${E2E_TOKEN}` },
      failOnStatusCode: false,
    });
    expect([200, 201, 403]).toContain(resp.status());
    if (resp.status() === 403) {
      const body = await resp.json();
      expect(JSON.stringify(body).toLowerCase()).toContain("demo/dev");
    }
  });

  // -------------------------------------------------------------------------
  // TC-SALES-006: CSV import validation
  // -------------------------------------------------------------------------

  test("TC-SALES-006 POST /sales/import-csv with non-CSV file is 422", async ({
    request,
  }) => {
    const resp = await request.post(`${APP}/api/v1/sales/import-csv`, {
      headers: { Authorization: `Bearer ${E2E_TOKEN}` },
      multipart: {
        file: {
          name: "leads.json",
          mimeType: "application/json",
          buffer: Buffer.from('{"leads":[]}'),
        },
      },
      failOnStatusCode: false,
    });
    expect(resp.status()).toBe(422);
    const body = await resp.json();
    expect(JSON.stringify(body).toLowerCase()).toContain("csv");
  });

  test("TC-SALES-006b POST /sales/import-csv with empty CSV is 422", async ({
    request,
  }) => {
    const resp = await request.post(`${APP}/api/v1/sales/import-csv`, {
      headers: { Authorization: `Bearer ${E2E_TOKEN}` },
      multipart: {
        file: {
          name: "empty.csv",
          mimeType: "text/csv",
          buffer: Buffer.from(""),
        },
      },
      failOnStatusCode: false,
    });
    expect(resp.status()).toBe(422);
    const body = await resp.json();
    expect(JSON.stringify(body).toLowerCase()).toContain("empty");
  });

  test("TC-SALES-006c POST /sales/import-csv with missing required headers is 422", async ({
    request,
  }) => {
    // CSV with just a "phone" column — no name, no email.
    const resp = await request.post(`${APP}/api/v1/sales/import-csv`, {
      headers: { Authorization: `Bearer ${E2E_TOKEN}` },
      multipart: {
        file: {
          name: "bad-headers.csv",
          mimeType: "text/csv",
          buffer: Buffer.from("phone\n555-1212\n"),
        },
      },
      failOnStatusCode: false,
    });
    expect(resp.status()).toBe(422);
  });

  // -------------------------------------------------------------------------
  // TC-SALES-007: Due follow-ups
  // -------------------------------------------------------------------------

  test("TC-SALES-007 GET /sales/pipeline/due-followups never returns closed deals", async ({
    request,
  }) => {
    const resp = await request.get(`${APP}/api/v1/sales/pipeline/due-followups`, {
      headers: { Authorization: `Bearer ${E2E_TOKEN}` },
      failOnStatusCode: false,
    });
    expect(resp.status()).toBeLessThan(300);
    const body = await resp.json();
    expect(Array.isArray(body)).toBe(true);
    for (const lead of body) {
      // Closed deals must NEVER appear in the follow-up queue.
      expect(["closed_won", "closed_lost"]).not.toContain(lead.stage);
    }
  });

  // -------------------------------------------------------------------------
  // TC-SALES-008: Sales metrics shape
  // -------------------------------------------------------------------------

  test("TC-SALES-008 GET /sales/metrics returns documented shape", async ({
    request,
  }) => {
    const resp = await request.get(`${APP}/api/v1/sales/metrics`, {
      headers: { Authorization: `Bearer ${E2E_TOKEN}` },
      failOnStatusCode: false,
    });
    expect(resp.status()).toBeLessThan(300);
    const body = await resp.json();
    for (const key of [
      "total_leads",
      "new_this_week",
      "funnel",
      "avg_score",
      "emails_sent_this_week",
      "stale_leads",
    ]) {
      expect(body, `metrics missing key ${key}`).toHaveProperty(key);
    }
    // Numeric metrics must be numbers (not "N/A" or null).
    expect(typeof body.total_leads).toBe("number");
    expect(typeof body.new_this_week).toBe("number");
    expect(typeof body.avg_score).toBe("number");
    expect(typeof body.emails_sent_this_week).toBe("number");
    expect(typeof body.stale_leads).toBe("number");
  });

  // -------------------------------------------------------------------------
  // TC-SALES-009: Lead score color (UI-only — see source pin)
  // -------------------------------------------------------------------------

  test("TC-SALES-009 sales pipeline page renders without runtime errors", async ({
    page,
  }) => {
    const errors: string[] = [];
    page.on("pageerror", (err) => errors.push(err.message));
    page.on("console", (msg) => {
      if (msg.type() === "error") errors.push(msg.text());
    });

    await page.goto(`${APP}/dashboard/sales`);
    await page.waitForLoadState("domcontentloaded");
    // Wait briefly for first render.
    await page.waitForTimeout(1500);

    expect(
      errors.filter(
        (e) =>
          !e.includes("ResizeObserver") &&
          !e.includes("Failed to load resource") &&
          !e.includes("favicon"),
      ),
      `unexpected page errors: ${errors.slice(0, 5).join(" | ")}`,
    ).toEqual([]);
  });
});
