// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
import { useSyncExternalStore } from "react";

const COMPANY_SCOPE_EVENT = "agenticorg:company-scope-changed";

function selectedCompanyId(): string {
  try {
    return localStorage.getItem("company_id") || "";
  } catch {
    return "";
  }
}

export function setSelectedCompanyId(id: string): void {
  if (id) localStorage.setItem("company_id", id);
  else localStorage.removeItem("company_id");
  window.dispatchEvent(new Event(COMPANY_SCOPE_EVENT));
}

function subscribe(onChange: () => void): () => void {
  const onStorage = (event: StorageEvent) => {
    if (event.key === "company_id" || event.key === null) onChange();
  };
  window.addEventListener(COMPANY_SCOPE_EVENT, onChange);
  window.addEventListener("storage", onStorage);
  return () => {
    window.removeEventListener(COMPANY_SCOPE_EVENT, onChange);
    window.removeEventListener("storage", onStorage);
  };
}

export function useSelectedCompanyId(): string {
  return useSyncExternalStore(subscribe, selectedCompanyId, () => "");
}
