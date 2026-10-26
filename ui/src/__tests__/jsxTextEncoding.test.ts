// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
import { describe, expect, it } from "vitest";
import ts from "typescript";

const sources = import.meta.glob("../**/*.tsx", { query: "?raw", import: "default", eager: true }) as Record<string, string>;
const escapedUnicode = /\\u(?:[\da-f]{4}|\{[\da-f]+\})/i;

function leakedEscapes(source: string): string[] {
  const file = ts.createSourceFile("view.tsx", source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const failures: string[] = [];
  function visit(node: ts.Node) {
    if (ts.isJsxText(node) || (ts.isJsxAttribute(node) && node.initializer && ts.isStringLiteral(node.initializer))) {
      if (escapedUnicode.test(node.getText(file))) failures.push(node.getText(file));
    }
    ts.forEachChild(node, visit);
  }
  visit(file);
  return failures;
}

describe("JSX text encoding", () => {
  it.each([
    String.raw`<input placeholder="Ask anything (\u2318K)" />`,
    String.raw`<span>Loading\u2026</span>`,
    String.raw`<button aria-label="Go \u{2192}" />`,
  ])("rejects a Unicode escape that JSX would display literally: %s", source => {
    expect(leakedEscapes(source)).not.toEqual([]);
  });

  it.each([
    String.raw`<input placeholder={"Ask anything (\u2318K)"} />`,
    String.raw`<span>{"\u2014"}</span>`,
    '<span>हिन्दी — Café</span>',
  ])("preserves JavaScript escapes and real Unicode: %s", source => {
    expect(leakedEscapes(source)).toEqual([]);
  });

  it("checks every JSX source and attribute across the UI", () => {
    const failures = Object.entries(sources).flatMap(([path, source]) => leakedEscapes(source).map(text => `${path}: ${text}`));
    expect(failures).toEqual([]);
  });
});
