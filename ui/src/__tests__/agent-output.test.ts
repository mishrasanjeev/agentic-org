import { describe, expect, it } from "vitest";

import { extractReadableAgentOutput } from "@/lib/agent-output";

describe("extractReadableAgentOutput", () => {
  it("unwraps raw_output text and hides internal fields", () => {
    const output = extractReadableAgentOutput({
      raw_output: {
        text: "Invoice Number: INV-000001\nAmount: INR 5,000",
        metadata: { trace_id: "trace-1" },
      },
      status: "completed",
      signature: "sig",
      extras: { debug: true },
    });

    expect(output).toBe("Invoice Number: INV-000001\nAmount: INR 5,000");
    expect(output).not.toContain("raw_output");
    expect(output).not.toContain("metadata");
    expect(output).not.toContain("signature");
  });

  it("parses JSON strings nested under answer", () => {
    const output = extractReadableAgentOutput({
      answer:
        '{"raw_output":{"text":"Clean assistant answer"},"status":"completed","metadata":{"debug":true}}',
    });

    expect(output).toBe("Clean assistant answer");
    expect(output).not.toContain("status");
    expect(output).not.toContain("metadata");
  });

  it("parses Python repr text envelopes", () => {
    const output = extractReadableAgentOutput(
      "{'type': 'text', 'text': 'Plain answer', 'extras': {'signature': 'sig'}}",
    );

    expect(output).toBe("Plain answer");
    expect(output).not.toContain("extras");
  });

  it("falls back without exposing metadata-only envelopes", () => {
    const output = extractReadableAgentOutput(
      { status: "completed", metadata: { trace_id: "trace-1" } },
      "No user-facing answer returned.",
    );

    expect(output).toBe("No user-facing answer returned.");
  });
});
