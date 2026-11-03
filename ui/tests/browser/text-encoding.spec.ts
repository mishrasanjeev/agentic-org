// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
import { expect, test } from "@playwright/test";

// Owned by text-encoding.config.ts, outside the hosted regression suite.
// Replay the rendered application with deterministic local responses, never a live account.
// The landing page is Insights (the home dashboard is retired; /dashboard redirects there): its heading, a decision
// tile, the empty and unavailable states, in English and Hindi, at phone and desktop widths, light and dark.
const SETTINGS = { timezone: "Asia/Kolkata", fiscal_year_start_month: 4, currency: "INR" };

function metric(key: string, title: string, extra: Record<string, unknown>) {
  return {
    key,
    title,
    definition: `${title} definition`,
    unit: "count",
    source: "run_facts",
    data_quality: "measured",
    computed_at: "2026-11-01T10:00:00Z",
    period: { from: "2026-10-03", to: "2026-11-01", granularity: "day" },
    value: null,
    ...extra,
  };
}

function executive(state: "empty" | "populated") {
  const success =
    state === "populated"
      ? metric("runs.success_rate", "Run success rate", { unit: "percent", value: 50, compare_value: 40, state: "act" })
      : metric("runs.success_rate", "Run success rate", {
          unit: "percent",
          data_quality: "no_data",
          hint: "No agent runs are recorded for this period.",
        });
  return { view: "executive", period: { from: "2026-10-03", to: "2026-11-01" }, tiles: [success], charts: [], needs_you: [] };
}

for (const width of [390, 1440]) {
  for (const colorScheme of ["light", "dark"] as const) {
    for (const language of ["en", "hi"]) {
      for (const state of ["empty", "populated", "unavailable"] as const) {
        test(`insights text: ${state}, ${language}, ${width}px, ${colorScheme}`, async ({ page, baseURL }, info) => {
          if (!baseURL || !["localhost", "127.0.0.1"].includes(new URL(baseURL).hostname)) {
            throw new Error("Text encoding fixture replay requires a local UI URL");
          }
          const origin = new URL(baseURL).origin;
          const errors: string[] = [];
          page.on("pageerror", error => errors.push(error.message));
          await page.setViewportSize({ width, height: 1000 });
          await page.emulateMedia({ colorScheme });
          await page.addInitScript(lang => localStorage.setItem("agenticorg_lang", lang), language);
          await page.route("**/*", async route => {
            const request = route.request();
            const url = new URL(request.url());
            if (url.origin !== origin) return route.abort();
            if (!url.pathname.startsWith("/api/")) return route.continue();
            if (request.method() !== "GET") return route.fulfill({ status: 200, json: {} });
            const respond = (json: unknown, status = 200) => route.fulfill({ status, json });
            if (url.pathname.endsWith("/auth/me")) return respond({
              user_id: "11111111-1111-4111-8111-111111111111", role: "admin", domain: "all",
              email: "admin@example.test", name: "Local Admin", onboarding_complete: true,
              tenant_id: "22222222-2222-4222-8222-222222222222",
            });
            if (url.pathname.endsWith("/insights/status")) {
              return respond({ enabled: true, views: ["executive"], platform_operator: false, settings: SETTINGS, late_data: false });
            }
            if (url.pathname.endsWith("/insights/views/executive")) {
              if (state === "unavailable") return respond({ detail: "unavailable" }, 503);
              return respond(executive(state));
            }
            if (url.pathname.endsWith("/insights/explorers")) return respond({ explorers: [] });
            if (url.pathname.endsWith("/insights/changes")) {
              return respond({ since: "2026-11-01T00:00:00Z", first_visit: false, items: [] });
            }
            if (url.pathname.endsWith("/insights/saved-views")) return respond({ saved_views: [] });
            if (url.pathname.endsWith("/product-facts")) return respond({ agent_count: 1, connector_count: 1, tool_count: 1, version: "test" });
            return respond([]);
          });
          await page.goto("/dashboard");
          await expect(page).toHaveURL(/\/dashboard\/insights$/);
          await page.evaluate(scheme => document.documentElement.classList.toggle("dark", scheme === "dark"), colorScheme);
          const heading = page.getByRole("heading", { name: language === "hi" ? "इनसाइट्स" : "Insights", exact: true, level: 1 });
          await expect(heading).toBeVisible();
          if (state === "populated") {
            await expect(page.getByText("50.0%", { exact: true })).toBeVisible();
          } else if (state === "empty") {
            await expect(page.getByText("No agent runs are recorded for this period.")).toBeVisible();
          } else {
            // A view that could not be read says so and offers to try again.
            const retry = language === "hi" ? "फिर से प्रयास करें" : "Try again";
            await expect(page.getByRole("button", { name: retry, exact: true })).toBeVisible();
          }
          await expect(page).toHaveTitle(/ — AgenticOrg$/);
          await expect(page.locator('input[placeholder^="Ask anything"]')).toHaveAttribute("placeholder", "Ask anything... (⌘K)");
          expect(await page.locator("body").innerText()).not.toMatch(/â€|Ã[\u0080-¿]|�/);
          expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
          for (const value of await page.locator('[data-testid="insights-tiles"] p').all()) {
            expect(await value.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
          }
          expect(errors).toEqual([]);
          await page.screenshot({ path: info.outputPath("insights-text.png"), fullPage: true });
        });
      }
    }
  }
}
