import { describe, expect, it } from "vitest";

import { extractApiError } from "@/lib/api";

function axiosLike(detail: unknown) {
  return { response: { data: { detail } } };
}

describe("extractApiError", () => {
  it("returns string detail as-is", () => {
    expect(extractApiError(axiosLike("boom"))).toBe("boom");
  });

  it("joins pydantic 422 error arrays as loc: msg", () => {
    const err = axiosLike([
      { loc: ["body", "params", "api_key"], msg: "inline secrets are not allowed", type: "value_error" },
      { loc: ["body", "name"], msg: "field required", type: "missing" },
    ]);
    expect(extractApiError(err)).toBe(
      "params.api_key: inline secrets are not allowed; name: field required",
    );
  });

  it("joins detail.row_errors per row", () => {
    const err = axiosLike({
      status: "invalid",
      row_errors: [
        { row_number: 1, employee_ref: "E-1", errors: ["pt_amount is required when slabs are not supplied"] },
        { row_number: 3, errors: ["row must be an object"] },
      ],
    });
    expect(extractApiError(err)).toBe(
      "Row 1 (E-1): pt_amount is required when slabs are not supplied; Row 3: row must be an object",
    );
  });

  it("joins detail.errors string arrays", () => {
    const err = axiosLike({ errors: ["registration_number must be 11 chars", "state not supported"] });
    expect(extractApiError(err)).toBe(
      "registration_number must be 11 chars; state not supported",
    );
  });

  it("falls back when nothing is usable", () => {
    expect(extractApiError(new Error("x"), "fallback")).toBe("fallback");
    expect(extractApiError(axiosLike([]), "fallback")).toBe("fallback");
  });
});
