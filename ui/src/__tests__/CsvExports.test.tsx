import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockGet, mockCreateObjectURL } = vi.hoisted(() => ({
  mockGet: vi.fn(),
  mockCreateObjectURL: vi.fn((_blob: Blob) => "blob:test"),
}));

vi.mock("@/lib/api", () => ({
  default: { get: mockGet },
}));

vi.mock("react-helmet-async", () => ({
  Helmet: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

import Audit from "@/pages/Audit";
import EnforceAuditLog from "@/pages/EnforceAuditLog";

function readBlob(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.addEventListener("load", () => resolve(String(reader.result)));
    reader.addEventListener("error", () => reject(reader.error));
    reader.readAsText(blob);
  });
}

describe("audit CSV downloads", () => {
  beforeEach(() => {
    mockGet.mockReset();
    mockCreateObjectURL.mockClear();
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: mockCreateObjectURL,
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: vi.fn(),
    });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("neutralizes an audit action before downloading it", async () => {
    mockGet.mockResolvedValue({
      data: {
        items: [{
          id: "audit-1",
          event_type: "agent.updated",
          actor_type: "user",
          action: "=2+2",
          outcome: "success",
          created_at: "2026-07-10T09:00:00Z",
        }],
      },
    });

    render(<Audit />);
    await screen.findByText("=2+2");
    fireEvent.click(screen.getByRole("button", { name: "Download CSV" }));

    const csv = await readBlob(mockCreateObjectURL.mock.calls[0][0] as Blob);
    expect(csv).toContain('"\'=2+2"');
  });

  it("neutralizes an enforcement reason before downloading it", async () => {
    mockGet.mockResolvedValue({
      data: {
        items: [{
          id: "enforce-1",
          timestamp: "2026-07-10T09:00:00Z",
          agent_name: "Finance Agent",
          connector: "erp",
          tool: "post_invoice",
          permission: "WRITE",
          result: "denied",
          reason: "+cmd",
        }],
      },
    });

    render(<EnforceAuditLog />);
    await screen.findByText("+cmd");
    fireEvent.click(screen.getByRole("button", { name: "Download CSV" }));

    const csv = await readBlob(mockCreateObjectURL.mock.calls[0][0] as Blob);
    expect(csv).toContain('"\'+cmd"');
  });
});
