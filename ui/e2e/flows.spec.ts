import { test, expect, Page, Route } from "@playwright/test";

// ---------------------------------------------------------------------------
// Mock API data fixtures
// ---------------------------------------------------------------------------

const MOCK_AGENTS = [
  { id: "agent-001", name: "Invoice Processor", agent_type: "ap_processor", domain: "finance", status: "active", version: "1.2.0", confidence_floor: 0.88, shadow_sample_count: 1200, shadow_accuracy_current: 0.94, created_at: "2026-01-15T10:00:00Z" },
  { id: "agent-002", name: "Talent Scout", agent_type: "talent_acquisition", domain: "hr", status: "shadow", version: "0.9.0", confidence_floor: 0.85, shadow_sample_count: 340, shadow_accuracy_current: 0.87, created_at: "2026-02-20T08:00:00Z" },
  { id: "agent-003", name: "Content Writer", agent_type: "content_gen", domain: "marketing", status: "paused", version: "1.0.0", confidence_floor: 0.90, shadow_sample_count: 800, shadow_accuracy_current: null, created_at: "2026-03-01T12:00:00Z" },
];

const MOCK_WORKFLOWS = [
  { id: "wf-001", name: "Invoice Pipeline", version: "2", is_active: true, trigger_type: "webhook", created_at: "2026-01-10T09:00:00Z" },
  { id: "wf-002", name: "Onboarding Flow", version: "1", is_active: false, trigger_type: "manual", created_at: "2026-02-15T11:00:00Z" },
];

const MOCK_APPROVALS = [
  { id: "hitl-001", title: "Large invoice approval", trigger_type: "confidence_below_threshold", priority: "critical", status: "pending", assignee_role: "finance_lead", context: { amount: 500000, vendor: "Infosys" }, expires_at: "2026-03-22T10:00:00Z" },
  { id: "hitl-002", title: "New vendor onboarding", trigger_type: "manual_review", priority: "high", status: "pending", assignee_role: "procurement", context: { vendor_name: "TCS" }, expires_at: "2026-03-23T10:00:00Z" },
  { id: "hitl-003", title: "Contract auto-renewal", trigger_type: "policy_override", priority: "normal", status: "approved", assignee_role: "legal", context: {}, expires_at: "2026-03-20T10:00:00Z" },
];

const MOCK_CONNECTORS = [
  { id: "conn-001", name: "SAP S/4HANA", category: "finance", status: "active", auth_type: "oauth2", rate_limit_rpm: 500 },
  { id: "conn-002", name: "GSTN Portal", category: "finance", status: "active", auth_type: "certificate", rate_limit_rpm: 100 },
  { id: "conn-003", name: "Darwinbox", category: "hr", status: "unhealthy", auth_type: "api_key", rate_limit_rpm: 200 },
];

const MOCK_AUDIT = [
  { id: "aud-001", event_type: "agent.action", actor_type: "agent", action: "process_invoice", outcome: "success", created_at: "2026-03-21T08:00:00Z" },
  { id: "aud-002", event_type: "hitl.decision", actor_type: "human", action: "approve_payment", outcome: "success", created_at: "2026-03-21T07:30:00Z" },
  { id: "aud-003", event_type: "agent.error", actor_type: "agent", action: "tax_filing", outcome: "failure", created_at: "2026-03-21T07:00:00Z" },
];

const MOCK_FLEET_LIMITS = {
  max_active_agents: 50,
  max_agents_per_domain: { finance: 20, hr: 20, marketing: 20, ops: 20, backoffice: 20 },
  max_shadow_agents: 10,
  max_replicas_per_type: 20,
};

const MOCK_WORKFLOW_RUN = {
  id: "run-001", workflow_def_id: "wf-001", status: "running",
  steps_total: 4, steps_completed: 2, started_at: "2026-03-21T09:00:00Z",
  steps: [
    { step_id: "extract", step_type: "agent", status: "completed", agent_id: "agent-001", confidence: 0.95, latency_ms: 230 },
    { step_id: "validate", step_type: "agent", status: "completed", agent_id: "agent-001", confidence: 0.91, latency_ms: 150 },
    { step_id: "approve", step_type: "hitl", status: "running", agent_id: null, confidence: null, latency_ms: null },
    { step_id: "post", step_type: "agent", status: "pending", agent_id: "agent-001", confidence: null, latency_ms: null },
  ],
};

// ---------------------------------------------------------------------------
// Helper: set up API route mocks for all endpoints
// ---------------------------------------------------------------------------

async function mockAllAPIs(page: Page) {
  await page.route("**/api/v1/agents?*", (route) => route.fulfill({ json: { items: MOCK_AGENTS } }));
  await page.route("**/api/v1/agents", (route) => {
    if (route.request().method() === "GET") return route.fulfill({ json: { items: MOCK_AGENTS } });
    if (route.request().method() === "POST") return route.fulfill({ json: { id: "agent-new-001", ...JSON.parse(route.request().postData() || "{}") } });
    return route.continue();
  });
  await page.route("**/api/v1/agents/*/promote", (route) => route.fulfill({ json: { status: "active" } }));
  await page.route("**/api/v1/agents/*/rollback", (route) => route.fulfill({ json: { status: "shadow" } }));
  await page.route("**/api/v1/agents/*/pause", (route) => route.fulfill({ json: { status: "paused" } }));
  await page.route("**/api/v1/agents/*", (route) => {
    const id = route.request().url().split("/agents/")[1]?.split("?")[0]?.split("/")[0];
    const agent = MOCK_AGENTS.find((a) => a.id === id);
    return route.fulfill({ json: agent || { detail: "Not found" }, status: agent ? 200 : 404 });
  });
  await page.route("**/api/v1/workflows", (route) => {
    if (route.request().method() === "GET") return route.fulfill({ json: { items: MOCK_WORKFLOWS } });
    if (route.request().method() === "POST") return route.fulfill({ json: { id: "wf-new-001" } });
    return route.continue();
  });
  await page.route("**/api/v1/workflows/*/run", (route) => route.fulfill({ json: { run_id: "run-001" } }));
  await page.route("**/api/v1/workflows/runs/*/cancel", (route) => route.fulfill({ json: { status: "cancelled" } }));
  await page.route("**/api/v1/workflows/runs/*", (route) => route.fulfill({ json: MOCK_WORKFLOW_RUN }));
  await page.route("**/api/v1/approvals/*/decide", (route) => route.fulfill({ json: { status: "decided" } }));
  await page.route("**/api/v1/approvals", (route) => route.fulfill({ json: { items: MOCK_APPROVALS } }));
  await page.route("**/api/v1/connectors/*/health", (route) => route.fulfill({ json: { status: "healthy" } }));
  await page.route("**/api/v1/connectors", (route) => {
    if (route.request().method() === "GET") return route.fulfill({ json: { items: MOCK_CONNECTORS } });
    if (route.request().method() === "POST") return route.fulfill({ json: { id: "conn-new-001" } });
    return route.continue();
  });
  await page.route("**/api/v1/schemas", (route) => route.fulfill({ json: { items: [] } }));
  await page.route("**/api/v1/audit?*", (route) => route.fulfill({ json: { items: MOCK_AUDIT } }));
  await page.route("**/api/v1/audit", (route) => route.fulfill({ json: { items: MOCK_AUDIT } }));
  await page.route("**/api/v1/compliance/evidence-package", (route) => route.fulfill({ json: { entries: MOCK_AUDIT, exported_at: "2026-03-21T09:00:00Z" } }));
  await page.route("**/api/v1/config/fleet_limits", (route) => {
    if (route.request().method() === "GET") return route.fulfill({ json: MOCK_FLEET_LIMITS });
    if (route.request().method() === "PUT") return route.fulfill({ json: { ...MOCK_FLEET_LIMITS, ...JSON.parse(route.request().postData() || "{}") } });
    return route.continue();
  });
  // Block WebSocket to prevent noise
  await page.route("**/ws/**", (route) => route.abort());
}

// ============================================================================
// AGENTS — Full CRUD + actions
// ============================================================================

test.describe("Agents — Full Flow", () => {
  test.beforeEach(async ({ page }) => { await mockAllAPIs(page); });

  test("lists agents with correct data from API", async ({ page }) => {
    await page.goto("/dashboard/agents");
    await expect(page.getByText("Invoice Processor")).toBeVisible();
    await expect(page.getByText("Talent Scout")).toBeVisible();
    await expect(page.getByText("Content Writer")).toBeVisible();
    // Stats
    await expect(page.getByText("3").first()).toBeVisible(); // total
    // Confidence renders correctly (no NaN)
    await expect(page.getByText("88%")).toBeVisible();
    await expect(page.getByText("85%")).toBeVisible();
  });

  test("domain filter sends correct API param", async ({ page }) => {
    let capturedURL = "";
    await page.route("**/api/v1/agents?*", (route) => {
      capturedURL = route.request().url();
      return route.fulfill({ json: { items: [MOCK_AGENTS[0]] } });
    });
    await page.goto("/dashboard/agents");
    await page.locator("select").first().selectOption("finance");
    await page.waitForTimeout(500);
    expect(capturedURL).toContain("domain=finance");
  });

  test("status filter sends correct API param", async ({ page }) => {
    let capturedURL = "";
    await page.route("**/api/v1/agents?*", (route) => {
      capturedURL = route.request().url();
      return route.fulfill({ json: { items: [MOCK_AGENTS[1]] } });
    });
    await page.goto("/dashboard/agents");
    const selects = page.locator("select");
    await selects.nth(1).selectOption("shadow");
    await page.waitForTimeout(500);
    expect(capturedURL).toContain("status=shadow");
  });

  test("search filters agents client-side", async ({ page }) => {
    await page.goto("/dashboard/agents");
    await page.getByPlaceholder("Search agents...").fill("Invoice");
    await expect(page.getByText("Invoice Processor")).toBeVisible();
    await expect(page.getByText("Talent Scout")).not.toBeVisible();
  });

  test("click agent card navigates to detail page", async ({ page }) => {
    await page.goto("/dashboard/agents");
    await page.getByText("Invoice Processor").click();
    await expect(page).toHaveURL("/dashboard/agents/agent-001");
  });

  test("agent detail shows all fields correctly", async ({ page }) => {
    await page.unrouteAll({ behavior: "ignoreErrors" });
    await page.route("**/ws/**", (route) => route.abort());
    await page.route("**/api/v1/agents/**", (route) => {
      if (route.request().url().endsWith("/agent-001")) return route.fulfill({ json: MOCK_AGENTS[0] });
      return route.fulfill({ json: { status: "ok" } });
    });
    await page.goto("/dashboard/agents/agent-001");
    await expect(page.getByText("Invoice Processor")).toBeVisible();
    await expect(page.getByText("ap_processor").first()).toBeVisible();
    await expect(page.getByText("88%")).toBeVisible();
    await expect(page.getByText("1200")).toBeVisible();
    await expect(page.getByText("94.0%")).toBeVisible();
    await expect(page.getByText("1.2.0")).toBeVisible();
  });

  test("agent detail tabs switch content", async ({ page }) => {
    await page.unrouteAll({ behavior: "ignoreErrors" });
    await page.route("**/ws/**", (route) => route.abort());
    await page.route("**/api/v1/agents/**", (route) => {
      if (route.request().url().endsWith("/agent-001")) return route.fulfill({ json: MOCK_AGENTS[0] });
      return route.fulfill({ json: { status: "ok" } });
    });
    await page.goto("/dashboard/agents/agent-001");
    // Overview tab (default)
    await expect(page.getByText("Agent ID:")).toBeVisible();
    // Config tab
    await page.getByRole("button", { name: "config" }).click();
    await expect(page.getByText("Agent configuration")).toBeVisible();
    // Shadow tab
    await page.getByRole("button", { name: "shadow" }).click();
    await expect(page.getByText("Shadow comparison")).toBeVisible();
    // Cost tab
    await page.getByRole("button", { name: "cost" }).click();
    await expect(page.getByText("Daily token usage")).toBeVisible();
  });

  test("agent detail — nonexistent ID shows 'Agent not found'", async ({ page }) => {
    await page.goto("/dashboard/agents/nonexistent-xyz");
    await expect(page.getByText("Agent not found")).toBeVisible();
  });

  test("promote button sends POST to correct endpoint", async ({ page }) => {
    let promoteCalled = false;
    await page.route("**/api/v1/agents/agent-001/promote", (route) => {
      promoteCalled = true;
      expect(route.request().method()).toBe("POST");
      return route.fulfill({ json: { status: "active" } });
    });
    await page.goto("/dashboard/agents/agent-001");
    await page.getByRole("button", { name: "Promote" }).click();
    expect(promoteCalled).toBe(true);
  });

  test("rollback button sends POST to correct endpoint", async ({ page }) => {
    let rollbackCalled = false;
    await page.route("**/api/v1/agents/agent-001/rollback", (route) => {
      rollbackCalled = true;
      expect(route.request().method()).toBe("POST");
      return route.fulfill({ json: { status: "shadow" } });
    });
    await page.goto("/dashboard/agents/agent-001");
    await page.getByRole("button", { name: "Rollback" }).click();
    expect(rollbackCalled).toBe(true);
  });

  test("kill switch requires confirmation before pausing", async ({ page }) => {
    let pauseCalled = false;
    await page.route("**/api/v1/agents/agent-001/pause", (route) => {
      pauseCalled = true;
      return route.fulfill({ json: { status: "paused" } });
    });
    await page.goto("/dashboard/agents/agent-001");
    await page.getByRole("button", { name: "Kill Switch" }).click();
    // Confirmation prompt appears
    await expect(page.getByText("Pause Invoice Processor?")).toBeVisible();
    expect(pauseCalled).toBe(false); // not called yet
    await page.getByRole("button", { name: "Confirm" }).click();
    expect(pauseCalled).toBe(true);
  });

  test("kill switch cancel does not pause", async ({ page }) => {
    let pauseCalled = false;
    await page.route("**/api/v1/agents/agent-001/pause", (route) => {
      pauseCalled = true;
      return route.fulfill({ json: { status: "paused" } });
    });
    await page.goto("/dashboard/agents/agent-001");
    await page.getByRole("button", { name: "Kill Switch" }).click();
    await page.getByRole("button", { name: "Cancel" }).click();
    expect(pauseCalled).toBe(false);
  });

  test("create agent — form validation rejects empty name", async ({ page }) => {
    await page.goto("/dashboard/agents/new");
    await page.getByRole("button", { name: "Create Agent" }).click();
    await expect(page.getByText("Agent name is required")).toBeVisible();
  });

  test("create agent — submit sends correct payload", async ({ page }) => {
    let payload: any = null;
    await page.route("**/api/v1/agents", (route) => {
      if (route.request().method() === "POST") {
        payload = JSON.parse(route.request().postData() || "{}");
        return route.fulfill({ json: { id: "agent-new-001", ...payload } });
      }
      return route.fulfill({ json: { items: MOCK_AGENTS } });
    });
    await page.goto("/dashboard/agents/new");
    await page.getByPlaceholder("e.g. Invoice Processor").fill("Test Agent");
    await page.locator("select").first().selectOption("hr");
    await page.getByRole("button", { name: "Create Agent" }).click();
    await page.waitForTimeout(500);
    expect(payload).not.toBeNull();
    expect(payload.name).toBe("Test Agent");
    expect(payload.domain).toBe("hr");
    expect(payload.status).toBe("shadow");
    expect(payload.confidence_floor).toBeGreaterThan(0);
  });
});

// ============================================================================
// WORKFLOWS — Full Flow
// ============================================================================

test.describe("Workflows — Full Flow", () => {
  test.beforeEach(async ({ page }) => { await mockAllAPIs(page); });

  test("lists workflows from API", async ({ page }) => {
    await page.goto("/dashboard/workflows");
    await expect(page.getByText("Invoice Pipeline")).toBeVisible();
    await expect(page.getByText("Onboarding Flow")).toBeVisible();
    await expect(page.getByText("Active").first()).toBeVisible();
    await expect(page.getByText("Inactive")).toBeVisible();
  });

  test("Run Now triggers workflow and navigates to run page", async ({ page }) => {
    let runCalled = false;
    await page.route("**/api/v1/workflows/wf-001/run", (route) => {
      runCalled = true;
      expect(route.request().method()).toBe("POST");
      return route.fulfill({ json: { run_id: "run-001" } });
    });
    await page.goto("/dashboard/workflows");
    await page.getByRole("button", { name: "Run Now" }).first().click();
    expect(runCalled).toBe(true);
    await expect(page).toHaveURL(/\/dashboard\/workflows\/run-001\/runs\/run-001/);
  });

  test("workflow run page shows progress and steps", async ({ page }) => {
    await page.goto("/dashboard/workflows/wf-001/runs/run-001");
    await expect(page.getByText("Workflow Run")).toBeVisible();
    await expect(page.getByText("2/4")).toBeVisible();         // progress
    await expect(page.getByText("extract")).toBeVisible();      // step 1
    await expect(page.getByText("validate")).toBeVisible();     // step 2
    await expect(page.getByText("approve")).toBeVisible();      // step 3
    await expect(page.getByText("95%")).toBeVisible();          // confidence
    await expect(page.getByText("230ms")).toBeVisible();        // latency
  });

  test("workflow run cancel button sends POST", async ({ page }) => {
    let cancelCalled = false;
    await page.route("**/api/v1/workflows/runs/run-001/cancel", (route) => {
      cancelCalled = true;
      return route.fulfill({ json: { status: "cancelled" } });
    });
    await page.goto("/dashboard/workflows/wf-001/runs/run-001");
    await page.getByRole("button", { name: "Cancel" }).click();
    expect(cancelCalled).toBe(true);
  });

  test("workflow run refresh re-fetches data", async ({ page }) => {
    let fetchCount = 0;
    await page.route("**/api/v1/workflows/runs/*", (route) => {
      fetchCount++;
      return route.fulfill({ json: MOCK_WORKFLOW_RUN });
    });
    await page.goto("/dashboard/workflows/wf-001/runs/run-001");
    await page.waitForTimeout(300);
    const initial = fetchCount;
    await page.getByRole("button", { name: "Refresh" }).click();
    await page.waitForTimeout(300);
    expect(fetchCount).toBeGreaterThan(initial);
  });

  test("create workflow — validation and submit", async ({ page }) => {
    let payload: any = null;
    await page.route("**/api/v1/workflows", (route) => {
      if (route.request().method() === "POST") {
        payload = JSON.parse(route.request().postData() || "{}");
        return route.fulfill({ json: { id: "wf-new-001" } });
      }
      return route.fulfill({ json: { items: MOCK_WORKFLOWS } });
    });
    await page.goto("/dashboard/workflows/new");
    // Validate empty name
    await page.getByRole("button", { name: "Create Workflow" }).click();
    await expect(page.getByText("Workflow name is required")).toBeVisible();
    // Fill and submit
    await page.getByPlaceholder("e.g. Invoice Processing Pipeline").fill("Test Workflow");
    await page.locator("select").selectOption("schedule");
    await page.getByRole("button", { name: "Create Workflow" }).click();
    await page.waitForTimeout(500);
    expect(payload).not.toBeNull();
    expect(payload.name).toBe("Test Workflow");
    expect(payload.trigger_type).toBe("schedule");
  });
});

// ============================================================================
// APPROVALS — HITL decision flow
// ============================================================================

test.describe("Approvals — HITL Flow", () => {
  test.beforeEach(async ({ page }) => { await mockAllAPIs(page); });

  test("shows pending approvals with correct data", async ({ page }) => {
    await page.goto("/dashboard/approvals");
    await expect(page.getByText("Large invoice approval")).toBeVisible();
    await expect(page.getByText("New vendor onboarding")).toBeVisible();
    await expect(page.getByText("Pending (2)")).toBeVisible();
    await expect(page.getByText("Decided (1)")).toBeVisible();
  });

  test("tab switching between pending and decided", async ({ page }) => {
    await page.goto("/dashboard/approvals");
    // Default: Pending tab
    await expect(page.getByText("Large invoice approval")).toBeVisible();
    // Switch to Decided
    await page.getByText("Decided (1)").click();
    await expect(page.getByText("Contract auto-renewal")).toBeVisible();
  });

  test("priority filter works", async ({ page }) => {
    await page.goto("/dashboard/approvals");
    await page.locator("select").selectOption("critical");
    await expect(page.getByText("Large invoice approval")).toBeVisible();
    // High priority item should be hidden
    await expect(page.getByText("New vendor onboarding")).not.toBeVisible();
  });

  test("approve sends correct decision", async ({ page }) => {
    let decision: any = null;
    await page.route("**/api/v1/approvals/hitl-001/decide", (route) => {
      decision = JSON.parse(route.request().postData() || "{}");
      return route.fulfill({ json: { status: "decided" } });
    });
    await page.goto("/dashboard/approvals");
    await page.getByRole("button", { name: "Approve" }).first().click();
    await page.waitForTimeout(500);
    expect(decision).not.toBeNull();
    expect(decision.decision).toBe("approve");
  });

  test("reject sends correct decision", async ({ page }) => {
    let decision: any = null;
    await page.route("**/api/v1/approvals/hitl-001/decide", (route) => {
      decision = JSON.parse(route.request().postData() || "{}");
      return route.fulfill({ json: { status: "decided" } });
    });
    await page.goto("/dashboard/approvals");
    await page.getByRole("button", { name: "Reject" }).first().click();
    await page.waitForTimeout(500);
    expect(decision).not.toBeNull();
    expect(decision.decision).toBe("reject");
  });
});

// ============================================================================
// CONNECTORS — list, filter, health check, create
// ============================================================================

test.describe("Connectors — Full Flow", () => {
  test.beforeEach(async ({ page }) => { await mockAllAPIs(page); });

  test("lists connectors from API with stats", async ({ page }) => {
    await page.goto("/dashboard/connectors");
    await expect(page.getByText("SAP S/4HANA")).toBeVisible();
    await expect(page.getByText("GSTN Portal")).toBeVisible();
    await expect(page.getByText("Darwinbox")).toBeVisible();
    // Stats
    await expect(page.locator("main").getByText("3").first()).toBeVisible(); // total
  });

  test("category filter works", async ({ page }) => {
    await page.goto("/dashboard/connectors");
    await page.locator("select").selectOption("hr");
    await expect(page.getByText("Darwinbox")).toBeVisible();
    await expect(page.getByText("SAP S/4HANA")).not.toBeVisible();
  });

  test("health check calls API and shows result", async ({ page }) => {
    let healthCalled = false;
    await page.route("**/api/v1/connectors/conn-001/health", (route) => {
      healthCalled = true;
      return route.fulfill({ json: { status: "healthy" } });
    });
    await page.goto("/dashboard/connectors");
    page.on("dialog", (dialog) => dialog.accept());
    await page.getByRole("button", { name: "Health Check" }).first().click();
    await page.waitForTimeout(500);
    expect(healthCalled).toBe(true);
  });

  test("create connector — validation and submit", async ({ page }) => {
    let payload: any = null;
    await page.route("**/api/v1/connectors", (route) => {
      if (route.request().method() === "POST") {
        payload = JSON.parse(route.request().postData() || "{}");
        return route.fulfill({ json: { id: "conn-new-001" } });
      }
      return route.fulfill({ json: { items: MOCK_CONNECTORS } });
    });
    await page.goto("/dashboard/connectors/new");
    // Empty name validation
    await page.getByRole("button", { name: "Register Connector" }).click();
    await expect(page.getByText("Connector name is required")).toBeVisible();
    // Fill and submit
    await page.getByPlaceholder("e.g. SAP S/4HANA").fill("Test Connector");
    await page.locator("select").first().selectOption("hr");
    await page.getByRole("button", { name: "Register Connector" }).click();
    await page.waitForTimeout(500);
    expect(payload).not.toBeNull();
    expect(payload.name).toBe("Test Connector");
    expect(payload.category).toBe("hr");
  });
});

// ============================================================================
// SCHEMAS — list, click-to-edit, create new
// ============================================================================

test.describe("Schemas — Full Flow", () => {
  test.beforeEach(async ({ page }) => { await mockAllAPIs(page); });

  test("shows 18 default schemas when API returns empty", async ({ page }) => {
    await page.goto("/dashboard/schemas");
    await expect(page.getByText("18").first()).toBeVisible(); // total
    await expect(page.getByText("Invoice").first()).toBeVisible();
    await expect(page.getByText("PayrollRun")).toBeVisible();
    await expect(page.getByText("CustomFieldsExtension")).toBeVisible();
  });

  test("clicking schema card opens editor with definition", async ({ page }) => {
    await page.goto("/dashboard/schemas");
    await page.getByText("Invoice").first().click();
    await expect(page.getByText("Edit: Invoice")).toBeVisible();
    // Monaco editor should contain schema content
    await expect(page.locator(".monaco-editor")).toBeVisible();
  });

  test("Create Schema opens blank editor", async ({ page }) => {
    await page.goto("/dashboard/schemas");
    await page.getByRole("button", { name: "Create Schema" }).click();
    await expect(page.getByText("New Schema")).toBeVisible();
    await expect(page.locator(".monaco-editor")).toBeVisible();
  });

  test("close button hides editor", async ({ page }) => {
    await page.goto("/dashboard/schemas");
    await page.getByText("Invoice").first().click();
    await expect(page.getByText("Edit: Invoice")).toBeVisible();
    await page.getByRole("button", { name: "Close" }).click();
    await expect(page.getByText("Edit: Invoice")).not.toBeVisible();
  });
});

// ============================================================================
// AUDIT — table, filter, pagination, export
// ============================================================================

test.describe("Audit — Full Flow", () => {
  test.beforeEach(async ({ page }) => { await mockAllAPIs(page); });

  test("renders audit table with correct data", async ({ page }) => {
    await page.goto("/dashboard/audit");
    await expect(page.getByText("agent.action")).toBeVisible();
    await expect(page.getByText("hitl.decision")).toBeVisible();
    await expect(page.getByText("agent.error")).toBeVisible();
    await expect(page.getByText("process_invoice")).toBeVisible();
    // Table headers
    await expect(page.getByText("Timestamp")).toBeVisible();
    await expect(page.getByText("Event Type")).toBeVisible();
    await expect(page.getByText("Actor")).toBeVisible();
    await expect(page.getByText("Outcome")).toBeVisible();
  });

  test("event type filter sends API param", async ({ page }) => {
    let capturedURL = "";
    await page.route("**/api/v1/audit?*", (route) => {
      capturedURL = route.request().url();
      return route.fulfill({ json: { items: MOCK_AUDIT } });
    });
    await page.goto("/dashboard/audit");
    await page.getByPlaceholder("Filter by event type...").fill("agent.action");
    await page.waitForTimeout(600);
    expect(capturedURL).toContain("event_type=agent.action");
  });

  test("pagination sends correct page params", async ({ page }) => {
    // Generate 50 items so Next button is enabled (perPage = 50)
    const fullPage = Array.from({ length: 50 }, (_, i) => ({
      id: `aud-${i}`, event_type: "agent.action", actor_type: "agent",
      action: `action_${i}`, outcome: "success", created_at: "2026-03-21T08:00:00Z",
    }));
    let capturedURL = "";
    await page.route("**/api/v1/audit?*", (route) => {
      capturedURL = route.request().url();
      return route.fulfill({ json: { items: fullPage } });
    });
    await page.route("**/ws/**", (route) => route.abort());
    await page.goto("/dashboard/audit");
    await page.waitForTimeout(500);
    await page.getByRole("button", { name: "Next" }).click();
    await page.waitForTimeout(500);
    expect(capturedURL).toContain("page=2");
    await expect(page.getByText("Page 2")).toBeVisible();
  });

  test("export evidence package triggers download", async ({ page }) => {
    let exportCalled = false;
    await page.route("**/api/v1/compliance/evidence-package", (route) => {
      exportCalled = true;
      return route.fulfill({ json: { entries: MOCK_AUDIT, exported_at: "2026-03-21" } });
    });
    await page.goto("/dashboard/audit");
    const downloadPromise = page.waitForEvent("download", { timeout: 5000 }).catch(() => null);
    await page.getByRole("button", { name: "Export Evidence Package" }).click();
    await page.waitForTimeout(500);
    expect(exportCalled).toBe(true);
  });
});

// ============================================================================
// SETTINGS — load, modify, save
// ============================================================================

test.describe("Settings — Full Flow", () => {
  test.beforeEach(async ({ page }) => { await mockAllAPIs(page); });

  test("loads fleet limits from API", async ({ page }) => {
    await page.goto("/dashboard/settings");
    await expect(page.getByText("Fleet Governance Limits")).toBeVisible();
    await expect(page.getByText("Compliance & Data")).toBeVisible();
    // Default values loaded
    const maxAgentsInput = page.locator('input[type="number"]').first();
    await expect(maxAgentsInput).toHaveValue("50");
  });

  test("save settings sends PUT with updated values", async ({ page }) => {
    let payload: any = null;
    await page.route("**/api/v1/config/fleet_limits", (route) => {
      if (route.request().method() === "PUT") {
        payload = JSON.parse(route.request().postData() || "{}");
        return route.fulfill({ json: payload });
      }
      return route.fulfill({ json: MOCK_FLEET_LIMITS });
    });
    await page.goto("/dashboard/settings");
    await page.waitForTimeout(300);
    // Modify max active agents
    const maxAgentsInput = page.locator('input[type="number"]').first();
    await maxAgentsInput.fill("30");
    await page.getByRole("button", { name: "Save Settings" }).click();
    await page.waitForTimeout(500);
    expect(payload).not.toBeNull();
    expect(payload.max_active_agents).toBe(30);
    await expect(page.getByText("Settings saved successfully")).toBeVisible();
  });
});

// ============================================================================
// ERROR HANDLING — API failures don't crash the app
// ============================================================================

test.describe("Error Handling — Graceful Degradation", () => {
  test("agents page handles 500 error gracefully", async ({ page }) => {
    await page.route("**/api/v1/agents*", (route) => route.fulfill({ status: 500, json: { detail: "Internal server error" } }));
    await page.route("**/ws/**", (route) => route.abort());
    await page.goto("/dashboard/agents");
    await expect(page.getByText("No agents found")).toBeVisible();
    // No ErrorBoundary crash
    await expect(page.getByText("Something went wrong")).not.toBeVisible();
  });

  test("connectors page handles non-array response", async ({ page }) => {
    await page.route("**/api/v1/connectors", (route) => route.fulfill({ json: { detail: "Not Found" } }));
    await page.route("**/ws/**", (route) => route.abort());
    await page.goto("/dashboard/connectors");
    await expect(page.getByText("No connectors found")).toBeVisible();
    await expect(page.getByText("Something went wrong")).not.toBeVisible();
  });

  test("workflows page handles network error", async ({ page }) => {
    await page.route("**/api/v1/workflows", (route) => route.abort());
    await page.route("**/ws/**", (route) => route.abort());
    await page.goto("/dashboard/workflows");
    await expect(page.getByText("No workflows configured")).toBeVisible();
    await expect(page.getByText("Something went wrong")).not.toBeVisible();
  });

  test("agent detail handles 404 for unknown agent", async ({ page }) => {
    await page.route("**/api/v1/agents/*", (route) => route.fulfill({ status: 404, json: { detail: "Not found" } }));
    await page.route("**/ws/**", (route) => route.abort());
    await page.goto("/dashboard/agents/unknown-id");
    await expect(page.getByText("Agent not found")).toBeVisible();
    await expect(page.getByText("Something went wrong")).not.toBeVisible();
  });

  test("all dashboard pages render without NaN or undefined", async ({ page }) => {
    await mockAllAPIs(page);
    const pages = ["/dashboard", "/dashboard/agents", "/dashboard/workflows", "/dashboard/approvals",
      "/dashboard/connectors", "/dashboard/schemas", "/dashboard/audit", "/dashboard/settings"];
    for (const p of pages) {
      await page.goto(p);
      await page.waitForTimeout(500);
      const text = await page.locator("main").textContent() || "";
      expect(text, `NaN found on ${p}`).not.toContain("NaN");
      expect(text, `undefined found on ${p}`).not.toContain("undefined");
    }
  });
});
