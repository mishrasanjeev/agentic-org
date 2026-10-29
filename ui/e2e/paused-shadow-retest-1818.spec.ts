// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
import { expect, test } from "@playwright/test";

// Deterministic UI contract replay only; the real HTTP lifecycle is covered
// by tests/regression/test_paused_shadow_retest_1818.py. No provider calls.
for (const width of [1440, 390]) {
  for (const colorScheme of ["light", "dark"] as const) {
    for (const sampleCount of [16, 0]) {
      test(`paused shadow recovery: ${sampleCount} samples, ${width}px, ${colorScheme}`, async ({ page, baseURL }, info) => {
        test.skip(
          !["localhost", "127.0.0.1"].includes(new URL(baseURL!).hostname),
          "Fixture-backed recovery replay requires a local UI",
        );
        await page.setViewportSize({ width, height: 1000 });
        await page.emulateMedia({ colorScheme });
        const agent = {
          id: "11111111-1111-4111-8111-111111111818",
          name: "Shadow Recovery Agent", employee_name: "Shadow Recovery Agent",
          agent_type: "bookkeeper", domain: "finance", status: "shadow",
          visibility: "tenant", owner_user_id: null, version: "1.0.0",
          shadow_sample_count: sampleCount, shadow_min_samples: 20,
          shadow_accuracy_current: (sampleCount ? 0.483 : 0) as number | null,
          shadow_accuracy_floor: 0.8, confidence_floor: 0.88,
          authorized_tools: [], connector_ids: [], max_retries: 0,
          llm_model: "gemini-3.5-flash-lite", hitl_condition: "confidence < 0.88",
          cost_controls: {}, config: {},
        };
        const mutations: string[] = [];
        const errors: string[] = [];
        page.on("pageerror", (error) => errors.push(error.message));
        await page.route("**/*", async (route) => {
          const url = new URL(route.request().url());
          if (url.origin !== new URL(baseURL!).origin) return route.abort();
          if (!url.pathname.startsWith("/api/")) return route.continue();
          const respond = (json: unknown, status = 200) => route.fulfill({ status, json });
          if (url.pathname.endsWith("/auth/me")) return respond({
            user_id: "11111111-1111-4111-8111-111111111111", role: "admin",
            email: "admin@example.test", name: "Local Admin", domain: "all",
            tenant_id: "22222222-2222-4222-8222-222222222222", onboarding_complete: true,
          });
          if (route.request().method() === "GET") {
            if (url.pathname === `/api/v1/agents/${agent.id}`) return respond(agent);
            return respond([]);
          }
          const action = url.pathname.split("/").at(-1)!;
          mutations.push(action);
          if (action === "pause") agent.status = "paused";
          else if (action === "retest") {
            agent.shadow_sample_count = 0;
            agent.shadow_accuracy_current = null;
          } else if (action === "resume") {
            if (agent.shadow_sample_count > 0 && agent.shadow_accuracy_current! < 0.8) {
              return respond({ detail: "Shadow accuracy 0.483 is below floor 0.800; use /retest to re-evaluate" }, 409);
            }
            agent.status = "shadow";
          } else if (action === "run") {
            expect(agent.status).toBe("shadow");
            agent.shadow_sample_count += 1;
            agent.shadow_accuracy_current = 0.9;
            return respond({ shadow_metrics: { sample_counted: true, accuracy_updated: true } });
          } else if (action === "promote") {
            return respond({ detail: "Shadow agent has 1/20 scored samples; cannot promote until minimum is met" }, 409);
          } else return respond({ detail: "Unexpected mutation in isolated UI test" }, 405);
          return respond({ id: agent.id, status: agent.status, retest: action === "retest" });
        });
        await page.goto(`/dashboard/agents/${agent.id}`);
        await page.evaluate((scheme) => {
          document.documentElement.classList.toggle("dark", scheme === "dark");
        }, colorScheme);
        await page.getByRole("button", { name: "Kill Switch", exact: true }).click();
        await page.getByRole("button", { name: "Confirm", exact: true }).click();
        await expect(page.getByRole("button", { name: "Resume", exact: true })).toBeVisible();
        if (sampleCount > 0) {
          await page.getByRole("button", { name: "Resume", exact: true }).click();
          await expect(page.getByText(/Shadow accuracy 0.483 is below floor/)).toBeVisible();
        }
        await page.getByRole("button", { name: "shadow", exact: true }).click();
        const retest = page.getByRole("button", { name: "Retest", exact: true });
        await expect(retest).toBeVisible();
        const contrast = await retest.evaluate((element) => {
          const luminance = (color: string) => {
            const [r, g, b] = color.match(/[\d.]+/g)!.slice(0, 3).map((value) => {
              const channel = Number(value) / 255;
              return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
            });
            return 0.2126 * r + 0.7152 * g + 0.0722 * b;
          };
          const style = getComputedStyle(element);
          const fg = luminance(style.color), bg = luminance(style.backgroundColor);
          return (Math.max(fg, bg) + 0.05) / (Math.min(fg, bg) + 0.05);
        });
        expect(contrast).toBeGreaterThanOrEqual(4.5);
        const toolbar = retest.locator("..");
        for (const control of await toolbar.locator("input, button").all()) {
          const box = await control.boundingBox();
          expect(box!.height).toBeGreaterThanOrEqual(44);
          expect(await control.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
          expect(await control.evaluate((element) => element.scrollHeight <= element.clientHeight)).toBe(true);
        }
        expect(await page.evaluate(() => document.documentElement.scrollWidth === document.documentElement.clientWidth)).toBe(true);
        await page.screenshot({ path: info.outputPath("paused-retest.png"), fullPage: true });
        await retest.click();
        await expect(retest).toHaveCount(0);
        await expect(page.getByRole("button", { name: "Resume", exact: true })).toBeVisible();
        expect(agent.status).toBe("paused");
        await page.getByRole("button", { name: "Resume", exact: true }).click();
        await expect(page.getByRole("button", { name: "Promote", exact: true })).toBeVisible();
        await page.getByRole("button", { name: "Generate Test Sample", exact: true }).click();
        await expect(page.getByText(/Generated 1 shadow sample/)).toBeVisible();
        await page.getByRole("button", { name: "Promote", exact: true }).click();
        await expect(page.getByText(/cannot promote until minimum is met/)).toBeVisible();
        expect(agent.status).toBe("shadow");
        expect(mutations).toEqual(sampleCount ? ["pause", "resume", "retest", "resume", "run", "promote"] :
          ["pause", "retest", "resume", "run", "promote"]);
        expect(errors).toEqual([]);
      });
    }
  }
}
