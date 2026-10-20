// SPDX-License-Identifier: Apache-2.0
import { expect, test } from "@playwright/test";

test("CERT-In workflows and sandbox boundaries are available without JavaScript", async ({ browser, baseURL, request }) => {
  const path = "/blog/cert-in-ai-agent-security";
  const response = await request.get(path);
  expect(response.status()).toBe(200);
  expect(await response.text()).toContain("cert-in-accountable-handoffs.png");
  const context = await browser.newContext({ javaScriptEnabled: false });
  try {
    const page = await context.newPage();
    await page.goto(`${baseURL}${path}`);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("AgenticOrg and CERT-In: make the handoffs accountable");
    await expect(page.getByRole("figure")).toHaveCount(3);
    await expect(page.getByText("The merchant example is illustrative and sandbox-only.", { exact: false })).toBeVisible();
    await expect(page.getByText("Live verification and activation stay disabled.", { exact: false })).toBeVisible();
    await expect(page.getByRole("link", { name: "CERT-In CIAD-2026-0049: AI Agents as Attack Vectors" })).toHaveAttribute("href", /VLCODE=CIAD-2026-0049$/);
  } finally {
    await context.close();
  }
});

for (const width of [320, 768, 1440]) {
  test(`CERT-In workflows fit and the contents work by keyboard at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/blog/cert-in-ai-agent-security");
    const figures = page.getByRole("figure");
    await expect(figures).toHaveCount(3);
    for (const figure of await figures.all()) {
      await expect(figure.getByRole("listitem")).toHaveCount(4);
    }
    await expect(page.getByText("Illustrative containment design / deployment validation required", { exact: true })).toBeVisible();
    for (const link of await page.locator("footer a").all()) {
      const bounds = await link.boundingBox();
      expect(bounds?.height).toBeGreaterThanOrEqual(44);
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    const contents = page.locator("summary");
    await contents.focus();
    await page.keyboard.press("Enter");
    await expect(contents.locator("..")).toHaveAttribute("open", "");
    const link = page.getByRole("link", { name: "Workflow 1: prepare the exception, not the verdict", exact: true });
    await link.focus();
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/#article-section-/);
    await expect(page.getByRole("heading", { name: "Workflow 1: prepare the exception, not the verdict", exact: true })).toBeInViewport();
  });
}

test("OACP explainer diagrams, comparison and switch status are available without JavaScript", async ({ browser, baseURL, request }) => {
  const path = "/blog/oacp-one-trust-layer-under-ucp-acp-ap2";
  const response = await request.get(path);
  expect(response.status()).toBe(200);
  expect(await response.text()).toContain("oacp-one-trust-layer-under-ucp-acp-ap2.png");
  const context = await browser.newContext({ javaScriptEnabled: false });
  try {
    const page = await context.newPage();
    await page.goto(`${baseURL}${path}`);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("OACP explained: one trust layer under UCP, ACP and AP2");
    await expect(page.getByRole("figure")).toHaveCount(5);
    await expect(page.getByRole("table")).toHaveCount(1);
    await expect(page.getByText("ship behind switches that default to off", { exact: false }).first()).toBeVisible();
    await expect(page.getByText("OACP does not replace UCP, ACP or AP2. It feeds them.", { exact: true })).toBeVisible();
  } finally {
    await context.close();
  }
});

for (const scheme of ["light", "dark"] as const) {
  for (const width of [320, 390, 1440]) {
    test(`OACP explainer fits at ${width}px in ${scheme} mode`, async ({ browser, baseURL }) => {
      const context = await browser.newContext({ viewport: { width, height: 900 }, colorScheme: scheme });
      try {
        const page = await context.newPage();
        await page.goto(`${baseURL}/blog/oacp-one-trust-layer-under-ucp-acp-ap2`);
        await expect(page.getByRole("figure")).toHaveCount(5);
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
        for (const svg of await page.locator("figure svg").all()) {
          const bounds = await svg.boundingBox();
          expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(width);
        }
        const background = await page.locator(".figure-article").evaluate((el) => getComputedStyle(el).backgroundColor);
        expect(background === "rgb(255, 255, 255)").toBe(scheme === "light");
      } finally {
        await context.close();
      }
    });
  }
}

const OACP_GUIDES = [
  "oacp-overview", "oacp-how-it-works", "oacp-buyer-journey", "oacp-merchant-setup",
  "oacp-safety-and-trust", "oacp-feature-switches", "oacp-faq",
];

test("OACP guides keep their figures and summaries without JavaScript", async ({ browser, baseURL }) => {
  const context = await browser.newContext({ javaScriptEnabled: false });
  try {
    const page = await context.newPage();
    await page.goto(`${baseURL}/docs/oacp-how-it-works`);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("How OACP works, end to end");
    await expect(page.locator(".docs-summary").first()).toContainText("In 30 seconds");
    await expect(page.locator("main .docs-figure")).toHaveCount(2);
    await expect(page.locator("main .docs-figure svg[role='img']")).toHaveCount(2);
  } finally {
    await context.close();
  }
});

for (const scheme of ["light", "dark"] as const) {
  for (const width of [320, 390, 1440]) {
    test(`OACP guides fit at ${width}px in ${scheme} mode`, async ({ browser, baseURL }) => {
      const context = await browser.newContext({ viewport: { width, height: 900 }, colorScheme: scheme });
      try {
        const page = await context.newPage();
        for (const slug of OACP_GUIDES) {
          await page.goto(`${baseURL}/docs/${slug}`);
          await expect(page.locator("main .docs-figure svg").first()).toBeVisible();
          expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), slug).toBe(true);
          for (const svg of await page.locator("main .docs-figure svg").all()) {
            const bounds = await svg.boundingBox();
            expect(bounds!.x + bounds!.width, slug).toBeLessThanOrEqual(width);
          }
          const background = await page.locator(".docs-site").evaluate((el) => getComputedStyle(el).backgroundColor);
          expect(background === "rgb(255, 255, 255)", slug).toBe(scheme === "light");
        }
      } finally {
        await context.close();
      }
    });
  }
}

test("public content is available in HTML without JavaScript", async ({ browser, baseURL }) => {
  const context = await browser.newContext({ javaScriptEnabled: false });
  try {
    const page = await context.newPage();
    await page.goto(`${baseURL}/blog/shopify-merchant-seller-commerce-agent-oacp`);
    await expect(page.getByRole("heading", { level: 1 })).toHaveCount(1);
    await expect(page.getByRole("heading", { name: "Gated Reference Journey" })).toBeVisible();
    await expect(page.getByText("Shopify remains the operational source of record", { exact: false })).toBeVisible();
  } finally {
    await context.close();
  }
});

test("evaluation page has source-aware context before live scores load", async ({ browser, baseURL, request }) => {
  const response = await request.get("/evals");
  expect(response.status()).toBe(200);
  expect(await response.text()).toContain("missing measurements are not passing results");

  const context = await browser.newContext({ javaScriptEnabled: false });
  try {
    const page = await context.newPage();
    await page.goto(`${baseURL}/evals`);
    await expect(page.getByRole("heading", { level: 1, name: "Agent Evaluations" })).toHaveCount(1);
    await expect(page.getByRole("heading", { name: "How to interpret this scorecard" })).toBeVisible();
  } finally {
    await context.close();
  }
});

test("public links resolve to real routes and unknown paths are 404", async ({ page, request }) => {
  await page.goto("/resources/agent-activity-audit-and-live-feed");
  await page.getByRole("link", { name: "Explore the platform" }).click();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveCount(1);

  await page.goto("/docs/api-sdk-mcp");
  await page.getByRole("link", { name: "external A2A buyer access" }).click();
  await expect(page).toHaveURL(/\/docs\/seller-a2a-commerce-journey$/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveCount(1);

  expect((await request.get("/platform")).status()).toBe(404);
  expect((await request.get("/a2a-interoperability.md")).status()).toBe(404);
});

test("founder byline has a visible, factual author bio", async ({ page, request }) => {
  const path = "/blog/ai-agents-for-ca-firms-gst-tds-automation";
  const response = await request.get(path);
  expect(response.status()).toBe(200);
  expect(await response.text()).toContain("Founder &amp; CEO of Orchestrum Technologies LLP");

  await page.goto(path);
  await expect(page.getByRole("heading", { name: "About the author" })).toBeVisible();
  await expect(page.getByRole("link", { name: "X", exact: true })).toHaveAttribute("href", "https://x.com/mishrak_sanjeev");
  await expect(page.getByRole("link", { name: "LinkedIn" })).toHaveAttribute("href", "https://www.linkedin.com/in/sanjeev-kumar-a184174");
});
