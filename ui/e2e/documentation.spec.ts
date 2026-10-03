// SPDX-License-Identifier: Apache-2.0
import { test, expect, type Locator } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import manual from "../src/content/userDocs.generated.json" with { type: "json" };

async function expectLoadedImage(image: Locator) {
  await expect(image).toBeVisible();
  await expect
    .poll(
      () => image.evaluate(
        (element: HTMLImageElement) => element.complete && element.naturalWidth > 0,
      ),
      { timeout: 10_000, message: "Guide image must finish loading successfully" },
    )
    .toBe(true);
}

test("public text discovery files use one MIME type and include every guide", async ({ request }) => {
  for (const path of ["/health", "/robots.txt", "/llms.txt", "/llms-full.txt"]) {
    const response = await request.get(path);
    expect(response.status()).toBe(200);
    expect(response.headersArray().filter((header) =>
      header.name.toLowerCase() === "content-type",
    )).toHaveLength(1);
    expect(response.headers()["content-type"]).toMatch(/^text\/plain(?:; charset=utf-8)?$/i);
  }
  const index = await request.get("/llms.txt");
  expect(await index.text()).toContain("https://agenticorg.ai/docs");
  const full = await request.get("/llms-full.txt");
  const content = await full.text();
  for (const article of manual.articles) {
    expect(content).toContain(article.title);
  }
});

test("public guide routes preserve security headers and deliberate cache policy", async ({ request }) => {
  for (const path of ["/", "/docs", "/docs/first-agent", "/docs/not-a-real-guide"]) {
    const response = await request.get(path);
    expect(response.status()).toBe(path.endsWith("not-a-real-guide") ? 404 : 200);
    const headers = response.headers();
    expect(headers["content-security-policy"]).toContain("frame-ancestors 'none'");
    expect(headers["content-security-policy"]).not.toMatch(/script-src[^;]*'unsafe-(inline|eval)'/);
    expect(headers["x-frame-options"]).toBe("DENY");
    expect(headers["x-content-type-options"]).toBe("nosniff");
    expect(headers["referrer-policy"]).toBe("strict-origin-when-cross-origin");
    if (response.ok()) expect(headers["cache-control"]).toContain("no-store");
  }
});

test("all guide routes, source images, canonical metadata and narrow layouts work", async ({
  page,
}, testInfo) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  for (const article of manual.articles) {
    const response = await page.goto(`/docs/${article.slug}`);
    expect(response?.status()).toBe(200);
    await expect(page.locator("h1")).toHaveText(article.title);
    await expect(page.locator('link[rel="canonical"]')).toHaveAttribute(
      "href",
      `https://agenticorg.ai/docs/${article.slug}`,
    );
    await expect
      .poll(async () => {
        const raw = await page
          .locator('script[type="application/ld+json"]')
          .textContent();
        const schema = JSON.parse(raw ?? "{}");
        return schema["@graph"]?.some(
          (node: { "@type": string; url?: string }) =>
            node["@type"] === "TechArticle" &&
            node.url === `https://agenticorg.ai/docs/${article.slug}`,
        );
      })
      .toBe(true);
    await expect(page.locator(".docs-prose h2").first()).toBeVisible();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth + 1,
      ),
    ).toBe(true);
    for (const image of await page.locator(".docs-prose img").all()) {
      await expectLoadedImage(image);
    }
  }
  await page.goto("/docs/bfsi-business-onboarding");
  await expect(page.locator("h1")).toHaveText(/business/i);
  await page.screenshot({
    path: testInfo.outputPath("bfsi-guide.png"),
    fullPage: true,
  });
  expect(errors).toEqual([]);
});

test("guide screenshots remain verifiable over a delayed download", async ({ page }) => {
  let downloadRequested = false;
  await page.route("**/screenshots/agents.webp", async (route) => {
    downloadRequested = true;
    const response = await route.fetch();
    await new Promise((resolve) => setTimeout(resolve, 1_500));
    await route.fulfill({ response });
  });
  await page.goto("/docs/create-agents", { waitUntil: "domcontentloaded" });
  await expect(page.locator("h1")).toHaveText("Create and manage agents");
  await expectLoadedImage(page.locator(".docs-prose img"));
  expect(downloadRequested).toBe(true);
});

test("full-text search, empty state, guide navigation and mobile menu work", async ({
  page,
}, testInfo) => {
  await page.goto("/docs");
  await expect(page.locator("h1")).toHaveText("AgenticOrg documentation");
  await page.screenshot({
    path: testInfo.outputPath("manual-home.png"),
    fullPage: true,
  });
  await page.getByRole("searchbox").fill("LibreOffice");
  await expect(
    page.getByRole("region", { name: "Search results" }),
  ).toContainText("Knowledge");
  await page
    .getByRole("region", { name: "Search results" })
    .getByRole("link")
    .filter({ hasText: "Knowledge" })
    .first()
    .click();
  await expect(page).toHaveURL(/\/docs\/knowledge-and-ocr$/);
  await expect(page.getByRole("searchbox")).toHaveValue("");
  await page.getByRole("searchbox").fill("unfindable-abc");
  await expect(page.getByText(/No matching guide/)).toBeVisible();
  await page.getByRole("button", { name: "Clear search" }).click();
  if (testInfo.project.name === "mobile") {
    await page.getByRole("button", { name: "Open guide navigation" }).click();
    await page
      .getByRole("navigation", { name: "Documentation guides" })
      .getByRole("link", { name: manual.articles[1].title, exact: true })
      .click();
    await expect(
      page.getByRole("button", { name: "Open guide navigation" }),
    ).toHaveAttribute("aria-expanded", "false");
  } else {
    await page
      .getByRole("navigation", { name: "Documentation guides" })
      .getByRole("link", { name: manual.articles[1].title, exact: true })
      .click();
  }
  await expect(page).toHaveURL(/\/docs\/first-agent$/);
  const contents =
    testInfo.project.name === "mobile"
      ? page.locator(".docs-mobile-toc")
      : page.locator(".docs-toc");
  if (testInfo.project.name === "mobile")
    await contents.locator("summary").click();
  await contents
    .getByRole("link", { name: "Step 2: upload and verify knowledge" })
    .click();
  await expect(page).toHaveURL(/#step-2-upload-and-verify-knowledge$/);
  await expect(
    page.locator("#step-2-upload-and-verify-knowledge"),
  ).toBeInViewport();
  await page
    .getByRole("navigation", { name: "Previous and next guide" })
    .getByRole("link")
    .last()
    .click();
  await expect(page).toHaveURL(/\/docs\/workspace-and-roles$/);
});

test("guides are readable without JavaScript and invalid slugs return 404", async ({
  browser,
  baseURL,
  request,
}) => {
  const context = await browser.newContext({
    javaScriptEnabled: false,
    baseURL,
  });
  const page = await context.newPage();
  await page.goto("/docs/first-agent");
  await expect(page.locator("h1")).toHaveText(manual.articles[1].title);
  await expect(
    page.getByRole("heading", { name: "Step 2: upload and verify knowledge" }),
  ).toBeVisible();
  await page.goto("/docs/bfsi-business-onboarding");
  await expect(page.locator("main")).toContainText("Example Bank");
  await context.close();
  const missing = await request.get("/docs/not-a-real-guide");
  expect(missing.status()).toBe(404);
});

test("BFSI process maps and overview anchor render without JavaScript", async ({
  browser,
  baseURL,
}) => {
  const context = await browser.newContext({ javaScriptEnabled: false, baseURL });
  const page = await context.newPage();
  for (const article of manual.articles.filter((item) => item.group === "BFSI Playbooks")) {
    await page.goto(`/docs/${article.slug}`);
    const map = page.getByRole("list", { name: "Process map" });
    await expect(map).toBeVisible();
    await expect(map.getByText("Owner:", { exact: true }).first()).toBeVisible();
    await expect(map.getByText("Human decision", { exact: true }).first()).toBeVisible();
    await expect(map.getByText("Blocked / exception", { exact: true }).first()).toBeVisible();
  }
  await page.goto("/docs");
  await page.getByRole("link", { name: "Jump to BFSI process maps" }).click();
  await expect(page).toHaveURL(/\/docs#bfsi-playbooks$/);
  await expect(page.locator("#bfsi-playbooks")).toBeVisible();
  await context.close();
});

test("local documentation-host alias keeps redirects proxy-safe", async ({ request, baseURL }) => {
  const hostname = new URL(baseURL ?? "https://agenticorg.ai").hostname;
  test.skip(
    !["127.0.0.1", "localhost", "[::1]"].includes(hostname),
    "Host-header routing is tested against local nginx; hosted routing has its own HTTPS test.",
  );
  const forwardedHeaders: Record<string, string>[] = [
    {},
    { "X-Forwarded-Proto": "https" },
    { "X-Forwarded-Proto": "http", "X-Forwarded-Host": "untrusted.example:8080" },
  ];
  for (const forwarded of forwardedHeaders) {
    const alias = await request.get("/", {
      headers: { Host: "docs.agenticorg.ai", ...forwarded },
      maxRedirects: 0,
    });
    expect(alias.status()).toBe(302);
    expect(alias.headers().location).toBe("/docs");
  }
  for (const host of ["agenticorg.ai", "app.agenticorg.ai"]) {
    const landing = await request.get("/", {
      headers: { Host: host },
      maxRedirects: 0,
    });
    expect(landing.status()).toBe(200);
    expect(landing.headers().location).toBeUndefined();
  }
});

test("configured documentation hostname works from its HTTPS root", async ({ page, request }) => {
  const target = process.env.DOCS_HOST_BASE_URL;
  if (!target) {
    test.skip(true, "Set DOCS_HOST_BASE_URL to verify an explicitly reviewed hosted alias.");
    return;
  }
  const origin = new URL(target).origin;
  expect(origin).toBe("https://docs.agenticorg.ai");
  const root = await request.get(`${origin}/`, { maxRedirects: 0 });
  expect(root.status()).toBe(302);
  expect(root.headers().location).toBe("/docs");
  const response = await page.goto(`${origin}/`);
  expect(response?.status()).toBe(200);
  await expect(page).toHaveURL(`${origin}/docs`);
  await expect(page.locator("h1")).toHaveText("AgenticOrg documentation");
  await page.getByRole("searchbox").fill("LibreOffice");
  await page.getByRole("region", { name: "Search results" })
    .getByRole("link").filter({ hasText: "Knowledge" }).first().click();
  await expect(page).toHaveURL(`${origin}/docs/knowledge-and-ocr`);
  await expect(page.locator('link[rel="canonical"]')).toHaveAttribute(
    "href", "https://agenticorg.ai/docs/knowledge-and-ocr",
  );
  expect((await request.get(`${origin}/docs/not-a-real-guide`)).status()).toBe(404);
});

test("landing documentation links and accessible reader work", async ({
  page,
}, testInfo) => {
  await page.route("**/api/**", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "{}" }),
  );
  await page.goto("/");
  const section = page.locator("#documentation");
  await section.scrollIntoViewIfNeeded();
  await expect(section.getByRole("heading", { level: 2 })).toBeVisible();
  await section.screenshot({ path: testInfo.outputPath("landing-docs.png") });
  await section.getByRole("link", { name: /complete.*manual/i }).click();
  await expect(page).toHaveURL(/\/docs$/);
  await page.goto("/docs/bfsi-business-onboarding");
  await expect(page.locator(".docs-prose")).toBeVisible();
  const results = await new AxeBuilder({ page })
    .include(".docs-site")
    .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
    .analyze();
  expect(
    results.violations.map((violation) => ({
      id: violation.id,
      nodes: violation.nodes.map((node) => node.target),
    })),
  ).toEqual([]);
  await page.emulateMedia({ media: "print" });
  await expect(page.locator(".docs-sidebar")).not.toBeVisible();
  await expect(page.locator("h1")).toBeVisible();
});

test("BFSI overview jump, commerce map link and mobile process labels work", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.route("**/api/**", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "{}" }),
  );
  await page.goto("/");
  await page.locator("#documentation").getByRole("link", { name: /BFSI process maps/ }).click();
  await expect(page).toHaveURL(/\/docs#bfsi-playbooks$/);
  await expect(page.locator("#bfsi-playbooks")).toBeInViewport();
  const header = await page.locator(".docs-header").boundingBox();
  const destination = await page.locator("#bfsi-playbooks > h2").boundingBox();
  expect(destination!.y).toBeGreaterThanOrEqual(header!.y + header!.height);
  await page.locator("#bfsi-playbooks")
    .getByRole("link", { name: /merchant enablement and agentic commerce/i }).click();
  const map = page.getByRole("list", { name: "Process map" });
  await expect(map).toBeVisible();
  await expect(map.getByText("Owner:", { exact: true })).toHaveCount(6);
  await expect(map.getByText("Human decision", { exact: true }).first()).toBeVisible();
  await expect(map.getByText("Blocked / exception", { exact: true }).first()).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
  const accessible = await new AxeBuilder({ page })
    .include(".docs-site")
    .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
    .analyze();
  expect(accessible.violations.map((violation) => violation.id)).toEqual([]);
  await page.goto("/docs/commerce");
  await page.getByRole("link", { name: "merchant-services process map" }).click();
  await expect(page).toHaveURL(/\/docs\/bfsi-merchant-services#process-map$/);
  await expect(page.locator("#process-map")).toBeInViewport();
});

test("overview and table guides are accessible at compact and tablet widths", async ({
  page,
}) => {
  for (const width of [320, 768, 1100]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/docs");
    await expect(page.locator("h1")).toHaveText("AgenticOrg documentation");
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth + 1,
      ),
    ).toBe(true);
    const home = await new AxeBuilder({ page })
      .include(".docs-site")
      .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
      .analyze();
    expect(home.violations.map((violation) => violation.id)).toEqual([]);
    await page.goto("/docs/knowledge-and-ocr");
    await expect(page.locator(".docs-prose table")).toBeVisible();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth + 1,
      ),
    ).toBe(true);
    const guide = await new AxeBuilder({ page })
      .include(".docs-site")
      .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
      .analyze();
    expect(guide.violations.map((violation) => violation.id)).toEqual([]);
  }
});
