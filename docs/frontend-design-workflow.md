# Frontend design workflow — impeccable skill pack

**Rule:** every UI-touching PR runs three design commands before push.
Playwright owns correctness; these three own aesthetic quality.

## One-time setup

The skill pack is installed globally at `~/.claude/skills/`. Verify:

```bash
ls ~/.claude/skills/impeccable/SKILL.md  # must exist
ls ~/.claude/skills/audit/SKILL.md
ls ~/.claude/skills/critique/SKILL.md
ls ~/.claude/skills/polish/SKILL.md
```

If missing, re-install from `https://github.com/pbakaus/impeccable`:

```bash
git clone --depth 1 https://github.com/pbakaus/impeccable /tmp/imp
cp -r /tmp/imp/source/skills/* ~/.claude/skills/
```

First use ever — once per design-system context — run
`/impeccable teach` so the skill captures your typography, color, and
spacing baseline. Subsequent commands compare against that baseline.

## Required commands per UI PR

| Command | Purpose | When it runs |
|---|---|---|
| `/audit <area>` | technical quality report (a11y, responsive, perf) — **no edits** | before making changes |
| `/critique <area>` | UX review (hierarchy, clarity, emotional resonance) | before push |
| `/polish <area>` | final alignment pass + shipping readiness | last step before push |

`<area>` is a short descriptor: `/audit dashboard`, `/critique governance settings`, `/polish connectors edit`.

Example combined run:

```
/audit settings
/critique settings
/polish settings
```

Paste the resulting report summary into the PR description under a
`## Design review` heading. Reviewers should scan it before approval.

## Optional commands

The pack includes 15 more (`/distill`, `/clarify`, `/optimize`,
`/harden`, `/animate`, `/colorize`, `/bolder`, `/quieter`, `/delight`,
`/adapt`, `/typeset`, `/layout`, `/overdrive`, `/impeccable craft`,
`/impeccable extract`). Use when a `/critique` recommends them — don't
run the full 18 every PR.

## Anti-patterns the skill catches

From impeccable's curated list — the aesthetic-bias defaults every
LLM learned and that Playwright can't see:

- Overused fonts (Inter, Arial, Helvetica, system defaults)
- Pure black / pure gray (always tint toward a warm or cool neutral)
- Gray text on colored backgrounds (contrast failure)
- Cards nested in cards nested in cards
- Bounce or elastic easing on UI motion (dated)
- Purple gradients on everything

If a `/critique` flags one of these, fix it in the same PR.

## How this fits the Enterprise Readiness bar

The Readiness checklist (`docs/ENTERPRISE_READINESS_CHECKLIST.md`)
asserts every UI feature has Playwright coverage. Impeccable adds a
second dimension: every UI feature has *aesthetic* coverage. The two
are complementary — Playwright protects the feature from regressing
mechanically, impeccable protects it from looking generic.

Neither is a substitute for the other. Both run on every UI PR.
