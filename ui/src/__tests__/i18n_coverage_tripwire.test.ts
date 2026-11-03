/**
 * i18n coverage tripwire — Codex 2026-04-22 review follow-up.
 *
 * The point of this test isn't to prove every page is fully translated;
 * that's a multi-PR initiative and pretending otherwise is exactly the
 * "papered-over" pattern Codex flagged. Instead, this test pins a
 * minimum contract:
 *
 *   Every high-traffic in-app page must import ``useTranslation`` so
 *   the useful surfaces are reachable by the language switcher and new
 *   strings added to those files use ``t()`` by default.
 *
 * Uses Vite's ``import.meta.glob`` so the test doesn't depend on Node
 * types (the repo's tsconfig excludes ``@types/node``).
 */
import { describe, it, expect } from "vitest";
import en from "../locales/en.json";
import hi from "../locales/hi.json";

// Load every page source as a raw string at test time. Vite resolves
// these into inline strings, so no fs/path imports are required.
const pageSources = import.meta.glob("../pages/*.tsx", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

const chatPanelSource = (
  import.meta.glob("../components/ChatPanel.tsx", {
    query: "?raw",
    import: "default",
    eager: true,
  }) as Record<string, string>
)["../components/ChatPanel.tsx"];

const layoutSource = (
  import.meta.glob("../components/Layout.tsx", {
    query: "?raw",
    import: "default",
    eager: true,
  }) as Record<string, string>
)["../components/Layout.tsx"];

// Insights components hold most of the Insights page's visible strings.
const insightsComponentSources = import.meta.glob("../components/insights/*.tsx", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

// The home, CxO, Partner and Observatory dashboards are retired (their features live in Insights).
const CRITICAL_PAGES = [
  "ABMDashboard.tsx",
  "ReportScheduler.tsx",
  "KnowledgeBase.tsx",
  "MarketingOperations.tsx",
  "InsightsDashboard.tsx",
  "InsightsEntity.tsx",
  "PlatformInsights.tsx",
];

function findSource(filename: string): string {
  const key = Object.keys(pageSources).find((k) => k.endsWith(`/${filename}`));
  if (!key) throw new Error(`page source not found: ${filename}`);
  return pageSources[key];
}

function getNested(obj: unknown, keyPath: string): unknown {
  return keyPath.split(".").reduce<unknown>((acc, key) => {
    if (!acc || typeof acc !== "object") return undefined;
    return (acc as Record<string, unknown>)[key];
  }, obj);
}

describe("i18n coverage tripwire (Codex 2026-04-22 gap)", () => {
  for (const page of CRITICAL_PAGES) {
    it(`${page} imports useTranslation`, () => {
      const source = findSource(page);
      const hasImport =
        /from\s+["']react-i18next["']/.test(source) &&
        /useTranslation/.test(source);
      expect(hasImport).toBe(true);
    });
  }

  it("ChatPanel uses agent_id + company_id on history fetch", () => {
    expect(chatPanelSource).toBeTruthy();
    expect(chatPanelSource).toContain("agent_id");
    expect(chatPanelSource).toContain("company_id");
  });




  it("Layout nav and role translation keys exist in English and Hindi", () => {
    expect(layoutSource).toBeTruthy();
    const keys = new Set([
      ...Array.from(
        layoutSource.matchAll(/labelKey:\s*["']([^"']+)["']/g),
        (match) => match[1],
      ),
      ...Array.from(
        layoutSource.matchAll(/(?:titleKey|domainKey):\s*["']([^"']+)["']/g),
        (match) => match[1],
      ),
    ]);

    expect(keys.size).toBeGreaterThan(0);
    for (const key of keys) {
      expect(getNested(en, key), key).toBeDefined();
      expect(getNested(hi, key), key).toBeDefined();
    }
  });

  it("static page/component t() keys exist in English and Hindi", () => {
    const sources = {
      ...pageSources,
      ...insightsComponentSources,
      "../components/Layout.tsx": layoutSource,
      "../components/ChatPanel.tsx": chatPanelSource,
    };
    const keys = new Set<string>();
    for (const source of Object.values(sources)) {
      for (const match of source.matchAll(/\bt\(\s*["']([A-Za-z0-9_.-]+)["']/g)) {
        keys.add(match[1]);
      }
    }

    expect(keys.size).toBeGreaterThan(0);
    for (const key of keys) {
      expect(getNested(en, key), key).toBeDefined();
      expect(getNested(hi, key), key).toBeDefined();
    }
  });

});
