/**
 * Module 15: Settings — TC-SET-001 through 005.
 *
 * API contract tests for /config/fleet_limits + /governance/config.
 * Compliance lives here — the PII flag, data-region, and
 * audit-retention floor are referenced by SOC-2 evidence
 * collection. Failure paths matter as much as happy paths.
 */
import { expect, test } from "@playwright/test";

import { APP, E2E_TOKEN, requireAuth } from "./helpers/auth";

test.describe("Module 15: Settings @qa @settings @governance", () => {
  test.beforeEach(() => {
    requireAuth();
  });

  // -------------------------------------------------------------------------
  // TC-SET-001: Settings page loads — endpoint shape
  // -------------------------------------------------------------------------

  test("TC-SET-001 GET /config/fleet_limits returns documented schema", async ({
    request,
  }) => {
    const resp = await request.get(`${APP}/api/v1/config/fleet_limits`, {
      headers: { Authorization: `Bearer ${E2E_TOKEN}` },
      failOnStatusCode: false,
    });
    expect(resp.status()).toBeLessThan(300);
    const body = await resp.json();
    for (const k of ["max_active_agents", "max_shadow_agents"]) {
      expect(body, `missing key ${k}`).toHaveProperty(k);
    }
    expect(typeof body.max_active_agents).toBe("number");
  });

  test("TC-SET-001b GET /governance/config returns documented schema", async ({
    request,
  }) => {
    const resp = await request.get(`${APP}/api/v1/governance/config`, {
      headers: { Authorization: `Bearer ${E2E_TOKEN}` },
      failOnStatusCode: false,
    });
    expect(resp.status()).toBeLessThan(300);
    const body = await resp.json();
    for (const k of [
      "pii_masking",
      "data_region",
      "audit_retention_years",
    ]) {
      expect(body, `missing key ${k}`).toHaveProperty(k);
    }
    expect(typeof body.pii_masking).toBe("boolean");
    expect(typeof body.audit_retention_years).toBe("number");
    // data_region must be one of the closed enum values.
    expect(["IN", "EU", "US"]).toContain(body.data_region);
  });

  // -------------------------------------------------------------------------
  // TC-SET-002: Update fleet limits
  // -------------------------------------------------------------------------

  test("TC-SET-002 PUT /config/fleet_limits round-trips a value", async ({
    request,
  }) => {
    // Fetch current to roundtrip cleanly without altering tenant state.
    const get = await request.get(`${APP}/api/v1/config/fleet_limits`, {
      headers: { Authorization: `Bearer ${E2E_TOKEN}` },
      failOnStatusCode: false,
    });
    expect(get.status()).toBeLessThan(300);
    const current = await get.json();

    const resp = await request.put(`${APP}/api/v1/config/fleet_limits`, {
      headers: {
        Authorization: `Bearer ${E2E_TOKEN}`,
        "Content-Type": "application/json",
      },
      data: current,
      failOnStatusCode: false,
    });
    expect(resp.status()).toBeLessThan(300);
    const body = await resp.json();
    expect(body.max_active_agents).toBe(current.max_active_agents);
    expect(body.max_shadow_agents).toBe(current.max_shadow_agents);
  });

  // -------------------------------------------------------------------------
  // TC-SET-003: Toggle PII masking — partial update semantics
  // -------------------------------------------------------------------------

  test("TC-SET-003 PUT /governance/config no-op (empty body) returns current state", async ({
    request,
  }) => {
    // Empty body = no fields changed = no-op branch returns
    // current state without an audit row.
    const get = await request.get(`${APP}/api/v1/governance/config`, {
      headers: { Authorization: `Bearer ${E2E_TOKEN}` },
      failOnStatusCode: false,
    });
    const before = await get.json();

    const resp = await request.put(`${APP}/api/v1/governance/config`, {
      headers: {
        Authorization: `Bearer ${E2E_TOKEN}`,
        "Content-Type": "application/json",
      },
      data: {},
      failOnStatusCode: false,
    });
    expect(resp.status()).toBeLessThan(300);
    const after = await resp.json();
    expect(after.pii_masking).toBe(before.pii_masking);
    expect(after.data_region).toBe(before.data_region);
    expect(after.audit_retention_years).toBe(before.audit_retention_years);
  });

  // -------------------------------------------------------------------------
  // TC-SET-004: Change data region — closed enum
  // -------------------------------------------------------------------------

  test("TC-SET-004 PUT /governance/config rejects invalid data_region", async ({
    request,
  }) => {
    // Foundation #8 false-green prevention: a Literal type must
    // reject "APAC" with 422; silently accepting it would
    // persist a region the platform doesn't actually support.
    const resp = await request.put(`${APP}/api/v1/governance/config`, {
      headers: {
        Authorization: `Bearer ${E2E_TOKEN}`,
        "Content-Type": "application/json",
      },
      data: { data_region: "APAC" },
      failOnStatusCode: false,
    });
    expect(resp.status()).toBe(422);
  });

  test("TC-SET-004b PUT /governance/config rejects audit_retention out of range", async ({
    request,
  }) => {
    // Field(ge=1, le=10) — 0 is below floor; 11 is above ceiling.
    for (const value of [0, 11]) {
      const resp = await request.put(`${APP}/api/v1/governance/config`, {
        headers: {
          Authorization: `Bearer ${E2E_TOKEN}`,
          "Content-Type": "application/json",
        },
        data: { audit_retention_years: value },
        failOnStatusCode: false,
      });
      expect(resp.status(), `value=${value}`).toBe(422);
    }
  });

  // -------------------------------------------------------------------------
  // TC-SET-005: Non-admin cannot access settings
  // -------------------------------------------------------------------------

  test("TC-SET-005 GET /config/fleet_limits without auth returns 401/403", async ({
    request,
  }) => {
    const resp = await request.get(`${APP}/api/v1/config/fleet_limits`, {
      failOnStatusCode: false,
    });
    expect([401, 403]).toContain(resp.status());
  });

  test("TC-SET-005b PUT /config/fleet_limits without auth returns 401/403", async ({
    request,
  }) => {
    const resp = await request.put(`${APP}/api/v1/config/fleet_limits`, {
      headers: { "Content-Type": "application/json" },
      data: { max_active_agents: 100, max_shadow_agents: 100 },
      failOnStatusCode: false,
    });
    expect([401, 403]).toContain(resp.status());
  });

  test("TC-SET-005c PUT /governance/config without auth returns 401/403", async ({
    request,
  }) => {
    const resp = await request.put(`${APP}/api/v1/governance/config`, {
      headers: { "Content-Type": "application/json" },
      data: { pii_masking: true },
      failOnStatusCode: false,
    });
    expect([401, 403]).toContain(resp.status());
  });
});
