// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
});

describe("text encoding browser isolation", () => {
  it("owns only the fixture spec and starts a strict loopback preview by default", async () => {
    vi.stubEnv("BASE_URL", "");
    const { default: config } = await import("../text-encoding.config");
    expect(config.testDir).toBe("../tests/browser");
    expect(config.testMatch).toBe("text-encoding.spec.ts");
    expect(config.use?.baseURL).toBe("http://127.0.0.1:4187");
    expect(config.webServer).toMatchObject({
      command: "npm run preview -- --host 127.0.0.1 --port 4187 --strictPort",
      reuseExistingServer: false,
    });
    expect(config.retries).toBe(0);
  });

  it.each(["http://127.0.0.1:5187", "http://localhost:8080/"])("accepts an explicit local image at %s", async url => {
    vi.stubEnv("BASE_URL", url);
    const { default: config } = await import("../text-encoding.config");
    expect(config.use?.baseURL).toBe(new URL(url).origin);
    expect(config.webServer).toBeUndefined();
  });

  it.each([
    "https://app.example.test", "http://other.example.test", "http://127.0.0.1.example.test",
    "https://localhost:5187", "http://user:password@localhost:5187", "http://localhost:5187/dashboard",
    "http://localhost:5187/?token=synthetic", "http://localhost:5187/#fragment", "not a URL",
  ])("refuses unsafe or ambiguous origins before any browser starts: %s", async url => {
    vi.stubEnv("BASE_URL", url);
    await expect(import("../text-encoding.config")).rejects.toThrow();
  });
});
