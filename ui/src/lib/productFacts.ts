/**
 * Canonical source for every externally visible product count or version.
 *
 * Every UI surface that cites a connector count, agent count, tool count,
 * or version number must read it through {@link useProductFacts} or the
 * build-time fallback in {@link PRODUCT_FACTS_FALLBACK}. Hardcoding a
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
 * Safe fallback used before the API responds. Values are deliberately
 * conservative ("50+"-style) so they cannot overpromise; the real numbers
 * land as soon as /api/v1/product-facts resolves.
 */
export const PRODUCT_FACTS_FALLBACK: ProductFacts = {
  version: "",
  connector_count: 0,
  agent_count: 0,
  tool_count: 0,
};

export function useProductFacts(): {
  facts: ProductFacts;
  loading: boolean;
  error: boolean;
} {
  const [facts, setFacts] = useState<ProductFacts>(PRODUCT_FACTS_FALLBACK);
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
        if (!cancelled) setError(true);
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
