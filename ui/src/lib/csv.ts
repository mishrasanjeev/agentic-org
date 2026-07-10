const FORMULA_PREFIX = /^[=+\-@\r\n\t]/;

/** Encode an untrusted value as a spreadsheet-safe CSV cell. */
export function escapeCsvCell(value: unknown): string {
  const text = String(value ?? "");
  const guarded = FORMULA_PREFIX.test(text) ? `'${text}` : text;
  return `"${guarded.replace(/"/g, '""')}"`;
}

export function buildCsv(
  headers: readonly string[],
  rows: ReadonlyArray<ReadonlyArray<unknown>>,
): string {
  return [
    headers.join(","),
    ...rows.map((row) => row.map(escapeCsvCell).join(",")),
  ].join("\n");
}
