// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setSelectedCompanyId, useSelectedCompanyId } from "@/lib/companyScope";

describe("selected company subscription", () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => vi.restoreAllMocks());

  it("reacts to selection and clearing in the same page", () => {
    const { result } = renderHook(useSelectedCompanyId);
    expect(result.current).toBe("");
    act(() => setSelectedCompanyId("company-a"));
    expect(result.current).toBe("company-a");
    act(() => setSelectedCompanyId(""));
    expect(result.current).toBe("");
  });

  it("reads a stored selection on mount and follows another tab", () => {
    localStorage.setItem("company_id", "company-a");
    const { result } = renderHook(useSelectedCompanyId);
    expect(result.current).toBe("company-a");
    act(() => {
      localStorage.setItem("company_id", "company-b");
      window.dispatchEvent(new StorageEvent("storage", { key: "company_id" }));
    });
    expect(result.current).toBe("company-b");
    act(() => {
      localStorage.clear();
      window.dispatchEvent(new StorageEvent("storage", { key: null }));
    });
    expect(result.current).toBe("");
  });

  it("removes both subscriptions on unmount", () => {
    const remove = vi.spyOn(window, "removeEventListener");
    const { unmount } = renderHook(useSelectedCompanyId);
    unmount();
    expect(remove).toHaveBeenCalledWith("agenticorg:company-scope-changed", expect.any(Function));
    expect(remove).toHaveBeenCalledWith("storage", expect.any(Function));
  });
});
