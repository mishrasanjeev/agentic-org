import { describe, expect, it } from "vitest";

import { AUTH_FIELD_HINTS, AUTH_TYPES, authTypeLabel } from "@/lib/connector-constants";

describe("connector auth constants", () => {
  it("includes custom auth as a shared create/edit option", () => {
    expect(AUTH_TYPES).toContain("custom");
    expect(AUTH_FIELD_HINTS.custom).toContain("Custom authentication");
    expect(authTypeLabel("custom")).toBe("Custom Credential");
  });
});
