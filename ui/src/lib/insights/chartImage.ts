// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
/**
 * A chart as a picture file: the drawn SVG with the title, the period and
 * filters and the data quality burned in above it (PRD: exported charts carry
 * their context). SVG is the serialised chart; PNG draws that SVG on a canvas
 * at twice its size. Colours are resolved from the page's CSS tokens so the
 * file looks the same outside the app (light theme).
 */

const HEADER = 54;
const PAD = 12;

function escapeXml(text: string): string {
  return text.replace(/[<>&"']/g, (c) => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;", "'": "&apos;" })[c] ?? c);
}

/** Replace `hsl(var(--token))` and `var(--token)` paint values with the computed colour, so the file stands alone. */
function resolveTokens(markup: string, root: Element | null): string {
  if (!root || typeof getComputedStyle === "undefined") return markup;
  const style = getComputedStyle(root);
  return markup.replace(/hsl\(var\((--[\w-]+)\)(?:\s*\/\s*([\d.]+))?\)|var\((--[\w-]+)\)/g, (match, hslToken, alpha, plainToken) => {
    const token = hslToken || plainToken;
    const raw = style.getPropertyValue(token).trim();
    if (!raw) return match;
    if (hslToken) return alpha ? `hsl(${raw} / ${alpha})` : `hsl(${raw})`;
    return raw;
  });
}

export interface ChartContext {
  title: string;
  /** e.g. "2026-10-01 to 2026-10-31 · compare: previous" */
  subtitle: string;
  quality: string;
}

/** The chart's SVG with its context drawn above it, or null when the chart has no SVG (a table-only chart). */
export function chartSvg(container: Element | null, context: ChartContext): string | null {
  const svg = container?.querySelector("svg");
  if (!svg) return null;
  const box = svg.getBoundingClientRect();
  const width = Math.max(320, Math.round(box.width || Number(svg.getAttribute("width")) || 640));
  const height = Math.max(160, Math.round(box.height || Number(svg.getAttribute("height")) || 320));
  const clone = svg.cloneNode(true) as SVGElement;
  clone.setAttribute("x", "0");
  clone.setAttribute("y", String(HEADER));
  clone.setAttribute("width", String(width));
  clone.setAttribute("height", String(height));
  const inner = new XMLSerializer().serializeToString(clone);
  const total = height + HEADER + PAD;
  const markup =
    `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${total}" viewBox="0 0 ${width} ${total}">` +
    `<rect width="100%" height="100%" fill="#ffffff"/>` +
    `<text x="${PAD}" y="20" font-family="Arial, sans-serif" font-size="15" font-weight="bold" fill="#111827">${escapeXml(context.title)}</text>` +
    `<text x="${PAD}" y="38" font-family="Arial, sans-serif" font-size="11" fill="#4b5563">${escapeXml(context.subtitle)}</text>` +
    `<text x="${width - PAD}" y="38" text-anchor="end" font-family="Arial, sans-serif" font-size="11" fill="#4b5563">${escapeXml(context.quality)}</text>` +
    inner +
    `</svg>`;
  return resolveTokens(markup, document.documentElement);
}

/** The SVG drawn on a canvas at twice its size, as a PNG. */
export function svgToPng(markup: string): Promise<Blob> {
  return new Promise((resolve, reject) => {
    const width = Number(/width="(\d+)"/.exec(markup)?.[1] ?? 640);
    const height = Number(/height="(\d+)"/.exec(markup)?.[1] ?? 360);
    const url = URL.createObjectURL(new Blob([markup], { type: "image/svg+xml;charset=utf-8" }));
    const image = new Image();
    image.onload = () => {
      const canvas = document.createElement("canvas");
      canvas.width = width * 2;
      canvas.height = height * 2;
      const ctx = canvas.getContext("2d");
      if (!ctx) {
        URL.revokeObjectURL(url);
        return reject(new Error("canvas_unavailable"));
      }
      ctx.scale(2, 2);
      ctx.drawImage(image, 0, 0);
      URL.revokeObjectURL(url);
      canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error("png_failed"))), "image/png");
    };
    image.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error("svg_unreadable"));
    };
    image.src = url;
  });
}
