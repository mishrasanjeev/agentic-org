// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
import { describe, expect, it } from "vitest";
import { textEncodingOrigin } from "./textEncodingOrigin";

describe("text encoding browser isolation", () => {
  it("defaults only to a local preview origin", () => {
    expect(textEncodingOrigin()).toBe("http://127.0.0.1:4187");
    expect(textEncodingOrigin("")).toBe("http://127.0.0.1:4187");
  });

  it.each(["http://127.0.0.1:5187", "http://localhost:8080/"])("accepts an explicit local image at %s", url => {
    expect(textEncodingOrigin(url)).toBe(new URL(url).origin);
  });

  it.each([
    "https://app.example.test", "http://other.example.test", "http://127.0.0.1.example.test",
    "https://localhost:5187", "http://user:password@localhost:5187", "http://localhost:5187/dashboard",
    "http://localhost:5187/?token=synthetic", "http://localhost:5187/#fragment", "not a URL",
  ])("refuses unsafe or ambiguous origins before any browser starts: %s", url => {
    expect(() => textEncodingOrigin(url)).toThrow();
  });
});
