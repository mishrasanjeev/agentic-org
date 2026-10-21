// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
/**
 * Waiting for a knowledge upload to become searchable.
 *
 * An upload is indexed inside the request when the embedding service answers.
 * When it does not (the hosted embedding service scales to zero and a cold
 * start took about 80 s on 2026-10-20), the upload is stored as `processing`
 * with `embedding_temporarily_unavailable`, the page says "Retry pending",
 * and the maintenance worker retries it (core/rag/upload_recovery.py: 30 s,
 * then 60 s, on a sweep every 60 s). That round trip can never fit the
 * suite's 60 s test timeout.
 *
 * KNOWLEDGE_INDEX_BUDGET_MS covers the first two retries in the worst sweep
 * phase. tests/regression/test_knowledge_upload_e2e_budget.py replays the
 * product's retry schedule against this number, so changing either one
 * without the other fails there.
 */
import type { Locator, Response } from "@playwright/test";

import { expect } from "./test";

export const KNOWLEDGE_INDEX_BUDGET_MS = 240_000;

/** Test timeout for a spec that waits for indexing: the budget plus the rest of the flow. */
export const KNOWLEDGE_INDEX_TEST_TIMEOUT_MS = KNOWLEDGE_INDEX_BUDGET_MS + 60_000;

const RETRY_PENDING = "Indexing temporarily unavailable. Retry pending.";

export type AcceptedUpload = {
  document_id: string;
  status: string;
  ingestion_error?: string | null;
};

/** Reads the 201 upload response and checks it is one of the two states the product promises. */
export async function acceptedUpload(upload: Response): Promise<AcceptedUpload> {
  expect(upload.status()).toBe(201);
  const accepted = (await upload.json()) as AcceptedUpload;
  expect(accepted.document_id).toBeTruthy();
  if (accepted.status !== "indexed") {
    // Only a transient embedding failure may leave an accepted upload unindexed.
    expect(accepted, "an accepted upload that is not indexed must be a pending retry").toMatchObject({
      status: "processing",
      ingestion_error: "embedding_temporarily_unavailable",
    });
  }
  return accepted;
}

/**
 * Waits until the document's row reaches a final state and requires `indexed`.
 * A pending retry must be shown as such while it waits; `failed` ends the wait
 * at once instead of running out the budget.
 */
export async function expectRowIndexed(row: Locator, accepted: AcceptedUpload): Promise<void> {
  const status = row.getByText(/^(processing|indexed|failed)$/);
  if (accepted.status === "processing") {
    await expect(row).toContainText(RETRY_PENDING);
  }
  await expect(status).toHaveText(/^(indexed|failed)$/, { timeout: KNOWLEDGE_INDEX_BUDGET_MS });
  await expect(status).toHaveText("indexed");
  await expect(row).not.toContainText("Retry pending");
}
