export const PRODUCT_OWNER = "Orchestrum Technologies LLP";
export const INVENTOR_OWNER = "Sanjeev Kumar";
export const PRIMARY_CONTACT_EMAIL = "sanjeev@orchestrum.in";
export const SECONDARY_CONTACT_EMAIL = "mishra.sanjeev@gmail.com";

interface ProductOwnershipProps {
  tone?: "dark" | "light";
  compact?: boolean;
  className?: string;
}

export default function ProductOwnership({
  tone = "dark",
  compact = false,
  className = "",
}: ProductOwnershipProps) {
  const textClass = tone === "dark" ? "text-slate-400" : "text-slate-600";
  const strongClass = tone === "dark" ? "text-slate-200" : "text-slate-900";
  const linkClass = tone === "dark"
    ? "text-slate-300 hover:text-white"
    : "text-blue-700 hover:text-blue-900";

  return (
    <div
      data-testid="product-ownership"
      className={`${textClass} ${compact ? "text-xs" : "text-sm"} leading-relaxed ${className}`.trim()}
    >
      <p>
        AgenticOrg is owned by <strong className={strongClass}>{PRODUCT_OWNER}</strong>.
      </p>
      <p>
        Inventor / Owner: <strong className={strongClass}>{INVENTOR_OWNER}</strong>
      </p>
      <p className="flex flex-wrap gap-x-2">
        <span>Contact:</span>
        <a className={`${linkClass} break-all transition-colors`} href={`mailto:${PRIMARY_CONTACT_EMAIL}`}>
          {PRIMARY_CONTACT_EMAIL}
        </a>
        <span aria-hidden="true">|</span>
        <a className={`${linkClass} break-all transition-colors`} href={`mailto:${SECONDARY_CONTACT_EMAIL}`}>
          {SECONDARY_CONTACT_EMAIL}
        </a>
      </p>
    </div>
  );
}
