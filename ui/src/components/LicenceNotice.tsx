// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
import { Link } from "react-router";
import { useTranslation } from "react-i18next";
import { LICENSING_PATH } from "./ProductOwnership";

interface LicenceNoticeProps {
  className?: string;
}

/** One-line platform licence notice with a link to the licensing page. */
export default function LicenceNotice({ className = "" }: LicenceNoticeProps) {
  const { t } = useTranslation();
  return (
    <p data-testid="licence-notice" className={`text-xs leading-relaxed text-muted-foreground ${className}`.trim()}>
      {t("licence.notice")}{" "}
      {/* Underlined so the link is not distinguished by colour alone (WCAG 1.4.1). */}
      <Link to={LICENSING_PATH} className="text-foreground underline underline-offset-2 hover:opacity-80 transition-opacity">
        {t("licence.link")}
      </Link>
    </p>
  );
}
