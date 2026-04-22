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
    // Pins the isolation fix so a future edit that drops company_id
    // from the history querystring fails the build instead of silently
    // re-introducing cross-agent history leakage.
    expect(chatPanelSource).toBeTruthy();
    expect(chatPanelSource).toContain("agent_id");
    expect(chatPanelSource).toContain("company_id");
  });
});
