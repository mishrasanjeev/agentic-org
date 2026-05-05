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

const CRITICAL_PAGES = [
  "Dashboard.tsx",
  "PartnerDashboard.tsx",
  "ABMDashboard.tsx",
  "ReportScheduler.tsx",
  "KnowledgeBase.tsx",
  "CBODashboard.tsx",
  "CEODashboard.tsx",
  "CFODashboard.tsx",
  "CHRODashboard.tsx",
  "CMODashboard.tsx",
  "COODashboard.tsx",
];

function findSource(filename: string): string {
  const key = Object.keys(pageSources).find((k) => k.endsWith(`/${filename}`));
  if (!key) throw new Error(`page source not found: ${filename}`);
  return pageSources[key];
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

  // Codex 2026-04-23 reopen: the previous test only checked the
  // ``useTranslation`` import; it did not prove visible strings were
  // wrapped. This stricter assertion counts ``t("...")`` call sites on
  // Dashboard.tsx and requires at least 10 — enough that incremental
  // regressions ("someone dropped the import back to unused") get
  // caught. Each subsequent page-translation PR should raise the
  // floor as it lands.
  it("Dashboard.tsx has substantive t() coverage (>=10 call sites)", () => {
    const source = findSource("Dashboard.tsx");
    const matches = source.match(/\bt\(\s*["']/g) ?? [];
    expect(matches.length).toBeGreaterThanOrEqual(10);
  });

  it("PartnerDashboard.tsx has translated dashboard metric labels", () => {
    const source = findSource("PartnerDashboard.tsx");
    const matches = source.match(/\bt\(\s*["']partnerDashboard\./g) ?? [];
    expect(matches.length).toBeGreaterThanOrEqual(20);
    expect(source).toContain("partnerDashboard.overdueFilings");
    expect(source).not.toContain(">Overdue<");
    expect(source).not.toContain("{overdueFilings} clients");
  });

  it("PartnerDashboard locale keys exist in English and Hindi", () => {
    const source = findSource("PartnerDashboard.tsx");
    const keys = Array.from(
      source.matchAll(/\bt\(\s*["']partnerDashboard\.([^"']+)/g),
      (match) => match[1],
    );

    for (const key of keys) {
      expect(en.partnerDashboard).toHaveProperty(key);
      expect(hi.partnerDashboard).toHaveProperty(key);
    }
  });

  it("Dashboard.tsx no longer documents English-fallback policy", () => {
    // Codex flagged the old comment "falls back to the existing
    // English where no key is defined yet" as the product telling on
    // itself. The fallback policy is gone; the comment is too.
    const source = findSource("Dashboard.tsx");
    expect(source).not.toContain(
      "falls back to the existing English",
    );
  });
});
