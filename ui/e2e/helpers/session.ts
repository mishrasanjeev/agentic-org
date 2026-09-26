// SPDX-License-Identifier: Apache-2.0
/**
 * Lifetime of the shared E2E session token.
 *
 * Kept free of Playwright imports so the logic is unit-tested with the rest
 * of the frontend (`session.test.ts`, which the vitest config includes).
 * `./auth` wires it to the suite's `E2E_TOKEN`.
 */

/** Log in again when less than this much of the session is left. */
export const SESSION_REFRESH_MARGIN_MS = 15 * 60_000;
/** Minimum gap between login attempts after a failure, so a failing login is not hammered. */
export const RELOGIN_RETRY_MS = 60_000;

/** The `exp` of a JWT session token in epoch milliseconds, or null when the token is not a JWT. */
export function sessionExpiresAt(token: string): number | null {
  const parts = token.split(".");
  if (parts.length !== 3) return null;
  try {
    const base64 = parts[1].replace(/-/g, "+").replace(/_/g, "/");
    const payload: unknown = JSON.parse(atob(base64.padEnd(Math.ceil(base64.length / 4) * 4, "=")));
    const exp = (payload as { exp?: unknown } | null)?.exp;
    return typeof exp === "number" ? exp * 1000 : null;
  } catch {
    return null;
  }
}

/** Log in with a password and return the session token (`access_token`). */
export async function loginForToken(
  baseUrl: string,
  email: string,
  password: string,
  fetchImpl: typeof fetch = fetch,
): Promise<string> {
  const resp = await fetchImpl(`${baseUrl}/api/v1/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!resp.ok) throw new Error(`login returned HTTP ${resp.status}`);
  const data: unknown = await resp.json();
  const token = (data as { access_token?: unknown } | null)?.access_token;
  if (typeof token !== "string" || !token) throw new Error("the login response had no access_token");
  return token;
}

/**
 * Hands out a session token that is not about to expire.
 *
 * `fresh()` returns the current token while more than 15 minutes of it are
 * left. Otherwise it logs in again and returns the new token. A failed login
 * is retried at most once a minute; until the token actually expires the old
 * one is still returned. Once it has expired and cannot be renewed, `fresh()`
 * throws with the reason, so the run fails on the real cause instead of on a
 * spec's assertions about 401 responses.
 */
export class SessionKeeper {
  private lastAttempt = Number.NEGATIVE_INFINITY;
  private lastFailure = "no login has been attempted yet";

  constructor(
    private currentToken: string,
    private readonly login: () => Promise<string>,
  ) {}

  get token(): string {
    return this.currentToken;
  }

  async fresh(now: number): Promise<string> {
    if (!this.currentToken) return this.currentToken;
    const expiresAt = sessionExpiresAt(this.currentToken);
    if (expiresAt === null || expiresAt - now > SESSION_REFRESH_MARGIN_MS) return this.currentToken;
    if (now - this.lastAttempt >= RELOGIN_RETRY_MS) {
      this.lastAttempt = now;
      try {
        this.currentToken = await this.login();
        return this.currentToken;
      } catch (err) {
        this.lastFailure = err instanceof Error ? err.message : String(err);
      }
    }
    if (expiresAt <= now) {
      throw new Error(
        `The E2E session expired at ${new Date(expiresAt).toISOString()} and could not be renewed: ` +
          `${this.lastFailure}. Set E2E_EMAIL and E2E_PASSWORD so the suite can log in again.`,
      );
    }
    return this.currentToken;
  }
}
