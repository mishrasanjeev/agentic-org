// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
// Every Insights namespace (the dashboard, explorers, run evidence, value, downloads, BI access and the
// platform view) carries the same keys in English and Hindi, and the Hindi strings are translated; every
// explorer the UI links to exists in the server registry.
import { describe, expect, it } from "vitest";
import explorerKeys from "@/lib/insights/explorerKeys.generated.json";
import en from "../locales/en.json";
import hi from "../locales/hi.json";

type Tree = Record<string, unknown>;

function leaves(obj: unknown, prefix = ""): Map<string, unknown> {
  const out = new Map<string, unknown>();
  if (!obj || typeof obj !== "object") {
    out.set(prefix, obj);
    return out;
  }
  for (const [k, v] of Object.entries(obj as Tree)) {
    for (const [key, value] of leaves(v, prefix ? `${prefix}.${k}` : k)) out.set(key, value);
  }
  return out;
}

const NAMESPACES = Object.keys(en as Tree).filter((ns) => /^(insights|platformInsights)/.test(ns));

// Strings that are the same in both languages on purpose: file formats and short codes.
const SAME_IN_BOTH = new Set([
  "insights.export.csv",
  "insights.export.json",
  "insights.export.xlsx",
  "platformInsights.gate.short",
]);

describe("Insights locales", () => {
  it("covers every Insights namespace", () => {
    expect(NAMESPACES).toEqual(
      expect.arrayContaining([
        "insights",
        "insightsExplore",
        "insightsRun",
        "insightsValue",
        "insightsDownloads",
        "insightsBi",
        "platformInsights",
      ]),
    );
  });

  for (const ns of NAMESPACES) {
    it(`${ns}: English and Hindi carry the same keys`, () => {
      const enKeys = [...leaves((en as Tree)[ns]).keys()].sort();
      const hiKeys = [...leaves((hi as Tree)[ns]).keys()].sort();
      expect(hiKeys).toEqual(enKeys);
    });

    it(`${ns}: Hindi strings are translated, not copied English`, () => {
      const hiLeaves = leaves((hi as Tree)[ns]);
      const copied: string[] = [];
      for (const [key, value] of leaves((en as Tree)[ns])) {
        const full = `${ns}.${key}`;
        if (SAME_IN_BOTH.has(full) || typeof value !== "string" || !/[A-Za-z]{3}/.test(value)) continue;
        if (hiLeaves.get(key) === value) copied.push(full);
      }
      expect(copied).toEqual([]);
    });
  }
});

// Every explorer the UI links to is one the server registry declares (scripts/generate_insights_metric_keys.py).
const SOURCES = import.meta.glob(["../pages/**/*.tsx", "../components/**/*.tsx", "../lib/**/*.ts"], {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

describe("Insights explorer links", () => {
  it("every explorer linked from the UI exists in the registry", () => {
    const known = new Set(explorerKeys as string[]);
    const linked = new Set<string>();
    for (const [file, src] of Object.entries(SOURCES)) {
      for (const m of src.matchAll(/\/dashboard\/insights\/explore\/([a-z_]+)/g)) {
        linked.add(m[1]);
        expect(known.has(m[1]), `${file}: ${m[1]}`).toBe(true);
      }
    }
    expect(linked.size).toBeGreaterThan(3);
  });
});
