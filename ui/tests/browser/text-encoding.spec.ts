// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
import { expect, test } from "@playwright/test";

// Owned by text-encoding.config.ts, outside the hosted regression suite.
// Replay the rendered application with deterministic local responses, never a live account.
for (const width of [390, 1440]) {
  for (const colorScheme of ["light", "dark"] as const) {
    for (const language of ["en", "hi"]) {
      for (const state of ["empty", "populated", "unavailable"] as const) {
        test(`approval text: ${state}, ${language}, ${width}px, ${colorScheme}`, async ({ page, baseURL }, info) => {
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
            if (request.method() !== "GET") return route.fulfill({ status: 405, json: { detail: "Read-only fixture" } });
            const respond = (json: unknown, status = 200) => route.fulfill({ status, json });
            if (url.pathname.endsWith("/auth/me")) return respond({
              user_id: "11111111-1111-4111-8111-111111111111", role: "admin", domain: "all",
              email: "admin@example.test", name: "Local Admin", onboarding_complete: true,
              tenant_id: "22222222-2222-4222-8222-222222222222",
            });
            if (url.pathname.endsWith("/approvals")) {
              if (state === "unavailable") return respond({ detail: "unavailable" }, 503);
              const status = url.searchParams.get("status");
              const total = state === "empty" ? 0 : status === "approved" ? 3 : status === "rejected" ? 1 : 4;
              return respond({ items: [], total, page: 1, per_page: 20 });
            }
            if (url.pathname.endsWith("/product-facts")) return respond({ agent_count: 1, connector_count: 1, tool_count: 1, version: "test" });
            return respond([]);
          });
          await page.goto("/dashboard");
          await page.evaluate(scheme => document.documentElement.classList.toggle("dark", scheme === "dark"), colorScheme);
          const heading = page.getByRole("heading", { name: language === "hi" ? "अनुमोदन हल हुए" : "Approvals Resolved", exact: true });
          await expect(heading).toBeVisible();
          const card = heading.locator("../..");
          await expect(card.getByText(state === "populated" ? "50%" : "\u2014", { exact: true })).toBeVisible();
          await expect(page).toHaveTitle(/ \u2014 AgenticOrg$/);
          await expect(page.locator('input[placeholder^="Ask anything"]')).toHaveAttribute("placeholder", "Ask anything... (\u2318K)");
          expect(await page.locator("body").innerText()).not.toMatch(/\u00e2\u20ac|\u00c3[\u0080-\u00bf]|\ufffd/);
          expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
          for (const metric of await card.locator("p").all()) {
            expect(await metric.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
          }
          expect(errors).toEqual([]);
          await page.screenshot({ path: info.outputPath("dashboard-text.png"), fullPage: true });
        });
      }
    }
  }
}
