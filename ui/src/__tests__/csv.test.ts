import { describe, expect, it } from "vitest";

import { buildCsv, escapeCsvCell } from "@/lib/csv";

describe("CSV formula neutralization", () => {
  it.each(["=1+1", "+cmd", "-1", "@SUM(A1:A2)", "\rvalue", "\nvalue", "\tvalue"])(
    "renders a dangerous prefix as text: %j",
    (value) => {
      expect(escapeCsvCell(value)).toBe(`"'${value}"`);
    },
  );

  it("escapes quotes while preserving ordinary text", () => {
    expect(escapeCsvCell('safe "quoted" value')).toBe('"safe ""quoted"" value"');
  });

  it("applies the guard to every exported row", () => {
    expect(buildCsv(["action", "actor"], [["=2+2", "+reviewer"]])).toBe(
      'action,actor\n"\'=2+2","\'+reviewer"',
    );
  });
});
