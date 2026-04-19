import {
  ReactNode,
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

/**
 * BrandingContext — fetches white-label branding from /api/v1/branding
 * once on mount and applies the colors as CSS custom properties so the
 * Tailwind theme picks them up. Falls back to AgenticOrg defaults.
 *
 * Lookup uses the current window.location.host so the backend can match
 * a custom domain like ``app.customer.com`` to the customer's tenant.
 */

export interface Branding {
  productName: string;
  logoUrl: string | null;
  faviconUrl: string | null;
  primaryColor: string;
  accentColor: string;
  supportEmail: string | null;
  footerText: string | null;
}

const DEFAULT_BRANDING: Branding = {
  productName: "AgenticOrg",
  logoUrl: null,
  faviconUrl: null,
  primaryColor: "#7c3aed",
  accentColor: "#1e293b",
  supportEmail: "sanjeev@agenticorg.ai",
  footerText: null,
};

const BrandingContext = createContext<Branding>(DEFAULT_BRANDING);

export function useBranding(): Branding {
  return useContext(BrandingContext);
}

interface ApiBranding {
  product_name: string;
  logo_url: string | null;
  favicon_url: string | null;
  primary_color: string;
  accent_color: string;
  support_email: string | null;
  footer_text: string | null;
}

function applyToCssVars(b: Branding) {
  const root = document.documentElement;
  root.style.setProperty("--brand-primary", b.primaryColor);
  root.style.setProperty("--brand-accent", b.accentColor);
}

function applyFavicon(url: string | null) {
  if (!url) return;
  const link =
    (document.querySelector("link[rel='icon']") as HTMLLinkElement | null) ||
    (() => {
      const l = document.createElement("link");
      l.rel = "icon";
      document.head.appendChild(l);
      return l;
    })();
  link.href = url;
}

export function BrandingProvider({ children }: { children: ReactNode }) {
  const [branding, setBranding] = useState<Branding>(DEFAULT_BRANDING);

  useEffect(() => {
    // Host is now derived server-side from the request headers
    // (LOW-14 remediation — client-supplied host enabled enumeration).
    const url = `/api/v1/branding`;

    fetch(url)
      .then((r) => (r.ok ? r.json() : null))
      .then((api: ApiBranding | null) => {
        if (!api) return;
        const next: Branding = {
          productName: api.product_name,
          logoUrl: api.logo_url,
          faviconUrl: api.favicon_url,
          primaryColor: api.primary_color,
          accentColor: api.accent_color,
          supportEmail: api.support_email,
          footerText: api.footer_text,
        };
        setBranding(next);
        applyToCssVars(next);
        applyFavicon(next.faviconUrl);
        if (next.productName) {
          document.title = next.productName;
        }
      })
      .catch(() => {
        // Best effort — fall back to default branding silently.
      });
  }, []);

  const value = useMemo(() => branding, [branding]);
  return <BrandingContext.Provider value={value}>{children}</BrandingContext.Provider>;
}
