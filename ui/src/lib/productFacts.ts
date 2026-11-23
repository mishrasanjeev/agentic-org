/**
 * Canonical source for every externally visible product count or version.
 *
 * Every UI surface that cites a connector count, agent count, tool count,
 * or version number must read it through {@link useProductFacts} or the
 * build-time seed in {@link productFactsSeed}. Hardcoding a
 * number inside a page is a regression and will fail the product-claims
 * Playwright spec.
 */
import { useEffect, useState } from "react";
import api from "@/lib/api";

export interface ProductFacts {
  version: string;
  connector_count: number;
  agent_count: number;
  tool_count: number;
}

/**
 * Values used when no product facts are known. Zero counts render as
 * "Unavailable" so a page never shows a number the endpoint did not publish.
 */
export const PRODUCT_FACTS_FALLBACK: ProductFacts = {
  version: "",
  connector_count: 0,
  agent_count: 0,
  tool_count: 0,
};

/**
 * Build-time seed. `ui/scripts/prerender-public.mjs` reads
 * /api/v1/product-facts while it renders the public pages, sets
 * {@link PRODUCT_FACTS_SEED_GLOBAL} for the server render, and writes the same
 * JSON into each page as a non-executable `<script type="application/json">`
 * with id {@link PRODUCT_FACTS_SEED_ID}. Crawlers that do not run JavaScript
 * then read real counts, and the browser starts from the numbers already in
 * the HTML. The live endpoint still replaces the seed once it answers.
 */
export const PRODUCT_FACTS_SEED_ID = "agenticorg-product-facts";
export const PRODUCT_FACTS_SEED_GLOBAL = "__AGENTICORG_PRODUCT_FACTS__";

function isPositiveInteger(value: unknown): value is number {
  return Number.isInteger(value) && Number(value) > 0;
}

/** Returns the facts only when every field is populated; otherwise null. */
export function parseProductFacts(value: unknown): ProductFacts | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return null;
  const data = value as Record<string, unknown>;
  if (typeof data.version !== "string" || !/^\d+\.\d+\.\d+/.test(data.version)) return null;
  if (
    !isPositiveInteger(data.connector_count) ||
    !isPositiveInteger(data.agent_count) ||
    !isPositiveInteger(data.tool_count)
  ) {
    return null;
  }
  return {
    version: data.version,
    connector_count: data.connector_count,
    agent_count: data.agent_count,
    tool_count: data.tool_count,
  };
}

export function productFactsSeed(): ProductFacts | null {
  const fromServer = (globalThis as Record<string, unknown>)[PRODUCT_FACTS_SEED_GLOBAL];
  if (fromServer !== undefined) return parseProductFacts(fromServer);
  if (typeof document === "undefined") return null;
  const text = document.getElementById(PRODUCT_FACTS_SEED_ID)?.textContent;
  if (!text) return null;
  try {
    return parseProductFacts(JSON.parse(text));
  } catch {
    return null;
  }
}

export function useProductFacts(): {
  facts: ProductFacts;
  loading: boolean;
  error: boolean;
} {
  const [facts, setFacts] = useState<ProductFacts>(() => productFactsSeed() ?? PRODUCT_FACTS_FALLBACK);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api
      .get("/product-facts")
      .then(({ data }) => {
        if (cancelled) return;
        setFacts({
          version: String(data.version ?? ""),
          connector_count: Number(data.connector_count ?? 0),
          agent_count: Number(data.agent_count ?? 0),
          tool_count: Number(data.tool_count ?? 0),
        });
      })
      .catch(() => {
        if (cancelled) return;
        // The live endpoint is authoritative: drop the build seed when it fails.
        setFacts(PRODUCT_FACTS_FALLBACK);
        setError(true);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return { facts, loading, error };
}
