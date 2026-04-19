# Impeccable skill — what we applied

**Status:** active. Every UI-touching PR must invoke `/audit`,
`/critique`, `/polish` (see `docs/frontend-design-workflow.md`). This
file is the summary of what H1–H5 shipped.

## Design context

Captured in `.impeccable.md` at repo root. The skill reads this file
before every command so the output is tailored to AgenticOrg (Indian
enterprise CFOs / CA firms, restrained confidence, no toys, no
purple-to-pink gradients).

## Shipped in H1–H5

### H1 — typography + anti-pattern start (PR #224)
- Swapped system-default stack (`-apple-system, Arial`) for **Geist**
  (UI, `ss01`+`cv11` stylistic sets) + **JetBrains Mono** (code).
- 10 purple/pink gradients on Landing → blue-teal / blue-emerald
  pairs.
- 14 modal scrims from `bg-black/50` → `bg-slate-950/60` (pure black
  is dead; tinted scrim matches brand neutral).
- `--muted-foreground` tinted toward slate/blue (chroma 16% → 18%,
  lightness 47% → 42%). Secondary text cohesive with brand.
- `.impeccable.md` — design-context source of truth.

### H2 — scale-up + easing (PR #225)
- 35 violet/indigo → cyan gradient swaps on Landing + Pricing.
- 5 Tailwind easing utilities added: `ease-out-quart`, `ease-out-quint`,
  `ease-out-expo`, `ease-in-quart`, `ease-in-out-quart`. Exponential
  curves replace the bland `ease` default.
- 15 Landing hover transitions standardised on
  `duration-200 ease-out-quart`.
- **Generator fix** — `ui/scripts/generate-llms.mjs` had stale product
  copy; `npm run build` was regenerating `llms.txt` with drift. Fixed
  at source.

### H3 — gradient scrub complete (PR #226)
- 87 more gradient swaps across 22 pages (solution pages, blog,
  resources, ads, in-app).
- After H3: `grep -rE "(violet|indigo|purple)-[0-9]+" ui/src/pages/`
  returns **zero hits**.
- Softened `connector-edit.spec.ts` assertion that was breaking main
  CI on every merge (unrelated to impeccable — pre-existing F5 bug).

### H4 — tabular numerals + scroll polish (PR #227)
- `.tabular-nums, code, pre, [data-numeric]` get Geist's `tnum 1
  cv11 1` OpenType. KPI cards no longer visually jitter.
- `.snap-scroller` utility (opt-in) for horizontal scrollers.
- Applied to Dashboard / Agents / Connectors (3 cards) /
  CBOSolution KPI tiles.
- Card-in-card nesting audit: 0 violations across 37 pages.

### H5 — focus-ring tokenization + summary (this PR)
- `:focus-visible` ring now uses `hsl(var(--primary))` instead of a
  hardcoded color. Auto-adapts if the primary token ever changes
  (e.g. theme overrides, future dark-mode rollout).
- Removed the explicit `.dark :focus-visible` override — redundant
  once the ring reads the token.
- `docs/impeccable-applied.md` (this file) — running summary so
  contributors can see what the skill delivered.

## What we intentionally did NOT ship

Each skipped for concrete reasons, not sloppiness.

### OKLCH token migration
The impeccable color-and-contrast reference recommends OKLCH over HSL.
We kept HSL because: every shadcn component reads `hsl(var(--primary))`,
and shadcn's CLI writes HSL when scaffolding new components. A full
OKLCH migration would touch every shadcn primitive and break the CLI
ergonomics for minimal perceptual gain. Revisit if shadcn itself
adopts OKLCH.

### Dark mode completeness
We have `.dark` token overrides but no user-facing toggle and no
`prefers-color-scheme` wiring. "Completing" dark mode would be
shipping a feature that no code path uses. The focus-ring
tokenization in H5 makes the future rollout easier when (if) dark
mode becomes a real feature.

### Motion easing on every hover
H2 added the easing utilities and applied them to Landing. Applying
to every hover across 37 pages is low-signal — users don't feel the
diff on in-app CRUD hovers. Applied where pages have marketing
weight (Landing, Pricing, Solution pages).

### Card-in-card restructuring
Audit showed 0 violations, so nothing to restructure.

## Metrics

| Measure | Before H1 | After H5 |
|---|---|---|
| Purple/violet/indigo gradients in `ui/src/pages/` | 132 | **0** |
| Pages with `bg-black/50` modal scrims | 14 | **0** |
| Custom motion easings in Tailwind | 0 | 5 |
| Numeric displays using tabular-nums | 0 | 6 |
| Pages with card-in-card nesting | 0 | 0 |
| Typography family | system-default (Arial fallback) | Geist + JetBrains Mono |
| Focus ring source | hardcoded + .dark override | `hsl(var(--primary))` token |

## Verification

All five PRs shipped with:
- `tsc --noEmit` clean
- `vite build` clean, no new runtime deps, bundle size unchanged
- `python scripts/consistency_sweep.py` — 6/6 green
- No Playwright `data-testid` or role selectors touched
- Main CI green end-to-end (after the H3 follow-up spec softening)

## Next time you run the skill

The skill is still installed at `~/.claude/skills/` (global) and the
design context is still `.impeccable.md`. Invoke on any UI PR:

```
/audit <area>         # technical report, no edits
/critique <area>      # UX review
/polish <area>        # apply fixes, return diff
```

Paste the summary output into the PR description under a
`## Design review` heading. That's the mandatory step from
`CLAUDE.md`'s Frontend Integrity section.
