// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
import { Link } from "react-router";
import { useTranslation } from "react-i18next";

/** The two pages of the marketing area: operations (this page) and ABM accounts. */
export default function MarketingTabs({ current }: { current: "operations" | "abm" }) {
  const { t } = useTranslation();
  const tab = (key: "operations" | "abm", to: string, label: string) => (
    <Link
      to={to}
      aria-current={current === key ? "page" : undefined}
      className={`rounded-md border px-3 py-1.5 text-xs ${current === key ? "bg-muted font-medium" : "hover:bg-muted"}`}
    >
      {label}
    </Link>
  );
  return (
    <nav aria-label={t("marketingOps.tabs", "Marketing pages")} className="flex gap-2">
      {tab("operations", "/dashboard/marketing-ops", t("marketingOps.operations", "Operations"))}
      {tab("abm", "/dashboard/marketing-ops/abm", t("marketingOps.abm", "ABM accounts"))}
    </nav>
  );
}
