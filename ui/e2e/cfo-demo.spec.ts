/**
 * AgenticOrg — CFO Demo Recording
 *
 * Records a cinematic walkthrough of the live app for CFO presentation.
 * Run:  npx playwright test video/cfo-demo.spec.ts --headed
 * Output: video/recordings/
 */
import { test, expect } from "@playwright/test";

const APP = "https://app.agenticorg.ai";

// Slow, deliberate pace so viewers can follow
const PAUSE = 2500; // pause on each screen
const SHORT = 1500;
const LONG = 4000;

test.use({
  viewport: { width: 1920, height: 1080 },
  video: { mode: "on", size: { width: 1920, height: 1080 } },
  launchOptions: { slowMo: 300 },
  colorScheme: "light",
});

test("CFO Demo — AgenticOrg Finance Agents in Action", async ({ page }) => {
  test.setTimeout(180_000); // 3 min max

  // ─── Scene 1: Landing Page ─────────────────────────────
  await page.goto(APP, { waitUntil: "networkidle" });
  await page.waitForTimeout(LONG);

  // Scroll through the landing page slowly
  await smoothScroll(page, 600, 1500);
  await page.waitForTimeout(PAUSE);
  await smoothScroll(page, 600, 1500);
  await page.waitForTimeout(PAUSE);
  await smoothScroll(page, 800, 1500);
  await page.waitForTimeout(SHORT);

  // Scroll back to top
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: "smooth" }));
  await page.waitForTimeout(SHORT);

  // ─── Scene 2: Navigate to Dashboard — Agent Fleet ──────
  await page.goto(`${APP}/dashboard/agents`, { waitUntil: "networkidle" });
  await page.waitForTimeout(LONG);

  // Hover over agent cards to show interactivity
  const agentCards = page.locator('[class*="card"], [class*="Card"]').first();
  if (await agentCards.isVisible()) {
    await agentCards.hover();
    await page.waitForTimeout(SHORT);
  }

  // Click on the first finance agent if visible
  const financeAgent = page.getByText(/ap.processor|recon|tax.compliance|ar.collection|close.agent|fpa/i).first();
  if (await financeAgent.isVisible({ timeout: 3000 }).catch(() => false)) {
    await financeAgent.click();
    await page.waitForTimeout(LONG);

    // Show agent detail — metrics, config, shadow mode
    const tabs = page.locator('[role="tab"], button:has-text("Config"), button:has-text("Shadow"), button:has-text("Cost")');
    for (let i = 0; i < await tabs.count() && i < 4; i++) {
      const tab = tabs.nth(i);
      if (await tab.isVisible()) {
        await tab.click();
        await page.waitForTimeout(PAUSE);
      }
    }
  }

  // ─── Scene 3: Approval Queue (HITL) ───────────────────
  await page.goto(`${APP}/dashboard/approvals`, { waitUntil: "networkidle" });
  await page.waitForTimeout(LONG);

  // Show pending approvals
  const pendingTab = page.getByText(/pending/i).first();
  if (await pendingTab.isVisible({ timeout: 2000 }).catch(() => false)) {
    await pendingTab.click();
    await page.waitForTimeout(PAUSE);
  }

  // Show decided tab
  const decidedTab = page.getByText(/decided/i).first();
  if (await decidedTab.isVisible({ timeout: 2000 }).catch(() => false)) {
    await decidedTab.click();
    await page.waitForTimeout(PAUSE);
  }

  // Try clicking an approval card to show detail
  const approvalCard = page.locator('[class*="card"], [class*="Card"]').first();
  if (await approvalCard.isVisible({ timeout: 2000 }).catch(() => false)) {
    await approvalCard.hover();
    await page.waitForTimeout(SHORT);
  }

  // ─── Scene 4: Workflows ────────────────────────────────
  await page.goto(`${APP}/dashboard/workflows`, { waitUntil: "networkidle" });
  await page.waitForTimeout(LONG);

  // Show workflow list
  await smoothScroll(page, 400, 1000);
  await page.waitForTimeout(PAUSE);

  // Click on a workflow if available
  const viewBtn = page.getByText(/view|details/i).first();
  if (await viewBtn.isVisible({ timeout: 2000 }).catch(() => false)) {
    await viewBtn.click();
    await page.waitForTimeout(LONG);
  }

  // ─── Scene 5: Connectors ──────────────────────────────
  await page.goto(`${APP}/dashboard/connectors`, { waitUntil: "networkidle" });
  await page.waitForTimeout(LONG);

  // Filter to finance connectors
  const categoryFilter = page.locator("select, [role='combobox']").first();
  if (await categoryFilter.isVisible({ timeout: 2000 }).catch(() => false)) {
    await categoryFilter.click();
    await page.waitForTimeout(SHORT);
    const financeOpt = page.getByText(/finance/i).first();
    if (await financeOpt.isVisible({ timeout: 1000 }).catch(() => false)) {
      await financeOpt.click();
      await page.waitForTimeout(PAUSE);
    }
  }

  // Scroll through connectors
  await smoothScroll(page, 500, 1200);
  await page.waitForTimeout(PAUSE);

  // ─── Scene 6: Schema Registry ─────────────────────────
  await page.goto(`${APP}/dashboard/schemas`, { waitUntil: "networkidle" });
  await page.waitForTimeout(PAUSE);
  await smoothScroll(page, 400, 1000);
  await page.waitForTimeout(SHORT);

  // ─── Scene 7: Audit Log ───────────────────────────────
  await page.goto(`${APP}/dashboard/audit`, { waitUntil: "networkidle" });
  await page.waitForTimeout(LONG);

  // Scroll through audit entries
  await smoothScroll(page, 500, 1200);
  await page.waitForTimeout(PAUSE);

  // ─── Scene 8: Create Agent Flow ───────────────────────
  await page.goto(`${APP}/dashboard/agents/new`, { waitUntil: "networkidle" });
  await page.waitForTimeout(PAUSE);

  // Fill in agent creation form if fields exist
  const nameInput = page.locator('input[name="name"], input[placeholder*="name" i]').first();
  if (await nameInput.isVisible({ timeout: 2000 }).catch(() => false)) {
    await nameInput.click();
    await nameInput.fill("invoice-processor-v2");
    await page.waitForTimeout(SHORT);
  }

  const domainSelect = page.locator('select[name="domain"], [name="domain"]').first();
  if (await domainSelect.isVisible({ timeout: 1000 }).catch(() => false)) {
    await domainSelect.selectOption({ label: "Finance" }).catch(() => {});
    await page.waitForTimeout(SHORT);
  }

  await page.waitForTimeout(PAUSE);

  // ─── Scene 9: Create Workflow Flow ────────────────────
  await page.goto(`${APP}/dashboard/workflows/new`, { waitUntil: "networkidle" });
  await page.waitForTimeout(PAUSE);

  const wfName = page.locator('input[name="name"], input[placeholder*="name" i]').first();
  if (await wfName.isVisible({ timeout: 2000 }).catch(() => false)) {
    await wfName.click();
    await wfName.fill("invoice-processing-pipeline");
    await page.waitForTimeout(SHORT);
  }

  await page.waitForTimeout(PAUSE);

  // ─── Scene 10: Back to Landing — final shot ───────────
  await page.goto(APP, { waitUntil: "networkidle" });
  await page.waitForTimeout(LONG);

  // Final pause on hero
  await page.waitForTimeout(PAUSE);
});

/** Smooth scroll helper */
async function smoothScroll(page: any, distance: number, duration: number) {
  await page.evaluate(
    ([d, t]: [number, number]) => {
      return new Promise<void>((resolve) => {
        const start = window.scrollY;
        const startTime = Date.now();
        function step() {
          const elapsed = Date.now() - startTime;
          const progress = Math.min(elapsed / t, 1);
          const ease = progress < 0.5
            ? 2 * progress * progress
            : -1 + (4 - 2 * progress) * progress;
          window.scrollTo(0, start + d * ease);
          if (progress < 1) requestAnimationFrame(step);
          else resolve();
        }
        step();
      });
    },
    [distance, duration]
  );
}
