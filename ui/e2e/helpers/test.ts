/**
 * `test` and `expect` for specs that use the shared E2E session.
 *
 * The session token lasts 60 minutes and the production suite runs for
 * longer. These automatic fixtures log in again before the token gets close
 * to expiry: once when a worker starts, before any `beforeAll` hook, and
 * before every test. Import `E2E_TOKEN` from `./auth` (a live binding) and
 * read it where it is used, so each test sees the current token.
 */
import { expect, test as base } from "@playwright/test";

import { ensureFreshE2EToken } from "./auth";

export const test = base.extend<{ _e2eSession: void }, { _e2eSessionWorker: void }>({
  _e2eSessionWorker: [
    async ({}, use) => {
      await ensureFreshE2EToken();
      await use();
    },
    { scope: "worker", auto: true },
  ],
  _e2eSession: [
    async ({}, use) => {
      await ensureFreshE2EToken();
      await use();
    },
    { auto: true },
  ],
});

export { expect };
