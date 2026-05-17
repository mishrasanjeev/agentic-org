import { describe, expect, it } from "vitest";

import { errorDetailToMessage } from "@/pages/AgentDetail";

/**
 * Uday CA-Firms 2026-05-17 bug 1 — UI half of the promotion reopen.
 * The activation gate returns a structured 409 object; the lifecycle
 * handlers must never push a raw object into the string error state
 * (it crashes React rendering and blanks the agent page).
 */
describe("errorDetailToMessage", () => {
  it("renders the connector gate 409 as a readable sentence", () => {
    const err = {
      response: {
        data: {
          detail: {
            error: "connector_not_ready_for_activation",
            message:
              "Agents can become active only when all linked connectors are healthy and refreshable.",
            connectors: [
              { connector: "income_tax_india", reason: "missing_connector_config" },
              { connector: "tally", reason: "missing_connector_config" },
            ],
          },
        },
      },
    };
    const msg = errorDetailToMessage(err, "Promote failed");
    expect(msg).toContain("income_tax_india (missing connector config)");
    expect(msg).toContain("tally (missing connector config)");
    expect(msg).not.toContain("[object Object]");
    expect(typeof msg).toBe("string");
  });

  it("passes through a plain string detail", () => {
    const err = { response: { data: { detail: "Shadow accuracy too low" } } };
    expect(errorDetailToMessage(err, "x")).toBe("Shadow accuracy too low");
  });

  it("joins a FastAPI validation array", () => {
    const err = {
      response: {
        data: {
          detail: [
            { msg: "field required", loc: ["body", "name"] },
            { msg: "invalid value", loc: ["body", "x"] },
          ],
        },
      },
    };
    expect(errorDetailToMessage(err, "x")).toBe(
      "field required; invalid value",
    );
  });

  it("uses message then error for a bare object", () => {
    expect(
      errorDetailToMessage(
        { response: { data: { detail: { message: "boom" } } } },
        "x",
      ),
    ).toBe("boom");
    expect(
      errorDetailToMessage(
        { response: { data: { detail: { error: "nope" } } } },
        "x",
      ),
    ).toBe("nope");
  });

  it("falls back when detail is missing or empty", () => {
    expect(errorDetailToMessage({}, "fallback")).toBe("fallback");
    expect(errorDetailToMessage(undefined, "fallback")).toBe("fallback");
    expect(
      errorDetailToMessage(
        { response: { data: { detail: "   " } } },
        "fallback",
      ),
    ).toBe("fallback");
  });
});
