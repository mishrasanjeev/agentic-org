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
/**
 * Minimum gap between login attempts after a failure, shared by every worker
 * of a run: at most four attempts a minute, under the login endpoint's five
 * per minute, and short enough that a worker waiting it out (see
 * `SessionKeeper`) stays within Playwright's fixture timeout.
 */
export const RELOGIN_RETRY_MS = 15_000;

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
 * Where failed logins are recorded, so the retry gap holds across processes.
 *
 * Playwright starts a new worker process after every failed test, and each
 * one builds its own `SessionKeeper`. Without a shared record, an expired
 * token that cannot be renewed would cost one login attempt per test.
 */
export interface LoginFailureLog {
  read(): { at: number; reason: string } | null;
  write(at: number, reason: string): void;
}

/**
 * Hands out a session token that is not about to expire.
 *
 * `fresh()` returns the current token while more than 15 minutes of it are
 * left. Otherwise it logs in again and returns the new token. A failed login
 * is retried at most once every `RELOGIN_RETRY_MS`, across every keeper
 * sharing the same `LoginFailureLog`; until the token actually expires the
 * old one is still returned. A keeper whose token has already expired, and
 * whose retry is held back only by another keeper's failure, waits for the
 * rest of that gap and then tries once: a new worker starts with the runner's
 * original token, so failing it outright would fail every test started in
 * that gap over one transient error. Once the token has expired and cannot be renewed,
 * `fresh()` throws with the reason, so the run fails on the real cause
 * instead of on a spec's assertions about 401 responses.
 */
export class SessionKeeper {
  private lastAttempt = Number.NEGATIVE_INFINITY;
  private lastFailure = "no login has been attempted yet";

  constructor(
    private currentToken: string,
    private readonly login: () => Promise<string>,
    private readonly failures: LoginFailureLog | null = null,
    private readonly sleep: (ms: number) => Promise<void> = (ms) => new Promise((r) => setTimeout(r, ms)),
  ) {}

  get token(): string {
    return this.currentToken;
  }

  async fresh(now: number): Promise<string> {
    if (!this.currentToken) return this.currentToken;
    const expiresAt = sessionExpiresAt(this.currentToken);
    if (expiresAt === null || expiresAt - now > SESSION_REFRESH_MARGIN_MS) return this.currentToken;
    const shared = this.failures?.read() ?? null;
    if (shared && shared.at > this.lastAttempt) this.lastFailure = shared.reason;
    const blockedBy = Math.max(this.lastAttempt, shared?.at ?? Number.NEGATIVE_INFINITY);
    const remaining = RELOGIN_RETRY_MS - (now - blockedBy);
    if (remaining > 0 && expiresAt <= now && blockedBy !== this.lastAttempt) {
      await this.sleep(remaining);
      now += remaining;
    }
    if (now - blockedBy >= RELOGIN_RETRY_MS) {
      this.lastAttempt = now;
      try {
        this.currentToken = await this.login();
        return this.currentToken;
      } catch (err) {
        this.lastFailure = err instanceof Error ? err.message : String(err);
        this.failures?.write(now, this.lastFailure);
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
