/**
 * Playwright recordings for demo videos — one per script.
 * Run: cd ui && npx playwright test e2e/video-recordings.spec.ts --config=playwright-demo.config.ts --headed
 * Output: video/recordings/
 */
import { test, Page } from "@playwright/test";

const APP = "https://app.agenticorg.ai";
const PAUSE = 3000;
const LONG = 5000;
const OBSERVATORY_WAIT = 10000;

async function login(page: Page, email: string, password: string) {
  await page.goto(`${APP}/login`, { waitUntil: "networkidle" });
  await page.waitForTimeout(1000);
  await page.fill('input[type="email"]', email);
  await page.fill('input[type="password"]', password);
  await page.click('button[type="submit"]');
  await page.waitForURL(/\/dashboard/, { timeout: 15000 });
  await page.waitForTimeout(PAUSE);
}

async function smoothScroll(page: Page, distance: number, duration: number) {
  await page.evaluate(([d, t]) => {
    return new Promise<void>((resolve) => {
      const start = window.scrollY;
      const startTime = Date.now();
      function step() {
        const elapsed = Date.now() - startTime;
        const progress = Math.min(elapsed / t, 1);
        const ease = progress < 0.5 ? 2 * progress * progress : -1 + (4 - 2 * progress) * progress;
        window.scrollTo(0, start + d * ease);
        if (progress < 1) requestAnimationFrame(step);
        else resolve();
      }
      step();
    });
  }, [distance, duration]);
}

// ═══════════════════════════════════════════════════════════
// VIDEO 1: Platform Overview (CEO)
// ═══════════════════════════════════════════════════════════
test("01 — Platform Overview", async ({ page }) => {
  test.setTimeout(180_000);

  // Scene 1: Landing page
  await page.goto(APP, { waitUntil: "networkidle" });
  await page.waitForTimeout(LONG);
  await smoothScroll(page, 800, 2000);
  await page.waitForTimeout(PAUSE);
  await smoothScroll(page, 800, 2000);
  await page.waitForTimeout(PAUSE);

  // Scene 2: Login as CEO → Dashboard
  await login(page, "ceo@agenticorg.local", "ceo123!");
  await page.waitForTimeout(LONG);
  await smoothScroll(page, 400, 1500);
  await page.waitForTimeout(PAUSE);

  // Scene 3: Observatory
  await page.click('a[href="/dashboard/observatory"]');
  await page.waitForTimeout(OBSERVATORY_WAIT);

  // Scene 4: Approvals
  await page.click('a[href="/dashboard/approvals"]');
  await page.waitForTimeout(LONG);

  // Scene 5: Connectors → Workflows
  await page.click('a[href="/dashboard/connectors"]');
  await page.waitForTimeout(PAUSE);
  await page.click('a[href="/dashboard/workflows"]');
  await page.waitForTimeout(PAUSE);

  // Scene 6: Audit → back to landing
  await page.click('a[href="/dashboard/audit"]');
  await page.waitForTimeout(PAUSE);
  await page.goto(APP, { waitUntil: "networkidle" });
  await page.waitForTimeout(LONG);
});

// ═══════════════════════════════════════════════════════════
// VIDEO 2: CFO — Finance
// ═══════════════════════════════════════════════════════════
test("02 — CFO Finance", async ({ page }) => {
  test.setTimeout(180_000);

  // Login as CFO
  await login(page, "cfo@agenticorg.local", "cfo123!");
  await page.waitForTimeout(LONG);

  // Agents page — 6 finance agents
  await page.click('a[href="/dashboard/agents"]');
  await page.waitForTimeout(LONG);
  await smoothScroll(page, 300, 1500);
  await page.waitForTimeout(PAUSE);

  // Observatory — AP Processor traces
  await page.click('a[href="/dashboard/observatory"]');
  await page.waitForTimeout(OBSERVATORY_WAIT);

  // Approvals — 3 pending CFO items
  await page.click('a[href="/dashboard/approvals"]');
  await page.waitForTimeout(LONG);

  // Click into Recon Agent detail
  await page.click('a[href="/dashboard/agents"]');
  await page.waitForTimeout(PAUSE);
  const reconAgent = page.getByText("Reconciliation Agent").first();
  if (await reconAgent.isVisible({ timeout: 3000 }).catch(() => false)) {
    await reconAgent.click();
    await page.waitForTimeout(LONG);
  }
});

// ═══════════════════════════════════════════════════════════
// VIDEO 3: CHRO — HR
// ═══════════════════════════════════════════════════════════
test("03 — CHRO HR", async ({ page }) => {
  test.setTimeout(180_000);

  await login(page, "chro@agenticorg.local", "chro123!");
  await page.waitForTimeout(LONG);

  // Agents — 6 HR agents
  await page.click('a[href="/dashboard/agents"]');
  await page.waitForTimeout(LONG);
  await smoothScroll(page, 300, 1500);
  await page.waitForTimeout(PAUSE);

  // Observatory
  await page.click('a[href="/dashboard/observatory"]');
  await page.waitForTimeout(OBSERVATORY_WAIT);

  // Approvals — 2 pending
  await page.click('a[href="/dashboard/approvals"]');
  await page.waitForTimeout(LONG);

  // Audit log
  await page.click('a[href="/dashboard/audit"]');
  await page.waitForTimeout(LONG);
});

// ═══════════════════════════════════════════════════════════
// VIDEO 4: CMO — Marketing
// ═══════════════════════════════════════════════════════════
test("04 — CMO Marketing", async ({ page }) => {
  test.setTimeout(180_000);

  await login(page, "cmo@agenticorg.local", "cmo123!");
  await page.waitForTimeout(LONG);

  // Agents — 5 marketing agents
  await page.click('a[href="/dashboard/agents"]');
  await page.waitForTimeout(LONG);

  // Observatory
  await page.click('a[href="/dashboard/observatory"]');
  await page.waitForTimeout(OBSERVATORY_WAIT);

  // Approvals — 1 pending
  await page.click('a[href="/dashboard/approvals"]');
  await page.waitForTimeout(LONG);

  // Agent detail — Brand Monitor
  await page.click('a[href="/dashboard/agents"]');
  await page.waitForTimeout(PAUSE);
  const brandAgent = page.getByText("Brand Monitor").first();
  if (await brandAgent.isVisible({ timeout: 3000 }).catch(() => false)) {
    await brandAgent.click();
    await page.waitForTimeout(LONG);
  }
});

// ═══════════════════════════════════════════════════════════
// VIDEO 5: COO — Operations
// ═══════════════════════════════════════════════════════════
test("05 — COO Operations", async ({ page }) => {
  test.setTimeout(180_000);

  await login(page, "coo@agenticorg.local", "coo123!");
  await page.waitForTimeout(LONG);

  // Agents — 5 ops agents
  await page.click('a[href="/dashboard/agents"]');
  await page.waitForTimeout(LONG);

  // Observatory — incident response
  await page.click('a[href="/dashboard/observatory"]');
  await page.waitForTimeout(OBSERVATORY_WAIT);

  // Approvals — 1 pending
  await page.click('a[href="/dashboard/approvals"]');
  await page.waitForTimeout(LONG);

  // Audit log
  await page.click('a[href="/dashboard/audit"]');
  await page.waitForTimeout(LONG);

  // Connectors
  await page.click('a[href="/dashboard/connectors"]');
  await page.waitForTimeout(PAUSE);
});
