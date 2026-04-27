/**
 * Module 2: Authentication — automated coverage of 15 TCs.
 *
 * Reference each TC id in the test name so
 * `python -m scripts.qa_matrix generate` flips the matrix to
 * ``automated`` for the corresponding rows.
 *
 * Three TCs are deliberately gated and mark themselves test.skip
 * with a documented reason — running them against production would
 * either pollute the lead/org tables or trip our prod rate limiter:
 *
 *   - TC-AUTH-005 / 006: signup creates real orgs. Gated behind
 *     QA_ALLOW_PROD_SIGNUP=1 — operators run on staging or with
 *     an explicit opt-in for cleanup.
 *   - TC-AUTH-015: rate-limit brute force would lock the runner's
 *     egress IP for 15 minutes. Gated behind QA_ALLOW_PROD_RATELIMIT=1
 *     — only runs in dedicated rate-limit test environments.
 *
 * All other TCs use the demo accounts seeded by the platform.
 */
import { expect, test } from "@playwright/test";

const APP = process.env.BASE_URL || "https://app.agenticorg.ai";

const DEMO = {
  ceo:     { email: "ceo@agenticorg.local",     password: "ceo123!"   },
  cfo:     { email: "cfo@agenticorg.local",     password: "cfo123!"   },
  chro:    { email: "chro@agenticorg.local",    password: "chro123!"  },
  cmo:     { email: "cmo@agenticorg.local",     password: "cmo123!"   },
  coo:     { email: "coo@agenticorg.local",     password: "coo123!"   },
  auditor: { email: "auditor@agenticorg.local", password: "audit123!" },
} as const;

// ---------------------------------------------------------------------------
// TC-AUTH-001: Login with demo CEO credentials
// ---------------------------------------------------------------------------

test("TC-AUTH-001 demo CEO login lands on dashboard", async ({ page }) => {
  await page.goto(`${APP}/login`, { waitUntil: "domcontentloaded" });
  await page.locator('input[type="email"]').fill(DEMO.ceo.email);
  await page.locator('input[type="password"]').fill(DEMO.ceo.password);
  await page.locator('button[type="submit"]').click();
  // Either the dashboard route loads or onboarding kicks in (first
  // login). Accept both — the manual TC asserts "redirects to
  // /dashboard" but onboarding is a documented intermediate.
  await page.waitForURL(/\/dashboard|\/onboarding/, { timeout: 30_000 });
});

// ---------------------------------------------------------------------------
// TC-AUTH-002: All 6 demo roles log in successfully
// ---------------------------------------------------------------------------

for (const [role, creds] of Object.entries(DEMO)) {
  test(`TC-AUTH-002 demo ${role} login lands on dashboard`, async ({ page }) => {
    test.setTimeout(60_000);
    await page.goto(`${APP}/login`, { waitUntil: "domcontentloaded" });
    await page.locator('input[type="email"]').fill(creds.email);
    await page.locator('input[type="password"]').fill(creds.password);
    await page.locator('button[type="submit"]').click();
    await page.waitForURL(
      // Auditor lands on /dashboard/audit or /dashboard depending on
      // current routing — accept any /dashboard*.
      /\/dashboard(\/.*)?|\/onboarding/,
      { timeout: 30_000 },
    );
  });
}

// ---------------------------------------------------------------------------
// TC-AUTH-003: Wrong password rejected with generic error
// ---------------------------------------------------------------------------

test("TC-AUTH-003 wrong password shows error and stays on login", async ({ page }) => {
  await page.goto(`${APP}/login`, { waitUntil: "domcontentloaded" });
  await page.locator('input[type="email"]').fill(DEMO.ceo.email);
  await page.locator('input[type="password"]').fill("definitely-wrong-pw-zzz");
  await page.locator('button[type="submit"]').click();
  // Error toast / inline. The manual TC documents "Invalid email or
  // password" — accept any error mention so a copy edit doesn't break.
  await expect(
    page.getByText(/invalid|incorrect|wrong|denied|failed|too many/i).first(),
  ).toBeVisible({ timeout: 10_000 });
  expect(page.url()).toContain("/login");
});

// ---------------------------------------------------------------------------
// TC-AUTH-004: Non-existent email rejected with same generic error
// (no information leakage about whether the email exists)
// ---------------------------------------------------------------------------

test("TC-AUTH-004 non-existent email returns the generic invalid-credential error", async ({ page }) => {
  await page.goto(`${APP}/login`, { waitUntil: "domcontentloaded" });
  await page.locator('input[type="email"]').fill("nonexistent-user-zzz@example.invalid");
  await page.locator('input[type="password"]').fill("anything-goes-here");
  await page.locator('button[type="submit"]').click();
  // Must show a generic error, NOT something like "user not found"
  // (information leakage). Pin both: error visible + no user-leak phrase.
  await expect(
    page.getByText(/invalid|incorrect|wrong|denied|failed|too many/i).first(),
  ).toBeVisible({ timeout: 10_000 });
  const body = (await page.locator("body").textContent()) || "";
  // Negative assertion: no "no such user" / "user does not exist" leak.
  expect(body).not.toMatch(/no such user|user does not exist|email not found/i);
});

// ---------------------------------------------------------------------------
// TC-AUTH-005 / 006: Signup happy path + duplicate email
// Gated — see file header.
// ---------------------------------------------------------------------------

test("TC-AUTH-005 signup creates a new org and lands on onboarding", async ({ page }) => {
  test.skip(
    process.env.QA_ALLOW_PROD_SIGNUP !== "1",
    "Signup creates real orgs in prod. Set QA_ALLOW_PROD_SIGNUP=1 to opt in.",
  );
  const ts = Date.now();
  const email = `qa-test-${ts}@agenticorg-test.invalid`;
  await page.goto(`${APP}/signup`, { waitUntil: "domcontentloaded" });
  await page.getByLabel(/Organization/i).first().fill(`QA Test Corp ${ts}`);
  await page.getByLabel(/^Name$/i).first().fill(`QA Tester ${ts}`);
  await page.getByLabel(/^Email$/i).first().fill(email);
  await page.getByLabel(/^Password$/i).first().fill("TestPass123!");
  await page.getByLabel(/Confirm/i).first().fill("TestPass123!");
  await page.getByRole("button", { name: /Create Account|Sign Up/i }).click();
  await page.waitForURL(/\/onboarding|\/dashboard/, { timeout: 20_000 });
});

test("TC-AUTH-006 signup with duplicate email is rejected", async ({ page }) => {
  test.skip(
    process.env.QA_ALLOW_PROD_SIGNUP !== "1",
    "Requires creating a real org in prod first. Opt in via QA_ALLOW_PROD_SIGNUP=1.",
  );
  // Reuse the demo CEO email — guaranteed to already exist.
  await page.goto(`${APP}/signup`, { waitUntil: "domcontentloaded" });
  await page.getByLabel(/Organization/i).first().fill("QA Dup Corp");
  await page.getByLabel(/^Name$/i).first().fill("QA Dup Tester");
  await page.getByLabel(/^Email$/i).first().fill(DEMO.ceo.email);
  await page.getByLabel(/^Password$/i).first().fill("TestPass123!");
  await page.getByLabel(/Confirm/i).first().fill("TestPass123!");
  await page.getByRole("button", { name: /Create Account|Sign Up/i }).click();
  await expect(
    page.getByText(/already exists|already registered|in use/i).first(),
  ).toBeVisible({ timeout: 10_000 });
});

// ---------------------------------------------------------------------------
// TC-AUTH-007: Password mismatch — client-side validation
//
// The signup form uses a disabled-button-as-validation pattern: the
// "Create account" button is disabled until all required fields are
// filled AND the two passwords match. So the actual contract under
// test is "submit button stays disabled when passwords don't match".
// ---------------------------------------------------------------------------

test("TC-AUTH-007 signup keeps submit disabled when passwords don't match", async ({ page }) => {
  await page.goto(`${APP}/signup`, { waitUntil: "networkidle" });
  // Fill org / name / email so the only blocking issue is the
  // password mismatch.
  const allInputs = page.locator("input");
  // Fields by order: Org name, full name, email, password, confirm.
  await allInputs.nth(0).fill("Mismatch Org");
  await allInputs.nth(1).fill("Mismatch Tester");
  await page.locator('input[type="email"]').first().fill("mismatch-no-create@agenticorg-test.invalid");
  const pwInputs = page.locator('input[type="password"]');
  await pwInputs.nth(0).fill("Pass1234!");
  await pwInputs.nth(1).fill("DifferentPass!");
  const submit = page.getByRole("button", { name: /Create account|Sign Up/i }).first();
  // The contract: button stays disabled OR clicks land on /signup with
  // a mismatch hint. Either way, no /onboarding redirect happens.
  const enabled = await submit.isEnabled();
  if (enabled) {
    await submit.click();
    // No navigation off /signup.
    await page.waitForTimeout(500);
    expect(page.url()).toContain("/signup");
  } else {
    expect(enabled).toBe(false);
  }
});

// ---------------------------------------------------------------------------
// TC-AUTH-008: Empty required fields — submit button must be disabled
// ---------------------------------------------------------------------------

test("TC-AUTH-008 signup keeps submit disabled when required fields are empty", async ({ page }) => {
  await page.goto(`${APP}/signup`, { waitUntil: "networkidle" });
  // No fills — submit must be disabled.
  const submit = page
    .getByRole("button", { name: /Create account|Sign Up/i })
    .first();
  await expect(submit).toBeDisabled({ timeout: 5_000 });
  // No navigation.
  expect(page.url()).toContain("/signup");
});

// ---------------------------------------------------------------------------
// TC-AUTH-009: Google OAuth button visibility
// ---------------------------------------------------------------------------

test("TC-AUTH-009 Google sign-in button is visible (or absent without error)", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.goto(`${APP}/login`, { waitUntil: "domcontentloaded" });
  // Either the button renders OR it's absent — both are valid per the
  // manual TC. The assertion that matters: no JS errors when the
  // OAuth widget is missing.
  const googleBtn = page.getByRole("button", { name: /Google|Sign in with Google/i }).first();
  const visible = await googleBtn.isVisible({ timeout: 5_000 }).catch(() => false);
  // Either way is OK; just no exceptions.
  expect(errors.filter((e) => /google|gsi|oauth/i.test(e))).toEqual([]);
  // If visible, it should be clickable (sanity).
  if (visible) {
    expect(await googleBtn.isEnabled()).toBe(true);
  }
});

// ---------------------------------------------------------------------------
// TC-AUTH-010: Logout
//
// The platform serves the SPA shell at every /dashboard/* path
// (200 OK) and the in-page ProtectedRoute kicks the user to /login
// after mount. So `goto + waitForURL(/login/)` is the right contract,
// not `goto + immediate URL check`.
// ---------------------------------------------------------------------------

test("TC-AUTH-010 logout clears session and redirects protected routes", async ({ page }) => {
  await page.goto(`${APP}/login`, { waitUntil: "domcontentloaded" });
  await page.locator('input[type="email"]').fill(DEMO.ceo.email);
  await page.locator('input[type="password"]').fill(DEMO.ceo.password);
  await page.locator('button[type="submit"]').click();
  await page.waitForURL(/\/dashboard|\/onboarding/, { timeout: 30_000 });

  await page.evaluate(() => {
    localStorage.removeItem("token");
    localStorage.removeItem("user");
  });
  await page.goto(`${APP}/dashboard`, { waitUntil: "domcontentloaded" });
  await page.waitForURL(/\/login/, { timeout: 10_000 });
});

// ---------------------------------------------------------------------------
// TC-AUTH-011: Token expiry
// ---------------------------------------------------------------------------

test("TC-AUTH-011 manually deleting the token redirects protected routes to /login", async ({ page }) => {
  await page.goto(`${APP}/login`, { waitUntil: "domcontentloaded" });
  await page.locator('input[type="email"]').fill(DEMO.ceo.email);
  await page.locator('input[type="password"]').fill(DEMO.ceo.password);
  await page.locator('button[type="submit"]').click();
  await page.waitForURL(/\/dashboard|\/onboarding/, { timeout: 30_000 });

  await page.evaluate(() => localStorage.removeItem("token"));
  await page.goto(`${APP}/dashboard/agents`, { waitUntil: "domcontentloaded" });
  await page.waitForURL(/\/login/, { timeout: 10_000 });
});

// ---------------------------------------------------------------------------
// TC-AUTH-012: Protected routes redirect to /login when unauthenticated
// ---------------------------------------------------------------------------

const PROTECTED_ROUTES = [
  "/dashboard",
  "/dashboard/agents",
  "/dashboard/agents/new",
  "/dashboard/approvals",
  "/dashboard/sales",
  "/dashboard/settings",
];

for (const route of PROTECTED_ROUTES) {
  test(`TC-AUTH-012 ${route} redirects unauthenticated users to /login`, async ({ page }) => {
    // Clean context — no cookies/localStorage. The SPA's
    // ProtectedRoute fires the redirect after mount, so wait for the
    // URL to change (don't check it synchronously after goto).
    await page.goto(`${APP}${route}`, { waitUntil: "domcontentloaded" });
    await page.waitForURL(/\/login/, { timeout: 10_000 });
  });
}

// ---------------------------------------------------------------------------
// TC-AUTH-013: Auditor RBAC — cannot reach agent creation / settings / connectors
// ---------------------------------------------------------------------------

test("TC-AUTH-013 auditor cannot navigate to agent-creation / settings / connectors", async ({ page }) => {
  await page.goto(`${APP}/login`, { waitUntil: "domcontentloaded" });
  await page.locator('input[type="email"]').fill(DEMO.auditor.email);
  await page.locator('input[type="password"]').fill(DEMO.auditor.password);
  await page.locator('button[type="submit"]').click();
  await page.waitForURL(/\/dashboard|\/onboarding/, { timeout: 30_000 });

  // Try the three forbidden routes. Each must either redirect to a
  // non-target route OR show a "denied" message — but MUST NOT
  // render the agent wizard / settings form.
  for (const target of [
    "/dashboard/agents/new",
    "/dashboard/settings",
    "/dashboard/connectors",
  ]) {
    await page.goto(`${APP}${target}`, { waitUntil: "domcontentloaded" });
    // Accept any of: redirect away from target / "denied" copy /
    // 403 banner. Not allowed: form/wizard headings rendering.
    const onTarget = page.url().includes(target);
    const deniedVisible = await page
      .getByText(/access denied|forbidden|unauthorized|not allowed/i)
      .first()
      .isVisible()
      .catch(() => false);
    if (onTarget && !deniedVisible) {
      // Page rendered without a denial — verify auditor isn't
      // actually seeing the privileged surface (e.g. "Save"
      // buttons). If they are, the RBAC contract is broken.
      const saveBtn = page
        .getByRole("button", { name: /Save|Create|Update/i })
        .first();
      expect(
        await saveBtn.isVisible().catch(() => false),
        `Auditor reached ${target} without a denial banner`,
      ).toBe(false);
    }
  }
});

// ---------------------------------------------------------------------------
// TC-AUTH-014: CFO cannot create agents
// ---------------------------------------------------------------------------

test("TC-AUTH-014 CFO cannot reach the agent-creation wizard", async ({ page }) => {
  await page.goto(`${APP}/login`, { waitUntil: "domcontentloaded" });
  await page.locator('input[type="email"]').fill(DEMO.cfo.email);
  await page.locator('input[type="password"]').fill(DEMO.cfo.password);
  await page.locator('button[type="submit"]').click();
  await page.waitForURL(/\/dashboard|\/onboarding/, { timeout: 30_000 });

  await page.goto(`${APP}/dashboard/agents/new`, {
    waitUntil: "domcontentloaded",
  });
  const onTarget = page.url().includes("/dashboard/agents/new");
  const deniedVisible = await page
    .getByText(/access denied|forbidden|unauthorized|not allowed|admin only/i)
    .first()
    .isVisible()
    .catch(() => false);
  if (onTarget && !deniedVisible) {
    // Wizard rendered without a denial — verify the wizard control
    // isn't actually accessible (no "Next" / step controls).
    const wizardStep = page
      .getByRole("button", { name: /^Next$|^Create Agent$/i })
      .first();
    expect(
      await wizardStep.isVisible().catch(() => false),
      "CFO reached /dashboard/agents/new wizard without a denial banner",
    ).toBe(false);
  }
});

// ---------------------------------------------------------------------------
// TC-AUTH-015: Rate limiting on brute-force login
// Gated — runs only with QA_ALLOW_PROD_RATELIMIT=1.
// ---------------------------------------------------------------------------

test("TC-AUTH-015 11 wrong-password attempts trigger HTTP 429 lockout", async ({
  request,
}) => {
  test.skip(
    process.env.QA_ALLOW_PROD_RATELIMIT !== "1",
    "Triggering the rate limiter in prod locks our egress IP for 15 min. Set QA_ALLOW_PROD_RATELIMIT=1 to opt in.",
  );
  let last429 = 0;
  for (let i = 0; i < 11; i++) {
    const resp = await request.post(`${APP}/api/v1/auth/login`, {
      data: { email: DEMO.ceo.email, password: "definitely-wrong-pw" },
      failOnStatusCode: false,
    });
    if (resp.status() === 429) last429 = i + 1;
  }
  expect(last429, "expected at least one HTTP 429 within 11 attempts").toBeGreaterThan(0);
  // Then verify a CORRECT-password attempt is also blocked.
  const blockedResp = await request.post(`${APP}/api/v1/auth/login`, {
    data: { email: DEMO.ceo.email, password: DEMO.ceo.password },
    failOnStatusCode: false,
  });
  expect(blockedResp.status()).toBe(429);
});
