// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
// The sign-in page and Settings carry the one-line platform licence notice, with a link to the
// licensing page, in English and Hindi.
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import en from "../locales/en.json";
import hi from "../locales/hi.json";

vi.mock("@/contexts/AuthContext", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/contexts/AuthContext")>();
  return { ...actual, useAuth: () => ({ login: vi.fn(), loginWithGoogle: vi.fn(), completeMfa: vi.fn() }) };
});

vi.mock("@/contexts/BrandingContext", () => ({
  useBranding: () => ({ productName: "AgenticOrg", logoUrl: null }),
}));

vi.mock("@/lib/api", () => ({
  default: {
    get: vi.fn().mockRejectedValue(new Error("no api in unit tests")),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
}));

import Login from "@/pages/Login";
import Settings from "@/pages/Settings";

const NOTICE =
  "Licensed under the Orchestrum Personal Use License 1.0: use by or for an organisation needs a signed agreement with Orchestrum Technologies LLP.";

function expectNotice() {
  const notice = screen.getByTestId("licence-notice");
  expect(notice).toHaveTextContent(NOTICE);
  expect(screen.getByRole("link", { name: "Licence terms" })).toHaveAttribute("href", "/docs/licensing");
}

describe("licence notice", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("en");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ json: async () => ({}) }));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("appears below the sign-in form", () => {
    render(
      <MemoryRouter initialEntries={["/login"]}>
        <Login />
      </MemoryRouter>,
    );
    expectNotice();
  });

  it("appears on the Settings page", () => {
    render(
      <MemoryRouter initialEntries={["/dashboard/settings"]}>
        <Settings />
      </MemoryRouter>,
    );
    expectNotice();
  });

  it("has English and Hindi strings", () => {
    expect(en.licence.notice).toBe(NOTICE);
    expect(Object.keys(hi.licence).sort()).toEqual(Object.keys(en.licence).sort());
    expect(hi.licence.notice).toContain("Orchestrum Personal Use License 1.0");
    expect(hi.licence.notice).toContain("Orchestrum Technologies LLP");
    expect(hi.licence.notice).not.toBe(en.licence.notice);
  });
});
