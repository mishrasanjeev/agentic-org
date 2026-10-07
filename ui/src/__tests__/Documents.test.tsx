// SPDX-License-Identifier: Apache-2.0
/**
 * Document review: the list, a document's fields with confidence tones and boxes, corrections and decisions.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { HelmetProvider } from "react-helmet-async";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockGet = vi.fn();
const mockPost = vi.fn();

vi.mock("@/lib/api", () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  },
  extractApiError: (_err: unknown, fallback: string) => fallback,
}));

import Documents, { boxStyle, confidenceTone } from "@/pages/Documents";

const ID = "11111111-1111-4111-8111-111111111111";
const SUMMARY = {
  id: ID,
  filename: "slip.pdf",
  status: "review",
  pages: 1,
  document_types: ["salary_slip"],
  review_reasons: ["required field pay_period not found"],
  corrections: 0,
  created_at: "2026-10-07T10:00:00+00:00",
};
const DETAIL = {
  ...SUMMARY,
  pages_detail: [{ number: 1, width: 595, height: 842, source: "text", ocr: "not_needed" }],
  image_dpi: 110,
  documents: [
    {
      index: 0,
      document_type: "salary_slip",
      confidence: 0.91,
      pages: [1],
      fields: [
        { name: "net_pay", value: "41250", confidence: 0.95, page: 1, bbox: [60, 80, 120, 92], status: "found", required: true, corrected: false },
        { name: "pay_period", value: null, confidence: 0, page: null, bbox: null, status: "missing", required: true, corrected: false },
      ],
      extra_fields: [{ name: "branch", value: "Pune", confidence: 0.6, page: 1, bbox: [60, 100, 100, 110], status: "found", required: false, corrected: false }],
      tables: [{ page: 1, bbox: [60, 200, 500, 300], header: ["a"], rows: [["1"]] }],
      review: { needed: true, reasons: ["required field pay_period not found"] },
    },
  ],
};

beforeEach(() => {
  mockGet.mockReset();
  mockPost.mockReset();
  (globalThis as unknown as { URL: typeof URL }).URL.createObjectURL = vi.fn(() => "blob:page");
  (globalThis as unknown as { URL: typeof URL }).URL.revokeObjectURL = vi.fn();
  mockGet.mockImplementation((url: string) => {
    if (url.endsWith(".png")) return Promise.resolve({ data: new Blob(["png"]) });
    if (url === "/idp/documents") return Promise.resolve({ data: { documents: [SUMMARY], total: 1 } });
    return Promise.resolve({ data: DETAIL });
  });
  mockPost.mockImplementation((url: string) => Promise.resolve({ data: url.endsWith("/decide") ? { ...SUMMARY, status: "approved" } : DETAIL }));
});

function renderPage() {
  return render(
    <HelmetProvider>
      <Documents />
    </HelmetProvider>,
  );
}

describe("Documents page", () => {
  it("lists documents needing review and shows a document's fields with their tones", async () => {
    renderPage();
    fireEvent.click(await screen.findByTestId(`document-row-${ID}`));
    await screen.findByTestId("document-fields");
    expect(mockGet).toHaveBeenCalledWith("/idp/documents", { params: { limit: "50", status: "review" } });
    expect(screen.getByTestId("tone-0:net_pay").textContent).toBe("95%");
    expect(screen.getByTestId("tone-0:pay_period").textContent).toBe("missing");
    expect(screen.getByTestId("tone-0:branch").textContent).toBe("60%");
    expect(screen.getByTestId("document-reasons").textContent).toContain("pay_period");
    await waitFor(() => expect(mockGet).toHaveBeenCalledWith(`/idp/documents/${ID}/pages/1.png`, { responseType: "blob" }));
  });

  it("saves a correction and decides the document", async () => {
    renderPage();
    fireEvent.click(await screen.findByTestId(`document-row-${ID}`));
    await screen.findByTestId("field-input-0:pay_period");
    fireEvent.change(screen.getByTestId("field-input-0:pay_period"), { target: { value: "September 2026" } });
    fireEvent.click(screen.getByTestId("field-save-0:pay_period"));
    await waitFor(() => expect(mockPost).toHaveBeenCalledWith(`/idp/documents/${ID}/fields`, { document_index: 0, field: "pay_period", value: "September 2026" }));
    fireEvent.click(screen.getByTestId("document-approve"));
    await waitFor(() => expect(mockPost).toHaveBeenCalledWith(`/idp/documents/${ID}/decide`, { decision: "approve", notes: "" }));
    await screen.findByTestId("documents-notice");
  });

  it("saving an untouched field sends the displayed value", async () => {
    renderPage();
    fireEvent.click(await screen.findByTestId(`document-row-${ID}`));
    await screen.findByTestId("field-save-0:branch");
    fireEvent.click(screen.getByTestId("field-save-0:branch"));
    await waitFor(() => expect(mockPost).toHaveBeenCalledWith(`/idp/documents/${ID}/fields`, { document_index: 0, field: "branch", value: "Pune" }));
    expect(mockPost).not.toHaveBeenCalledWith(`/idp/documents/${ID}/fields`, expect.objectContaining({ value: "" }));
  });

  it("drops a document detail that arrives after another document was selected", async () => {
    const OTHER = "22222222-2222-4222-8222-222222222222";
    const otherSummary = { ...SUMMARY, id: OTHER, filename: "statement.pdf" };
    const otherDetail = { ...DETAIL, ...otherSummary };
    let releaseFirst: (value: { data: unknown }) => void = () => undefined;
    mockGet.mockImplementation((url: string) => {
      if (url.endsWith(".png")) return Promise.resolve({ data: new Blob(["png"]) });
      if (url === "/idp/documents") return Promise.resolve({ data: { documents: [SUMMARY, otherSummary], total: 2 } });
      if (url === `/idp/documents/${ID}`) return new Promise((resolve) => { releaseFirst = resolve; });
      return Promise.resolve({ data: otherDetail });
    });
    renderPage();
    fireEvent.click(await screen.findByTestId(`document-row-${ID}`));
    fireEvent.click(screen.getByTestId(`document-row-${OTHER}`));
    await waitFor(() => expect(mockGet).toHaveBeenCalledWith(`/idp/documents/${OTHER}/pages/1.png`, { responseType: "blob" }));
    releaseFirst({ data: DETAIL });
    await new Promise((resolve) => setTimeout(resolve, 0));
    fireEvent.click(screen.getByTestId("document-approve"));
    await waitFor(() => expect(mockPost).toHaveBeenCalledWith(`/idp/documents/${OTHER}/decide`, { decision: "approve", notes: "" }));
    expect(mockPost).not.toHaveBeenCalledWith(`/idp/documents/${ID}/decide`, expect.anything());
    expect(mockGet).not.toHaveBeenCalledWith(`/idp/documents/${ID}/pages/1.png`, { responseType: "blob" });
  });

  it("says when nothing is in the chosen status", async () => {
    mockGet.mockImplementation((url: string) => (url === "/idp/documents" ? Promise.resolve({ data: { documents: [], total: 0 } }) : Promise.resolve({ data: DETAIL })));
    renderPage();
    await screen.findByTestId("documents-empty");
  });

  it("tones and box scaling are deterministic", () => {
    const field = DETAIL.documents[0].fields[0];
    expect(confidenceTone(field)).toBe("high");
    expect(confidenceTone({ ...field, confidence: 0.5 })).toBe("low");
    expect(confidenceTone({ ...field, corrected: true })).toBe("corrected");
    expect(confidenceTone(DETAIL.documents[0].fields[1])).toBe("missing");
    const style = boxStyle([60, 80, 120, 92], { width: 595, height: 842 }, { width: 1190, height: 1684 });
    expect(style).toEqual({ left: 120, top: 160, width: 120, height: 24 });
  });
});
