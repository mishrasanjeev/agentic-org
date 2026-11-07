// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
import { expect, type Locator, type Page, type TestInfo } from "@playwright/test";

type Theme = "light" | "dark";

export async function initializeInsightsTheme(page: Page, theme: Theme) {
  // Set the theme before the styles render, including on a company-switch reload.
  await page.addInitScript((dark) => {
    const apply = () => document.documentElement.classList.toggle("dark", dark);
    if (document.documentElement) {
      apply();
    } else {
      const observer = new MutationObserver(() => {
        if (!document.documentElement) return;
        apply();
        observer.disconnect();
      });
      observer.observe(document, { childList: true });
    }
  }, theme === "dark");
}

export async function verifyInsightsSurface(page: Page, root: Locator, width: number, info: TestInfo, name: string, theme: Theme, minText = 20) {
  expect(await page.evaluate(() => document.documentElement.classList.contains("dark"))).toBe(theme === "dark");
  const geometry = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    document: document.documentElement.scrollWidth,
    overflow: Array.from(document.querySelectorAll("main *")).map((el) => ({
      tag: el.tagName, testId: el.getAttribute("data-testid"), className: el.className,
      width: el.getBoundingClientRect().width, right: el.getBoundingClientRect().right,
      scrollWidth: el.scrollWidth, clientWidth: el.clientWidth, overflowX: getComputedStyle(el).overflowX,
    })).filter((el) => el.right > document.documentElement.clientWidth || el.width > document.documentElement.clientWidth),
  }));
  await info.attach(`${name}-geometry`, { body: JSON.stringify(geometry, null, 2), contentType: "application/json" });
  expect(geometry.document).toBe(geometry.viewport);
  const controls = await root.evaluate((element) => [element, ...element.querySelectorAll("button, select, input")]
    .filter((el) => el.matches("button, select, input") && el.getBoundingClientRect().height > 0 && getComputedStyle(el).visibility !== "hidden")
    .map((el) => ({ height: el.getBoundingClientRect().height, fits: el.scrollWidth <= el.clientWidth })));
  for (const control of controls) {
    expect(control.fits).toBe(true);
    if (width === 390) expect(control.height).toBeGreaterThanOrEqual(44);
  }
  const contrasts = await root.evaluate((element) => {
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = 1;
    const ctx = canvas.getContext("2d")!;
    const luminance = (pixels: Uint8ClampedArray) => {
      const c = Array.from(pixels).slice(0, 3).map((value) => {
        const n = value / 255;
        return n <= 0.04045 ? n / 12.92 : ((n + 0.055) / 1.055) ** 2.4;
      });
      return c[0] * 0.2126 + c[1] * 0.7152 + c[2] * 0.0722;
    };
    const selector = "h1,h2,h3,p,th,td,dt,dd,code,button,label,select,a,span";
    return [element, ...element.querySelectorAll(selector)]
      .filter((el) => el.matches(selector) && el.getBoundingClientRect().height > 1 && el.textContent?.trim()
        && !(el instanceof HTMLButtonElement && el.disabled))
      .map((el) => {
        ctx.fillStyle = "white";
        ctx.fillRect(0, 0, 1, 1);
        const ancestors: Element[] = [];
        for (let parent: Element | null = el; parent; parent = parent.parentElement) ancestors.unshift(parent);
        for (const parent of ancestors) {
          ctx.fillStyle = getComputedStyle(parent).backgroundColor;
          ctx.fillRect(0, 0, 1, 1);
        }
        const bg = luminance(ctx.getImageData(0, 0, 1, 1).data);
        ctx.fillStyle = getComputedStyle(el).color;
        ctx.fillRect(0, 0, 1, 1);
        const fg = luminance(ctx.getImageData(0, 0, 1, 1).data);
        return { text: el.textContent!.trim().slice(0, 80), contrast: (Math.max(fg, bg) + 0.05) / (Math.min(fg, bg) + 0.05) };
      });
  });
  await info.attach(`${name}-text-contrast`, { body: JSON.stringify(contrasts, null, 2), contentType: "application/json" });
  expect(contrasts.length).toBeGreaterThan(minText);
  expect(contrasts.filter((item) => item.contrast < 4.5)).toEqual([]);
  await expect(root).not.toContainText(/NaN|undefined|Infinity|\[object Object\]/);
  await page.mouse.move(0, 0);
  await page.screenshot({ path: info.outputPath(`${name}.png`), fullPage: true });
  const scroll = await root.evaluate((element) => {
    const main = element.closest("main");
    if (!main || main.scrollHeight <= main.clientHeight) return null;
    const previous = main.scrollTop;
    main.scrollTop = main.scrollHeight;
    return { previous, current: main.scrollTop };
  });
  if (scroll) {
    expect(scroll.current).toBeGreaterThan(0);
    await page.screenshot({ path: info.outputPath(`${name}-lower.png`) });
    await root.evaluate((element, previous) => { element.closest("main")!.scrollTop = previous; }, scroll.previous);
  }
}
