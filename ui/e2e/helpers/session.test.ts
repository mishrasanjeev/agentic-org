// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it, vi } from "vitest";

import {
  RELOGIN_RETRY_MS,
  type LoginFailureLog,
  SESSION_REFRESH_MARGIN_MS,
  SessionKeeper,
  loginForToken,
  sessionExpiresAt,
} from "./session";

/**
 * The production Playwright suite runs for more than an hour on one 60-minute
 * session token, so every test after the first hour failed with 401s that
 * read as product bugs (post-deploy run, 2026-09-26). The keeper logs in again
 * before expiry and fails loudly when it cannot.
 */

const NOW = Date.UTC(2026, 8, 26, 12, 0, 0);

function jwtExpiringAt(ms: number): string {
  const encode = (value: object) =>
    btoa(JSON.stringify(value)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  return `${encode({ alg: "HS256", typ: "JWT" })}.${encode({ sub: "shopper-01", exp: Math.floor(ms / 1000) })}.sig`;
}

describe("sessionExpiresAt", () => {
  it("reads exp from a JWT in milliseconds", () => {
    expect(sessionExpiresAt(jwtExpiringAt(NOW + 3_600_000))).toBe(NOW + 3_600_000);
  });

  it("returns null for tokens it cannot read", () => {
    expect(sessionExpiresAt("opaque-session-token")).toBeNull();
    expect(sessionExpiresAt("a.not-base64-json.c")).toBeNull();
    expect(sessionExpiresAt(`x.${btoa(JSON.stringify({ sub: "no-exp" }))}.y`)).toBeNull();
  });
});

describe("SessionKeeper.fresh", () => {
  it("keeps a token with more than the margin left and does not log in", async () => {
    const token = jwtExpiringAt(NOW + SESSION_REFRESH_MARGIN_MS + 1_000);
    const login = vi.fn(async () => "new");
    const keeper = new SessionKeeper(token, login);
    await expect(keeper.fresh(NOW)).resolves.toBe(token);
    expect(login).not.toHaveBeenCalled();
  });

  it("logs in again inside the margin and hands out the new token", async () => {
    const renewed = jwtExpiringAt(NOW + 3_600_000);
    const login = vi.fn(async () => renewed);
    const keeper = new SessionKeeper(jwtExpiringAt(NOW + SESSION_REFRESH_MARGIN_MS - 1_000), login);
    await expect(keeper.fresh(NOW)).resolves.toBe(renewed);
    expect(keeper.token).toBe(renewed);
    await expect(keeper.fresh(NOW + 1_000)).resolves.toBe(renewed);
    expect(login).toHaveBeenCalledTimes(1);
  });

  it("renews a token that has already expired", async () => {
    const renewed = jwtExpiringAt(NOW + 3_600_000);
    const keeper = new SessionKeeper(jwtExpiringAt(NOW - 60_000), async () => renewed);
    await expect(keeper.fresh(NOW)).resolves.toBe(renewed);
  });

  it("keeps the old token while it is valid, and retries a failed login at most once a minute", async () => {
    const token = jwtExpiringAt(NOW + 5 * 60_000);
    const login = vi.fn(async (): Promise<string> => {
      throw new Error("login returned HTTP 503");
    });
    const keeper = new SessionKeeper(token, login);
    await expect(keeper.fresh(NOW)).resolves.toBe(token);
    await expect(keeper.fresh(NOW + RELOGIN_RETRY_MS - 1)).resolves.toBe(token);
    expect(login).toHaveBeenCalledTimes(1);
    await expect(keeper.fresh(NOW + RELOGIN_RETRY_MS)).resolves.toBe(token);
    expect(login).toHaveBeenCalledTimes(2);
  });

  it("throws with the reason once the token has expired and cannot be renewed", async () => {
    const expiredAt = NOW - 1_000;
    const keeper = new SessionKeeper(jwtExpiringAt(expiredAt), async () => {
      throw new Error("E2E_EMAIL and E2E_PASSWORD are not set");
    });
    await expect(keeper.fresh(NOW)).rejects.toThrow(
      `The E2E session expired at ${new Date(expiredAt).toISOString()} and could not be renewed: ` +
        "E2E_EMAIL and E2E_PASSWORD are not set.",
    );
    // Inside the retry window it still fails, with the last reason.
    await expect(keeper.fresh(NOW + 1)).rejects.toThrow("E2E_EMAIL and E2E_PASSWORD are not set");
  });

  it("leaves an empty or opaque token alone", async () => {
    const login = vi.fn(async () => "new");
    await expect(new SessionKeeper("", login).fresh(NOW)).resolves.toBe("");
    await expect(new SessionKeeper("opaque", login).fresh(NOW)).resolves.toBe("opaque");
    expect(login).not.toHaveBeenCalled();
  });
});

describe("SessionKeeper with a shared LoginFailureLog", () => {
  // Playwright starts a new worker, and so a new keeper, after every failed
  // test; the shared log keeps the once-a-minute retry gap across them.
  function memoryLog(): LoginFailureLog {
    let entry: { at: number; reason: string } | null = null;
    return {
      read: () => entry,
      write: (at, reason) => {
        entry = { at, reason };
      },
    };
  }

  it("a failure in one keeper holds back the next keeper's login for a minute", async () => {
    const log = memoryLog();
    const failing = async (): Promise<string> => {
      throw new Error("login returned HTTP 503");
    };
    const token = jwtExpiringAt(NOW + 5 * 60_000);
    await new SessionKeeper(token, failing, log).fresh(NOW);

    const login = vi.fn(async () => jwtExpiringAt(NOW + 3_600_000));
    const next = new SessionKeeper(token, login, log);
    await expect(next.fresh(NOW + 1_000)).resolves.toBe(token);
    expect(login).not.toHaveBeenCalled();
    await expect(next.fresh(NOW + RELOGIN_RETRY_MS)).resolves.not.toBe(token);
    expect(login).toHaveBeenCalledTimes(1);
  });

  it("an expired token reports the reason another keeper recorded", async () => {
    const log = memoryLog();
    log.write(NOW - 1_000, "login returned HTTP 401");
    const login = vi.fn(async () => "new");
    const keeper = new SessionKeeper(jwtExpiringAt(NOW - 60_000), login, log);
    await expect(keeper.fresh(NOW)).rejects.toThrow("could not be renewed: login returned HTTP 401.");
    expect(login).not.toHaveBeenCalled();
  });
});

describe("loginForToken", () => {
  function jsonResponse(status: number, body: unknown): Response {
    return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
  }

  it("posts the credentials as JSON and returns access_token", async () => {
    const fetchImpl = vi.fn(async (_url: RequestInfo | URL, _init?: RequestInit) =>
      jsonResponse(200, { access_token: "tok" }),
    );
    await expect(
      loginForToken("https://app.example.test", "e2e@example.com", "placeholder-password", fetchImpl),
    ).resolves.toBe("tok");
    const [url, init] = fetchImpl.mock.calls[0];
    expect(url).toBe("https://app.example.test/api/v1/auth/login");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({ email: "e2e@example.com", password: "placeholder-password" });
  });

  it("rejects a non-2xx response with its status", async () => {
    const fetchImpl = vi.fn(async () => jsonResponse(429, { detail: "Too many login attempts" }));
    await expect(loginForToken("https://app.example.test", "e", "p", fetchImpl)).rejects.toThrow(
      "login returned HTTP 429",
    );
  });

  it("rejects a response without a token", async () => {
    const fetchImpl = vi.fn(async () => jsonResponse(200, { token_type: "bearer" }));
    await expect(loginForToken("https://app.example.test", "e", "p", fetchImpl)).rejects.toThrow(
      "the login response had no access_token",
    );
  });
});
