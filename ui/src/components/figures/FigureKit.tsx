// SPDX-License-Identifier: Apache-2.0
// Building blocks for theme-aware inline SVG diagrams. Colours come from CSS tokens
// (figureKit.css), so a diagram follows the page's light or dark scheme.
import type { ReactNode } from "react";

export type Tone = "neutral" | "grantex" | "agenticorg" | "oacp" | "protocol" | "refuse" | "allow" | "hold";

export function Box({ x, y, w, h, tone = "neutral", rx = 10 }: { x: number; y: number; w: number; h: number; tone?: Tone; rx?: number }) {
  return <rect x={x} y={y} width={w} height={h} rx={rx} className={`fig-box fig-box-${tone}`} />;
}

export function Text({ x, y, children, kind = "body", anchor = "start", tone }: {
  x: number; y: number; children: ReactNode; kind?: "title" | "strong" | "body" | "small" | "owner" | "mono"; anchor?: "start" | "middle" | "end"; tone?: Tone;
}) {
  return <text x={x} y={y} textAnchor={anchor} className={`fig-text fig-text-${kind}${tone ? ` fig-ink-${tone}` : ""}`}>{children}</text>;
}

export function Chip({ x, y, w, h, label, size = "body", tone = "protocol" }: { x: number; y: number; w: number; h: number; label: string; size?: "body" | "small"; tone?: Tone }) {
  return (
    <g>
      <Box x={x} y={y} w={w} h={h} tone={tone} rx={h / 2} />
      <Text x={x + w / 2} y={y + h / 2 + (size === "small" ? 4.5 : 5.5)} anchor="middle" kind={size === "small" ? "small" : "strong"} tone={tone === "protocol" ? undefined : tone}>{label}</Text>
    </g>
  );
}

export function Svg({ id, width, height, title, desc, children }: { id: string; width: number; height: number; title: string; desc: string; children: ReactNode }) {
  return (
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-labelledby={`${id}-svg-title ${id}-svg-desc`} className="fig-svg" focusable="false">
      <title id={`${id}-svg-title`}>{title}</title>
      <desc id={`${id}-svg-desc`}>{desc}</desc>
      <defs>
        <marker id={`${id}-arrow`} viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" className="fig-arrowhead" />
        </marker>
      </defs>
      {children}
    </svg>
  );
}

/** A tick inside a circle, for a passed check. */
export function Tick({ cx, cy, r = 11, tone = "allow" }: { cx: number; cy: number; r?: number; tone?: Tone }) {
  return (
    <g>
      <circle cx={cx} cy={cy} r={r} className={`fig-box fig-box-${tone}`} />
      <path d={`M ${cx - r * 0.45} ${cy + r * 0.02} L ${cx - r * 0.1} ${cy + r * 0.38} L ${cx + r * 0.48} ${cy - r * 0.36}`} className={`fig-mark fig-mark-${tone}`} />
    </g>
  );
}

/** A cross inside a circle, for a refusal. */
export function Cross({ cx, cy, r = 11 }: { cx: number; cy: number; r?: number }) {
  const d = r * 0.42;
  return (
    <g>
      <circle cx={cx} cy={cy} r={r} className="fig-box fig-box-refuse" />
      <path d={`M ${cx - d} ${cy - d} L ${cx + d} ${cy + d} M ${cx + d} ${cy - d} L ${cx - d} ${cy + d}`} className="fig-cross" />
    </g>
  );
}

export interface Step { owner: string; tone: Tone; title: string; detail?: string; chips?: string[]; badge?: string; badgeTone?: Tone }

/** Numbered steps down a dashed spine; reads top to bottom on any screen width. */
export function StepStack({ id, title, desc, steps }: { id: string; title: string; desc: string; steps: Step[] }) {
  const row = 96;
  const height = 12 + steps.length * row - 6;
  return (
    <Svg id={id} width={420} height={height} title={title} desc={desc}>
      <line x1={26} y1={30} x2={26} y2={height - 40} className="fig-wire fig-spine" />
      {steps.map((step, i) => {
        const y = 12 + i * row;
        const badgeTone = step.badgeTone ?? "allow";
        return (
          <g key={step.title}>
            <circle cx={26} cy={y + 39} r={16} className={`fig-box fig-box-${step.tone}`} />
            <Text x={26} y={y + 45} anchor="middle" kind="strong">{String(i + 1)}</Text>
            <Box x={56} y={y} w={352} h={78} tone={step.tone} />
            <Text x={70} y={y + 22} kind="owner" tone={step.tone}>{step.owner}</Text>
            <Text x={70} y={y + 45} kind="strong">{step.title}</Text>
            {step.detail && <Text x={70} y={y + 66} kind="small">{step.detail}</Text>}
            {step.chips?.map((chip, c) => <Chip key={chip} x={70 + c * 64} y={y + 52} w={56} h={20} label={chip} size="small" />)}
            {step.badge && (() => {
              const w = Math.max(84, step.badge.length * 8 + 18);
              return (
                <g>
                  <Box x={396 - w} y={y + 9} w={w} h={22} tone={badgeTone} rx={11} />
                  <Text x={396 - w / 2} y={y + 24.5} anchor="middle" kind="small" tone={badgeTone}>{step.badge}</Text>
                </g>
              );
            })()}
          </g>
        );
      })}
    </Svg>
  );
}
