// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
/**
 * Colour references for Insights charts and chips. Every colour is a CSS
 * token from globals.css (light and .dark variants), never a literal.
 */
import type { MetricState } from "./api";

export const CHART_TOKEN_COUNT = 8;

/** Categorical series colour i (0-based), cycling through --chart-1..8. */
export function chartColor(index: number): string {
  const i = ((Math.trunc(index) % CHART_TOKEN_COUNT) + CHART_TOKEN_COUNT) % CHART_TOKEN_COUNT;
  return `hsl(var(--chart-${i + 1}))`;
}

/** Map API state words (metric ok/watch/act and budget ok/warn/breached) to a state token. */
export function normaliseState(state: string | null | undefined): MetricState | null {
  switch (state) {
    case "ok":
      return "ok";
    case "watch":
    case "warn":
    case "warning":
      return "watch";
    case "act":
    case "breached":
    case "critical":
      return "act";
    default:
      return null;
  }
}

export function stateColor(state: MetricState): string {
  return `hsl(var(--state-${state}))`;
}

/** Tailwind classes for a state chip: tinted background, state-coloured text. */
export const STATE_CHIP_CLASSES: Record<MetricState, string> = {
  ok: "bg-[hsl(var(--state-ok)/0.12)] text-[hsl(var(--state-ok))]",
  watch: "bg-[hsl(var(--state-watch)/0.14)] text-[hsl(var(--state-watch))]",
  act: "bg-[hsl(var(--state-act)/0.12)] text-[hsl(var(--state-act))]",
};
