// SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from "vitest";
import {
  CaseApiError,
  citationAnchors,
  citedRecords,
  describeCaseReason,
  toCaseApiError,
} from "@/lib/governedCases";
import { axiosError, memoFixture } from "./fixtures/governedCase";

describe("governed case API errors", () => {
  it("keeps the API's stable reason code and detail", () => {
    const error = toCaseApiError(axiosError(404, { error: { reason: "governed_cases_disabled", detail: "" } }));
    expect(error).toBeInstanceOf(CaseApiError);
    expect(error.reason).toBe("governed_cases_disabled");
    expect(error.status).toBe(404);
    expect(describeCaseReason(error.reason)).toMatch(/not enabled/);
  });

  it("reduces responses without a reason to a status class, never to success", () => {
    expect(toCaseApiError(axiosError(422, { detail: [{ msg: "bad" }] })).reason).toBe("request_invalid");
    expect(toCaseApiError(axiosError(403, {})).reason).toBe("forbidden");
    expect(toCaseApiError(axiosError(502, "<html>")).reason).toBe("server_error");
    expect(toCaseApiError(new Error("offline")).reason).toBe("network_error");
  });

  it("shows an unknown reason code rather than hiding it", () => {
    expect(describeCaseReason("something_new")).toContain("something_new");
  });
});

describe("memo citations", () => {
  it("indexes every distinct cited record once, with the sections, fields and excerpts that cite it", () => {
    const records = citedRecords(memoFixture());
    const keys = records.map((r) => r.record_id);
    expect(new Set(keys).size).toBe(keys.length);
    expect(keys).toEqual([
      "mock:company:00000001:profile",
      "mock:company:00000001:owners",
      "mock:screening:scr-0000000000000001",
      "mock:watchlist:entry:0007",
    ]);
    const watchlist = records.find((r) => r.record_id === "mock:watchlist:entry:0007");
    expect(watchlist?.sections).toEqual(["screening"]);
    expect(watchlist?.excerpts.map((e) => e.excerpt_ref)).toEqual(["excerpt:mock-watchlist-0007"]);
  });

  it("gives every cited record and attached excerpt an attribute-safe anchor", () => {
    const memo = memoFixture();
    const anchors = citationAnchors(memo);
    for (const record of citedRecords(memo)) {
      expect(anchors.recordId(record.provider, record.record_id)).toMatch(/^cited-record-\d+$/);
    }
    expect(anchors.excerptId("excerpt:mock-watchlist-0007")).toBe("excerpt-1");
    expect(anchors.excerptId("excerpt:not-attached")).toBeNull();
  });
});
