#!/usr/bin/env node
// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
import { readFileSync, writeFileSync } from "node:fs";
import { Writable } from "node:stream";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { HelmetProvider } from "react-helmet-async";
import { renderToPipeableStream, renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { createServer } from "vite";
import { JSDOM } from "jsdom";
import { loadRouteDescriptors, outputPathsForRoute, UI_ROOT } from "./generate-static-seo.mjs";

function renderPage(App, AuthProvider, BrandingProvider, path) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let stream;
    stream = renderToPipeableStream(
      React.createElement(HelmetProvider, null,
        React.createElement(QueryClientProvider, { client: new QueryClient() },
          React.createElement(MemoryRouter, { initialEntries: [path] },
            React.createElement(BrandingProvider, null,
              React.createElement(AuthProvider, null, React.createElement(App)))))),
      {
        onAllReady() {
          stream.pipe(new Writable({
            write(chunk, _encoding, callback) { chunks.push(chunk); callback(); },
            final(callback) { callback(); resolve(Buffer.concat(chunks).toString("utf8")); },
          }));
        },
        onError(error) { reject(error); },
      },
    );
  });
}

export function visibleMarkup(rendered) {
  const document = new JSDOM("<!doctype html><html><body>" + rendered + "</body></html>").window.document;
  document.body.querySelectorAll("title, meta, link, script[type='application/ld+json']")
    .forEach((element) => element.remove());
  if (document.body.querySelector("script")) {
    throw new Error("Unexpected executable script in server-rendered body");
  }
  return document.body.innerHTML;
}

export const DEFAULT_PRODUCT_FACTS_URL = "https://app.agenticorg.ai/api/v1/product-facts";

// Reads the live product facts for the build. Returns null (and the pages keep
// their "Unavailable" copy) when the endpoint is off, unreachable, or returns
// anything but fully populated facts, so an offline build still succeeds.
// AGENTICORG_PRODUCT_FACTS_URL points the build at another environment;
// "off" skips the request.
export async function fetchProductFacts(parse, {
  url = process.env.AGENTICORG_PRODUCT_FACTS_URL || DEFAULT_PRODUCT_FACTS_URL,
  fetchImpl = globalThis.fetch,
  timeoutMs = 10_000,
  warn = console.warn,
} = {}) {
  if (url === "off") return null;
  try {
    const response = await fetchImpl(url, {
      headers: { accept: "application/json" },
      signal: AbortSignal.timeout(timeoutMs),
    });
    if (!response.ok) throw new Error("HTTP " + response.status);
    const facts = parse(await response.json());
    if (!facts) throw new Error("response did not carry populated product facts");
    return facts;
  } catch (error) {
    warn("Product facts not seeded from " + url + ": " + (error?.message || error));
    return null;
  }
}

// A non-executable JSON data block, so the page's CSP script hashes are unaffected.
export function productFactsSeedTag(id, facts) {
  return '<script type="application/json" id="' + id + '">' +
    JSON.stringify(facts).replace(/</g, "\\u003c") + "</script>";
}

const ALWAYS_FORBIDDEN = [
  ["the pricing loading banner", /Loading the complete billing catalog/],
  ["the pricing failure banner", /complete billing catalog is unavailable/],
];
const FORBIDDEN_WHEN_FACTS_SEEDED = [
  ["a pending version", /Version pending/],
  ["an unavailable product count", />\s*Unavailable\s*</],
];

// Prerendered HTML is what crawlers read; it must never be a loading state.
export function assertNoPlaceholders(markup, path, factsSeeded) {
  const rules = factsSeeded ? [...ALWAYS_FORBIDDEN, ...FORBIDDEN_WHEN_FACTS_SEEDED] : ALWAYS_FORBIDDEN;
  for (const [label, pattern] of rules) {
    if (pattern.test(markup)) {
      throw new Error("Prerendered " + path + " contains " + label);
    }
  }
}

export function evalsMarkup(route) {
  return renderToStaticMarkup(React.createElement("main", { className: "min-h-screen mx-auto max-w-5xl px-6 py-12" },
    React.createElement("h1", { className: "text-3xl font-bold" }, route.name),
    React.createElement("p", { className: "mt-4" }, route.summary),
    React.createElement("section", { className: "mt-8" },
      React.createElement("h2", { className: "text-xl font-semibold" }, "How to interpret this scorecard"),
      React.createElement("p", { className: "mt-3" },
        "Evaluation dimensions include quality, safety, performance, reliability, security, and cost. " +
        "Scores and timestamps load from the live evaluation source; missing measurements are not passing results. " +
        "The scorecard is not a production service guarantee or a substitute for tenant-specific review.")),
    React.createElement("a", { className: "mt-6 inline-block underline", href: "/docs" }, "Read the documentation")));
}

export async function prerenderPublic(root = UI_ROOT) {
  const { routes } = loadRouteDescriptors(root);
  const server = await createServer({
    root,
    server: { middlewareMode: true },
    appType: "custom",
    logLevel: "error",
  });
  let count = 0;
  let seedGlobal = null;
  try {
    const { default: App } = await server.ssrLoadModule("/src/App.tsx");
    const { AuthProvider } = await server.ssrLoadModule("/src/contexts/AuthContext.tsx");
    const { BrandingProvider } = await server.ssrLoadModule("/src/contexts/BrandingContext.tsx");
    const productFactsModule = await server.ssrLoadModule("/src/lib/productFacts.ts");
    const facts = await fetchProductFacts(productFactsModule.parseProductFacts);
    if (facts) {
      seedGlobal = productFactsModule.PRODUCT_FACTS_SEED_GLOBAL;
      globalThis[seedGlobal] = facts;
    }
    const seedTag = facts ? productFactsSeedTag(productFactsModule.PRODUCT_FACTS_SEED_ID, facts) : "";
    for (const route of routes.filter((item) => item.index !== false)) {
      const markup = route.path === "/evals"
        ? evalsMarkup(route)
        : visibleMarkup(await renderPage(App, AuthProvider, BrandingProvider, route.path));
      if ((markup.match(/<h1\b/gi) || []).length !== 1) {
        throw new Error("Public route did not render exactly one heading: " + route.path);
      }
      assertNoPlaceholders(markup, route.path, Boolean(facts));
      for (const path of outputPathsForRoute(root, route, routes)) {
        const html = readFileSync(path, "utf8");
        let next = html.replace(/<noscript>[\s\S]*?<\/noscript>\s*/, "")
          .replace('<div id="root"></div>', '<div id="root">' + markup + "</div>");
        if (seedTag) next = next.replace("</head>", "  " + seedTag + "\n</head>");
        if (next === html) throw new Error("Prerender insertion failed: " + path);
        writeFileSync(path, next, "utf8");
      }
      count += 1;
    }
  } finally {
    if (seedGlobal) delete globalThis[seedGlobal];
    await server.close();
  }
  return count;
}

if (process.argv[1]?.replaceAll("\\", "/").endsWith("/prerender-public.mjs")) {
  console.log("Prerendered " + await prerenderPublic() + " public routes.");
}
