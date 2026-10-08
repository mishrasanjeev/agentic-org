// SPDX-License-Identifier: Apache-2.0
/**
 * The business console: settings by group, saving within bounds, options as checkboxes, reset to default.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockGet = vi.fn();
const mockPut = vi.fn();
const mockDelete = vi.fn();

vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    put: (...args: unknown[]) => mockPut(...args),
    delete: (...args: unknown[]) => mockDelete(...args),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  },
  extractApiError: (_err: unknown, fallback: string) => fallback,
}));

import BusinessConsole, { parseInput, show } from "@/components/BusinessConsole";

const FLOOR = {
  key: "documents.type_confidence_floor",
  title: "Document type confidence floor",
  description: "Below this a document goes to review.",
  group: "documents",
  kind: "number",
  default: 0.6,
  applies: "core/idp/pipeline.py",
  minimum: 0,
  maximum: 1,
  unit: "",
  options: [],
  value: 0.75,
  source: "set",
  updated_by: "admin-1",
  updated_at: "2026-10-07T10:00:00+00:00",
  previous: 0.6,
};
const KINDS = {
  ...FLOOR,
  key: "content.approval_kinds",
  title: "Draft kinds that wait for approval",
  group: "content",
  kind: "list",
  default: ["notice", "circular"],
  options: ["notice", "circular", "letter"],
  value: ["notice", "circular"],
  source: "default",
  updated_by: null,
  updated_at: null,
  previous: null,
  minimum: null,
  maximum: null,
};
const PAYLOAD = {
  groups: [
    { key: "documents", title: "Document processing", settings: [FLOOR] },
    { key: "content", title: "Content services", settings: [KINDS] },
  ],
  total: 2,
};

describe("BusinessConsole", () => {
  beforeEach(() => {
    mockGet.mockReset();
    mockPut.mockReset();
    mockDelete.mockReset();
    mockGet.mockResolvedValue({ data: PAYLOAD });
  });

  it("formats values and parses inputs by kind", () => {
    expect(show(null)).toBe("");
    expect(show(0.6)).toBe("0.6");
    expect(show(["a"])).toBe('["a"]');
    expect(parseInput({ ...FLOOR, kind: "number" }, "0.8")).toBe(0.8);
    expect(parseInput({ ...FLOOR, kind: "integer" }, "3.7")).toBe(3);
    expect(parseInput({ ...FLOOR, kind: "boolean" }, "true")).toBe(true);
    expect(parseInput({ ...KINDS, options: [] }, "a, b")).toEqual(["a", "b"]);
    expect(parseInput(KINDS, "", { notice: true, letter: true })).toEqual(["notice", "letter"]);
    expect(parseInput({ ...FLOOR, kind: "rules" }, '[{"field":"amount"}]')).toEqual([{ field: "amount" }]);
  });

  it("lists the settings by group with their source and previous value", async () => {
    render(<BusinessConsole />);
    const card = await screen.findByTestId("console-documents.type_confidence_floor");
    expect(card.textContent).toContain("Set by admin-1");
    expect(card.textContent).toContain("previously 0.6");
    expect(card.textContent).toContain("0 to 1");
    expect(screen.getByTestId("console-content.approval_kinds").textContent).toContain("Default");
    expect(screen.getByText("Content services")).toBeTruthy();
  });

  it("saves a number within bounds and reloads", async () => {
    mockPut.mockResolvedValue({ data: {} });
    render(<BusinessConsole />);
    const input = await screen.findByTestId("console-input-documents.type_confidence_floor");
    fireEvent.change(input, { target: { value: "0.8" } });
    fireEvent.click(screen.getByTestId("console-save-documents.type_confidence_floor"));
    await waitFor(() => expect(mockPut).toHaveBeenCalledWith("/workbench/console/documents.type_confidence_floor", { value: 0.8 }));
    await waitFor(() => expect(mockGet).toHaveBeenCalledTimes(2));
  });

  it("saves a list from its options and resets a set value", async () => {
    mockPut.mockResolvedValue({ data: {} });
    mockDelete.mockResolvedValue({ data: {} });
    render(<BusinessConsole />);
    await screen.findByTestId("console-content.approval_kinds");
    fireEvent.click(screen.getByTestId("console-option-content.approval_kinds-letter"));
    fireEvent.click(screen.getByTestId("console-save-content.approval_kinds"));
    await waitFor(() => expect(mockPut).toHaveBeenCalledWith("/workbench/console/content.approval_kinds", { value: ["notice", "circular", "letter"] }));
    expect((screen.getByTestId("console-reset-content.approval_kinds") as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByTestId("console-reset-documents.type_confidence_floor"));
    await waitFor(() => expect(mockDelete).toHaveBeenCalledWith("/workbench/console/documents.type_confidence_floor"));
  });

  it("reports a value the backend refused", async () => {
    mockPut.mockRejectedValue(new Error("422"));
    render(<BusinessConsole />);
    await screen.findByTestId("console-input-documents.type_confidence_floor");
    fireEvent.click(screen.getByTestId("console-save-documents.type_confidence_floor"));
    expect(await screen.findByRole("alert")).toHaveTextContent("The value was not saved.");
  });
});
