// SPDX-License-Identifier: Apache-2.0
/**
 * Global teardown: end the demo accounts' sessions after a run.
 *
 * Specs sign in as the seeded demo users and send their tokens as bearer
 * headers, which a failing request's call log can print into the reports the
 * pipeline uploads. Revoking every session of those accounts (`logout-all`
 * sets the user's `sessions_invalid_before`) makes any such token dead first.
 * Best effort: a failure is reported, never thrown, so it cannot hide the
 * run's own result. Runs only when `E2E_REVOKE_DEMO_SESSIONS=1`, which the
 * deploy workflow sets; the E2E account itself is revoked by that workflow,
 * because its revocation gates the artifact upload.
 */
import { writeSync } from "node:fs";

import { APP, DEMO_ROLE_CREDENTIALS, DEMO_USER_CREDENTIALS, demoPasswordFromEnv } from "./auth";
import { loginForToken } from "./session";

const ACCOUNTS = { user: DEMO_USER_CREDENTIALS, ...DEMO_ROLE_CREDENTIALS } as const;

export default async function revokeDemoSessions(): Promise<void> {
  if (process.env.E2E_REVOKE_DEMO_SESSIONS !== "1") return;
  for (const [name, creds] of Object.entries(ACCOUNTS)) {
    const password = demoPasswordFromEnv(name as keyof typeof ACCOUNTS);
    if (!password) continue;
    try {
      const token = await loginForToken(APP, creds.email, password);
      if (process.env.GITHUB_ACTIONS === "true") writeSync(1, `::add-mask::${token}\n`);
      const resp = await fetch(`${APP}/api/v1/auth/logout-all`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
      });
      writeSync(
        1,
        resp.ok
          ? `demo account ${name}: sessions revoked\n`
          : `::warning::demo account ${name}: sessions not revoked (logout-all returned HTTP ${resp.status})\n`,
      );
    } catch (err) {
      writeSync(
        1,
        `::warning::demo account ${name}: sessions not revoked (${err instanceof Error ? err.message : String(err)})\n`,
      );
    }
  }
}
