import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  PRODUCT_FACTS_FALLBACK,
  PRODUCT_FACTS_SEED_GLOBAL,
  PRODUCT_FACTS_SEED_ID,
  parseProductFacts,
  productFactsSeed,
  useProductFacts,
} from "@/lib/productFacts";

const apiMock = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("@/lib/api", () => ({ default: apiMock }));

const SEED = { version: "9.8.7", connector_count: 11, agent_count: 22, tool_count: 33 };
const LIVE = { version: "9.8.8", connector_count: 12, agent_count: 23, tool_count: 34 };

function addSeedElement(text: string) {
  const element = document.createElement("script");
  element.type = "application/json";
  element.id = PRODUCT_FACTS_SEED_ID;
  element.textContent = text;
  document.head.appendChild(element);
}

describe("product facts build seed", () => {
  beforeEach(() => apiMock.get.mockReset());
  afterEach(() => {
    document.getElementById(PRODUCT_FACTS_SEED_ID)?.remove();
    delete (globalThis as Record<string, unknown>)[PRODUCT_FACTS_SEED_GLOBAL];
  });

  it("accepts only fully populated facts", () => {
    expect(parseProductFacts(SEED)).toEqual(SEED);
    expect(parseProductFacts({ ...SEED, version: "" })).toBeNull();
    expect(parseProductFacts({ ...SEED, agent_count: 0 })).toBeNull();
    expect(parseProductFacts({ ...SEED, tool_count: "33" })).toBeNull();
    expect(parseProductFacts([SEED])).toBeNull();
  });

  it("reads the server-render global before the page element", () => {
    addSeedElement(JSON.stringify(LIVE));
    (globalThis as Record<string, unknown>)[PRODUCT_FACTS_SEED_GLOBAL] = SEED;
    expect(productFactsSeed()).toEqual(SEED);
  });

  it("reads the JSON block written into the prerendered page", () => {
    addSeedElement(JSON.stringify(SEED));
    expect(productFactsSeed()).toEqual(SEED);
  });

  it("ignores a malformed page block", () => {
    addSeedElement("{not json");
    expect(productFactsSeed()).toBeNull();
  });

  it("starts from the seed and switches to the live endpoint", async () => {
    addSeedElement(JSON.stringify(SEED));
    let resolve: (value: { data: typeof LIVE }) => void = () => {};
    apiMock.get.mockReturnValueOnce(new Promise((done) => { resolve = done; }));
    const { result } = renderHook(() => useProductFacts());
    expect(result.current.facts).toEqual(SEED);
    resolve({ data: LIVE });
    await waitFor(() => expect(result.current.facts).toEqual(LIVE));
    expect(apiMock.get).toHaveBeenCalledWith("/product-facts");
  });

  it("drops the seed when the live endpoint fails", async () => {
    addSeedElement(JSON.stringify(SEED));
    apiMock.get.mockRejectedValueOnce(new Error("offline"));
    const { result } = renderHook(() => useProductFacts());
    expect(result.current.facts).toEqual(SEED);
    await waitFor(() => expect(result.current.error).toBe(true));
    expect(result.current.facts).toEqual(PRODUCT_FACTS_FALLBACK);
  });

  it("starts from the fallback when the page has no seed", () => {
    apiMock.get.mockReturnValueOnce(new Promise(() => {}));
    const { result } = renderHook(() => useProductFacts());
    expect(result.current.facts).toEqual(PRODUCT_FACTS_FALLBACK);
  });
});
